//******************************************************************************
//  radar_configuration.c
//
//  Clock/GPIO/UART/ADC/Timer/DMA setup, plus the low-level UART frame
//  helpers used to stream data to the host PC. Extracted from main.c so
//  this setup code can be reused/reasoned about independently of the
//  main loop and ISRs (which stay in main.c, since ADC12_B_ISR is the
//  actual interrupt entry point and needs to be where the ring buffer
//  it fills is easiest to see alongside it).
//
//  Frame types on the wire (all prefixed [0xAA][0x55], multiplexed on one
//  UART stream, told apart by the marker byte):
//    (no marker, legacy)  raw IFI/IFQ sample pair          -- UART_putFrame(), currently unused
//    0xC0                 spectrogram column (256 bytes)   -- UART_putSpectrogramColumn_DMA(), active
//    0xC2                 1 Hz ADC sample-rate snapshot    -- UART_putCountFrame()
//    0xC3                 1 Hz STFT/DMA timing snapshot    -- UART_putProfileFrame()
//******************************************************************************

#include "radar_configuration.h"

//******************************************************************************
// Globals
//******************************************************************************

// LEGACY / currently unused: were meant to hold the latest single IFI/IFQ
// sample for the old raw-frame streaming path (UART_putFrame(), below).
// Current firmware's ADC12_B ISR (in main.c) writes samples straight into
// I_queue/Q_queue instead, so nothing updates these two anymore. Left in
// place (rather than deleted) in case a future debug build wants a
// single-sample raw tap again -- but don't expect these to hold anything
// meaningful in the current build.
volatile int16_t IFI_result = 0;
volatile int16_t IFQ_result = 0;

volatile uint16_t I_queue[N_SAMPLES];
volatile uint16_t Q_queue[N_SAMPLES];
volatile int samples_index_in = 0;
volatile int samples_index_out = 0;

// Test 1: free-running ADC sample counter + 1 Hz snapshot state
volatile uint16_t adc_sample_count = 0;
volatile uint16_t count_snapshot = 0;
volatile bool count_snapshot_ready = false;

// Profiling: per-hop timing accumulators + 1 Hz snapshot state
volatile uint16_t stft_ticks_accum = 0;
volatile uint16_t dma_wait_ticks_accum = 0;
volatile uint16_t hop_count_accum = 0;
volatile uint16_t stft_ticks_snapshot = 0;
volatile uint16_t dma_wait_ticks_snapshot = 0;
volatile uint16_t hop_count_snapshot = 0;
volatile bool profile_snapshot_ready = false;

// Single contiguous buffer holding the 3-byte header + 256-byte column
// body, so the DMA can transfer it as one linear block.
static int8_t dma_tx_buffer[259];

//******************************************************************************
// Minimal UART helpers (blocking, no printf/retargeting needed)
//******************************************************************************
void UART_putc(uint8_t c)
{
    while (!(UCA0IFG & UCTXIFG));
    UCA0TXBUF = c;
}

void UART_puts(const char *s)
{
    while (*s) UART_putc((uint8_t)*s++);
}

// Writes a 16-bit unsigned value as decimal ASCII (max 4095 for 12-bit ADC).
// Not currently called by anything -- kept as a small debug utility.
void UART_putU16(uint16_t val)
{
    char buf[6];
    int8_t i = 5;
    buf[5] = '\0';

    if (val == 0)
    {
        UART_putc('0');
        return;
    }
    while (val > 0 && i > 0)
    {
        i--;
        buf[i] = (char)('0' + (val % 10));
        val /= 10;
    }
    UART_puts(&buf[i]);
}

//******************************************************************************
// Binary frame helpers (replace ASCII CSV for higher throughput)
//******************************************************************************

// LEGACY / currently unused: raw single-sample frame, no marker byte.
// [0xAA][0x55][IFI_lo][IFI_hi][IFQ_lo][IFQ_hi], little-endian, 6 bytes.
// Superseded by the on-chip STFT path (UART_putSpectrogramColumn_DMA(),
// below) -- kept for anyone who wants to fall back to streaming raw I/Q
// for debugging without recompiling STFT.c out of the build.
void UART_putFrame(uint16_t ifi, uint16_t ifq)
{
    UART_putc(0xAA);
    UART_putc(0x55);
    UART_putc((uint8_t)(ifi & 0xFF));
    UART_putc((uint8_t)((ifi >> 8) & 0xFF));
    UART_putc((uint8_t)(ifq & 0xFF));
    UART_putc((uint8_t)((ifq >> 8) & 0xFF));
}

// Sends one finished spectrogram column via DMA (non-blocking): [0xAA][0x55]
// [0xC0][len bytes of column data]. The 0xC0 marker is what lets the PC
// side (STFT_check.py / radar_stft_capture.py) tell this apart from the
// legacy raw-frame type above.
//
// Sequencing matters here -- read the numbered steps in order:
//   1-2. Pack the full 3-byte header + column body into one contiguous
//        buffer, since the DMA needs a single linear source region.
//   3-4. Point the DMA at everything AFTER the first header byte (the
//        first byte is sent manually in step 7, see why below).
//   5.   Clear UCTXIFG so the DMA doesn't see a stale flag and fire early.
//   6.   Arm the DMA channel (it now waits for a UCTXIFG rising edge).
//   7.   Manually write the first byte (0xAA) to UCA0TXBUF. This is the
//        trigger: once 0xAA moves into the shift register, the hardware
//        raises UCTXIFG, which the armed DMA sees as its rising-edge
//        trigger and takes over seamlessly for the rest of the buffer.
//        Without this manual kick, nothing would ever trigger the first
//        DMA transfer.
void UART_putSpectrogramColumn_DMA(int8_t *column, int len)
{
    dma_tx_buffer[0] = (int8_t)0xAA;
    dma_tx_buffer[1] = (int8_t)0x55;
    dma_tx_buffer[2] = (int8_t)0xC0;

    int i;
    for (i = 0; i < len; i++) {
        dma_tx_buffer[3 + i] = column[i];
    }

    DMA_setSrcAddress(DMA_CHANNEL_0, (uint32_t)&dma_tx_buffer[1], DMA_DIRECTION_INCREMENT);
    DMA_setTransferSize(DMA_CHANNEL_0, len + 2);

    UCA0IFG &= ~UCTXIFG;
    DMA_enableTransfers(DMA_CHANNEL_0);

    UCA0TXBUF = dma_tx_buffer[0];
}

// Frame (5 bytes): [0xAA][0x55][0xC2][count_lo][count_hi]. Distinct
// marker (0xC2) from the spectrogram-column marker (0xC0) lets the PC
// side tell the two frame types apart on the same multiplexed stream.
//
// Sent with blocking UART_putc (only 5 bytes, once a second) from the
// main loop -- NOT from the Timer_A1 ISR -- and only when
// dma_tx_in_progress is false, so it never races with an in-flight DMA
// column transfer writing to the same UCA0TXBUF.
void UART_putCountFrame(uint16_t count)
{
    UART_putc(0xAA);
    UART_putc(0x55);
    UART_putc(0xC2);
    UART_putc((uint8_t)(count & 0xFF));
    UART_putc((uint8_t)((count >> 8) & 0xFF));
}

// Frame (9 bytes): [0xAA][0x55][0xC3][hop_lo][hop_hi][stft_lo][stft_hi][dma_lo][dma_hi]
// hop_count = hops processed in the last 1 s window.
// stft_ticks / dma_wait_ticks = summed Timer_A1/ACLK ticks (32768 Hz)
// spent in STFT_compute_next_segment() / the DMA-wait loop over that same
// window. Divide by 32.768 to convert to milliseconds. Same multiplexing
// pattern as the 0xC0/0xC2 frames above, distinct marker (0xC3).
void UART_putProfileFrame(uint16_t hop_count, uint16_t stft_ticks, uint16_t dma_wait_ticks)
{
    UART_putc(0xAA);
    UART_putc(0x55);
    UART_putc(0xC3);
    UART_putc((uint8_t)(hop_count & 0xFF));
    UART_putc((uint8_t)((hop_count >> 8) & 0xFF));
    UART_putc((uint8_t)(stft_ticks & 0xFF));
    UART_putc((uint8_t)((stft_ticks >> 8) & 0xFF));
    UART_putc((uint8_t)(dma_wait_ticks & 0xFF));
    UART_putc((uint8_t)((dma_wait_ticks >> 8) & 0xFF));
}

//******************************************************************************
// Init Functions
//******************************************************************************
void Init_Clock(void)
{
    // Bump DCO to a true 8 MHz so 115200 baud can be generated accurately.
    // FRAM needs 0 wait states up to 8 MHz, so no FRCTL change is needed.
    CS_setDCOFreq(CS_DCORSEL_1, CS_DCOFSEL_3);           // DCO = 8 MHz
    CS_initClockSignal(CS_MCLK,  CS_DCOCLK_SELECT, CS_CLOCK_DIVIDER_1);
    CS_initClockSignal(CS_SMCLK, CS_DCOCLK_SELECT, CS_CLOCK_DIVIDER_1);

    // ACLK sourced from LFXTCLK -- the FR5994 LaunchPad ships with the
    // 32.768 kHz crystal (Y1) populated, so this is available and gives
    // an exact 32768 Hz ACLK, independent of MCLK/SMCLK and independent
    // of the ADC-trigger timer's clock (Timer_A2 runs off SMCLK). This is
    // what Init_RateTimer()'s 1 Hz tick is built on.
    //
    // NOTE: CS_REFOCLK_SELECT is NOT a valid macro on the FR5xx_6xx
    // DriverLib family (that name exists on FR2xx_4xx/MSP432 DriverLib,
    // which use a different CS module). The FR5xx_6xx cs.h only defines
    // CS_VLOCLK_SELECT, CS_DCOCLK_SELECT, CS_LFXTCLK_SELECT,
    // CS_HFXTCLK_SELECT, CS_LFMODOSC_SELECT, CS_MODOSC_SELECT -- confirmed
    // directly from TI's FR5xx_6xx cs.h source, not guessed.
    //
    // Starting the crystal requires its dedicated pins (PJ.4/PJ.5) to
    // already be muxed to crystal function -- see Init_GPIO(), which runs
    // BEFORE this function (see main.c ordering) specifically so those
    // pins (and the LPM5 unlock they depend on) are ready before
    // CS_turnOnLFXT() tries to start the oscillator.
    CS_setExternalClockSource(32768, 0);
    CS_turnOnLFXT(CS_LFXT_DRIVE_3);   // blocks until the oscillator fault flag clears
    CS_initClockSignal(CS_ACLK, CS_LFXTCLK_SELECT, CS_CLOCK_DIVIDER_1);
}

void Init_GPIO(void)
{
    // ADC input pins: P3.0 = A12 (IFI), P3.1 = A13 (IFQ)
    // Analog function on FR5xx/6xx requires both PxSEL1 and PxSEL0 set,
    // which DriverLib exposes as GPIO_TERNARY_MODULE_FUNCTION.
    GPIO_setAsPeripheralModuleFunctionInputPin(
        GPIO_PORT_P3, GPIO_PIN0 | GPIO_PIN1, GPIO_TERNARY_MODULE_FUNCTION);

    // UART pins: P2.0 = UCA0TXD, P2.1 = UCA0RXD (LaunchPad backchannel).
    // On the FR5994, UART on P2.0/P2.1 is the SECONDARY module function.
    GPIO_setAsPeripheralModuleFunctionInputPin(
        GPIO_PORT_P2, GPIO_PIN0 | GPIO_PIN1, GPIO_SECONDARY_MODULE_FUNCTION);

    // LFXT crystal pins: PJ.4 = XIN, PJ.5 = XOUT (dedicated crystal pins
    // on the FR5994's 80-pin package, primary module function). Must be
    // muxed to crystal function -- and LPM5 unlocked (below) -- before
    // Init_Clock() calls CS_turnOnLFXT(), which is why Init_GPIO() runs
    // before Init_Clock() in main().
    GPIO_setAsPeripheralModuleFunctionInputPin(
        GPIO_PORT_PJ, GPIO_PIN4 | GPIO_PIN5, GPIO_PRIMARY_MODULE_FUNCTION);

    // Unlock GPIO after ALL pin configuration is done (required on FR5xx/6xx)
    PMM_unlockLPM5();
}

void Init_UART(void)
{
    EUSCI_A_UART_initParam uartConfig = {0};
    uartConfig.selectClockSource = EUSCI_A_UART_CLOCKSOURCE_SMCLK;
    uartConfig.clockPrescalar    = 4;
    uartConfig.firstModReg       = 5;
    uartConfig.secondModReg      = 0x55;
    uartConfig.parity            = EUSCI_A_UART_NO_PARITY;
    uartConfig.msborLsbFirst     = EUSCI_A_UART_LSB_FIRST;
    uartConfig.numberofStopBits  = EUSCI_A_UART_ONE_STOP_BIT;
    uartConfig.uartMode          = EUSCI_A_UART_MODE;
    uartConfig.overSampling      = EUSCI_A_UART_OVERSAMPLING_BAUDRATE_GENERATION;

    EUSCI_A_UART_init(EUSCI_A0_BASE, &uartConfig);
    EUSCI_A_UART_enable(EUSCI_A0_BASE);
}

void Init_ADC(void)
{
    ADC12_B_initParam adcConfig = {0};
    adcConfig.sampleHoldSignalSourceSelect = ADC12_B_SAMPLEHOLDSOURCE_SC;  // TA2 CCR1 output (Table 9-18)
    adcConfig.clockSourceSelect            = ADC12_B_CLOCKSOURCE_ADC12OSC;
    adcConfig.clockSourceDivider           = ADC12_B_CLOCKDIVIDER_1;
    adcConfig.clockSourcePredivider        = ADC12_B_CLOCKPREDIVIDER__1;
    adcConfig.internalChannelMap           = ADC12_B_NOINTCH;

    ADC12_B_init(ADC12_B_BASE, &adcConfig);
    ADC12_B_enable(ADC12_B_BASE);

    ADC12_B_setupSamplingTimer(ADC12_B_BASE,
        ADC12_B_CYCLEHOLD_128_CYCLES, ADC12_B_CYCLEHOLD_128_CYCLES,
        ADC12_B_MULTIPLESAMPLESENABLE);

    // MEM0 = IFI (A12), first in the sequence.
    ADC12_B_configureMemoryParam memParam0 = {0};
    memParam0.memoryBufferControlIndex = ADC12_B_MEMORY_0;
    memParam0.inputSourceSelect        = ADC12_B_INPUT_A12;
    memParam0.refVoltageSourceSelect   = ADC12_B_VREFPOS_AVCC_VREFNEG_VSS;
    memParam0.endOfSequence            = ADC12_B_NOTENDOFSEQUENCE;
    ADC12_B_configureMemory(ADC12_B_BASE, &memParam0);

    // MEM1 = IFQ (A13), last in the sequence -- ADC12_B_ENDOFSEQUENCE here
    // is what makes CONSEQ_1 treat MEM0+MEM1 as one atomic A12->A13 sweep
    // per trigger, and is also what raises ADC12IFG1 (the interrupt this
    // firmware actually waits on) once both conversions are done.
    ADC12_B_configureMemoryParam memParam1 = {0};
    memParam1.memoryBufferControlIndex = ADC12_B_MEMORY_1;
    memParam1.inputSourceSelect        = ADC12_B_INPUT_A13;
    memParam1.refVoltageSourceSelect   = ADC12_B_VREFPOS_AVCC_VREFNEG_VSS;
    memParam1.endOfSequence            = ADC12_B_ENDOFSEQUENCE;
    ADC12_B_configureMemory(ADC12_B_BASE, &memParam1);

    ADC12_B_clearInterrupt(ADC12_B_BASE, 0, ADC12_B_IFG1);
    ADC12_B_enableInterrupt(ADC12_B_BASE, ADC12_B_IE1, 0, 0);

    // "Sequence of channels" mode: one trigger sweeps MEM0 then MEM1
    // automatically. NOTE: this also auto-clears ADC12ENC after each
    // completed sequence -- Timer2_A0_ISR (main.c) re-arms it
    // (ADC12ENC | ADC12SC) on every 4 kHz tick to compensate. Setting
    // ADC12ENC once here would NOT be enough (confirmed during bring-up).
    ADC12CTL1 = (ADC12CTL1 & ~ADC12CONSEQ_3) | ADC12CONSEQ_1;
}

void Init_TIMER(void)
{
    // TA2 up-mode timer at SAMPLING_RATE_HZ (4 kHz). CCR1's output feeds
    // directly into the ADC12 hardware trigger via ADC12SHSx = 5
    // (Table 9-18) -- no ISR needed on this timer itself, it's a pure
    // hardware signal path from timer to ADC.
    Timer_A_initUpModeParam upParam = {0};
    upParam.clockSource                              = TIMER_A_CLOCKSOURCE_SMCLK;
    upParam.clockSourceDivider                        = TIMER_A_CLOCKSOURCE_DIVIDER_1;
    upParam.timerPeriod                               = (uint16_t)((8000000UL / SAMPLING_RATE_HZ) - 1); // 1999
    upParam.timerInterruptEnable_TAIE                 = TIMER_A_TAIE_INTERRUPT_DISABLE;
    upParam.captureCompareInterruptEnable_CCR0_CCIE   = TIMER_A_CCIE_CCR0_INTERRUPT_ENABLE;
    upParam.timerClear                                = TIMER_A_DO_CLEAR;
    upParam.startTimer                                = false;
    Timer_A_initUpMode(TIMER_A2_BASE, &upParam);

    // CCR1 in compare mode, set/reset output -- produces one trigger
    // pulse per period. The compare value's exact position in the period
    // doesn't matter much; using roughly the midpoint, same idea as the
    // thesis code.
    Timer_A_initCompareModeParam compParam = {0};
    compParam.compareRegister        = TIMER_A_CAPTURECOMPARE_REGISTER_1;
    compParam.compareInterruptEnable = TIMER_A_CAPTURECOMPARE_INTERRUPT_DISABLE;
    compParam.compareOutputMode      = TIMER_A_OUTPUTMODE_SET_RESET;
    compParam.compareValue           = 1000;
    Timer_A_initCompareMode(TIMER_A2_BASE, &compParam);

    Timer_A_startCounter(TIMER_A2_BASE, TIMER_A_UP_MODE);
}

void Init_RateTimer(void)
{
    // Timer_A1, up mode, ACLK (32768 Hz crystal). Period 32767 -> counts
    // 0..32767 (32768 ticks) -> exactly 1 Hz. Deliberately a *different*
    // timer and clock source from Timer_A2 (the ADC trigger, SMCLK-based)
    // so this measurement can't itself perturb the thing it's measuring.
    Timer_A_initUpModeParam upParam = {0};
    upParam.clockSource                              = TIMER_A_CLOCKSOURCE_ACLK;
    upParam.clockSourceDivider                        = TIMER_A_CLOCKSOURCE_DIVIDER_1;
    upParam.timerPeriod                               = 32767;
    upParam.timerInterruptEnable_TAIE                 = TIMER_A_TAIE_INTERRUPT_DISABLE;
    upParam.captureCompareInterruptEnable_CCR0_CCIE   = TIMER_A_CCIE_CCR0_INTERRUPT_ENABLE;
    upParam.timerClear                                = TIMER_A_DO_CLEAR;
    upParam.startTimer                                = false;
    Timer_A_initUpMode(TIMER_A1_BASE, &upParam);

    Timer_A_startCounter(TIMER_A1_BASE, TIMER_A_UP_MODE);
}

void Init_DMA(void) {
    DMA_initParam dmaConfig = {0};
    dmaConfig.channelSelect = DMA_CHANNEL_0;
    dmaConfig.transferModeSelect = DMA_TRANSFER_SINGLE;
    dmaConfig.transferSize = 0; // set right before each transmission (varies: 6 or len+2 bytes)
    dmaConfig.triggerSourceSelect = DMA_TRIGGERSOURCE_15; // route UART TX flag to DMA
    dmaConfig.transferUnitSelect = DMA_SIZE_SRCBYTE_DSTBYTE;
    // DMA_TRIGGER_RISINGEDGE (DMALEVEL = 0) is required for peripheral
    // triggers like UCA0TXIFG. DMA_TRIGGER_HIGH (level-sensitive) is only
    // valid for the external DMAE0 pin trigger -- using it here caused
    // startup resync errors before this was tracked down.
    dmaConfig.triggerTypeSelect = DMA_TRIGGER_RISINGEDGE;

    DMA_init(&dmaConfig);

    // Destination is always the eUSCI_A0 transmit buffer (static address).
    DMA_setDstAddress(DMA_CHANNEL_0, (uint32_t)&UCA0TXBUF, DMA_DIRECTION_UNCHANGED);

    // Interrupt lets the CPU sleep (LPM0) while a transfer is in flight
    // and wake exactly when it's done -- see dma_tx_in_progress in main.c.
    DMA_clearInterrupt(DMA_CHANNEL_0);
    DMA_enableInterrupt(DMA_CHANNEL_0);
}
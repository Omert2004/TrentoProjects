//******************************************************************************
//  radar_configuration.c -- continuous raw I/Q streaming, DMA-driven TX.
//
//  Reverted to the original always-on architecture: the MCU starts
//  sampling at boot and streams every accepted I/Q pair over UART
//  forever, no host-initiated 'S' command, no RX dependency at all
//  (RX was never validated on this board/wiring -- sidestepped
//  entirely rather than debugged further).
//
//  UART TX is DMA-driven (same handshake pattern as the old
//  UART_putSpectrogramColumn_DMA()/DMA_ISR from the STFT project) so the
//  CPU isn't blocked in UART_putc() for the ~520us it takes to shift out
//  a 6-byte frame at 115200 baud -- it sleeps in LPM0 instead and lets
//  the ADC ISR wake it as needed. This does NOT raise the UART's
//  wire-speed ceiling (~11,520 B/s) above what continuous 4 kHz
//  dual-channel streaming needs (~24,000 B/s for 6-byte frames) -- the
//  ring buffer's drop-if-full guard in the ADC ISR (main.c) is what
//  keeps that deficit from corrupting anything, it just means some
//  samples are cleanly skipped rather than sent, during sustained load.
//******************************************************************************

#include "radar_configuration.h"

volatile uint16_t I_queue[N_SAMPLES];
volatile uint16_t Q_queue[N_SAMPLES];
volatile int samples_index_in = 0;
volatile int samples_index_out = 0;

volatile bool dma_tx_in_progress = false;

// 6-byte frame buffer: [0xAA][0x55][IFI_lo][IFI_hi][IFQ_lo][IFQ_hi].
// No marker byte -- this is the only frame type on the wire in this
// build, matching the format raw_print.py / rate_check.py already parse.
static uint8_t dma_tx_buffer[6];

void UART_putc(uint8_t c)
{
    while (!(UCA0IFG & UCTXIFG));
    UCA0TXBUF = c;
}

// Unused in this build (no host command protocol), kept only so
// anything still referencing it compiles; RX was never confirmed
// working on this board and nothing here depends on it.
uint8_t UART_getc_nonblocking(bool *got_byte)
{
    if (UCA0IFG & UCRXIFG)
    {
        *got_byte = true;
        return UCA0RXBUF;
    }
    *got_byte = false;
    return 0;
}

// Sends one IFI/IFQ pair via DMA (non-blocking). Same sequencing as the
// STFT project's UART_putSpectrogramColumn_DMA(): point DMA at
// everything after the first header byte, clear UCTXIFG so the DMA
// doesn't see a stale flag and fire early, arm the DMA, then manually
// write byte 0 -- that's the trigger that raises UCTXIFG and hands off
// to the (already-armed) DMA for the rest of the frame.
void UART_putFrame_DMA(uint16_t ifi, uint16_t ifq)
{
    dma_tx_buffer[0] = 0xAA;
    dma_tx_buffer[1] = 0x55;
    dma_tx_buffer[2] = (uint8_t)(ifi & 0xFF);
    dma_tx_buffer[3] = (uint8_t)((ifi >> 8) & 0xFF);
    dma_tx_buffer[4] = (uint8_t)(ifq & 0xFF);
    dma_tx_buffer[5] = (uint8_t)((ifq >> 8) & 0xFF);

    DMA_setSrcAddress(DMA_CHANNEL_0, (uint32_t)&dma_tx_buffer[1], DMA_DIRECTION_INCREMENT);
    DMA_setTransferSize(DMA_CHANNEL_0, 5);   // remaining 5 bytes after the manual first byte

    // Wait for TXBUF to genuinely be free FIRST -- this reflects real
    // hardware state (returns immediately if already idle, or blocks
    // briefly if the previous frame's last byte is still pending). Only
    // AFTER that do we clear UCTXIFG and arm the DMA: clearing before
    // confirming readiness was the earlier bug -- if TXBUF was already
    // empty, nothing would ever set the flag again (nothing left to
    // trigger it), deadlocking here forever and starving the watchdog.
    while (!(UCA0IFG & UCTXIFG));

    UCA0IFG &= ~UCTXIFG;
    DMA_enableTransfers(DMA_CHANNEL_0);

    UCA0TXBUF = dma_tx_buffer[0];
}

void Init_Clock(void)
{
    // Bump DCO to a true 8 MHz so 115200 baud can be generated accurately.
    CS_setDCOFreq(CS_DCORSEL_1, CS_DCOFSEL_3);           // DCO = 8 MHz
    CS_initClockSignal(CS_MCLK,  CS_DCOCLK_SELECT, CS_CLOCK_DIVIDER_1);
    CS_initClockSignal(CS_SMCLK, CS_DCOCLK_SELECT, CS_CLOCK_DIVIDER_1);
}

void Init_GPIO(void)
{
    // ADC input pins: P3.0 = A12 (IFI), P3.1 = A13 (IFQ)
    GPIO_setAsPeripheralModuleFunctionInputPin(
        GPIO_PORT_P3, GPIO_PIN0 | GPIO_PIN1, GPIO_TERNARY_MODULE_FUNCTION);

    // UART pins: P2.0 = UCA0TXD, P2.1 = UCA0RXD (LaunchPad backchannel)
    GPIO_setAsPeripheralModuleFunctionInputPin(
        GPIO_PORT_P2, GPIO_PIN0 | GPIO_PIN1, GPIO_SECONDARY_MODULE_FUNCTION);

    PMM_unlockLPM5();
}

void Init_UART(void)
{
    // 115200 baud @ 8 MHz SMCLK -- known-good values, previously
    // validated on this exact hardware. Not touching these again.
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
    adcConfig.sampleHoldSignalSourceSelect = ADC12_B_SAMPLEHOLDSOURCE_SC;  // TA2 CCR1 output
    adcConfig.clockSourceSelect            = ADC12_B_CLOCKSOURCE_ADC12OSC;
    adcConfig.clockSourceDivider           = ADC12_B_CLOCKDIVIDER_1;
    adcConfig.clockSourcePredivider        = ADC12_B_CLOCKPREDIVIDER__1;
    adcConfig.internalChannelMap           = ADC12_B_NOINTCH;

    ADC12_B_init(ADC12_B_BASE, &adcConfig);
    ADC12_B_enable(ADC12_B_BASE);

    ADC12_B_setupSamplingTimer(ADC12_B_BASE,
        ADC12_B_CYCLEHOLD_128_CYCLES, ADC12_B_CYCLEHOLD_128_CYCLES,
        ADC12_B_MULTIPLESAMPLESENABLE);

    ADC12_B_configureMemoryParam memParam0 = {0};
    memParam0.memoryBufferControlIndex = ADC12_B_MEMORY_0;
    memParam0.inputSourceSelect        = ADC12_B_INPUT_A12;
    memParam0.refVoltageSourceSelect   = ADC12_B_VREFPOS_AVCC_VREFNEG_VSS;
    memParam0.endOfSequence            = ADC12_B_NOTENDOFSEQUENCE;
    ADC12_B_configureMemory(ADC12_B_BASE, &memParam0);

    ADC12_B_configureMemoryParam memParam1 = {0};
    memParam1.memoryBufferControlIndex = ADC12_B_MEMORY_1;
    memParam1.inputSourceSelect        = ADC12_B_INPUT_A13;
    memParam1.refVoltageSourceSelect   = ADC12_B_VREFPOS_AVCC_VREFNEG_VSS;
    memParam1.endOfSequence            = ADC12_B_ENDOFSEQUENCE;
    ADC12_B_configureMemory(ADC12_B_BASE, &memParam1);

    ADC12_B_clearInterrupt(ADC12_B_BASE, 0, ADC12_B_IFG1);
    ADC12_B_enableInterrupt(ADC12_B_BASE, ADC12_B_IE1, 0, 0);

    // CONSEQ_1 auto-clears ADC12ENC after every A12->A13 sweep --
    // TIMER2_A0_ISR (main.c) re-arms it every tick.
    ADC12CTL1 = (ADC12CTL1 & ~ADC12CONSEQ_3) | ADC12CONSEQ_1;
}

void Init_TIMER(void)
{
    // TA2 up-mode @ SAMPLING_RATE_HZ. Started immediately here (unlike
    // the buffered-capture build) -- this design streams continuously
    // from boot, there's no separate "armed" state to wait for.
    Timer_A_initUpModeParam upParam = {0};
    upParam.clockSource                              = TIMER_A_CLOCKSOURCE_SMCLK;
    upParam.clockSourceDivider                        = TIMER_A_CLOCKSOURCE_DIVIDER_1;
    upParam.timerPeriod                               = (uint16_t)((8000000UL / SAMPLING_RATE_HZ) - 1);
    upParam.timerInterruptEnable_TAIE                 = TIMER_A_TAIE_INTERRUPT_DISABLE;
    upParam.captureCompareInterruptEnable_CCR0_CCIE   = TIMER_A_CCIE_CCR0_INTERRUPT_ENABLE;
    upParam.timerClear                                = TIMER_A_DO_CLEAR;
    upParam.startTimer                                = false;
    Timer_A_initUpMode(TIMER_A2_BASE, &upParam);

    Timer_A_initCompareModeParam compParam = {0};
    compParam.compareRegister        = TIMER_A_CAPTURECOMPARE_REGISTER_1;
    compParam.compareInterruptEnable = TIMER_A_CAPTURECOMPARE_INTERRUPT_DISABLE;
    compParam.compareOutputMode      = TIMER_A_OUTPUTMODE_SET_RESET;
    compParam.compareValue           = 1000;
    Timer_A_initCompareMode(TIMER_A2_BASE, &compParam);

    Timer_A_startCounter(TIMER_A2_BASE, TIMER_A_UP_MODE);
}

void Init_DMA(void)
{
    DMA_initParam dmaConfig = {0};
    dmaConfig.channelSelect = DMA_CHANNEL_0;
    dmaConfig.transferModeSelect = DMA_TRANSFER_SINGLE;
    dmaConfig.transferSize = 0;   // set right before each transmission
    dmaConfig.triggerSourceSelect = DMA_TRIGGERSOURCE_15;   // UART TX flag -> DMA
    dmaConfig.transferUnitSelect = DMA_SIZE_SRCBYTE_DSTBYTE;
    // DMA_TRIGGER_RISINGEDGE (edge-sensitive) is required for peripheral
    // triggers like UCA0TXIFG -- DMA_TRIGGER_HIGH is only valid for the
    // external DMAE0 pin. Using HIGH here caused startup resync errors
    // before this was tracked down in the earlier project.
    dmaConfig.triggerTypeSelect = DMA_TRIGGER_RISINGEDGE;

    DMA_init(&dmaConfig);

    DMA_setDstAddress(DMA_CHANNEL_0, (uint32_t)&UCA0TXBUF, DMA_DIRECTION_UNCHANGED);

    DMA_clearInterrupt(DMA_CHANNEL_0);
    DMA_enableInterrupt(DMA_CHANNEL_0);
}
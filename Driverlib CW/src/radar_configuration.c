//******************************************************************************
//  radar_configuration.c
//
//  Extracted from main.c so that clock/GPIO/UART/ADC setup and the low-level
//  UART helpers can be reused from other source files.
//
//  Contains:
//    - UART_putc / UART_puts / UART_putU16 (blocking UART helpers)
//    - Init_Clock, Init_GPIO, Init_UART, Init_ADC
//    - IFI_result / IFQ_result globals (populated by the ADC12_B ISR,
//      which still lives in main.c since it is the interrupt entry point)
//******************************************************************************

#include "radar_configuration.h"

//******************************************************************************
// Globals
//******************************************************************************
volatile int16_t IFI_result = 0;
volatile int16_t IFQ_result = 0;

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

// Writes a 16-bit unsigned value as decimal ASCII (max 4095 for 12-bit ADC)
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
// Binary frame helper (replaces ASCII CSV for higher throughput)
//******************************************************************************
// Frame (6 bytes): [0xAA][0x55][IFI_lo][IFI_hi][IFQ_lo][IFQ_hi], little-endian
void UART_putFrame(uint16_t ifi, uint16_t ifq)
{
    UART_putc(0xAA);
    UART_putc(0x55);
    UART_putc((uint8_t)(ifi & 0xFF));
    UART_putc((uint8_t)((ifi >> 8) & 0xFF));
    UART_putc((uint8_t)(ifq & 0xFF));
    UART_putc((uint8_t)((ifq >> 8) & 0xFF));
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

    // Unlock GPIO after ALL pin configuration is done (required on FR5xx/6xx)
    PMM_unlockLPM5();
}

void Init_UART(void)
{
    // Standard TI baud-rate-table entry for an 8 MHz BRCLK -> 115200 baud
    // (clockPrescalar=4, firstModReg(UCBRFx)=5, secondModReg(UCBRSx)=0x55,
    // regular oversampling mode - 8MHz/115200 = 69.4 clocks/bit, comfortably
    // inside the accurate range, unlike the old 1MHz/115200 combination).
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
    // ADC12CLK = ADC12OSC (internal ~5 MHz osc, always available regardless
    // of what MCLK/SMCLK happen to be set to elsewhere in the project)
    ADC12_B_initParam adcConfig = {0};
    adcConfig.sampleHoldSignalSourceSelect = ADC12_B_SAMPLEHOLDSOURCE_SC;
    adcConfig.clockSourceSelect            = ADC12_B_CLOCKSOURCE_ADC12OSC;
    adcConfig.clockSourceDivider           = ADC12_B_CLOCKDIVIDER_1;
    adcConfig.clockSourcePredivider        = ADC12_B_CLOCKPREDIVIDER__1;
    adcConfig.internalChannelMap           = ADC12_B_NOINTCH;

    ADC12_B_init(ADC12_B_BASE, &adcConfig);
    ADC12_B_enable(ADC12_B_BASE);

    // Longer sample-and-hold gives the radar's analog output stage time to settle
    ADC12_B_setupSamplingTimer(ADC12_B_BASE,
        ADC12_B_CYCLEHOLD_128_CYCLES, ADC12_B_CYCLEHOLD_128_CYCLES,
        ADC12_B_MULTIPLESAMPLESENABLE);

    // MEM0 = IFI (A12)
    ADC12_B_configureMemoryParam memParam0 = {0};
    memParam0.memoryBufferControlIndex = ADC12_B_MEMORY_0;
    memParam0.inputSourceSelect        = ADC12_B_INPUT_A12;
    memParam0.refVoltageSourceSelect   = ADC12_B_VREFPOS_AVCC_VREFNEG_VSS;
    memParam0.endOfSequence            = ADC12_B_NOTENDOFSEQUENCE;
    ADC12_B_configureMemory(ADC12_B_BASE, &memParam0);

    // MEM1 = IFQ (A13), end of sequence
    ADC12_B_configureMemoryParam memParam1 = {0};
    memParam1.memoryBufferControlIndex = ADC12_B_MEMORY_1;
    memParam1.inputSourceSelect        = ADC12_B_INPUT_A13;
    memParam1.refVoltageSourceSelect   = ADC12_B_VREFPOS_AVCC_VREFNEG_VSS;
    memParam1.endOfSequence            = ADC12_B_ENDOFSEQUENCE;
    ADC12_B_configureMemory(ADC12_B_BASE, &memParam1);

    ADC12_B_clearInterrupt(ADC12_B_BASE, 0, ADC12_B_IFG1);
    ADC12_B_enableInterrupt(ADC12_B_BASE, ADC12_B_IE1, 0, 0);
}
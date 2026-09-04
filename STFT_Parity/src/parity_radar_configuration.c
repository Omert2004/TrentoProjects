#include "parity_radar_configuration.h"

void Parity_UART_putc(uint8_t value)
{
    while (!(UCA0IFG & UCTXIFG))
        WDT_A_resetTimer(WDT_A_BASE);
    UCA0TXBUF = value;
    WDT_A_resetTimer(WDT_A_BASE);
}

void Parity_InitClock(void)
{
    CS_setDCOFreq(CS_DCORSEL_1, CS_DCOFSEL_3);
    CS_initClockSignal(CS_MCLK, CS_DCOCLK_SELECT, CS_CLOCK_DIVIDER_1);
    CS_initClockSignal(CS_SMCLK, CS_DCOCLK_SELECT, CS_CLOCK_DIVIDER_1);
}

void Parity_InitGPIO(void)
{
    GPIO_setAsPeripheralModuleFunctionInputPin(
        GPIO_PORT_P3, GPIO_PIN0 | GPIO_PIN1,
        GPIO_TERNARY_MODULE_FUNCTION);
    GPIO_setAsPeripheralModuleFunctionInputPin(
        GPIO_PORT_P2, GPIO_PIN0 | GPIO_PIN1,
        GPIO_SECONDARY_MODULE_FUNCTION);
    PMM_unlockLPM5();
}

void Parity_InitUART(void)
{
    EUSCI_A_UART_initParam uart = {0};
    uart.selectClockSource = EUSCI_A_UART_CLOCKSOURCE_SMCLK;
    uart.clockPrescalar = 4;
    uart.firstModReg = 5;
    uart.secondModReg = 0x55;
    uart.parity = EUSCI_A_UART_NO_PARITY;
    uart.msborLsbFirst = EUSCI_A_UART_LSB_FIRST;
    uart.numberofStopBits = EUSCI_A_UART_ONE_STOP_BIT;
    uart.uartMode = EUSCI_A_UART_MODE;
    uart.overSampling = EUSCI_A_UART_OVERSAMPLING_BAUDRATE_GENERATION;
    EUSCI_A_UART_init(EUSCI_A0_BASE, &uart);
    EUSCI_A_UART_enable(EUSCI_A0_BASE);
}

void Parity_InitADC(void)
{
    ADC12_B_initParam adc = {0};
    ADC12_B_configureMemoryParam mem0 = {0};
    ADC12_B_configureMemoryParam mem1 = {0};

    adc.sampleHoldSignalSourceSelect = ADC12_B_SAMPLEHOLDSOURCE_SC;
    adc.clockSourceSelect = ADC12_B_CLOCKSOURCE_ADC12OSC;
    adc.clockSourceDivider = ADC12_B_CLOCKDIVIDER_1;
    adc.clockSourcePredivider = ADC12_B_CLOCKPREDIVIDER__1;
    adc.internalChannelMap = ADC12_B_NOINTCH;
    ADC12_B_init(ADC12_B_BASE, &adc);
    ADC12_B_enable(ADC12_B_BASE);

    ADC12_B_setupSamplingTimer(
        ADC12_B_BASE,
        ADC12_B_CYCLEHOLD_128_CYCLES,
        ADC12_B_CYCLEHOLD_128_CYCLES,
        ADC12_B_MULTIPLESAMPLESENABLE);

    mem0.memoryBufferControlIndex = ADC12_B_MEMORY_0;
    mem0.inputSourceSelect = ADC12_B_INPUT_A12;
    mem0.refVoltageSourceSelect = ADC12_B_VREFPOS_AVCC_VREFNEG_VSS;
    mem0.endOfSequence = ADC12_B_NOTENDOFSEQUENCE;
    ADC12_B_configureMemory(ADC12_B_BASE, &mem0);

    mem1.memoryBufferControlIndex = ADC12_B_MEMORY_1;
    mem1.inputSourceSelect = ADC12_B_INPUT_A13;
    mem1.refVoltageSourceSelect = ADC12_B_VREFPOS_AVCC_VREFNEG_VSS;
    mem1.endOfSequence = ADC12_B_ENDOFSEQUENCE;
    ADC12_B_configureMemory(ADC12_B_BASE, &mem1);

    ADC12_B_clearInterrupt(ADC12_B_BASE, 0, ADC12_B_IFG1);
    ADC12_B_enableInterrupt(ADC12_B_BASE, ADC12_B_IE1, 0, 0);
    ADC12CTL1 = (ADC12CTL1 & ~ADC12CONSEQ_3) | ADC12CONSEQ_1;
}

void Parity_InitTimer(void)
{
    Timer_A_initUpModeParam timer = {0};
    timer.clockSource = TIMER_A_CLOCKSOURCE_SMCLK;
    timer.clockSourceDivider = TIMER_A_CLOCKSOURCE_DIVIDER_1;
    timer.timerPeriod =
        (uint16_t)((8000000UL / PARITY_SAMPLING_RATE_HZ) - 1UL);
    timer.timerInterruptEnable_TAIE = TIMER_A_TAIE_INTERRUPT_DISABLE;
    timer.captureCompareInterruptEnable_CCR0_CCIE =
        TIMER_A_CCIE_CCR0_INTERRUPT_ENABLE;
    timer.timerClear = TIMER_A_DO_CLEAR;
    timer.startTimer = false;
    Timer_A_initUpMode(TIMER_A2_BASE, &timer);
    Timer_A_startCounter(TIMER_A2_BASE, TIMER_A_UP_MODE);
}

void Parity_StopTimer(void)
{
    Timer_A_stop(TIMER_A2_BASE);
}

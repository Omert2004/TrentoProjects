//******************************************************************************
//  MSP430FR5994 - Continuous ADC Sampling of Radar IFI/IFQ (Analog Path)
//
//  PURPOSE:
//    Sample the radar's analog IFI and IFQ outputs continuously with the
//    MCU's own on-chip ADC12_B, and stream the raw 12-bit results out via
//    printf to the CCS Debug Console.
//
//  Hardware Setup:
//    - IFI  -> P3.0 / A12   (radar analog I output)
//    - IFQ  -> P3.1 / A13   (radar analog Q output)
//
//  Notes:
//    - Uses TI MSP430 DriverLib exclusively (driverlib.h).
//    - Requires sufficient Heap size configured in CCS Linker options for printf.
//******************************************************************************

#include <driverlib.h>
#include <stdint.h>
#include <stdio.h>

//******************************************************************************
// Globals
//******************************************************************************
volatile uint16_t IFI_result = 0;
volatile uint16_t IFQ_result = 0;

//******************************************************************************
// Init Functions
//******************************************************************************
void Init_GPIO(void)
{
    // ADC input pins: P3.0 = A12 (IFI), P3.1 = A13 (IFQ)
    // Analog function on FR5xx/6xx requires both PxSEL1 and PxSEL0 set,
    // which DriverLib exposes as GPIO_TERNARY_MODULE_FUNCTION.
    GPIO_setAsPeripheralModuleFunctionInputPin(
        GPIO_PORT_P3, GPIO_PIN0 | GPIO_PIN1, GPIO_TERNARY_MODULE_FUNCTION);

    // Unlock GPIO after ALL pin configuration is done (required on FR5xx/6xx)
    PMM_unlockLPM5();
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

//******************************************************************************
// Main
//******************************************************************************
int main(void)
{
    WDT_A_hold(WDT_A_BASE);

    Init_GPIO();
    Init_ADC();

    __enable_interrupt();

    printf("IFI,IFQ\n");

    while (1)
    {
        // One pass through the sequence: MEM0 (IFI) then MEM1 (IFQ)
        ADC12_B_startConversion(ADC12_B_BASE, ADC12_B_MEMORY_0,
                                 ADC12_B_SEQOFCHANNELS);

        __bis_SR_register(LPM0_bits + GIE);   // sleep until ISR wakes us

        // Print the parsed results to the CCS Debug Console
        printf("%u,%u\n", IFI_result, IFQ_result);

        __delay_cycles(50000);   // ~50 ms between prints @ 1 MHz -> ~20 lines/sec
    }
}

//******************************************************************************
// ADC12_B ISR
//******************************************************************************
#if defined(__TI_COMPILER_VERSION__) || defined(__IAR_SYSTEMS_ICC__)
#pragma vector = ADC12_VECTOR
__interrupt void ADC12_B_ISR(void)
#elif defined(__GNUC__)
void __attribute__ ((interrupt(ADC12_VECTOR))) ADC12_B_ISR(void)
#else
#error Compiler not supported!
#endif
{
    switch (__even_in_range(ADC12IV, ADC12IV_ADC12RDYIFG))
    {
        case ADC12IV_ADC12IFG1:                        // last channel in sequence (IFQ)
            IFI_result = ADC12_B_getResults(ADC12_B_BASE, ADC12_B_MEMORY_0);
            IFQ_result = ADC12_B_getResults(ADC12_B_BASE, ADC12_B_MEMORY_1);
            __bic_SR_register_on_exit(LPM0_bits);
            break;
        default:
            break;
    }
}

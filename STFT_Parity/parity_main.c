#include <driverlib.h>
#include <DSPLib.h>
#include <stdbool.h>
#include <stdint.h>

#include "STFT.h"
#include "parity_capture.h"
#include "parity_radar_configuration.h"

#if ENABLE_CLUTTER_CANCEL != 1
#error "Model-pilot parity requires ENABLE_CLUTTER_CANCEL == 1"
#endif

#if DIFF_SHIFT != 4
#error "Model-pilot parity requires DIFF_SHIFT == 4"
#endif

#define PARITY_PROTOCOL_VERSION  1U
#define PARITY_RAW_MARKER        0xE0U
#define PARITY_Q15_MARKER        0xE1U
#define PARITY_COLUMN_MARKER     0xE2U

static int16_t parity_raw_I[PARITY_CAPTURE_SAMPLES];
static int16_t parity_raw_Q[PARITY_CAPTURE_SAMPLES];
static int16_t parity_input_I[FFT_SIZE];
static int16_t parity_input_Q[FFT_SIZE];
static volatile uint16_t parity_sample_count = 0;
static volatile bool parity_capture_ready = false;
static uint16_t parity_capture_sequence = 0;

static uint16_t crc16_update(uint16_t crc, uint8_t value)
{
    uint16_t bit;
    crc ^= (uint16_t)value << 8;
    for (bit = 0; bit < 8; bit++)
        crc = (crc & 0x8000)
                ? (uint16_t)((crc << 1) ^ 0x1021)
                : (uint16_t)(crc << 1);
    return crc;
}

static void send_body_u8(uint16_t *crc, uint8_t value)
{
    *crc = crc16_update(*crc, value);
    Parity_UART_putc(value);
}

static void send_body_u16(uint16_t *crc, uint16_t value)
{
    send_body_u8(crc, (uint8_t)(value & 0xFF));
    send_body_u8(crc, (uint8_t)((value >> 8) & 0xFF));
}

static void send_body_u32(uint16_t *crc, uint32_t value)
{
    send_body_u16(crc, (uint16_t)(value & 0xFFFF));
    send_body_u16(crc, (uint16_t)((value >> 16) & 0xFFFF));
}

static uint16_t begin_frame(uint8_t marker)
{
    uint16_t crc = 0xFFFF;
    Parity_UART_putc(0xAA);
    Parity_UART_putc(0x55);
    send_body_u8(&crc, marker);
    return crc;
}

static void end_frame(uint16_t crc)
{
    Parity_UART_putc((uint8_t)(crc & 0xFF));
    Parity_UART_putc((uint8_t)((crc >> 8) & 0xFF));
}

static void send_raw_frame(void)
{
    uint16_t index;
    uint16_t crc = begin_frame(PARITY_RAW_MARKER);
    send_body_u8(&crc, PARITY_PROTOCOL_VERSION);
    send_body_u16(&crc, parity_capture_sequence);
    send_body_u32(&crc, PARITY_SAMPLING_RATE_HZ);
    send_body_u16(&crc, FFT_SIZE);
    send_body_u16(&crc, FFT_HOP);
    /* bit 0 = first-difference enabled; high nibble = DIFF_SHIFT */
    send_body_u8(&crc, (uint8_t)(1U | ((uint8_t)DIFF_SHIFT << 4)));
    send_body_u16(&crc, PARITY_CAPTURE_SAMPLES);
    send_body_u32(&crc, 0);      /* first source sample index */
    send_body_u32(&crc, 0);      /* cumulative capture drops */
    for (index = 0; index < PARITY_CAPTURE_SAMPLES; index++)
    {
        send_body_u16(&crc, (uint16_t)parity_raw_I[index]);
        send_body_u16(&crc, (uint16_t)parity_raw_Q[index]);
    }
    end_frame(crc);
}

void Parity_sendQ15Stage(uint8_t stage, const int16_t *values,
                         uint16_t value_count)
{
    uint16_t index;
    uint16_t crc = begin_frame(PARITY_Q15_MARKER);
    send_body_u8(&crc, PARITY_PROTOCOL_VERSION);
    send_body_u16(&crc, parity_capture_sequence);
    send_body_u8(&crc, stage);
    send_body_u16(&crc, value_count);
    for (index = 0; index < value_count; index++)
        send_body_u16(&crc, (uint16_t)values[index]);
    end_frame(crc);
}

static void send_column_frame(const int8_t *column)
{
    uint16_t index;
    uint16_t crc = begin_frame(PARITY_COLUMN_MARKER);
    send_body_u8(&crc, PARITY_PROTOCOL_VERSION);
    send_body_u16(&crc, parity_capture_sequence);
    send_body_u16(&crc, FFT_SIZE);
    for (index = 0; index < FFT_SIZE; index++)
        send_body_u8(&crc, (uint8_t)column[index]);
    end_frame(crc);
}

int main(void)
{
    uint16_t index;
    WDT_A_initWatchdogTimer(WDT_A_BASE, WDT_A_CLOCKSOURCE_SMCLK,
                            WDT_A_CLOCKDIVIDER_8192K);
    WDT_A_start(WDT_A_BASE);

    Parity_InitClock();
    Parity_InitGPIO();
    Parity_InitUART();
    Parity_InitADC();
    STFT_init();
    Parity_InitTimer();
    __enable_interrupt();

    while (!parity_capture_ready)
    {
        WDT_A_resetTimer(WDT_A_BASE);
        __bis_SR_register(LPM0_bits + GIE);
    }

    /* Convert 257 raw samples into the exact 256 signed differences consumed
     * by the production STFT. Scaling/clamping still occurs inside STFT.c. */
    for (index = 0; index < FFT_SIZE; index++)
    {
        parity_input_I[index] = parity_raw_I[index + 1U] - parity_raw_I[index];
        parity_input_Q[index] = parity_raw_Q[index + 1U] - parity_raw_Q[index];
    }

    /* Acquisition is permanently stopped before any diagnostic UART or DSP
     * work. Every transmitted stage therefore belongs to these exact samples. */
    while (1)
    {
        WDT_A_resetTimer(WDT_A_BASE);
        send_raw_frame();
        STFT_compute_next_segment(parity_input_I, parity_input_Q);
        send_column_frame(spectrogram[STFT_SEGMENTS - 1]);
        parity_capture_sequence++;

        /* Replay the same one-shot capture so the host can connect at any
         * time. No ADC conversions occur after parity_capture_ready. */
        __delay_cycles(8000000UL);
    }
}

#if defined(__TI_COMPILER_VERSION__) || defined(__IAR_SYSTEMS_ICC__)
#pragma vector = TIMER2_A0_VECTOR
__interrupt void TIMER2_A0_ISR(void)
#elif defined(__GNUC__)
void __attribute__((interrupt(TIMER2_A0_VECTOR))) TIMER2_A0_ISR(void)
#else
#error Compiler not supported!
#endif
{
    ADC12CTL0 |= ADC12ENC | ADC12SC;
}

#if defined(__TI_COMPILER_VERSION__) || defined(__IAR_SYSTEMS_ICC__)
#pragma vector = ADC12_B_VECTOR
__interrupt void ADC12_B_ISR(void)
#elif defined(__GNUC__)
void __attribute__((interrupt(ADC12_B_VECTOR))) ADC12_B_ISR(void)
#else
#error Compiler not supported!
#endif
{
    switch (__even_in_range(ADC12IV, ADC12IV_ADC12IFG31))
    {
        case ADC12IV_ADC12IFG1:
            if (parity_sample_count < PARITY_CAPTURE_SAMPLES)
            {
                parity_raw_I[parity_sample_count] = (int16_t)ADC12MEM0;
                parity_raw_Q[parity_sample_count] = (int16_t)ADC12MEM1;
                parity_sample_count++;
            }
            if (parity_sample_count == PARITY_CAPTURE_SAMPLES)
            {
                Parity_StopTimer();
                ADC12_B_disableInterrupt(ADC12_B_BASE, ADC12_B_IE1, 0, 0);
                parity_capture_ready = true;
                __bic_SR_register_on_exit(LPM0_bits);
            }
            break;
        default:
            break;
    }
}

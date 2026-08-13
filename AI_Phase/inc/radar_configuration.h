#ifndef RADAR_CONFIGURATION_H
#define RADAR_CONFIGURATION_H

#include <driverlib.h>
#include <stdint.h>
#include <stdbool.h>

//******************************************************************************
// Globals (defined in radar_configuration.c, shared with main.c / ISR)
//******************************************************************************
extern volatile int16_t IFI_result;
extern volatile int16_t IFQ_result;


#define SAMPLING_RATE_HZ 4000   // proven stable at 115200 baud with full margin
#define N_SAMPLES 512           // ring buffer depth, tune to taste

extern volatile uint16_t I_queue[N_SAMPLES];
extern volatile uint16_t Q_queue[N_SAMPLES];
extern volatile int samples_index_in;
extern volatile int samples_index_out;

//******************************************************************************
// Test 1: UART throughput isolation -- independent 1 Hz ADC rate counter.
//
// adc_sample_count is incremented once per accepted sample in the ADC12_B
// ISR (main.c). Timer_A1, clocked from ACLK (independent of the Timer_A2
// ADC trigger, so it can't perturb sampling), snapshots and zeroes that
// counter once a second. If the ADC/ring-buffer side is healthy, the
// snapshot should read ~SAMPLING_RATE_HZ regardless of whatever is
// happening downstream in DMA/UART -- this isolates "is the ADC keeping
// up?" from "is the transmit path keeping up?".
//******************************************************************************
extern volatile uint16_t adc_sample_count;
extern volatile uint16_t count_snapshot;
extern volatile bool count_snapshot_ready;

//******************************************************************************
// Profiling: per-hop STFT-compute time vs DMA-wait time, in Timer_A1/ACLK
// ticks (32768 Hz, ~30.5 us/tick). Accumulated each hop in the main loop,
// snapshotted and zeroed once a second by the same TIMER1_A0 ISR that
// drives the Test 1 rate counter -- this answers "of the ~250-330 ms per
// column, how much is LEA FFT / shift-and-append vs the unaccelerated
// log2f() magnitude loop vs waiting on the DMA/UART path?"
//******************************************************************************
extern volatile uint16_t stft_ticks_accum;
extern volatile uint16_t dma_wait_ticks_accum;
extern volatile uint16_t hop_count_accum;

extern volatile uint16_t stft_ticks_snapshot;
extern volatile uint16_t dma_wait_ticks_snapshot;
extern volatile uint16_t hop_count_snapshot;
extern volatile bool profile_snapshot_ready;

//******************************************************************************
// Minimal UART helpers (blocking, no printf/retargeting needed)
//******************************************************************************
void UART_putc(uint8_t c);
void UART_puts(const char *s);
void UART_putU16(uint16_t val);
void UART_putFrame(uint16_t ifi, uint16_t ifq);
void UART_putSpectrogramColumn_DMA(int8_t *column, int len);
void UART_putCountFrame(uint16_t count);
void UART_putProfileFrame(uint16_t hop_count, uint16_t stft_ticks, uint16_t dma_wait_ticks);

//******************************************************************************
// Init Functions
//******************************************************************************
void Init_Clock(void);
void Init_GPIO(void);
void Init_UART(void);
void Init_ADC(void);
void Init_TIMER(void);
void Init_DMA(void);
void Init_RateTimer(void);   // Test 1: independent 1 Hz ACLK timer for the ADC rate counter

#endif // RADAR_CONFIGURATION_H

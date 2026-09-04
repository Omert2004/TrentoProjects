#ifndef RADAR_CONFIGURATION_H
#define RADAR_CONFIGURATION_H

#include <driverlib.h>
#include <stdint.h>
#include <stdbool.h>

//******************************************************************************
// Globals (defined in radar_configuration.c, shared with main.c / ISR)
//******************************************************************************
#define SAMPLING_RATE_HZ        2000
#define N_SAMPLES               512

/* Protocol v1 markers.  The D-series markers intentionally make the
 * integrity-protected stream incompatible with the old unprotected C0/C2/C3
 * scripts, so a stale host tool cannot silently accept shifted columns. */
#define STFT_COLUMN_MARKER      0xD0
#define STFT_RATE_MARKER        0xD2
#define STFT_PROFILE_MARKER     0xD3
#define STFT_COLUMN_BYTES       256
#define STFT_COLUMN_PACKET_BYTES 271

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
extern volatile uint32_t adc_drop_count;
extern volatile uint32_t count_drop_snapshot;
extern volatile uint16_t diagnostic_sequence_snapshot;

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
void UART_putSpectrogramColumn_DMA(const int8_t *column,
                                   uint16_t column_sequence,
                                   uint32_t first_new_sample_index,
                                   uint32_t cumulative_drop_count);
void UART_putCountFrame(uint16_t report_sequence, uint16_t count,
                        uint32_t cumulative_drop_count);
void UART_putProfileFrame(uint16_t report_sequence, uint16_t hop_count,
                          uint16_t stft_ticks, uint16_t dma_wait_ticks);

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

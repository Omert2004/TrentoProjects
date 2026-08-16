#ifndef RADAR_CONFIGURATION_H
#define RADAR_CONFIGURATION_H

#include <driverlib.h>
#include <stdint.h>
#include <stdbool.h>

#define SAMPLING_RATE_HZ   2000
#define N_SAMPLES          256   // 128 ms buffer at 2 kHz; fits in 4 KiB RAM

// Packetized raw protocol v1. A full packet is 144 bytes:
//   sync(2) + marker(1) + packet_seq(2) + first_sample_index(4)
//   + sample_count(1) + cumulative_drop_count(4)
//   + 32 * I/Q(4) + CRC16(2)
// At 2000 samples/s this uses 9000 B/s, safely below the 115200-baud
// 8-N-1 capacity of 11520 B/s.
#define RAW_PACKET_MARKER       0xD4
#define RAW_PACKET_MAX_SAMPLES  32
#define RAW_PACKET_MAX_BYTES    (16 + (4 * RAW_PACKET_MAX_SAMPLES))

extern volatile uint16_t I_queue[N_SAMPLES];
extern volatile uint16_t Q_queue[N_SAMPLES];
extern volatile uint32_t sample_index_queue[N_SAMPLES];
extern volatile int samples_index_in;
extern volatile int samples_index_out;
extern volatile uint32_t adc_total_sample_count;
extern volatile uint32_t adc_drop_count;

// Set true right before UART_putPacket_DMA() kicks off a transfer and
// cleared by the DMA ISR after the last byte is moved into UCA0TXBUF.
extern volatile bool dma_tx_in_progress;

void UART_putc(uint8_t c);
uint8_t UART_getc_nonblocking(bool *got_byte);
void UART_putPacket_DMA(const uint16_t *ifi,
                        const uint16_t *ifq,
                        uint8_t sample_count,
                        uint16_t packet_sequence,
                        uint32_t first_sample_index,
                        uint32_t cumulative_drop_count);

void Init_Clock(void);
void Init_GPIO(void);
void Init_UART(void);
void Init_ADC(void);
void Init_TIMER(void);
void Init_DMA(void);

#endif // RADAR_CONFIGURATION_H

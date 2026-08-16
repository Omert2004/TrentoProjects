#ifndef RADAR_CONFIGURATION_H
#define RADAR_CONFIGURATION_H

#include <driverlib.h>
#include <stdint.h>
#include <stdbool.h>

#define SAMPLING_RATE_HZ   2000
#define N_SAMPLES          512   // ring buffer depth

extern volatile uint16_t I_queue[N_SAMPLES];
extern volatile uint16_t Q_queue[N_SAMPLES];
extern volatile int samples_index_in;
extern volatile int samples_index_out;

// Set true right before UART_putFrame_DMA() kicks off a transfer,
// cleared by the DMA ISR once the last byte has left the shift
// register. Sole handshake between the main loop and the DMA ISR.
extern volatile bool dma_tx_in_progress;

void UART_putc(uint8_t c);
uint8_t UART_getc_nonblocking(bool *got_byte);
void UART_putFrame_DMA(uint16_t ifi, uint16_t ifq);

void Init_Clock(void);
void Init_GPIO(void);
void Init_UART(void);
void Init_ADC(void);
void Init_TIMER(void);
void Init_DMA(void);

#endif // RADAR_CONFIGURATION_H
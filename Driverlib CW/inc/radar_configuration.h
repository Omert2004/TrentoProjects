#ifndef RADAR_CONFIGURATION_H
#define RADAR_CONFIGURATION_H

#include <driverlib.h>
#include <stdint.h>

//******************************************************************************
// Globals (defined in radar_configuration.c, shared with main.c / ISR)
//******************************************************************************
extern volatile int16_t IFI_result;
extern volatile int16_t IFQ_result;

#define SAMPLING_RATE_HZ 4000   // per-channel; timer period is set for 2x this (I then Q)
#define N_SAMPLES 512           // ring buffer depth, tune to taste

extern volatile uint16_t I_queue[N_SAMPLES];
extern volatile uint16_t Q_queue[N_SAMPLES];
extern volatile int samples_index_in;
extern volatile int samples_index_out;

//******************************************************************************
// Minimal UART helpers (blocking, no printf/retargeting needed)
//******************************************************************************
void UART_putc(uint8_t c);
void UART_puts(const char *s);
void UART_putU16(uint16_t val);
void UART_putFrame(uint16_t ifi, uint16_t ifq);

//******************************************************************************
// Init Functions
//******************************************************************************
void Init_Clock(void);
void Init_GPIO(void);
void Init_UART(void);
void Init_ADC(void);
void Init_TIMER(void);

#endif // RADAR_CONFIGURATION_H
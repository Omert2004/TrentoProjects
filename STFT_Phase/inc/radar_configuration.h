#ifndef RADAR_CONFIGURATION_H
#define RADAR_CONFIGURATION_H

#include <driverlib.h>
#include <stdint.h>

//******************************************************************************
// Globals (defined in radar_configuration.c, shared with main.c / ISR)
//******************************************************************************
extern volatile int16_t IFI_result;
extern volatile int16_t IFQ_result;


#define SAMPLING_RATE_HZ 1500   // proven stable at 115200 baud with full margin
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
void UART_putSpectrogramColumn(int8_t *column, int len);

//******************************************************************************
// Init Functions
//******************************************************************************
void Init_Clock(void);
void Init_GPIO(void);
void Init_UART(void);
void Init_ADC(void);
void Init_TIMER(void);

#endif // RADAR_CONFIGURATION_H
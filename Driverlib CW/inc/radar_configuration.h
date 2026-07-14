#ifndef RADAR_CONFIGURATION_H
#define RADAR_CONFIGURATION_H

#include <driverlib.h>
#include <stdint.h>

//******************************************************************************
// Globals (defined in radar_configuration.c, shared with main.c / ISR)
//******************************************************************************
extern volatile int16_t IFI_result;
extern volatile int16_t IFQ_result;

//******************************************************************************
// Minimal UART helpers (blocking, no printf/retargeting needed)
//******************************************************************************
void UART_putc(uint8_t c);
void UART_puts(const char *s);
void UART_putU16(uint16_t val);

//******************************************************************************
// Init Functions
//******************************************************************************
void Init_Clock(void);
void Init_GPIO(void);
void Init_UART(void);
void Init_ADC(void);

#endif // RADAR_CONFIGURATION_H
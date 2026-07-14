#ifndef RADAR_CONFIGURATION_H
#define RADAR_CONFIGURATION_H

#include <stdint.h>
#include <driverlib.h>

//******************************************************************************
// Globals
//******************************************************************************
extern volatile uint16_t IFI_result;
extern volatile uint16_t IFQ_result;

//******************************************************************************
// Function Prototypes
//******************************************************************************
void UART_putc(uint8_t c);
void UART_puts(const char *s);
void UART_putU16(uint16_t val);

void Init_Clock(void);
void Init_GPIO(void);
void Init_UART(void);
void Init_ADC(void);

#endif // RADAR_CONFIGURATION_H

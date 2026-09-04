#ifndef PARITY_RADAR_CONFIGURATION_H
#define PARITY_RADAR_CONFIGURATION_H

#include <driverlib.h>
#include <stdint.h>

#define PARITY_SAMPLING_RATE_HZ  2000UL
/* One predecessor plus FFT_SIZE samples produces exactly FFT_SIZE first
 * differences. This validates the same input transform used in AI_Phase. */
#define PARITY_CAPTURE_SAMPLES   (FFT_SIZE + 1U)

void Parity_InitClock(void);
void Parity_InitGPIO(void);
void Parity_InitUART(void);
void Parity_InitADC(void);
void Parity_InitTimer(void);
void Parity_StopTimer(void);
void Parity_UART_putc(uint8_t value);

#endif

#ifndef PARITY_CAPTURE_H
#define PARITY_CAPTURE_H

#include <stdint.h>

#define PARITY_STAGE_WINDOWED  1
#define PARITY_STAGE_FFT       2

void Parity_sendQ15Stage(uint8_t stage, const int16_t *values,
                         uint16_t value_count);

#endif

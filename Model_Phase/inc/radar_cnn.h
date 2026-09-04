#ifndef RADAR_CNN_H
#define RADAR_CNN_H

#include <stdint.h>

#define RADAR_CNN_CLASS_COUNT 4

typedef enum
{
    RADAR_CLASS_LEFT_HORIZONTAL_SCROLL = 0,
    RADAR_CLASS_RIGHT_HORIZONTAL_SCROLL = 1,
    RADAR_CLASS_CLICKING_HAND = 2,
    RADAR_CLASS_EMPTY = 3
} radar_class_t;

/* Input layout is spectrogram[time_column][frequency_bin], 15 x 256 bytes. */
uint8_t radar_cnn_classify(const int8_t *spectrogram_time_major,
                           int32_t logits[RADAR_CNN_CLASS_COUNT]);

#endif

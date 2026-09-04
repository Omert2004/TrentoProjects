#ifndef CNN_WEIGHTS_H
#define CNN_WEIGHTS_H

#include <stdint.h>

#define CNN_CONV1_REQUANT_Q15 186U
#define CNN_CONV2_REQUANT_Q15 30U

extern const int16_t cnn_conv1_weight[32];
extern const int32_t cnn_conv1_bias[8];
extern const int16_t cnn_conv2_weight[512];
extern const int32_t cnn_conv2_bias[16];
extern const int16_t cnn_fc_weight[2048];
extern const int32_t cnn_fc_bias[4];

#endif

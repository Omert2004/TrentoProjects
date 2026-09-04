/* Streamed integer inference for the 8/16-channel TinyCNN. */
#include "radar_cnn.h"
#include "cnn_weights.h"

#ifndef CNN_HOST_TEST
#include <driverlib.h>
#endif

#define CNN_INPUT_HEIGHT       256
#define CNN_INPUT_WIDTH         15
#define CNN_CROP_BEGIN          64
#define CNN_INITIAL_HEIGHT      64
#define CNN_INITIAL_WIDTH        8
#define CNN_POOL1_HEIGHT        32
#define CNN_POOL1_WIDTH          4
#define CNN_POOL2_HEIGHT        16
#define CNN_POOL2_WIDTH          2
#define CNN_CONV1_CHANNELS       8
#define CNN_CONV2_CHANNELS      16
#define CNN_FC_FEATURES        512

/* Three pool1 rows are sufficient for the two adjacent conv2 rows that
 * form one pool2 row.  This is the entire activation scratch: 96 bytes. */
static uint8_t cnn_pool1_rows[3][CNN_CONV1_CHANNELS][CNN_POOL1_WIDTH];

static uint8_t requantize_relu_u8(int32_t accumulator,
                                  uint16_t multiplier_q15)
{
    uint32_t scaled;
    uint32_t result;

    if (accumulator <= 0)
        return 0;

    scaled = (uint32_t)accumulator * (uint32_t)multiplier_q15 + 16384UL;
    result = scaled >> 15;
    return (result > 255UL) ? 255U : (uint8_t)result;
}

/* Central 128-bin crop followed by 2x2 stride-2 ceil-mode max pooling.
 * The only odd dimension is time: pooled column 7 contains time column 14
 * and one zero-padding value.  All CNN inputs are nonnegative. */
static uint8_t initial_pool_at(const int8_t *input,
                               int16_t row,
                               int16_t column)
{
    uint8_t maximum = 0;
    uint8_t row_offset;
    uint8_t column_offset;

    if (row < 0 || row >= CNN_INITIAL_HEIGHT ||
        column < 0 || column >= CNN_INITIAL_WIDTH)
        return 0;

    for (row_offset = 0; row_offset < 2; row_offset++)
    {
        uint16_t input_row = (uint16_t)(CNN_CROP_BEGIN + 2 * row + row_offset);
        for (column_offset = 0; column_offset < 2; column_offset++)
        {
            int16_t input_column = (int16_t)(2 * column + column_offset);
            uint8_t value;
            if (input_column >= CNN_INPUT_WIDTH)
                continue;
            value = (uint8_t)input[(uint16_t)input_column * CNN_INPUT_HEIGHT
                                   + input_row];
            if (value > maximum)
                maximum = value;
        }
    }
    return maximum;
}

/* 2x2 Keras-style "same" cross-correlation: padding is on bottom/right. */
static int32_t conv1_at(const int8_t *input,
                        uint8_t output_channel,
                        int16_t row,
                        int16_t column)
{
    int32_t accumulator = cnn_conv1_bias[output_channel];
    uint8_t kernel_row;
    uint8_t kernel_column;

    for (kernel_row = 0; kernel_row < 2; kernel_row++)
    {
        int16_t input_row = (int16_t)(row + kernel_row);
        if (input_row >= CNN_INITIAL_HEIGHT)
            continue;
        for (kernel_column = 0; kernel_column < 2; kernel_column++)
        {
            int16_t input_column = (int16_t)(column + kernel_column);
            uint8_t value;
            uint16_t weight_index;
            if (input_column >= CNN_INITIAL_WIDTH)
                continue;
            value = initial_pool_at(input, input_row, input_column);
            weight_index = (uint16_t)output_channel * 4U
                           + (uint16_t)kernel_row * 2U
                           + (uint16_t)kernel_column;
            accumulator += (int32_t)value
                           * (int32_t)cnn_conv1_weight[weight_index];
        }
    }
    return accumulator;
}

static void make_pool1_row(const int8_t *input,
                           int16_t pooled_row,
                           uint8_t destination)
{
    uint8_t output_channel;
    uint8_t pooled_column;

    if (pooled_row < 0 || pooled_row >= CNN_POOL1_HEIGHT)
    {
        for (output_channel = 0; output_channel < CNN_CONV1_CHANNELS;
             output_channel++)
            for (pooled_column = 0; pooled_column < CNN_POOL1_WIDTH;
                 pooled_column++)
                cnn_pool1_rows[destination][output_channel][pooled_column] = 0;
        return;
    }

    for (output_channel = 0; output_channel < CNN_CONV1_CHANNELS;
         output_channel++)
    {
        for (pooled_column = 0; pooled_column < CNN_POOL1_WIDTH;
             pooled_column++)
        {
            uint8_t maximum = 0;
            uint8_t row_offset;
            uint8_t column_offset;

            for (row_offset = 0; row_offset < 2; row_offset++)
                for (column_offset = 0; column_offset < 2; column_offset++)
                {
                    int32_t accumulator = conv1_at(
                        input,
                        output_channel,
                        (int16_t)(2 * pooled_row + row_offset),
                        (int16_t)(2 * pooled_column + column_offset));
                    uint8_t activation = requantize_relu_u8(
                        accumulator, CNN_CONV1_REQUANT_Q15);
                    if (activation > maximum)
                        maximum = activation;
                }
            cnn_pool1_rows[destination][output_channel][pooled_column] = maximum;
        }
    }
}

static int32_t conv2_at(uint8_t output_channel,
                        uint8_t scratch_row,
                        int16_t column)
{
    int32_t accumulator = cnn_conv2_bias[output_channel];
    uint8_t input_channel;
    uint8_t kernel_row;
    uint8_t kernel_column;

    for (input_channel = 0; input_channel < CNN_CONV1_CHANNELS;
         input_channel++)
        for (kernel_row = 0; kernel_row < 2; kernel_row++)
            for (kernel_column = 0; kernel_column < 2; kernel_column++)
            {
                int16_t input_column = (int16_t)(column + kernel_column);
                uint16_t weight_index;
                uint8_t value;
                if (input_column >= CNN_POOL1_WIDTH)
                    continue;
                value = cnn_pool1_rows[scratch_row + kernel_row]
                                       [input_channel][input_column];
                weight_index = (((uint16_t)output_channel
                                 * CNN_CONV1_CHANNELS
                                 + (uint16_t)input_channel) * 2U
                                + (uint16_t)kernel_row) * 2U
                               + (uint16_t)kernel_column;
                accumulator += (int32_t)value
                               * (int32_t)cnn_conv2_weight[weight_index];
            }
    return accumulator;
}

uint8_t radar_cnn_classify(const int8_t *input,
                           int32_t logits[RADAR_CNN_CLASS_COUNT])
{
    uint8_t class_index;
    uint8_t best_class = 0;
    uint8_t pooled2_row;

    for (class_index = 0; class_index < RADAR_CNN_CLASS_COUNT; class_index++)
        logits[class_index] = cnn_fc_bias[class_index];

    for (pooled2_row = 0; pooled2_row < CNN_POOL2_HEIGHT; pooled2_row++)
    {
        uint8_t scratch_row;
        uint8_t output_channel;
        uint8_t pooled2_column;
        int16_t first_pool1_row = (int16_t)(2 * pooled2_row);

        for (scratch_row = 0; scratch_row < 3; scratch_row++)
            make_pool1_row(input,
                           (int16_t)(first_pool1_row + scratch_row),
                           scratch_row);

        for (output_channel = 0; output_channel < CNN_CONV2_CHANNELS;
             output_channel++)
        {
            for (pooled2_column = 0; pooled2_column < CNN_POOL2_WIDTH;
                 pooled2_column++)
            {
                uint8_t maximum = 0;
                uint8_t row_offset;
                uint8_t column_offset;
                uint16_t feature_index;

                for (row_offset = 0; row_offset < 2; row_offset++)
                    for (column_offset = 0; column_offset < 2; column_offset++)
                    {
                        int32_t accumulator = conv2_at(
                            output_channel,
                            row_offset,
                            (int16_t)(2 * pooled2_column + column_offset));
                        uint8_t activation = requantize_relu_u8(
                            accumulator, CNN_CONV2_REQUANT_Q15);
                        if (activation > maximum)
                            maximum = activation;
                    }

                feature_index = (uint16_t)(
                    output_channel * (CNN_POOL2_HEIGHT * CNN_POOL2_WIDTH)
                    + pooled2_row * CNN_POOL2_WIDTH + pooled2_column);
                for (class_index = 0; class_index < RADAR_CNN_CLASS_COUNT;
                     class_index++)
                    logits[class_index] += (int32_t)maximum
                        * (int32_t)cnn_fc_weight[
                            (uint16_t)class_index * CNN_FC_FEATURES
                            + feature_index];
            }
        }

#ifndef CNN_HOST_TEST
        WDT_A_resetTimer(WDT_A_BASE);
#endif
    }

    for (class_index = 1; class_index < RADAR_CNN_CLASS_COUNT; class_index++)
        if (logits[class_index] > logits[best_class])
            best_class = class_index;

    return best_class;
}

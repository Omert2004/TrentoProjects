#include <driverlib.h>
#include <DSPLib.h>
#include <math.h>
#include "STFT.h"
#include "radar_configuration.h"

typedef struct {
    int bin;
    int negate_q;   // 0 = normal direction, 1 = flipped (tests I/Q sign convention)
    const char *label_id;  // just for your own reference, not transmitted
} test_case_t;

// Test matrix: DC, low bin, mid bin (already passed), near-Nyquist, and a
// negative-direction case to confirm I/Q ordering preserves motion sign.
static test_case_t tests[] = {
    { 0,   0, "DC" },
    { 1,   0, "low_bin" },
    { 20,  0, "mid_bin_known_good" },
    { 20,  1, "mid_bin_negated_Q" },
    { 100, 0, "high_bin" },
    { 127, 0, "near_nyquist" },
};
#define NUM_TESTS (sizeof(tests) / sizeof(tests[0]))

int main(void)
{
    WDT_A_hold(WDT_A_BASE);

    Init_Clock();
    Init_GPIO();
    Init_UART();

    __enable_interrupt();

    STFT_init();

    static uint16_t fake_I[FFT_SIZE];
    static uint16_t fake_Q[FFT_SIZE];

    while (1)
    {
        int t;
        for (t = 0; t < NUM_TESTS; t++)
        {
            int bin = tests[t].bin;
            int negate_q = tests[t].negate_q;

            int k;
            for (k = 0; k < FFT_SIZE; k++)
            {
                float angle = 2.0f * 3.14159265f * bin * k / FFT_SIZE;
                float q_val = negate_q ? -sinf(angle) : sinf(angle);
                fake_I[k] = (uint16_t)(2048 + 1500.0f * cosf(angle));
                fake_Q[k] = (uint16_t)(2048 + 1500.0f * q_val);
            }

            STFT_compute_next_segment(fake_I, fake_Q);

            int8_t peak_val = -1;
            int peak_bin = -1;
            int n;
            for (n = 0; n < FFT_SIZE; n++)
            {
                if (spectrogram[STFT_SEGMENTS - 1][n] > peak_val)
                {
                    peak_val = spectrogram[STFT_SEGMENTS - 1][n];
                    peak_bin = n;
                }
            }

            // Expected shifted bin: normal direction -> center+bin,
            // negated Q -> center-bin (mirrors on the other side, confirming
            // direction/sign is preserved correctly through the pipeline).
            int expected_bin = negate_q
                ? ((FFT_SIZE / 2 - bin) + FFT_SIZE) % FFT_SIZE
                : (bin + FFT_SIZE / 2) % FFT_SIZE;

            // Frame 1: test index (IFI slot) + expected bin (IFQ slot)
            UART_putFrame((uint16_t)t, (uint16_t)expected_bin);
            // Frame 2: actual peak bin (IFI slot) + actual peak value (IFQ slot)
            UART_putFrame((uint16_t)peak_bin, (uint16_t)peak_val);

            __delay_cycles(800000);  // ~0.1s pause so frames are easy to read/separate
        }

        __delay_cycles(4000000);  // longer pause before repeating the whole cycle
    }
}
#include <driverlib.h>
#include <DSPLib.h>
#include <math.h>
#include "STFT.h"
#include "radar_configuration.h"   // reuse Init_Clock/Init_GPIO/Init_UART/UART_putFrame

int main(void)
{
    WDT_A_hold(WDT_A_BASE);

    Init_Clock();
    Init_GPIO();
    Init_UART();

    __enable_interrupt();

    // Generate a pure Q15 sine wave at a known bin (bin 20 of 256), shaped
    // like real 12-bit ADC data, to verify the FFT peak lands where expected.
    static uint16_t fake_I[FFT_SIZE];
    static uint16_t fake_Q[FFT_SIZE];

    int k;
    for (k = 0; k < FFT_SIZE; k++)
    {
        float angle = 2.0f * 3.14159265f * 20.0f * k / FFT_SIZE;
        fake_I[k] = (uint16_t)(2048 + 1500.0f * cosf(angle));
        fake_Q[k] = (uint16_t)(2048 + 1500.0f * sinf(angle));
    }

    STFT_init();
    STFT_compute_next_segment(fake_I, fake_Q);

    // Find the peak bin in the newly-computed spectrogram row, and report
    // it over UART -- no debugger needed. Expected: peak bin near
    // FFT_SIZE/2 + 20 = 148, since DC is frequency-shifted to the middle.
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

    // Reuse UART_putFrame's 2x16-bit format: IFI slot = peak bin index,
    // IFQ slot = peak magnitude value. Sent repeatedly so denene.py catches
    // it even if you connect a moment late.
    while (1)
    {
        UART_putFrame((uint16_t)peak_bin, (uint16_t)peak_val);
    }
}
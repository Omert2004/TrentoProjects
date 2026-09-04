Set-Location "C:\Users\Oguzm\OneDrive - ozyegin.edu.tr\Desktop\Github_Projects\TrentoProjects\modelzoo"

$run = ".\outputs\cnn_recaptured_10sessions_seed_comparison\seed42_20260903_121114"

Compress-Archive `
    -LiteralPath `
        "$run\state_dict.pt", `
        ".\src\models\basics.py", `
        ".\conf\model\cnn.yaml" `
    -DestinationPath ".\cnn_seed42_for_msp430.zip" `
    -Force

Resolve-Path ".\cnn_seed42_for_msp430.zip"# STFT_Parity — model-pilot Q15/LEA validation

This diagnostic firmware validates the exact preprocessing contract selected
for the four-class model-pilot dataset:

- 2000 Hz complex I/Q sampling
- one-sample first difference on raw ADC codes
- `DIFF_SHIFT=4` with Q15 saturation
- 256-point quantized Q15 Hann window
- fixed-scaled `msp_cmplx_fft_fixed_q15` on LEA
- integer magnitude-squared, floor-log2, and fftshift

It captures 257 raw I/Q samples, stops acquisition, forms exactly 256 signed
differences, and repeatedly sends one CRC-protected diagnostic group:

1. `E0`: 257 raw I/Q pairs plus configuration flags
2. `E1`, stage 1: exact post-Hann Q15 complex buffer
3. `E1`, stage 2: exact post-LEA FFT Q15 complex buffer
4. `E2`: final on-board uint8-range column (`0..31`)

The raw flags use bit 0 for first-difference enable and the high nibble for
`DIFF_SHIFT`. For the current contract the byte is `0x41`.

## CCS build and flash

Use this as a standalone CCS project. Do not compile the continuous AI Phase
`main.c` into the parity build. The project must include `parity_main.c`, the
files under `inc/` and `src/`, DriverLib, DSPLib, and the supplied linker file.

The source deliberately refuses to compile unless:

```c
#define ENABLE_CLUTTER_CANCEL 1
#define DIFF_SHIFT 4
#define PARITY_SAMPLING_RATE_HZ 2000UL
```

Clean, rebuild, flash, and reset the board. Acquisition stops before UART or
DSP diagnostics, so the transmitted stages all belong to the exact same
one-shot capture. Reset again to acquire a different physical window.

## Host parity command

From `STFT_Parity\Python Files`:

```powershell
py -3.11 -m pip install -r requirements.txt
py -3.11 stft_parity_check.py --port COM7 --baud 115200 `
  --out-dir .\parity_captures_model_pilot
```

The default Hann path is `..\src\window_q15.c`; use `--window-file` only when
testing a different coefficient file.

The command saves a `.report.json` and `.npz`. Return both if a stage differs.
Interpret the report in this order:

- `window_stage`: difference, shift/saturation, and Hann multiplication
- `fft_stage_candidate`: generic Python radix-2 FFT versus LEA
- `postprocess_from_board_fft`: magnitude/log2/fftshift only
- `end_to_end_candidate`: full Python candidate versus board column

`Bit-exact parity: PASS` means every stage matched. `NOT YET PROVEN` can still
be usable for training if the only difference is LEA internal FFT rounding,
but it must not be described as bit exact. Use the reported mismatch count and
maximum absolute error when documenting that decision.

## Previous unfiltered baseline

The earlier filter-off experiment at 2 kHz found exact Hann multiplication and
exact postprocessing from the board FFT. The generic Python FFT differed in
332 of 512 Q15 values (maximum absolute difference 6); the end-to-end column
differed in 134 of 256 bins (maximum absolute difference 3). That localized
the remaining mismatch to undocumented LEA/DSPLib intermediate rounding. The
new run is still required because the production input is now difference +
shift 4 rather than centered raw ADC codes.

Do not use this diagnostic build for continuous inference or raw collection.
Reflash `AI_Phase` after parity work.

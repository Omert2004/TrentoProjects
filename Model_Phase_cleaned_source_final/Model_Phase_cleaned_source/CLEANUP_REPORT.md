# Model_Phase behavior-preserving cleanup

This package cleans the source tree without changing the validated radar or
inference pipeline. It is intended to replace the matching files in the
existing Code Composer Studio `Model_Phase` project; the TI DriverLib and
DSPLib directories remain in the existing project.

## Locked runtime behavior

- MSP430FR5994 target at an 8 MHz CPU clock.
- IFI on ADC A12 and IFQ on ADC A13.
- 2 kHz sampling rate.
- 256-sample FFT, 128-sample hop, Hann window, and 15 STFT columns.
- First-difference clutter cancellation with `DIFF_SHIFT = 4`.
- Integer 8/16-channel TinyCNN with 2,620 parameters and 83,968 MACs.
- Class order: left scroll, right scroll, clicking hand, empty.
- CRC-protected D0/D2/D3/D4 UART protocol.
- Acquisition pause and watchdog policy during inference.

The CNN weights, quantization constants, inference arithmetic, linker script,
DMA framing, ADC setup, STFT calculations, and class order are unchanged.

## Cleanup changes

- Corrected stale 4 kHz comments and Python defaults to 2 kHz.
- Replaced historical development notes with concise, current explanations.
- Removed unused `UART_puts` and `UART_putU16` declarations and definitions.
- Updated the protocol parser documentation to include D4 CNN result frames.
- Added argument validation and clearer formatting to the host utilities.
- Added missing final newlines to the generated Hann-window files.
- Added `requirements.txt` for the host dependency.
- Added offline UART protocol regression tests.
- Restored the host scripts to their real `New Python Files` directory and the
  reference linker map to `Debug/`.

## Requirements

Firmware build:

- Code Composer Studio with TI MSP430 compiler support.
- MSP430FR5xx/6xx DriverLib.
- MSP-DSPLib 1.30.00.02 (including LEA support).
- The project-specific `lnk_msp430fr5994.cmd` included here.

Host tools:

- Python 3.10 or newer.
- PySerial 3.5 or newer.

Install the host dependency from the project root:

```powershell
& "$env:USERPROFILE\venvs\modelzoo\Scripts\Activate.ps1"
python -m pip install -r .\requirements.txt
```

## Verification

Checks completed before packaging:

- All Python files compile successfully.
- Both offline protocol regression tests pass.
- The TinyCNN kernel compiles with GCC in `CNN_HOST_TEST` mode under
  `-Wall -Wextra -Werror -pedantic` and runs a synthetic inference.
- No stale 4 kHz configuration remains in active source files.
- The following protected files are byte-for-byte unchanged from the supplied
  working project:

| File | SHA-256 |
|---|---|
| `src/radar_cnn.c` | `affd51c17730a9c014ac872b990e8d4b5860e59a9ed506dd9de9fb172336001f` |
| `src/cnn_weights.c` | `303416ebd880e8996923892f9b6870ec22c8dd5686c4cdad9fcc72c83f5fc90a` |
| `inc/cnn_weights.h` | `ba10db47e7ef02b14d0b2dd48e7c6905c0ebdd6604ed1cdae02f8e5e7e8e3543` |
| `lnk_msp430fr5994.cmd` | `831036a49453bf921b170681dadaf3a9ec0819066af481c345ae22009f47961c` |

Run the offline host tests from the project root:

```powershell
python -m unittest discover -s .\tests -v
```

Then use Code Composer Studio:

1. Back up the current working project.
2. Replace only the corresponding files from this package.
3. Select **Project > Clean**, then **Project > Build Project**.
4. Confirm the build has no warnings.
5. Flash the board and run the prediction monitor:

```powershell
Set-Location ".\New Python Files"
python -u .\cnn_result_monitor.py --port COM7 --duration 300
```

The monitor should receive one D4 prediction approximately every two seconds,
matching the previously validated runtime behavior.

## Reference memory map

`Debug/Model_Phase.map` is the last successful build map supplied with the
project and predates recompilation of these cleanup-only changes:

| Region | Used | Capacity | Free |
|---|---:|---:|---:|
| General RAM | 3,684 B | 4,096 B | 412 B |
| LEA RAM | 2,048 B | 3,784 B | 1,736 B |
| FRAM + FRAM2 | 23,126 B | 262,008 B | 238,882 B |

General RAM remains the tightest resource. Future low-power or model changes
should therefore be introduced separately and measured with a fresh linker map.

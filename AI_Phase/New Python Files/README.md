# AI_Phase — validated 4 kHz on-chip STFT stream

This coordinated firmware/Python update replaces the unprotected C0/C2/C3
stream with CRC-protected D0/D2/D3 packets. Replace all project files together;
old firmware and new Python scripts are intentionally incompatible.

The firmware remains configured for 4000 Hz, `FFT_SIZE=256`, and `FFT_HOP=128`
(31.25 columns/s). `ENABLE_CLUTTER_CANCEL` is set to `0` for the initial
on-chip-versus-Python parity work.

## Firmware changes

- ADC interrupts remain active during LEA/STFT computation so the 4 kHz ring
  continues filling.
- ADC, Timer_A1, and DMA ISRs do not terminate DSPLib's LPM0 wait while
  `stft_in_progress` is true.
- The watchdog is fed inside hop draining and DMA waiting loops.
- Rate/profile frames are sent in the idle slot after the previous DMA, so they
  are not starved at full column rate.
- The DMA start waits for `UCTXIFG` before clearing it and arming the transfer.
- Ring overflows increment a cumulative 32-bit MCU drop counter.

## Wire protocol

All integers are little-endian. CRC16-CCITT-FALSE uses initial `0xFFFF`,
polynomial `0x1021`, no reflection, and no final XOR. CRC covers the marker
through the last payload byte; sync and CRC bytes are excluded.

```text
AA 55 D0 column_sequence:u16 first_new_accepted_sample_index:u32
         cumulative_mcu_drop_count:u32 column[256]:u8 crc:u16

AA 55 D2 report_sequence:u16 accepted_samples_last_second:u16
         cumulative_mcu_drop_count:u32 crc:u16

AA 55 D3 report_sequence:u16 hop_count:u16 stft_ticks:u16
         dma_wait_ticks:u16 crc:u16
```

A D0 packet is 271 bytes. At 31.25 columns/s it uses about 8469 bytes/s,
73.5% of a 115200-baud 8-N-1 link, before the small once-per-second diagnostic
frames.

## Setup and validation

```powershell
python3.11 -m pip install -r requirements.txt
python3.11 -m unittest discover -s tests -v
```

Clean, rebuild, and flash the CCS project. Then run:

```powershell
python3.11 test1_adc_rate.py --port COM7 --baud 115200 --duration 15
python3.11 profile_check.py --port COM7 --baud 115200 --duration 15
python3.11 rate_check.py --port COM7 --baud 115200 --sampling-rate 4000 --duration 15
```

All three should report `PASS`. A normal run should show approximately 4000
accepted ADC samples/s, 31–32 hops/s, 31.25 columns/s, zero CRC/sequence/sample
errors, and zero MCU drops. Startup alignment bytes are reported separately and
do not count as corruption.

For live inspection:

```powershell
python3.11 STFT_check.py --port COM7 --baud 115200 --every 10
python3.11 radar_stft_capture.py --port COM7 --baud 115200
```

`raw_print.py` is intentionally retired in this project; use the separate
Raw_Data_Capture project when raw I/Q is required.

## Validated dataset capture

```powershell
python3.11 radar_dataset_capture.py --port COM7 --baud 115200 `
  --gesture-class no_movement --duration-min 1
```

The `.txt` file contains the original, unmasked 256-bin columns. Its
`.metadata.json` sidecar records the configured and measured rates, full parser
statistics, first/last device sequences and sample indices, MCU drops,
diagnostic reports, and a SHA-256 of the data. A failed capture is retained for
diagnosis, exits with code 2, and is explicitly marked unsafe.

Transport validation does **not** claim that the on-chip Q15 STFT equals the PC
reference. Metadata deliberately leaves
`on_chip_vs_python_stft_parity_verified=false` until the planned same-window
parity test is completed.

View an offline capture with fixed 0..31 amplitude scaling and nearest-neighbor
rendering:

```powershell
python3.11 spectrogram_view.py dataset\no_movement\example.txt
python3.11 visualizer.py dataset\no_movement\example.txt
```

Create deterministic windows while preventing a source session from leaking
across train/validation/test splits:

```powershell
python3.11 splitter.py dataset\no_movement\example.txt windows `
  --split train --window-columns 15 --stride-columns 15
```

The shared `split_manifest.json` rejects any later attempt to assign the same
source-file hash to a different split.

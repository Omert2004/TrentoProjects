"""Create human-readable Pipeline-C spectrograms from model-pilot raw captures.

The PNG files produced by this script are for inspection and presentation.  They
contain axes, titles, event markers, and a colorbar.  Model training must use the
numeric 256x15 tensors exported by ``export_model_windows.py`` instead.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from filter_candidate_check import emulate_difference_q15
from raw_data import load_raw_csv, resolve_sampling_rate
from spectrogram_view import compute_spectrogram, positive_int


DEFAULT_FFT_SIZE = 256
DEFAULT_HOP = 128
DEFAULT_DIFF_SHIFT = 4
DEFAULT_FREQUENCY_LIMIT_HZ = 250.0
SPEED_ORDER = ("slow", "normal", "fast")
DISTANCE_ORDER = ("near", "mid", "far", "na")
CLASS_ORDER = (
    "empty",
    "clicking_hand",
    "left_horizontal_scroll",
    "right_horizontal_scroll",
)


@dataclass
class CaptureView:
    metadata_path: Path
    csv_path: Path
    metadata: dict[str, Any]
    subject: str
    session: str
    gesture_class: str
    speed: str
    distance: str
    sampling_rate_hz: float
    matrix: np.ndarray
    frequencies_hz: np.ndarray
    times_s: np.ndarray
    clipped_samples: int
    total_samples: int

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        return (
            self.subject,
            self.session,
            self.gesture_class,
            self.speed,
            self.distance,
        )


def positive_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def nonnegative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-root",
        action="append",
        default=None,
        help=(
            "Root containing pilot metadata; repeat for additional roots. "
            "Default: dataset/model-pilot/raw/fs2000"
        ),
    )
    parser.add_argument(
        "--out",
        default="dataset/model-pilot/spectrogram-visualizations",
    )
    parser.add_argument("--fft-size", type=positive_int, default=DEFAULT_FFT_SIZE)
    parser.add_argument("--hop", type=positive_int, default=DEFAULT_HOP)
    parser.add_argument("--diff-shift", type=nonnegative_int, default=DEFAULT_DIFF_SHIFT)
    parser.add_argument(
        "--frequency-limit-hz",
        type=positive_float,
        default=DEFAULT_FREQUENCY_LIMIT_HZ,
        help="Displayed Doppler range is +/- this value (training still uses all bins).",
    )
    parser.add_argument("--vmin", type=float, default=None)
    parser.add_argument("--vmax", type=float, default=None)
    parser.add_argument(
        "--session-context",
        help="Optional session_context.json containing posture and hand-height labels.",
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def load_context(path: str | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    sessions = payload.get("sessions", {})
    if not isinstance(sessions, dict):
        raise ValueError("session context must contain a sessions object")
    return {str(key).lower(): dict(value) for key, value in sessions.items()}


def metadata_csv_path(metadata_path: Path, metadata: dict[str, Any]) -> Path:
    raw_name = metadata.get("capture_csv")
    if raw_name:
        candidate = metadata_path.with_name(str(raw_name))
    else:
        candidate = metadata_path.with_name(
            metadata_path.name.removesuffix(".metadata.json") + ".csv"
        )
    if not candidate.exists():
        raise ValueError(f"missing raw CSV for {metadata_path}: {candidate}")
    return candidate


def capture_identity(metadata: dict[str, Any], metadata_path: Path) -> tuple[str, ...]:
    return (
        str(metadata.get("subject_id") or "unknown-subject"),
        str(metadata.get("session_id") or "unknown-session"),
        str(metadata.get("gesture_class") or metadata.get("label") or "unknown-class"),
        str(metadata.get("speed") or "na"),
        str(metadata.get("distance") or "na"),
        metadata_path.name,
    )


def load_capture(
    metadata_path: Path,
    *,
    fft_size: int,
    hop: int,
    diff_shift: int,
) -> CaptureView:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    if metadata.get("host_transport_validation_passed") is not True:
        raise ValueError(f"capture did not pass host validation: {metadata_path}")
    csv_path = metadata_csv_path(metadata_path, metadata)
    capture = load_raw_csv(csv_path)
    sampling_rate = resolve_sampling_rate(capture, None)

    matrices: list[np.ndarray] = []
    clipped_samples = 0
    total_samples = 0
    for _segment_id, i_values, q_values in capture.arrays_by_segment():
        signal = i_values.astype(float) + 1j * q_values.astype(float)
        filtered, clipped = emulate_difference_q15(signal, diff_shift)
        clipped_samples += clipped
        total_samples += len(signal)
        matrices.append(
            compute_spectrogram(
                filtered,
                fft_size=fft_size,
                hop=hop,
                window_name="hann",
            )
        )
    matrix = np.concatenate(matrices, axis=1)
    frequencies = np.fft.fftshift(
        np.fft.fftfreq(fft_size, d=1.0 / sampling_rate)
    )
    times = (np.arange(matrix.shape[1]) * hop + fft_size / 2.0) / sampling_rate
    subject, session, gesture_class, speed, distance, _ = capture_identity(
        metadata, metadata_path
    )
    return CaptureView(
        metadata_path=metadata_path,
        csv_path=csv_path,
        metadata=metadata,
        subject=subject,
        session=session,
        gesture_class=gesture_class,
        speed=speed,
        distance=distance,
        sampling_rate_hz=sampling_rate,
        matrix=matrix,
        frequencies_hz=frequencies,
        times_s=times,
        clipped_samples=clipped_samples,
        total_samples=total_samples,
    )


def display_band(capture: CaptureView, frequency_limit_hz: float) -> np.ndarray:
    return np.abs(capture.frequencies_hz) <= frequency_limit_hz


def add_event_regions(axis: plt.Axes, capture: CaptureView, *, labels: bool) -> None:
    events = capture.metadata.get("event_markers", [])
    for index, event in enumerate(events, start=1):
        start = float(event.get("start_s", 0.0))
        end = float(event.get("end_s", start))
        axis.axvspan(start, end, color="#52D1DC", alpha=0.12, linewidth=0)
        axis.axvline(start, color="#52D1DC", linewidth=0.7, alpha=0.9)
        if labels:
            axis.text(
                (start + end) / 2.0,
                0.98,
                str(event.get("repetition", index)),
                transform=axis.get_xaxis_transform(),
                ha="center",
                va="top",
                fontsize=7,
                color="#E9FBFC",
            )


def draw_spectrogram(
    axis: plt.Axes,
    capture: CaptureView,
    *,
    frequency_limit_hz: float,
    vmin: float,
    vmax: float,
    title: str,
    event_labels: bool = True,
) -> Any:
    band = display_band(capture, frequency_limit_hz)
    image = axis.imshow(
        capture.matrix[band, :],
        origin="lower",
        aspect="auto",
        interpolation="nearest",
        extent=(
            float(capture.times_s[0]),
            float(capture.times_s[-1]),
            float(capture.frequencies_hz[band][0]),
            float(capture.frequencies_hz[band][-1]),
        ),
        cmap="inferno",
        vmin=vmin,
        vmax=vmax,
    )
    axis.axhline(0.0, color="#7FE7EF", linestyle="--", linewidth=0.7, alpha=0.75)
    add_event_regions(axis, capture, labels=event_labels)
    axis.set_title(title, fontsize=9)
    axis.set_xlim(float(capture.times_s[0]), float(capture.times_s[-1]))
    return image


def posture_suffix(session: str, context: dict[str, dict[str, Any]]) -> str:
    values = context.get(session.lower(), {})
    parts = [str(values.get(key)) for key in ("posture", "hand_height") if values.get(key)]
    return f" ({', '.join(parts)})" if parts else ""


def full_capture_path(out_root: Path, capture: CaptureView) -> Path:
    return (
        out_root
        / "full-captures"
        / capture.subject
        / capture.session
        / capture.gesture_class
        / capture.speed
        / capture.distance
        / f"{capture.csv_path.stem}.png"
    )


def render_full_capture(
    capture: CaptureView,
    path: Path,
    *,
    frequency_limit_hz: float,
    vmin: float,
    vmax: float,
    context: dict[str, dict[str, Any]],
    diff_shift: int,
) -> None:
    fig, axis = plt.subplots(figsize=(12.0, 5.3), dpi=150)
    image = draw_spectrogram(
        axis,
        capture,
        frequency_limit_hz=frequency_limit_hz,
        vmin=vmin,
        vmax=vmax,
        title=(
            f"{capture.gesture_class} | {capture.speed} | {capture.distance} | "
            f"{capture.session}{posture_suffix(capture.session, context)}"
        ),
    )
    axis.set_xlabel("Time (s); cyan bands are labeled action windows")
    axis.set_ylabel("Doppler frequency (Hz)")
    colorbar = fig.colorbar(image, ax=axis, pad=0.015)
    colorbar.set_label("Magnitude (dB, shared display scale)")
    clip_percent = 100.0 * capture.clipped_samples / max(capture.total_samples, 1)
    fig.text(
        0.5,
        0.01,
        (
            "Visualization only: axes, title, event bands, and colorbar are not model input. "
            f"Pipeline C: difference, Q15 shift {diff_shift}; clipping {clip_percent:.5f}%."
        ),
        ha="center",
        va="bottom",
        fontsize=8,
        color="#46515A",
    )
    fig.tight_layout(rect=(0, 0.035, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def render_grid(
    items: list[tuple[str, CaptureView | None]],
    *,
    rows: int,
    columns: int,
    path: Path,
    heading: str,
    frequency_limit_hz: float,
    vmin: float,
    vmax: float,
) -> None:
    fig, axes = plt.subplots(
        rows,
        columns,
        figsize=(max(7.8, 4.25 * columns), 2.45 * rows + 0.55),
        dpi=150,
        squeeze=False,
        sharex=False,
        sharey=True,
    )
    image = None
    for axis, (title, capture) in zip(axes.ravel(), items):
        if capture is None:
            axis.text(0.5, 0.5, "No validated capture", ha="center", va="center")
            axis.set_title(title, fontsize=9)
            axis.set_axis_off()
            continue
        image = draw_spectrogram(
            axis,
            capture,
            frequency_limit_hz=frequency_limit_hz,
            vmin=vmin,
            vmax=vmax,
            title=title,
            event_labels=False,
        )
        axis.set_xlabel("Time (s)", fontsize=8)
        axis.tick_params(labelsize=7)
    for index, axis in enumerate(axes.ravel()):
        if index % columns == 0 and axis.axison:
            axis.set_ylabel("Doppler (Hz)", fontsize=8)
    for axis in axes.ravel()[len(items) :]:
        axis.set_axis_off()
    fig.suptitle(heading, fontsize=13, fontweight="bold")
    if image is not None:
        colorbar_axis = fig.add_axes([0.925, 0.15, 0.016, 0.68])
        colorbar = fig.colorbar(image, cax=colorbar_axis)
        colorbar.set_label("Magnitude (dB, shared scale)", fontsize=8)
        colorbar.ax.tick_params(labelsize=7)
    fig.text(
        0.5,
        0.008,
        "Human-inspection PNGs only; titles, axes, event bands, and colorbar are excluded from training tensors.",
        ha="center",
        fontsize=8,
        color="#46515A",
    )
    fig.subplots_adjust(left=0.07, right=0.89, top=0.91, bottom=0.08, hspace=0.42, wspace=0.18)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def choose_capture(
    lookup: dict[tuple[str, str, str, str], CaptureView],
    *,
    session: str,
    gesture_class: str,
    speed: str,
    distance: str,
) -> CaptureView | None:
    return lookup.get((session, gesture_class, speed, distance))


def render_comparisons(
    captures: list[CaptureView],
    out_root: Path,
    *,
    frequency_limit_hz: float,
    vmin: float,
    vmax: float,
    context: dict[str, dict[str, Any]],
) -> list[Path]:
    comparisons = out_root / "comparisons"
    lookup = {
        (capture.session, capture.gesture_class, capture.speed, capture.distance): capture
        for capture in captures
    }
    created: list[Path] = []

    sessions = sorted({capture.session for capture in captures})
    classes = sorted(
        {capture.gesture_class for capture in captures},
        key=lambda value: CLASS_ORDER.index(value) if value in CLASS_ORDER else 99,
    )
    for session in sessions:
        for gesture_class in classes:
            available = [
                capture
                for capture in captures
                if capture.session == session and capture.gesture_class == gesture_class
            ]
            if not available:
                continue
            distances = [
                value
                for value in DISTANCE_ORDER
                if any(capture.distance == value for capture in available)
            ]
            items = []
            for distance in distances:
                for speed in SPEED_ORDER:
                    items.append(
                        (
                            f"{distance} | {speed}",
                            choose_capture(
                                lookup,
                                session=session,
                                gesture_class=gesture_class,
                                speed=speed,
                                distance=distance,
                            ),
                        )
                    )
            path = comparisons / "session-matrices" / f"{session}_{gesture_class}.png"
            render_grid(
                items,
                rows=len(distances),
                columns=3,
                path=path,
                heading=f"{gesture_class} — {session}{posture_suffix(session, context)}",
                frequency_limit_hz=frequency_limit_hz,
                vmin=vmin,
                vmax=vmax,
            )
            created.append(path)

    representative_session = {
        "empty": "session03",
        "clicking_hand": "session03",
        "left_horizontal_scroll": "scrollpilot01",
        "right_horizontal_scroll": "scrollpilot01",
    }
    class_items: list[tuple[str, CaptureView | None]] = []
    for gesture_class in CLASS_ORDER:
        session = representative_session[gesture_class]
        distance = "na" if gesture_class == "empty" else "near"
        capture = choose_capture(
            lookup,
            session=session,
            gesture_class=gesture_class,
            speed="normal",
            distance=distance,
        )
        if capture is not None:
            class_items.append((f"{gesture_class}\n{session}, normal, {distance}", capture))
    if class_items:
        path = comparisons / "01_class_comparison_seated_normal.png"
        render_grid(
            class_items,
            rows=len(class_items),
            columns=1,
            path=path,
            heading="Class comparison — seated, normal speed, near/NA",
            frequency_limit_hz=frequency_limit_hz,
            vmin=vmin,
            vmax=vmax,
        )
        created.append(path)

    speed_items: list[tuple[str, CaptureView | None]] = []
    present_speed_classes = []
    for gesture_class in CLASS_ORDER:
        session = representative_session[gesture_class]
        distance = "na" if gesture_class == "empty" else "near"
        row = [
            choose_capture(
                lookup,
                session=session,
                gesture_class=gesture_class,
                speed=speed,
                distance=distance,
            )
            for speed in SPEED_ORDER
        ]
        if any(item is not None for item in row):
            present_speed_classes.append(gesture_class)
            speed_items.extend(
                (f"{gesture_class} | {speed}", capture)
                for speed, capture in zip(SPEED_ORDER, row)
            )
    if speed_items:
        path = comparisons / "02_speed_comparison_near.png"
        render_grid(
            speed_items,
            rows=len(present_speed_classes),
            columns=3,
            path=path,
            heading="Speed comparison — same class and near/NA range",
            frequency_limit_hz=frequency_limit_hz,
            vmin=vmin,
            vmax=vmax,
        )
        created.append(path)

    distance_items: list[tuple[str, CaptureView | None]] = []
    distance_classes = []
    for gesture_class in CLASS_ORDER:
        if gesture_class == "empty":
            continue
        session = representative_session[gesture_class]
        row = [
            choose_capture(
                lookup,
                session=session,
                gesture_class=gesture_class,
                speed="normal",
                distance=distance,
            )
            for distance in DISTANCE_ORDER[:3]
        ]
        if any(item is not None for item in row):
            distance_classes.append(gesture_class)
            distance_items.extend(
                (f"{gesture_class} | {distance}", capture)
                for distance, capture in zip(DISTANCE_ORDER[:3], row)
            )
    if distance_items:
        path = comparisons / "03_distance_comparison_normal.png"
        render_grid(
            distance_items,
            rows=len(distance_classes),
            columns=3,
            path=path,
            heading="Distance comparison — same class and normal speed",
            frequency_limit_hz=frequency_limit_hz,
            vmin=vmin,
            vmax=vmax,
        )
        created.append(path)

    session_items: list[tuple[str, CaptureView | None]] = []
    for session in ("session01", "session02", "session03", "session04", "session05"):
        capture = choose_capture(
            lookup,
            session=session,
            gesture_class="clicking_hand",
            speed="normal",
            distance="near",
        )
        if capture is not None:
            session_items.append((f"{session}{posture_suffix(session, context)}", capture))
    if session_items:
        path = comparisons / "04_session_posture_click_near_normal.png"
        render_grid(
            session_items,
            rows=len(session_items),
            columns=1,
            path=path,
            heading="Session/posture comparison — clicking hand, normal, near",
            frequency_limit_hz=frequency_limit_hz,
            vmin=vmin,
            vmax=vmax,
        )
        created.append(path)

    return created


def write_readme(
    path: Path,
    *,
    capture_count: int,
    comparison_count: int,
    args: argparse.Namespace,
    vmin: float,
    vmax: float,
) -> None:
    path.write_text(
        "\n".join(
            [
                "# Model-pilot spectrogram visualizations",
                "",
                f"- Validated captures rendered: {capture_count}",
                f"- Comparison sheets: {comparison_count}",
                f"- Pipeline: C (single-sample difference, Q15 shift {args.diff_shift})",
                f"- STFT: FFT {args.fft_size}, hop {args.hop}, Hann window",
                f"- Displayed Doppler band: ±{args.frequency_limit_hz:g} Hz",
                f"- Shared display scale: {vmin:.2f} to {vmax:.2f} dB",
                "",
                "These PNG files are for visual inspection and presentation only.",
                "Axes, titles, colorbars, and event overlays are not training data.",
                "Use export_model_windows.py to create numeric 256×15 float32 tensors",
                "for training. That exporter retains all 256 frequency bins.",
                "",
                "Cyan shaded bands show scheduled gesture intervals. The numbers in",
                "individual full-capture plots are repetition IDs. Empty captures have",
                "no gesture bands.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    if args.hop > args.fft_size:
        print("Error: --hop cannot exceed --fft-size.", file=sys.stderr)
        return 2
    if args.frequency_limit_hz >= 1000.0:
        print("Error: display frequency limit must be below the 1 kHz Nyquist limit.", file=sys.stderr)
        return 2
    if (args.vmin is None) != (args.vmax is None):
        print("Error: provide both --vmin and --vmax, or neither.", file=sys.stderr)
        return 2
    if args.vmin is not None and args.vmax <= args.vmin:
        print("Error: --vmax must exceed --vmin.", file=sys.stderr)
        return 2

    input_roots = [Path(value) for value in (args.input_root or ["dataset/model-pilot/raw/fs2000"])]
    metadata_paths: list[Path] = []
    for root in input_roots:
        metadata_paths.extend(root.rglob("*.metadata.json"))
    metadata_paths = sorted(set(metadata_paths))
    if not metadata_paths:
        print(f"Error: no metadata files found below {input_roots}.", file=sys.stderr)
        return 2

    out_root = Path(args.out)
    index_path = out_root / "visualization_index.json"
    if index_path.exists() and not args.force:
        print(f"Error: refusing to overwrite {index_path}; use --force.", file=sys.stderr)
        return 2
    try:
        context = load_context(args.session_context)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: invalid session context: {exc}", file=sys.stderr)
        return 2

    captures: list[CaptureView] = []
    seen: set[tuple[str, ...]] = set()
    for metadata_path in metadata_paths:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
            if metadata.get("host_transport_validation_passed") is not True:
                continue
            identity = capture_identity(metadata, metadata_path)
            if identity in seen:
                continue
            seen.add(identity)
            capture = load_capture(
                metadata_path,
                fft_size=args.fft_size,
                hop=args.hop,
                diff_shift=args.diff_shift,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        captures.append(capture)
        print(f"Loaded {capture.session} / {capture.gesture_class} / {capture.speed} / {capture.distance}")

    if not captures:
        print("Error: no validated captures were found.", file=sys.stderr)
        return 2

    if args.vmin is None:
        display_values = np.concatenate(
            [
                capture.matrix[display_band(capture, args.frequency_limit_hz), :].ravel()
                for capture in captures
            ]
        )
        vmin = float(np.percentile(display_values, 5.0))
        vmax = float(np.percentile(display_values, 99.5))
    else:
        vmin = float(args.vmin)
        vmax = float(args.vmax)

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
    index_entries = []
    for capture in captures:
        output_path = full_capture_path(out_root, capture)
        render_full_capture(
            capture,
            output_path,
            frequency_limit_hz=args.frequency_limit_hz,
            vmin=vmin,
            vmax=vmax,
            context=context,
            diff_shift=args.diff_shift,
        )
        index_entries.append(
            {
                "subject": capture.subject,
                "session": capture.session,
                "gesture_class": capture.gesture_class,
                "speed": capture.speed,
                "distance": capture.distance,
                "source_csv": str(capture.csv_path),
                "visualization_png": str(output_path),
                "event_count": len(capture.metadata.get("event_markers", [])),
                "clipped_samples": capture.clipped_samples,
                "total_samples": capture.total_samples,
                "posture": context.get(capture.session.lower(), {}).get("posture"),
                "hand_height": context.get(capture.session.lower(), {}).get("hand_height"),
            }
        )

    comparison_paths = render_comparisons(
        captures,
        out_root,
        frequency_limit_hz=args.frequency_limit_hz,
        vmin=vmin,
        vmax=vmax,
        context=context,
    )
    summary = {
        "schema_version": 1,
        "purpose": "human_visualization_only",
        "pipeline": {
            "name": "C",
            "difference_filter": True,
            "q15_shift": args.diff_shift,
            "fft_size": args.fft_size,
            "hop": args.hop,
            "window": "hann",
        },
        "display": {
            "frequency_limit_hz": args.frequency_limit_hz,
            "vmin_db": vmin,
            "vmax_db": vmax,
            "scale_scope": "all_loaded_captures",
            "axes_and_annotations_are_not_model_input": True,
        },
        "validated_capture_count": len(captures),
        "comparison_sheet_count": len(comparison_paths),
        "captures": index_entries,
        "comparison_sheets": [str(path) for path in comparison_paths],
    }
    out_root.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    write_readme(
        out_root / "README.md",
        capture_count=len(captures),
        comparison_count=len(comparison_paths),
        args=args,
        vmin=vmin,
        vmax=vmax,
    )
    print(
        f"Rendered {len(captures)} full captures and {len(comparison_paths)} comparison sheets to {out_root}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

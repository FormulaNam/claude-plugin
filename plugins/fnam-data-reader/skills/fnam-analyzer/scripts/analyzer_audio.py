"""PCM16 WAV / explicit-scale mono PCM CSV analysis. NumPy; Matplotlib for PNG."""
import argparse
import csv
import importlib.util
import math
from pathlib import Path
import re
import struct
import sys

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("fnam_core", Path(__file__).resolve().parents[2] / "fnam-core/scripts/fnam_core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
MAX_SAMPLES = 8_000_000


def wav_info(path):
    p = core.checked_file(path)
    meta, issues = {"format": "WAV", "size_bytes": p.stat().st_size}, []
    with p.open("rb") as f:
        header = f.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            return meta, ["Unsupported or damaged RIFF/WAVE header."]
        declared = struct.unpack("<I", header[4:8])[0] + 8
        if declared != meta["size_bytes"]:
            issues.append("RIFF declared size differs from actual file size.")
        while f.tell() + 8 <= meta["size_bytes"]:
            tag, size = struct.unpack("<4sI", f.read(8))
            offset = f.tell()
            if offset + size + (size % 2) > meta["size_bytes"]:
                issues.append("Truncated chunk or missing RIFF padding.")
            if tag == b"fmt ":
                if "encoding" in meta:
                    issues.append("Duplicate fmt chunk.")
                raw = f.read(min(size, 16))
                if len(raw) == 16:
                    enc, ch, rate, byte_rate, align, bits = struct.unpack("<HHIIHH", raw)
                    meta.update(encoding=enc, channels=ch, sample_rate_hz=rate,
                                byte_rate=byte_rate, block_align=align, bits_per_sample=bits)
                else:
                    issues.append("Incomplete fmt chunk.")
            elif tag == b"data":
                if "data_offset" in meta:
                    issues.append("Multiple data chunks are unsupported.")
                meta.update(data_offset=offset, declared_data_bytes=size,
                            actual_data_bytes=max(0, min(size, meta["size_bytes"] - offset)))
            f.seek(offset + size + size % 2)
        if f.tell() != meta["size_bytes"]:
            issues.append("Incomplete RIFF chunk structure.")
    if "encoding" not in meta or "data_offset" not in meta:
        issues.append("Missing fmt or data chunk.")
        return meta, issues
    if meta["encoding"] != 1 or meta["bits_per_sample"] != 16 or meta["channels"] not in (1, 2):
        issues.append("Reader supports PCM16 mono/stereo only.")
    align, rate = meta["block_align"], meta["sample_rate_hz"]
    if align != meta["channels"] * 2 or rate <= 0 or meta["byte_rate"] != rate * align:
        issues.append("Invalid PCM frame alignment/sample rate.")
    if align > 0:
        meta["sample_count"] = meta["actual_data_bytes"] // align
        if meta["actual_data_bytes"] % align:
            issues.append("Partial PCM frame.")
        meta["duration_seconds"] = meta["sample_count"] / rate if rate > 0 else None
    return meta, issues


def csv_data(path, rate=None, full_scale=None):
    import numpy as np
    samples, embedded = [], {}
    with core.checked_file(path, 64 * 1024**2).open(encoding="utf-8-sig", newline="") as f:
        for line in f:
            if len(line) > 1024 * 1024:
                raise core.DataError("CSV row too large for the PCM reader; use analyzer_frp.py for header-identified FRP vectors.")
            if not line.strip():
                continue
            if line.lstrip().startswith("#"):
                match = re.match(r"#\s*(sample_rate_hz|sample_rate|full_scale)\s*[:=]\s*([0-9.eE+-]+)\s*$", line.strip())
                if match:
                    key = "rate" if match[1] != "full_scale" else "scale"
                    value = float(match[2])
                    if key in embedded and embedded[key] != value:
                        raise core.DataError("Conflicting CSV header values.")
                    embedded[key] = value
                continue
            row = next(csv.reader([line]))
            if len(row) != 1:
                raise core.DataError("Only headerless single-column PCM CSV is supported.")
            samples.append(float(row[0]))
            if len(samples) > MAX_SAMPLES:
                raise core.DataError("CSV exceeds sample limit; provide a smaller CSV.")
    for explicit, key in ((rate, "rate"), (full_scale, "scale")):
        if explicit is not None and key in embedded and explicit != embedded[key]:
            raise core.DataError("Provided CSV definition conflicts with embedded metadata.")
    rate = embedded.get("rate", rate)
    full_scale = embedded.get("scale", full_scale)
    if rate is not None and (not core.finite(rate) or rate <= 0 or int(rate) != rate):
        raise core.DataError("Sample rate must be a positive integer.")
    if full_scale is not None and (not core.finite(full_scale) or full_scale <= 0):
        raise core.DataError("Full-scale reference must be positive and finite.")
    x = np.asarray(samples, dtype=np.float64).reshape(-1, 1)
    if not len(x) or not np.isfinite(x).all():
        raise core.DataError("Empty or non-finite PCM CSV.")
    return x, rate, full_scale, embedded


def analyze(path, start=0.0, duration=None, rate=None, full_scale=None, nfft=2048, details=False, plot=None, source_root=None):
    import numpy as np
    if not core.finite(start) or start < 0 or (duration is not None and (not core.finite(duration) or duration <= 0)):
        raise core.DataError("Invalid analysis interval.")
    if not isinstance(nfft, int) or not 16 <= nfft <= 65536:
        raise core.DataError("FFT size must be an integer in 16..65536.")
    p, warnings = Path(path), []
    if plot:
        if source_root is None:
            raise core.DataError("Plot requires --source-root: the supplied measurement folder, or the lone input file.")
        boundary = Path(source_root).resolve()
        source, destination = p.resolve(), Path(plot).resolve()
        if not ((boundary.is_dir() and source.is_relative_to(boundary)) or (boundary.is_file() and source == boundary)):
            raise core.DataError("Source root must exist and contain the input file.")
        if destination == boundary or (boundary.is_dir() and destination.is_relative_to(boundary)):
            raise core.DataError("Plot destination is inside the protected source root; choose a separate output location.")
    if p.suffix.lower() == ".wav":
        meta, issues = wav_info(p)
        if issues:
            return core.result(p, "unsupported" if any("Reader supports" in i for i in issues) else "error", meta, warnings=issues)
        rate, full_scale = meta["sample_rate_hz"], 32768.0
        begin = int(start * rate)
        count = max(0, meta["sample_count"] - begin)
        if duration is not None:
            count = min(count, int(duration * rate))
        if count * meta["channels"] > MAX_SAMPLES:
            raise core.DataError("Selected WAV interval exceeds 8,000,000 channel samples; use --start/--duration.")
        with p.open("rb") as f:
            f.seek(meta["data_offset"] + begin * meta["block_align"])
            x = np.frombuffer(f.read(count * meta["block_align"]), dtype="<i2").reshape(-1, meta["channels"]).astype(float)
        meta["definition_source"] = "WAV header / PCM16 specification"
    elif p.suffix.lower() == ".csv":
        x, rate, full_scale, embedded = csv_data(p, rate, full_scale)
        meta = {"format": "PCM_CSV", "channels": 1, "sample_count": len(x), "sample_rate_hz": rate,
                "duration_seconds": len(x) / rate if rate else None,
                "definition_source": {"embedded": embedded, "other_values": "explicit caller input; retain its provenance"}}
        if (start or duration is not None) and rate is None:
            raise core.DataError("A sample rate is required for a time interval.")
        begin = int(start * rate) if rate else 0
        x = x[begin:begin + int(duration * rate)] if duration is not None else x[begin:]
    else:
        raise core.DataError("Expected .wav or supported .csv input.")
    if not len(x):
        raise core.DataError("Selected interval contains no samples.")
    y = x / full_scale if full_scale else x
    if not np.isfinite(y).all() or np.max(np.abs(y)) > 1e100:
        raise core.DataError("Values exceed finite calculation bounds.")
    rows, plot_data = [], []
    n = min(nfft, len(y))
    if rate is None:
        warnings.append("Sample rate unknown: no Hz FFT or seconds axis.")
    if full_scale is None:
        warnings.append("Full-scale reference unknown: no dBFS; amplitudes remain in CSV units.")
    if rate and n >= 16 and n < len(y):
        warnings.append(f"FFT covers only the first {n} of {len(y)} selected frames; peaks are not a whole-recording summary.")
    params = {"start_sample": begin, "analyzed_frames": len(y), "full_scale": full_scale,
              "rms_definition": "20*log10(sqrt(mean((sample/full_scale)^2))); reference=1",
              "fft_size_requested": nfft, "fft_size_used": n, "fft_region": "first FFT-size frames of selected interval",
              "window": "periodic Hann", "amplitude_normalization": "abs(rfft(x*w))/sum(w); double interior bins only",
              "spectrum_unit": "peak amplitude / full scale" if full_scale else "CSV units (peak amplitude)",
              "channel_policy": "separate; no mixing", "detrend": "none", "zero_padding": False,
              "spectrogram_unit": "dBFS/Hz" if full_scale else "dB relative to 1 CSV-unit^2/Hz"}
    for ch in range(y.shape[1]):
        z = y[:, ch]
        rms = float(np.sqrt(np.mean(z * z)))
        row = {"channel": ch + 1, "rms": rms, "rms_dbfs": 20 * math.log10(rms) if full_scale and rms else None,
               "rms_dbfs_state": "silence_negative_infinity" if full_scale and rms == 0 else "known" if full_scale else "unknown_scale",
               "minimum": float(z.min()), "maximum": float(z.max()), "dc_mean": float(z.mean()),
               "pcm_rail_samples": int(np.count_nonzero((x[:, ch] == -32768) | (x[:, ch] == 32767))) if meta["format"] == "WAV" else None}
        if rate and n >= 16:
            w = .5 - .5 * np.cos(2 * np.pi * np.arange(n) / n)
            amp = np.abs(np.fft.rfft(z[:n] * w)) / w.sum()
            amp[1:-1 if n % 2 == 0 else None] *= 2
            freq = np.fft.rfftfreq(n, 1 / rate)
            candidates = [i for i in range(1, len(amp)) if amp[i] > 1e-12 and amp[i] > amp[i-1]
                          and (i == len(amp)-1 or amp[i] >= amp[i+1])]
            chosen = []
            for i in sorted(candidates, key=lambda j: amp[j], reverse=True):
                if all(abs(i-j) >= 3 for j in chosen):
                    chosen.append(i)
                if len(chosen) == 5:
                    break
            row["peaks"] = [{"frequency_hz": float(freq[i]), "peak_amplitude": float(amp[i])} for i in chosen]
            if details:
                row["spectrum"] = {"frequency_hz": freq.tolist(), "peak_amplitude": amp.tolist()}
            hop = max(1, n // 2)
            starts = np.arange(0, len(z) - n + 1, hop)
            cap = max(1, min(512, 2_000_000 // len(freq)))
            if len(starts) > cap:
                starts = starts[np.linspace(0, len(starts)-1, cap, dtype=int)]
            psd = np.stack([np.abs(np.fft.rfft(z[s:s+n] * w))**2 / (rate * (w*w).sum()) for s in starts], axis=1)
            psd[1:-1 if n % 2 == 0 else None] *= 2
            times = (begin + starts + n / 2) / rate
            params.update(spectrogram_hop_samples=hop, spectrogram_frames_rendered=len(starts),
                          spectrogram_total_frames=1 + (len(z)-n)//hop,
                          spectrogram_display_floor_db=-160, spectrogram_time="window centers relative to file start")
            plot_data.append((z, freq, amp, times, psd))
        else:
            row["peaks"] = []
            warnings.append("Spectrum unavailable: sample rate missing or interval shorter than 16 samples.")
        rows.append(row)
    if params.get("spectrogram_frames_rendered", 0) < params.get("spectrogram_total_frames", 0):
        warnings.append("Spectrogram windows are subsampled for display; omitted windows can contain events.")
    if plot:
        if not plot_data:
            raise core.DataError("Cannot render frequency/time plot without a sample rate and sufficient samples.")
        render(plot, plot_data, rate, begin, params)
        params["protected_source_root"] = str(boundary)
    warnings += ["RMS dBFS is a digital level, not dB SPL. Device/gain differences limit physical loudness comparisons.",
                 "PCM rail samples alone do not prove recording-chain clipping."]
    return core.result(p, "partial" if rate is None or full_scale is None or n < 16 else "supported", meta,
                       {"channels": rows, "plot": str(Path(plot).resolve()) if plot else None}, warnings, params)


def render(path, channels, rate, begin, params):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    p = Path(path)
    if p.suffix.lower() != ".png" or p.exists():
        raise core.DataError("Plot must be a new .png file outside the source data.")
    fig, axes = plt.subplots(len(channels), 3, figsize=(15, 4 * len(channels)), squeeze=False)
    for c, (z, freq, amp, times, psd) in enumerate(channels):
        sample = np.linspace(0, len(z)-1, min(len(z), 4000), dtype=int)
        axes[c, 0].plot((begin + sample) / rate, z[sample], linewidth=.6)
        axes[c, 0].set(xlabel="File time (s)", ylabel="Amplitude", title=f"Channel {c+1}: waveform (display sampled)")
        axes[c, 1].plot(freq, amp, linewidth=.8)
        axes[c, 1].set(xlabel="Frequency (Hz)", ylabel=params["spectrum_unit"], title="First-window FFT")
        mesh = axes[c, 2].pcolormesh(times, freq, 10*np.log10(np.maximum(psd, 1e-16)), shading="auto", vmin=-160)
        axes[c, 2].set(xlabel="File time (s)", ylabel="Frequency (Hz)", title="Spectrogram (selected windows)")
        fig.colorbar(mesh, ax=axes[c, 2], label=params["spectrogram_unit"])
    fig.tight_layout()
    try:
        with p.open("xb") as f:
            fig.savefig(f, format="png", dpi=120)
    finally:
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--rate", type=float, help="CSV sample rate, explicitly supplied with provenance")
    parser.add_argument("--full-scale", type=float, help="CSV full-scale reference, explicitly supplied with provenance")
    parser.add_argument("--nfft", type=int, default=2048)
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--plot", type=Path)
    parser.add_argument("--source-root", type=Path, help="Required for plots: protect the supplied measurement folder, or the single input file")
    a = parser.parse_args()
    return analyze(a.input, a.start, a.duration, a.rate, a.full_scale, a.nfft, a.details, a.plot, a.source_root)


if __name__ == "__main__":
    try:
        core.run(main)
    except ModuleNotFoundError as exc:
        core.emit(core.result("runtime", "unsupported", warnings=[str(exc), "Required runtime dependency unavailable; code provision is not automatic analysis."]))
        raise SystemExit(3)

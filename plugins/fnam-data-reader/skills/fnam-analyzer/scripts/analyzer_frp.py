"""FNam Frequency Response Preview: bounded CSV, scoped assets and stored FIR analysis."""
import argparse
from collections import Counter
import importlib.util
import math
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("analyzer_audio", Path(__file__).with_name("analyzer_audio.py"))
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)
core = audio.core
MAX_BYTES = 64 * 1024**2
MAX_VALUES = 1_000_000
ROLES = {"REC_SIGNAL": "AUDIO_CSV", "NOISE_SIGNAL": "AUDIO_CSV",
         "REC_SIGNAL_REARRANGED": "AUDIO_CSV", "NOISE_SIGNAL_REARRANGED": "AUDIO_CSV",
         "ACOUSTIC_MODEL_FIR": "MODEL_CSV"}


def number(headers, key, integer=False, minimum=None):
    value = headers.get(key)
    if value is None:
        return None
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise core.DataError(f"Invalid FRP header {key}.")
    if not math.isfinite(value) or (integer and value != int(value)) or (minimum is not None and value < minimum):
        raise core.DataError(f"Invalid FRP header {key}.")
    return int(value) if integer else value


def read_vector(path):
    """Known FRP header markers only; never flatten arbitrary tabular CSV."""
    import numpy as np
    headers, comments, vector = {}, [], None
    with core.checked_file(path, MAX_BYTES).open(encoding="utf-8-sig") as stream:
        for line in stream:
            if not line.strip():
                continue
            if line.lstrip().startswith("#"):
                if vector is not None or len(comments) >= 256 or len(line) > 8192:
                    raise core.DataError("FRP headers must precede one bounded numeric row.")
                text = line.strip()[1:].strip()
                comments.append(text)
                if ":" in text:
                    key, value = (v.strip() for v in text.split(":", 1))
                    key = key.lower()
                    if key in headers and headers[key] != value:
                        raise core.DataError(f"Conflicting FRP header: {key}")
                    headers[key] = value
                continue
            if vector is not None:
                raise core.DataError("FRP CSV requires one numeric vector row, not a multi-row table.")
            count = line.count(",") + 1
            if count > MAX_VALUES:
                raise core.DataError("FRP vector exceeds 1,000,000 values.")
            vector = np.fromiter((float(v.strip()) for v in line.strip().split(",")), dtype=float, count=count)
    if vector is None or not np.isfinite(vector).all() or np.max(np.abs(vector)) > 1e100:
        raise core.DataError("Empty, non-finite or out-of-range FRP vector.")
    markers = set(comments)
    rearranged = headers.get("rearrangement_role")
    if "AcousticModel_fir" in markers and rearranged is None:
        role = "ACOUSTIC_MODEL_FIR"
    elif rearranged == "SHIFTED (ANALYZER RECORDING)":
        role = "REC_SIGNAL_REARRANGED"
    elif rearranged == "REFERENCE (NOISE GENERATOR SIGNAL)" and "low_freq_response_preview_reference_signal" in markers:
        role = "NOISE_SIGNAL_REARRANGED"
    elif "rec_signal" in markers and rearranged is None:
        role = "REC_SIGNAL"
    elif "low_freq_response_preview_reference_signal" in markers and rearranged is None:
        role = "NOISE_SIGNAL"
    else:
        raise core.DataError("Unknown FRP CSV profile: role is not inferred from filename.")
    rate = number(headers, "sample_rate", True, 1)
    declared = number(headers, "fir_taps" if role == "ACOUSTIC_MODEL_FIR" else "total_samples", True, 1)
    warnings = []
    trimmed = [re.fullmatch(r"Trimmed to (\d+) samples to match aligned rec length\.", c) for c in comments]
    counts = [int(m[1]) for m in trimmed if m]
    if counts and (len(set(counts)) != 1 or counts[0] != len(vector)):
        raise core.DataError("Rearranged reference trim count conflicts with actual samples.")
    if declared is not None and declared != len(vector):
        if role == "NOISE_SIGNAL_REARRANGED" and counts and declared >= len(vector):
            warnings.append("total_samples is inherited from the original reference; actual count follows the explicit trim note.")
        else:
            raise core.DataError("FRP declared count conflicts with actual vector length.")
    return vector, {"format": "FNam_FRP_CSV", "profile": "observed header-marker profile (CSV has no schema version)",
                    "role": role, "sample_rate_hz": rate, "value_count": len(vector),
                    "stored_headers": headers, "stored_comments": comments}, warnings


def summarize(vector, meta, full_scale=None, details=False, fft_size=None):
    import numpy as np
    headers, role, rate = meta["stored_headers"], meta["role"], meta["sample_rate_hz"]
    params = {"provenance": "Reader-computed from exported vector; original app headers retained separately"}
    if role != "ACOUSTIC_MODEL_FIR":
        embedded = number(headers, "full_scale", minimum=0)
        if full_scale is not None and embedded is not None and full_scale != embedded:
            raise core.DataError("Explicit full scale conflicts with FRP header.")
        scale = embedded if embedded is not None else full_scale
        if scale is not None and (not core.finite(scale) or scale <= 0):
            raise core.DataError("Full scale must be positive and finite.")
        rms = float(np.sqrt(np.mean(vector * vector)))
        duration = len(vector)/rate if rate else None
        result = {"sample_count": len(vector), "duration_seconds": duration, "rms_csv_units": rms,
                  "minimum_csv_units": float(vector.min()), "maximum_csv_units": float(vector.max()),
                  "mean_csv_units": float(vector.mean()), "rms_dbfs": 20*math.log10(rms/scale) if rms and scale else None,
                  "rms_dbfs_state": "unknown_scale" if scale is None else "silence_negative_infinity" if rms == 0 else "known"}
        params.update(full_scale=scale, full_scale_source="header" if embedded is not None else "explicit_caller" if scale is not None else "unknown",
                      rms_region="whole exported vector including silence", rms_definition="sqrt(mean(sample**2)); dBFS only with explicit full scale")
        if rate and len(vector) >= 16:
            n = min(2048, len(vector))
            window = .5-.5*np.cos(2*np.pi*np.arange(n)/n)
            amplitude = np.abs(np.fft.rfft(vector[:n]*window))/window.sum()
            amplitude[1:-1 if n % 2 == 0 else None] *= 2
            frequency = np.fft.rfftfreq(n, 1/rate)
            result["signal_spectrum"] = {"frequency_hz": frequency.tolist(), "peak_amplitude_csv_units": amplitude.tolist()}
            params.update(signal_fft_size=n, signal_fft_region="first FFT-size samples only, not a whole-recording summary",
                          signal_fft_window="periodic Hann", signal_fft_normalization="abs(rfft(x*w))/sum(w); double interior bins only")
        if details:
            result["samples_csv_units"] = vector.tolist()
        return result, params
    if fft_size is None:
        fft_size = 1 << (len(vector)-1).bit_length()
    if isinstance(fft_size, bool) or not isinstance(fft_size, int) or not len(vector) <= fft_size <= 1_048_576:
        raise core.DataError("FIR FFT size must include every coefficient and be at most 1,048,576.")
    response = np.fft.rfft(vector, n=fft_size)
    amplitude = np.abs(response)
    frequency = np.fft.rfftfreq(fft_size, 1/rate) if rate else np.fft.rfftfreq(fft_size)
    phase = np.unwrap(np.angle(response)) * 180/np.pi
    indices = np.arange(len(response)) if details else np.unique(np.linspace(0, len(response)-1, min(4096, len(response)), dtype=int))
    lo, hi = number(headers, "band_masking_f0_hz", minimum=0), number(headers, "band_masking_f1_hz", minimum=0)
    for key, value in (("band_mask_f0_hz", lo), ("band_mask_f1_hz", hi)):
        other = number(headers, key, minimum=0)
        if other is not None and value is not None and other != value:
            raise core.DataError("Conflicting FIR band-mask definitions.")
    if lo is None: lo = number(headers, "band_mask_f0_hz", minimum=0)
    if hi is None: hi = number(headers, "band_mask_f1_hz", minimum=0)
    if (lo is None) != (hi is None) or (lo is not None and (lo >= hi or rate and hi > rate/2)):
        raise core.DataError("Invalid or incomplete FIR analysis band.")
    band = (frequency >= lo) & (frequency <= hi) if rate and lo is not None else np.ones(len(frequency), dtype=bool)
    peak_indices = np.flatnonzero(band)
    peak = int(peak_indices[np.argmax(amplitude[peak_indices])]) if len(peak_indices) else None
    peak_tap = int(np.argmax(np.abs(vector)))
    result = {"fir_taps": len(vector), "peak_coefficient_index": peak_tap,
              "peak_coefficient_time_seconds": peak_tap/rate if rate else None,
              "frequency_response": {"frequency_hz" if rate else "cycles_per_sample": frequency[indices].tolist(),
                  "magnitude": amplitude[indices].tolist(),
                  "magnitude_db": [20*math.log10(float(amplitude[j])) if amplitude[j] > 0 else None for j in indices],
                  "phase_degrees_unwrapped": [float(phase[j]) if amplitude[j] > 1e-12 else None for j in indices]},
              "maximum_response": {"frequency_hz": float(frequency[peak]) if rate and peak is not None else None,
                  "magnitude": float(amplitude[peak]) if peak is not None else None},
              "stored_band_hz": [lo, hi] if lo is not None else None}
    if details: result["fir_coefficients"] = vector.tolist()
    params.update(fft_size=fft_size, used_fir_coefficients=len(vector), window="none",
                  normalization="H[k] = rfft(stored FIR, n=fft_size); no /N, no one-sided doubling",
                  response_unit="stored model output/input ratio; dB relative to 1, not dBFS or dB SPL",
                  zero_padding=fft_size-len(vector), bin_spacing_hz=rate/fft_size if rate else None,
                  reported_bins=len(indices), total_bins=len(response), zero_magnitude_db="null means negative infinity",
                  phase_note="Null at near-zero magnitude; unwrap is not a physical delay estimate.",
                  estimation="Stored FIR transformed, not refitted; no H1/Welch/coherence equivalence claimed")
    return result, params


def analyze_vector(path, full_scale=None, details=False, fft_size=None):
    vector, meta, warnings = read_vector(path)
    data, params = summarize(vector, meta, full_scale, details, fft_size)
    if meta["sample_rate_hz"] is None:
        warnings.append("Sample rate absent: Hz and seconds unavailable.")
    if meta["role"] != "ACOUSTIC_MODEL_FIR" and params["full_scale"] is None:
        warnings.append("Amplitude scale is not declared: CSV units only. PCM_16BIT describes encoding, not CSV normalization.")
    warnings.append("A standalone vector does not establish a session relationship. FIR delays can include playback, capture and acoustic paths.")
    return core.result(path, "partial" if meta["sample_rate_hz"] is None else "supported", meta, data, warnings, params)


def resolve_asset(asset, document, sid, candidates):
    reference = asset.get("assetPath")
    base = {"status": "unresolved", "reference": reference, "path": None}
    if "sessionId" in asset and asset["sessionId"] != sid:
        return dict(base, status="conflict", reason="Asset session ID mismatch.")
    try:
        parts = core.reference_parts(reference)
    except core.DataError as exc:
        return dict(base, status="invalid_reference", reason=str(exc))
    parent = Path(document).resolve().parent
    if parent.parent.name == "FreqRespPre" and parent.name != sid:
        return dict(base, status="conflict", reason="Document folder/session mismatch.")
    if parts[:3] == ("FNamDoc", "Armory", "FreqRespPre"):
        if len(parts) < 5 or parts[3] != sid:
            return dict(base, status="conflict", reason="Export reference names another session.")
        if (parent.name, parent.parent.name, parent.parent.parent.name) != (sid, "FreqRespPre", "Armory"):
            return dict(base, reason="Export reference requires <pack>/Armory/FreqRespPre/<sessionId> document location.")
        pack = parent.parent.parent.parent
        target = (pack / Path(*parts[1:])).resolve()
        base.update(pack_anchor=str(pack), basis="document_pack_namespace_path")
    else:
        target = (parent / Path(*parts)).resolve()
        base["basis"] = "document_relative_path"
    if not target.is_relative_to(parent):
        return dict(base, status="conflict", reason="Asset escapes the document session folder.")
    base["expected_path"] = str(target)
    if target in candidates:
        return dict(base, status="matched", path=str(target))
    hints = sorted(str(p) for p in candidates if tuple(p.parts[-len(parts):]) == parts)
    return dict(base, status="ambiguous" if len(hints)>1 else "unresolved" if hints else "missing",
                candidates=hints, reason="No exact local asset at this document's path; suffix hits cannot substitute.")


def read_session(path, root, full_scale=None, details=False, fft_size=None):
    import numpy as np
    doc = core.read_json(path)
    session = doc.get("session")
    if not isinstance(session, dict): raise core.DataError("FRP session must be an object.")
    meta = {"schema_version": doc.get("schemaVersion"), "app_version": session.get("appVersion"), "stored_session": session}
    if doc.get("schemaType") != "FNam_3Tier_SessionEventAsset_JSON" or str(doc.get("schemaVersion")) != "1.0" or session.get("mode") != "FREQ_RESP_PREVIEW":
        return core.result(path, "unsupported", meta, {"stored_document": doc}, ["Unknown FRP schema/mode; no inferred semantics."])
    sid, events, assets = session.get("sessionId"), doc.get("events"), doc.get("assets")
    if not isinstance(sid, str) or not sid or not isinstance(events, list) or not isinstance(assets, list):
        raise core.DataError("FRP requires a session ID and event/asset arrays.")
    for rows, key in ((events, "eventId"), (assets, "assetId")):
        if not all(isinstance(row, dict) and isinstance(row.get(key), str) and row[key] for row in rows):
            raise core.DataError("Missing FRP event/asset identifier.")
        if len({row[key] for row in rows}) != len(rows): raise core.DataError("Duplicate scoped FRP identifier.")
    cfg = session.get("configSnapshot") or {}
    summary = doc.get("summary") or {}
    if not isinstance(cfg, dict) or not isinstance(summary, dict): raise core.DataError("Malformed FRP configuration/summary.")
    event_map = {e["eventId"]: e for e in events}
    role_counts = Counter(a.get("assetRole") for a in assets if isinstance(a.get("assetRole"), str))
    candidates, rows, loaded = core.files(root), [], {}
    warnings = ["Event wall-clock strings, stored relativeTimeMs and actual vector durations are separate clocks; no cross-device subtraction.",
                "measurementValid is an app-stored flag, not Reader certification. Stored FIR is not recomputed from the recordings."]
    for index, asset in enumerate(assets):
        role, eid = asset.get("assetRole"), asset.get("eventId")
        event = event_map.get(eid) if isinstance(eid, str) else None
        link = resolve_asset(asset, path, sid, candidates)
        row = {"asset_id": asset["assetId"], "event_id": eid, "role": role, "source_pointer": f"/assets/{index}", "stored_asset": asset, "link": link}
        if not isinstance(role, str) or role not in ROLES or asset.get("assetType") != ROLES[role]:
            link.update(status="unsupported", path=None, reason="Unsupported FRP asset role/type.")
        elif role_counts[role] != 1 or event is None or asset.get("rowKey", eid) != eid or event.get("sessionId") != sid or event.get("eventType") != "SESSION_CONTROL" or event.get("eventSubType") not in ("MEASUREMENT_START", "MEASUREMENT_STOP"):
            link.update(status="conflict", path=None, reason="Duplicate role, orphan or mismatched/unsupported session event.")
        if link["status"] == "matched":
            try:
                x, info, notes = read_vector(link["path"])
                h, rate = info["stored_headers"], info["sample_rate_hz"]
                if info["role"] != role: raise core.DataError("CSV header role conflicts with assetRole.")
                if cfg.get("channels") not in (None, 1): raise core.DataError("FRP vector profile is mono; channel configuration conflicts.")
                if cfg.get("sampleRate") is not None and (not core.finite(cfg["sampleRate"]) or cfg["sampleRate"] != rate):
                    raise core.DataError("FRP sample rate conflicts with session.")
                if role in ("REC_SIGNAL", "REC_SIGNAL_REARRANGED", "ACOUSTIC_MODEL_FIR") and h.get("session_id") != sid:
                    raise core.DataError("Analyzer CSV session ID conflicts with document.")
                if role.startswith("NOISE_SIGNAL"):
                    notes.append("Generator session_id/local_folder/file_name belong to the generator device; Analyzer JSON assetPath is the local reference.")
                if role == "ACOUSTIC_MODEL_FIR":
                    for value in (cfg.get("firTaps"), summary.get("firTaps")):
                        if value is not None and (not core.finite(value) or value != len(x)):
                            raise core.DataError("FIR coefficient count conflicts with session.")
                if cfg.get("signalType") is not None and h.get("signal_type") is not None and cfg["signalType"] != h["signal_type"]:
                    raise core.DataError("Signal type conflicts with session.")
                data, params = summarize(x, info, full_scale, details, fft_size)
                row.update(metadata=info, result=data, analysis_parameters=params, warnings=notes)
                loaded[role] = (x, info)
            except (core.DataError, ValueError, OSError) as exc:
                link.update(status="conflict", path=None, reason=str(exc))
        rows.append(row)
    alignment = []
    for role, shifted in (("REC_SIGNAL", "REC_SIGNAL_REARRANGED"), ("NOISE_SIGNAL", "NOISE_SIGNAL_REARRANGED")):
        if role not in loaded or shifted not in loaded: continue
        raw, rm = loaded[role]; aligned, am = loaded[shifted]
        lag = number(am["stored_headers"], "network_latency_samples", True, 0)
        check = {"raw_role": role, "aligned_role": shifted, "status": "unverified", "network_latency_samples": lag}
        if lag is not None:
            same_rate = rm["sample_rate_hz"] == am["sample_rate_hz"]
            expected = raw[lag:] if role == "REC_SIGNAL" else raw[:max(0, len(raw)-lag)]
            agrees = same_rate and len(expected) == len(aligned) and np.allclose(expected, aligned, rtol=0, atol=0.00000051)
            check.update(status="consistent" if agrees else "conflict", comparison_tolerance_csv_units=0.00000051,
                         operation="remove initial network-latency samples" if role == "REC_SIGNAL" else "trim end to aligned recording length")
            if summary.get("networkLatencySamples") is not None and summary["networkLatencySamples"] != lag:
                check.update(status="conflict", reason="CSV latency differs from stored session summary.")
        alignment.append(check)
    absent = sorted(set(ROLES)-{row["role"] for row in rows if isinstance(row["role"], str)})
    if absent: warnings.append("Missing asset roles: " + ", ".join(absent))
    partial = absent or any(r["link"]["status"] != "matched" for r in rows) or any(c["status"] != "consistent" for c in alignment)
    return core.result(path, "partial" if partial else "supported", meta,
                       {"stored_events": events, "stored_summary": summary, "assets": rows,
                        "link_counts": dict(Counter(r["link"]["status"] for r in rows)), "alignment_checks": alignment,
                        "missing_roles": absent}, warnings,
                       {"time_basis": "raw device-local timestamps and stored relative time; do not equate them",
                        "full_scale": full_scale, "source_boundary": str(Path(root).resolve())})


def render(path, result, source_root):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    source, boundary, dest = Path(result["source"]).resolve(), Path(source_root).resolve(), Path(path).resolve()
    if not (boundary.is_dir() and source.is_relative_to(boundary) or boundary.is_file() and source == boundary):
        raise core.DataError("Protected source root must contain the input.")
    if dest == boundary or boundary.is_dir() and dest.is_relative_to(boundary) or dest.exists() or dest.suffix.lower() != ".png":
        raise core.DataError("Plot must be a new PNG outside the protected source root.")
    models = [a for a in result["result"].get("assets", []) if a["link"]["status"] == "matched" and a["role"] == "ACOUSTIC_MODEL_FIR"]
    model = models[0] if models else result
    response = model["result"].get("frequency_response")
    if response is None or "frequency_hz" not in response: raise core.DataError("A confirmed FIR with sample rate is required for a frequency-response plot.")
    x = np.array(response["frequency_hz"])
    fir_path = model["link"]["path"] if models else result["source"]
    coefficients, fir_meta, _ = read_vector(fir_path)
    fig, axes = plt.subplots(3, 1, figsize=(11, 10))
    axes[0].plot(x, [np.nan if v is None else v for v in response["magnitude_db"]])
    axes[0].set(xlabel="Frequency (Hz)", ylabel="Model magnitude (dB re 1)", title="Stored FIR response (Reader-computed; not dB SPL)")
    axes[1].plot(x, [np.nan if v is None else v for v in response["phase_degrees_unwrapped"]])
    axes[1].set(xlabel="Frequency (Hz)", ylabel="Unwrapped phase (degrees)")
    # Include the largest coefficient even when subsampling a long impulse for display.
    points = np.unique(np.append(np.linspace(0,len(coefficients)-1,min(4096,len(coefficients)),dtype=int),np.argmax(np.abs(coefficients))))
    axes[2].plot(points/fir_meta["sample_rate_hz"],coefficients[points],linewidth=.7)
    axes[2].set(xlabel="FIR time (s)",ylabel="Stored coefficient",title="Impulse response (display sampled; includes system delays)")
    for ax in axes: ax.grid(True)
    fig.tight_layout()
    try:
        with dest.open("xb") as out: fig.savefig(out, format="png", dpi=120)
    finally:
        plt.close(fig)
    result["result"]["plot"] = str(dest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--root", type=Path, help="Supplied file/folder boundary; required for session JSON")
    parser.add_argument("--full-scale", type=float, help="Explicit CSV full-scale reference; never inferred from PCM_16BIT")
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--fft-size", type=int, help="FIR transform size; must include all coefficients")
    parser.add_argument("--plot", type=Path)
    parser.add_argument("--source-root", type=Path)
    args = parser.parse_args()
    if args.input.suffix.lower() == ".json":
        if args.root is None: raise core.DataError("Session JSON analysis requires --root.")
        result = read_session(args.input, args.root, args.full_scale, args.details, args.fft_size)
    elif args.input.suffix.lower() == ".csv":
        result = analyze_vector(args.input, args.full_scale, args.details, args.fft_size)
    else:
        raise core.DataError("FRP expects a session JSON or a header-identified CSV vector.")
    if args.plot:
        if args.source_root is None: raise core.DataError("Plot requires --source-root.")
        render(args.plot, result, args.source_root)
    return result


if __name__ == "__main__":
    try:
        core.run(main)
    except ModuleNotFoundError as exc:
        core.emit(core.result("runtime", "unsupported", warnings=[str(exc), "Required runtime dependency unavailable; code provision is not automatic analysis."]))
        raise SystemExit(3)

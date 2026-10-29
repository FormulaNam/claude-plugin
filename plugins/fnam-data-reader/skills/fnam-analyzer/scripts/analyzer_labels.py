"""Resolve Analyzer label records to supplied WAVs without filename guessing."""
import argparse
import importlib.util
from pathlib import Path
import sys

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("analyzer_audio", Path(__file__).with_name("analyzer_audio.py"))
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)
core = audio.core


def measurement_context(stored, linked, display_offset):
    """Keep recorded settings, app reanalysis and caller-requested display math separate."""
    warnings = []
    version = stored.get("analysisVersion")
    projection = {"status": "unavailable", "value_db": None, "unit": "dB_app_display",
                  "offset_db": display_offset, "offset_source": "explicit_caller" if display_offset is not None else "unknown",
                  "current_ui_verified": False, "spl_verified": False}
    ctx = {"analysis_version": version, "applies_to_linked_wav": linked,
           "display_projection": projection, "interpretation_status": "supported"}
    if type(version) is not int or version not in (1, 2) or stored.get("layoutVersion") != 2:
        ctx["interpretation_status"] = "unknown" if version is None else "unsupported"
        projection["reason"] = "Unsupported/unknown analysis version or layout; original fields are preserved only."
        return ctx, warnings

    def number(field, positive_integer=False):
        value = stored.get(field)
        if value is None:
            return None
        if not core.finite(value) or (positive_integer and (value <= 0 or value != int(value))):
            warnings.append(f"Invalid {field}; original value retained without numeric interpretation.")
            return None
        return value

    def weighting(field):
        value = stored.get(field)
        if value is None:
            return None
        if not isinstance(value, str) or value not in ("None", "A-weighting", "B-weighting", "C-weighting"):
            warnings.append(f"Unsupported {field}; do not substitute another weighting field.")
            return None
        return value

    window = stored.get("windowType")
    if window is not None and (not isinstance(window, str) or not window.strip()):
        warnings.append("Invalid windowType; original value retained.")
        window = None
    ctx["recording_settings"] = {
        "time_offset_db": number("timeCalibrationOffsetDb"), "fft_offset_db": number("fftCalibrationOffsetDb"),
        "weighting": weighting("weighting"), "window_type": window,
        "fft_size": number("fftSize", True), "sample_rate_hz": number("sampleRateHz", True),
        "recorded_at": stored.get("recordedAt")}
    legacy = stored.get("sourceType") != "analyzer_mic"
    ctx["recording_settings_provenance"] = "unverified_legacy" if legacy else "app_stored"
    if legacy:
        warnings.append("Recording-setting provenance is unverified; reanalysis does not recover unknown historical settings or repair legacy values.")
    aw = weighting("analysisWeighting") if version == 2 else None
    level = number("overallTimeLevelDbfs") if version == 2 else None
    ctx["analysis"] = {"raw_rms_dbfs": number("overallRmsDbfs"), "raw_rms_weighting": "None",
                       "time_level_dbfs": level, "weighting": aw, "time_offset_applied": False,
                       "level_basis": "whole_file_energy_mean_including_silence" if version == 2 else "raw_pcm_rms",
                       "analyzed_at": stored.get("analyzedAt"), "source": "app_stored",
                       "weighted_level_recomputed_by_reader": False}
    if version == 2 and aw is None:
        warnings.append("analysisWeighting is unknown; recording weighting cannot define the stored Time level.")
    if not linked:
        projection["reason"] = "WAV relationship is unconfirmed; do not apply record metadata to the supplied WAV."
    elif version != 2 or aw is None or level is None:
        projection["reason"] = "Requires analysisVersion 2, an explicit analysisWeighting and a finite stored Time level."
    elif display_offset is None:
        projection["reason"] = "Current display Time offset not supplied; recording/FFT offsets are not substitutes."
    elif not core.finite(level + display_offset):
        projection["reason"] = "Display calculation exceeds finite bounds."
    else:
        projection.update(status="conditional", value_db=level + display_offset, weighting=aw,
                          basis="stored_overallTimeLevelDbfs_plus_explicit_offset",
                          reason="Conditional display value for stored analysis weighting; current app settings and physical SPL are unverified.")
    return ctx, warnings


def read_labels(path, root, label=None, display_time_offset_db=None):
    if display_time_offset_db is not None and not core.finite(display_time_offset_db):
        raise core.DataError("Display Time offset must be finite numeric dB; zero is valid, unknown is omitted.")
    doc = core.read_json(path)
    meta = {"schema_version": doc.get("schemaVersion"), "app_version": doc.get("appVersion"),
            "provenance": "app-stored metadata; matching does not establish authenticity"}
    if str(doc.get("schemaVersion")) != "1":
        return core.result(path, "unsupported", meta, {"available_fields": list(doc)},
                           ["Unknown label schema: no label or numeric semantics inferred."])
    if not isinstance(doc.get("files"), dict) or not isinstance(doc.get("labels"), list):
        raise core.DataError("Invalid label schema shape.")
    labels = {}
    for item in doc["labels"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("name"), str):
            raise core.DataError("Invalid label entry.")
        if item["id"] in labels:
            raise core.DataError("Duplicate label ID.")
        labels[item["id"]] = item["name"]
    candidates, records, issues = core.files(root), [], []
    scope = Path(root).resolve()
    info_parent = Path(path).resolve().parent
    # A document in the known Info layout identifies its own pack, including a
    # renamed export. Otherwise only explicit FNamDoc roots establish the namespace.
    named_scope = (info_parent.parent if info_parent.name == "0. Info" else
                   scope if scope.is_dir() and scope.name == "FNamDoc" else
                   scope / "FNamDoc" if (scope / "FNamDoc").is_dir() else None)
    for key, stored in doc["files"].items():
        if not isinstance(stored, dict):
            issues.append(f"Malformed record: {key}")
            continue
        label_id = stored.get("labelId")
        label_name = labels.get(label_id)
        if label is not None and label not in (label_id, label_name):
            continue
        reference = stored.get("relativePath")
        folder_key = stored.get("folderKey")
        active_scope = named_scope if folder_key == "fnamdoc" else scope if scope.is_dir() else info_parent
        scoped = [p for p in candidates if active_scope is not None and p.is_relative_to(active_scope)]
        if not reference:
            link = {"status": "unresolved", "path": None,
                    "reason": "No explicit relativePath; filename/URI alone is not an automatic link."}
        elif active_scope is None or folder_key not in (None, "fnamdoc"):
            link = {"status": "unresolved", "reference": reference, "path": None,
                    "basis": "unconfirmed_namespace", "reason": "No confirmed root for this folderKey."}
        else:
            link = core.resolve_reference(reference, active_scope, scoped)
            if link["status"] == "matched" and folder_key == "fnamdoc":
                link["basis"] = "fnamdoc_namespace_path"
        warnings = []
        if label_id is not None and label_id not in labels:
            warnings.append("Referenced label ID is absent from the label table.")
        if stored.get("layoutVersion") != 2:
            link = {"status": "unsupported", "reason": "Unknown record layout; no automatic binding."}
        if link["status"] == "matched":
            actual, wav_issues = audio.wav_info(link["path"])
            comparisons = {"sizeBytes": "size_bytes", "sampleRateHz": "sample_rate_hz",
                           "channels": "channels", "sampleCount": "sample_count"}
            conflicts = [field for field, measured in comparisons.items()
                         if stored.get(field) is not None and stored[field] != actual.get(measured)]
            if conflicts or wav_issues:
                link.update(status="conflict", mismatched_fields=conflicts, wav_issues=wav_issues)
                warnings.append("Do not apply this label to the WAV until the conflict is resolved.")
        context, context_warnings = measurement_context(stored, link["status"] == "matched", display_time_offset_db)
        warnings.extend(context_warnings)
        records.append({"record_key": key, "source_pointer": "/files/" + key.replace("~", "~0").replace("/", "~1"),
                        "stored_label_id": label_id, "stored_label_name": label_name,
                        "applied_label": label_name if link["status"] == "matched" and label_id in labels else None,
                        "link": link, "stored_metadata": stored, "measurement_context": context, "warnings": warnings})
    counts = {s: sum(r["link"]["status"] == s for r in records)
              for s in sorted({r["link"]["status"] for r in records})}
    return core.result(path, "partial" if issues or any(r["link"]["status"] != "matched" or r["warnings"] for r in records) else "supported",
                       meta, {"records": records, "link_counts": counts, "labels": labels}, issues,
                       {"comparison_policy": "Compare recording device/gain/weighting/calibration and analysis definition; missing remains unknown."})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("info", type=Path)
    parser.add_argument("--root", type=Path, required=True, help="User-provided file or folder boundary")
    parser.add_argument("--label", help="Exact label name or ID")
    parser.add_argument("--display-time-offset-db", type=float,
                        help="Explicit current/display Time offset for conditional stored-Time-level display math; never inferred from recorded offsets")
    a = parser.parse_args()
    return read_labels(a.info, a.root, a.label, a.display_time_offset_db)


if __name__ == "__main__":
    core.run(main)

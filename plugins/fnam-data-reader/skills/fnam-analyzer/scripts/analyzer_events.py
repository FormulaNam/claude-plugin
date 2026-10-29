"""FNam SOUND_EVENT_LOGGER schema 1.0: scoped links and explicit epoch-ms time."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("analyzer_audio", Path(__file__).with_name("analyzer_audio.py"))
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)
core = audio.core


def epoch_ms(value):
    # Confirmed schema uses epoch milliseconds; avoid treating seconds as ms.
    return core.finite(value) and value == int(value) and 946684800000 <= value < 4102444800000


def resolve_clip(asset, document, session_id, root, files):
    """Resolve local references; export paths require the document's own pack anchor."""
    reference = asset.get("assetPath")
    unresolved = {"status": "unresolved", "reference": reference, "path": None}
    if "sessionId" in asset and asset["sessionId"] != session_id:
        return dict(unresolved, status="conflict", reason="Asset session ID mismatch.")
    try:
        parts = core.reference_parts(reference)
    except core.DataError as exc:
        return dict(unresolved, status="invalid_reference", reason=str(exc))
    for i, part in enumerate(parts[:-1]):
        if part == "EventLog" and parts[i+1] != session_id:
            return dict(unresolved, status="conflict", reason="Asset path names a different event session.")
    parent = Path(document).resolve().parent
    if parent.parent.name == "EventLog" and parent.name != session_id:
        return dict(unresolved, status="conflict", reason="Document folder conflicts with session ID.")
    reason = "No document-relative file; candidates elsewhere do not establish a session relationship."
    # A qualified path describes an export layout, not a globally unique identity.
    # Bind its namespace to this JSON's physical pack, never to another suffix hit.
    if len(parts) >= 5 and parts[:4] == ("FNamDoc", "Armory", "EventLog", session_id):
        if parent.name == session_id and parent.parent.name == "EventLog" and parent.parent.parent.name == "Armory":
            pack = parent.parent.parent.parent
            target = (pack / Path(*parts[1:])).resolve()
            if not target.is_relative_to(parent):
                return dict(unresolved, status="conflict", reason="Resolved asset path escapes the document's session folder.")
            unresolved.update(pack_anchor=str(pack), expected_path=str(target))
            if target in files:
                return dict(unresolved, status="matched", path=str(target), basis="document_pack_namespace_path")
            reason = "The referenced clip is absent from the supplied files at this document's pack path."
        else:
            reason = "Export reference requires a document at <pack>/Armory/EventLog/<sessionId>; pack anchor is unconfirmed."
    else:
        direct = (parent / Path(*parts)).resolve()
        if not direct.is_relative_to(parent):
            return dict(unresolved, status="conflict", reason="Resolved relative path escapes the document folder.")
        if direct in files:
            return dict(unresolved, status="matched", path=str(direct), basis="document_relative_path")
    candidates = sorted(str(p) for p in files if tuple(p.parts[-len(parts):]) == tuple(parts))
    return dict(unresolved, status="ambiguous" if len(candidates) > 1 else "unresolved" if candidates else "missing",
                candidates=candidates, basis="unverified_suffix_candidates" if candidates else "no_resolved_path", reason=reason)


def read_events(path, root, bin_seconds=60):
    if not core.finite(bin_seconds) or bin_seconds <= 0:
        raise core.DataError("Bin width must be positive and finite.")
    d = core.read_json(path)
    s = d.get("session", {})
    if not isinstance(s, dict):
        raise core.DataError("Session must be an object.")
    meta = {"schema_version": d.get("schemaVersion"), "app_version": s.get("appVersion"),
            "mode": s.get("mode"), "session_id": s.get("sessionId"), "stored_session": s}
    if d.get("schemaType") != "FNam_3Tier_SessionEventAsset_JSON" or str(d.get("schemaVersion")) != "1.0" or s.get("mode") != "SOUND_EVENT_LOGGER":
        return core.result(path, "unsupported", meta, {"available_fields": list(d)},
                           ["Unknown schema/mode: time, event and SPL semantics not inferred."])
    es, assets = d.get("events"), d.get("assets")
    if not isinstance(es, list) or not isinstance(assets, list):
        raise core.DataError("Events/assets must be arrays.")
    if not all(isinstance(e, dict) and isinstance(e.get("eventId"), str) for e in es):
        raise core.DataError("Malformed event or missing event ID.")
    if not all(isinstance(a, dict) and isinstance(a.get("assetId"), str) for a in assets):
        raise core.DataError("Malformed asset or missing asset ID.")
    ids, aids = [e["eventId"] for e in es], [a["assetId"] for a in assets]
    if len(set(ids)) != len(ids) or len(set(aids)) != len(aids):
        raise core.DataError("Duplicate event/asset ID within session: links are ambiguous.")
    cfg = s.get("configSnapshot") if isinstance(s.get("configSnapshot"), dict) else {}
    start, end = s.get("startTime"), s.get("endTime")
    elapsed = (end-start)/1000 if epoch_ms(start) and epoch_ms(end) and end >= start else None
    files, rows, warnings = core.files(root), [], []
    if s.get("appVersion") != "1.0.0":
        warnings.append("App build is not regression-verified; interpretation is limited to declared schema 1.0 fields.")
    for a in assets:
        if a.get("eventId") not in ids:
            warnings.append(f"Orphan asset: {a['assetId']}")
    event_values, relative_values, supported_count = [], [], 0
    for index, e in enumerate(es):
        problems, links = [], []
        supported = e.get("eventType") == "AUDIO_EVENT" and e.get("eventSubType") == "SPL_THRESHOLD"
        supported_count += int(supported)
        same_session = e.get("sessionId") == s.get("sessionId") and bool(s.get("sessionId"))
        if not same_session:
            problems.append("Event session ID mismatch; relative time and asset binding disabled.")
        timestamp, stored_relative = e.get("timestamp"), e.get("relativeTimeMs")
        relative = (timestamp-start) if supported and same_session and epoch_ms(start) and epoch_ms(timestamp) else None
        if relative is not None and (relative < 0 or (epoch_ms(end) and timestamp > end)):
            problems.append("Event timestamp outside session interval.")
            relative = None
        if relative is not None and stored_relative is not None and stored_relative != relative:
            problems.append("Stored relativeTimeMs disagrees with epoch-ms timestamps; relative analysis disabled.")
            relative = None
        if relative is not None:
            relative_values.append(relative)
        if not supported:
            problems.append("Unsupported event kind; stored fields retained without time/SPL interpretation or clip binding.")
        value = e.get("maxSplDb")
        if supported and same_session and core.finite(value):
            event_values.append((value, e["eventId"]))
        for a in (a for a in assets if a.get("eventId") == e["eventId"]):
            link = resolve_clip(a, path, s.get("sessionId"), root, files) if supported and same_session else {"status": "unresolved", "path": None}
            link.update(asset_id=a["assetId"], event_id=e["eventId"], asset_role=a.get("assetRole"),
                        source_pointer=f"/assets/{assets.index(a)}", wav_start_epoch_ms=None, event_sample_index=None)
            if a.get("assetType") != "AUDIO_WAV" or a.get("assetRole") != "EVENT_CLIP":
                link["status"] = "unsupported"
            if link["status"] == "matched":
                info, issues = audio.wav_info(link["path"])
                for field, measured in (("sampleRate", "sample_rate_hz"), ("channels", "channels")):
                    if cfg.get(field) is not None and cfg[field] != info.get(measured):
                        issues.append(f"WAV conflicts with session {field}.")
                link["wav_metadata"] = info
                if issues:
                    link.update(status="conflict", warnings=issues)
            links.append(link)
        if not links:
            audio_state = "recording_disabled" if cfg.get("wavSaveEnabled") is False else "no_asset_reference"
            if audio_state == "no_asset_reference":
                problems.append("Event has no clip reference; recording availability is unknown/incomplete.")
        else:
            audio_state = "linked" if all(a["status"] == "matched" for a in links) else "unresolved"
        rows.append({"event_id": e["eventId"], "source_pointer": f"/events/{index}", "stored_event": e,
                     "event_time_utc": datetime.fromtimestamp(timestamp/1000, timezone.utc).isoformat() if supported and epoch_ms(timestamp) else None,
                     "verified_relative_ms": relative, "audio_state": audio_state, "assets": links, "warnings": problems})
    summary = d.get("summary") if isinstance(d.get("summary"), dict) else {}
    if s.get("totalCount") != len(es) or summary.get("eventCount") != len(es):
        warnings.append("Stored event counts disagree with actual event array.")
    peak = max((v for v, _ in event_values), default=None)
    if peak is not None and summary.get("maxSplDb") != peak:
        warnings.append("Stored maximum differs from supported-event maxima; unsupported kinds are excluded.")
    ordered = sorted(relative_values)
    bins = Counter(int(t / (1000 * bin_seconds)) for t in relative_values)
    if len(relative_values) != len(es):
        warnings.append("SPL time statistics include only supported event kinds with verified time relationships.")
    warnings.append("WAV start and event sample offset are unknown; pre/post settings do not prove sample alignment.")
    issues = any(r["warnings"] or r["audio_state"] == "unresolved" for r in rows)
    data = {"events": rows, "actual_duration_seconds": elapsed, "event_count": len(es),
            "supported_event_count": supported_count, "unsupported_event_count": len(es)-supported_count,
            "event_rate_per_hour": supported_count*3600/elapsed if elapsed and len(relative_values) == supported_count and (supported_count or not es) else None,
            "maximum_stored_event_spl_db": peak, "stored_summary": summary,
            "ranked_events": [{"event_id": i, "stored_max_spl_db": v} for v, i in sorted(event_values, reverse=True)],
            "intervals_seconds": [(b-a)/1000 for a,b in zip(ordered, ordered[1:])],
            "bins": [{"start_seconds": i*bin_seconds, "count": count} for i,count in sorted(bins.items())],
            "comparison_conditions": {k: cfg.get(k) for k in ("thresholdDbSpl", "weighting", "minimumEventDurationMs", "cooldownMs", "sampleRate", "channels")}}
    return core.result(path, "partial" if issues or len(warnings) > 1 else "supported", meta, data, warnings,
                       {"bin_seconds": bin_seconds, "time_basis": "schema 1.0 epoch milliseconds; UTC display",
                        "statistics_scope": "AUDIO_EVENT/SPL_THRESHOLD only; rate requires verified times for all supported events",
                        "empty_event_maximum": "unknown, never 0 dB SPL", "clip_time_basis": "unknown"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("info", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bin-seconds", type=float, default=60)
    a = parser.parse_args()
    return read_events(a.info, a.root, a.bin_seconds)


if __name__ == "__main__":
    core.run(main)

"""Bounded, read-only discovery and explicit file-reference resolution. Python 3.10+."""
import argparse
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import zipfile

MAX_FILE = 256 * 1024**2
MAX_JSON = 8 * 1024**2
MAX_TOTAL = 512 * 1024**2
MAX_ENTRIES = 5000


class DataError(ValueError):
    pass


def result(source, status="supported", metadata=None, data=None, warnings=None, parameters=None):
    return {"source": str(source), "status": status, "metadata": metadata or {},
            "result": data or {}, "analysis_parameters": parameters or {}, "warnings": warnings or []}


def emit(value):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2))


def run(main):
    try:
        emit(main())
    except (DataError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        emit(result("input", "error", warnings=[str(exc)]))
        raise SystemExit(2)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def checked_file(path, limit=MAX_FILE):
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise DataError(f"Not a regular input file: {p}")
    if p.stat().st_size > limit:
        raise DataError(f"File exceeds {limit} bytes: {p}; provide a smaller input/segment.")
    return p


def read_json(path):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise DataError(f"Duplicate JSON key: {key}")
            out[key] = value
        return out
    def invalid(value):
        raise DataError(f"Non-finite JSON number: {value}")
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            invalid(value)
        return parsed
    with checked_file(path, MAX_JSON).open(encoding="utf-8-sig") as f:
        data = json.load(f, object_pairs_hook=pairs, parse_constant=invalid, parse_float=number)
    if not isinstance(data, dict):
        raise DataError("Expected a JSON object.")
    return data


def files(root):
    p = Path(root)
    if p.is_file():
        return [checked_file(p).resolve()]
    if not p.is_dir() or p.is_symlink():
        raise DataError(f"Not an input directory: {p}")
    found, directory_count = [], 0
    boundary = p.resolve()
    for directory, dirs, names in os.walk(p, followlinks=False):
        directory_count += 1
        if directory_count > MAX_ENTRIES:
            raise DataError("Input directory count exceeds limit.")
        dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink()
                         and (Path(directory) / d).resolve().is_relative_to(boundary))
        for name in sorted(names):
            item = Path(directory) / name
            if item.is_symlink():
                continue
            resolved = item.resolve()
            if not resolved.is_relative_to(boundary):
                raise DataError("Input path escapes the provided root.")
            found.append(resolved)
            if len(found) > MAX_ENTRIES:
                raise DataError(f"Input exceeds {MAX_ENTRIES} files; select a smaller folder.")
    return found


def reference_parts(reference):
    if not isinstance(reference, str) or not reference:
        raise DataError("Missing explicit file reference.")
    name = reference.replace("\\", "/")
    parts = PurePosixPath(name).parts
    if name.startswith("/") or any(p in ("..", ".") or ":" in p for p in parts):
        raise DataError(f"Unsafe or nonportable reference: {reference}")
    if not parts:
        raise DataError("Empty reference.")
    return parts


def resolve_reference(reference, root, candidates=None):
    """Match exact root-relative paths; suffix hits are unverified candidates only."""
    try:
        parts = reference_parts(reference)
    except DataError as exc:
        return {"status": "invalid_reference", "reference": reference, "reason": str(exc)}
    base = Path(root).resolve()
    candidates = files(base) if candidates is None else candidates
    direct = (base / Path(*parts)).resolve() if base.is_dir() else None
    matches = [p for p in candidates if direct is not None and p == direct]
    unique = sorted({str(p) for p in matches})
    if unique:
        return {"status": "matched" if len(unique) == 1 else "ambiguous",
                "reference": reference, "path": unique[0] if len(unique) == 1 else None,
                "candidates": unique, "basis": "root_relative_path"}
    hints = sorted({str(p) for p in candidates if tuple(p.parts[-len(parts):]) == parts})
    return {"status": "ambiguous" if len(hints) > 1 else "unresolved" if hints else "missing",
            "reference": reference, "path": None, "candidates": hints,
            "basis": "unverified_suffix_candidates" if hints else "no_resolved_path"}


def zip_members(path):
    checked_file(path)
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            raise DataError("ZIP entry count exceeds limit.")
        seen, total = set(), 0
        for info in entries:
            parts = reference_parts(info.filename)
            # Reject Windows aliases/ADS even when unpacking on another OS.
            reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10)}
            if any(p.endswith((".", " ")) or p.split(".")[0].upper() in reserved for p in parts):
                raise DataError("Unsafe ZIP path alias.")
            key = "/".join(parts).casefold()
            if key in seen or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                raise DataError("Duplicate, linked or encrypted ZIP entry.")
            seen.add(key)
            total += info.file_size
            if info.file_size > MAX_FILE or total > MAX_TOTAL:
                raise DataError("ZIP expanded size exceeds limit.")
            if info.file_size > max(1, info.compress_size) * 200:
                raise DataError("ZIP compression ratio exceeds 200:1.")
        return entries


def unpack(path, destination):
    entries = zip_members(path)
    target = Path(destination)
    if target.exists():
        raise DataError("Extraction destination must be a new directory; no overwrite.")
    target.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(path) as archive:
        for info in entries:
            output = target.joinpath(*reference_parts(info.filename))
            if not output.resolve().is_relative_to(target.resolve()):
                raise DataError("ZIP path escapes destination.")
            if info.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.open(info) as src, output.open("xb") as dst:
                while chunk := src.read(1024 * 1024):
                    written += len(chunk)
                    if written > info.file_size or written > MAX_FILE:
                        raise DataError("ZIP expansion exceeded declared size.")
                    dst.write(chunk)
    return target


def identify(path):
    p = checked_file(path)
    kind, version, mode, app_version = p.suffix.lower().lstrip("."), None, None, None
    if kind == "json":
        d = read_json(p)
        version = d.get("schemaVersion")
        session = d.get("session") if isinstance(d.get("session"), dict) else d
        mode, app_version = session.get("mode"), session.get("appVersion")
        if isinstance(d.get("files"), dict) and isinstance(d.get("labels"), list):
            kind = "analyzer_labels" if str(version) == "1" else "unknown_labels_version"
        elif mode == "SOUND_EVENT_LOGGER":
            kind = "sound_event_logger" if str(version) == "1.0" else "unknown_event_version"
        elif mode == "FREQ_RESP_PREVIEW":
            kind = "frequency_response_preview" if str(version) == "1.0" else "unknown_frp_version"
        else:
            kind = "unsupported_json"
    elif kind == "csv":
        # Header markers identify vectors without parsing an unbounded first data row.
        with p.open(encoding="utf-8-sig") as stream:
            for _ in range(256):
                line = stream.readline(8193)
                if not line or not line.lstrip().startswith("#") or len(line) > 8192:
                    break
                if line.strip()[1:].strip() in ("AcousticModel_fir", "rec_signal", "low_freq_response_preview_reference_signal") or line.startswith("# rearrangement_role:"):
                    kind = "frequency_response_preview_csv"
                    mode = "FREQ_RESP_PREVIEW"
                    break
    return {"path": str(p.resolve()), "kind": kind, "schema_version": version,
            "app_version": app_version, "mode": mode, "size_bytes": p.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--extract-to", type=Path)
    args = parser.parse_args()
    root = args.input
    if root.suffix.lower() == ".zip" and root.is_file():
        entries = zip_members(root)
        if not args.extract_to:
            return result(root, data={"members": [{"name": i.filename, "size_bytes": i.file_size} for i in entries],
                                      "next_step": "Use --extract-to with a new output directory to analyze members."})
        root = unpack(root, args.extract_to)
    rows = []
    for p in files(root):
        try:
            rows.append(identify(p))
        except (ValueError, OSError) as exc:
            rows.append({"path": str(p), "status": "error", "reason": str(exc)})
    return result(args.input, data={"root": str(root.resolve()), "files": rows})


if __name__ == "__main__":
    run(main)

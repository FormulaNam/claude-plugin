# FNam Data Reader procedures

These procedures define how FormulaNam's reference implementation reads, links and
calculates each supported format. Implement them in generated code as written. Where a
step says "error", stop that input and report the reason; where it says "status", record
that value and continue. Formulas use NumPy names (`rfft`, `rfftfreq`, `unwrap`, `angle`).

## 1. Shared rules

### 1.1 Result shape

For every analyzed input keep: `source` (path), `status` (`supported`, `partial`,
`unsupported` or `error`), `metadata`, `result`, `analysis_parameters` and `warnings`.
Use `partial` when a needed definition (rate, scale, link) is missing or a link is not
`matched`. Non-finite numbers are null with an explicit state.

### 1.2 Regular files and inventory

- An input file must be a regular file, not a symbolic link, and within its size limit
  (256 MiB general, 8 MiB JSON, 64 MiB CSV). Otherwise error.
- A folder inventory walks the folder without following links, skips linked entries,
  rejects any entry whose resolved path leaves the folder, and stops with an error above
  5,000 files or 5,000 directories.

### 1.3 JSON reading

Read as UTF-8 (a leading BOM is allowed). The root must be an object. Duplicate keys in
any object, and `NaN`/`Infinity` or numbers that are not finite, are errors.

### 1.4 Reference parts

A file reference must be a non-empty string. Replace `\` with `/` and split into parts.
It is invalid if it starts with `/`, or if any part is `.` or `..`, or contains `:`.

### 1.5 Reference resolution (common)

Given a reference and a root folder:

1. Join the root and the reference parts. If exactly that path is in the inventory:
   status `matched`, `basis=root_relative_path`.
2. Otherwise collect inventory files whose last N path parts equal the N reference parts
   ("suffix candidates"). One candidate: status `unresolved`; several: `ambiguous`;
   none: `missing`. Record the candidates with `basis=unverified_suffix_candidates`
   (or `no_resolved_path` when none). Never select a candidate path.

An invalid reference gives status `invalid_reference`.

### 1.6 ZIP handling

Before extracting, check every entry; any failure is an error for the whole archive:

- at most 5,000 entries; every name passes the reference-part rules (1.4);
- no part ending in `.` or space, and no Windows device name (`CON`, `PRN`, `AUX`,
  `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`, compared before the first dot, case-insensitive);
- no two names equal after case-folding; no symbolic-link entries; no encrypted entries;
- each declared size at most 256 MiB, total declared size at most 512 MiB, and declared
  size at most 200 times the compressed size.

Extract into a new directory that must not exist yet. Each output path must stay inside
it, files are created without overwriting, and extraction stops with an error if the
bytes written exceed the declared size or 256 MiB.

### 1.7 Format identification

- `.json`: read per 1.3. Let `session` be the `session` object if present, otherwise the
  root. If the root has an object `files` and a list `labels`: labels (supported only
  when `schemaVersion` is `1`). Else if `session.mode` is `SOUND_EVENT_LOGGER`: event log
  (supported only for `schemaVersion` `1.0`). Else if `session.mode` is
  `FREQ_RESP_PREVIEW`: FRP session (supported only for `1.0`). Else unsupported JSON.
- `.csv`: read at most the first 256 lines that start with `#` (each at most 8,192
  characters). If one of them, after removing `#` and spaces, is exactly
  `AcousticModel_fir`, `rec_signal` or `low_freq_response_preview_reference_signal`, or
  a line starts with `# rearrangement_role:`, it is an FRP vector CSV. Otherwise treat it
  as a candidate PCM CSV.
- `.wav`: candidate PCM WAV.

Never infer a format or role from the filename.

### 1.8 Protected boundary and plots

The protected boundary is the supplied measurement folder, the ZIP extraction root, or
the lone input file. It must contain the analyzed input. A plot must be a new `.png`
file that does not exist yet and is not inside the boundary (or equal to the lone file).

## 2. Audio procedure (WAV and PCM CSV)

### 2.1 WAV header

Read the first 12 bytes: they must be `RIFF`, a size, `WAVE`; otherwise
"Unsupported or damaged RIFF/WAVE header". Record as issues: declared RIFF size + 8
differing from the file size; a chunk whose size (plus one padding byte when odd)
passes the end of the file; a duplicate `fmt ` chunk; an `fmt ` chunk shorter than 16
bytes; more than one `data` chunk; a chunk walk that does not end exactly at the end of
the file; a missing `fmt ` or `data` chunk.

From `fmt `: encoding, channels, sample rate, byte rate, block align, bits per sample
(little-endian). The Reader supports encoding 1 (PCM), 16 bits, 1 or 2 channels only
(status `unsupported` otherwise). Block align must equal channels x 2, the rate must be
positive and byte rate must equal rate x block align. Frame count = available data bytes
// block align (available = declared data size, cut at the end of the file); a remainder
is a "partial PCM frame" issue. Duration = frames / rate. Any issue makes the WAV
`error` (or `unsupported` for the format restriction) with no calculation.

### 2.2 Selecting samples

Inputs: start seconds (>= 0, default 0), optional duration seconds (> 0), FFT size N
(integer 16 to 65536, default 2048). First frame = floor(start x rate); frame count =
remaining frames, limited to floor(duration x rate) when a duration is given. If frames
x channels exceeds 8,000,000, error and ask for a shorter interval. Decode little-endian
int16 and divide by 32768 (full scale). Channels stay separate; never mix them.

### 2.3 PCM CSV

Read as UTF-8 (BOM allowed). Skip blank lines. A line longer than 1 MiB is an error
(FRP vectors use section 5). Lines starting with `#` are comments; a comment matching
`# sample_rate_hz: v`, `# sample_rate: v` or `# full_scale: v` (`:` or `=`) defines rate
or full scale, and two different values for one key are an error. Every other line must
be exactly one CSV field holding a number; more than one field is an error, and more
than 8,000,000 samples is an error. An explicit rate or full scale supplied with
provenance must agree with an embedded one. The rate must be a positive integer; the
full scale must be positive and finite. All samples must be finite and the vector must
be non-empty. A time interval needs a rate. Without a full scale, values stay in CSV
units and no dBFS is computed.

### 2.4 Calculations per channel

With `y` = selected samples divided by full scale (or raw CSV values without one):

- `rms = sqrt(mean(y**2))`; `rms_dbfs = 20*log10(rms)` only with a full scale and
  rms > 0 (`silence_negative_infinity` when rms = 0, `unknown_scale` without scale).
- minimum, maximum, mean (`dc_mean`).
- WAV only: `pcm_rail_samples` = count of raw values equal to -32768 or 32767.
- Values above 1e100 in magnitude are an error.

FFT (only with a rate and at least 16 samples): n = min(N, selected frames); periodic
Hann `w[k] = 0.5 - 0.5*cos(2*pi*k/n)`, k = 0..n-1; on the first n samples only:
`amp = abs(rfft(y[:n]*w)) / sum(w)`, then multiply bins 1..end by 2, excluding the last
bin when n is even (DC and even-n Nyquist are not doubled). Frequencies `rfftfreq(n, 1/rate)`.
Warn when n is smaller than the selected frames.

Peaks: candidate bins i >= 1 with `amp[i] > 1e-12`, `amp[i] > amp[i-1]` and (i is the
last bin or `amp[i] >= amp[i+1]`). Take candidates from largest amplitude down, keeping
one only if it is at least 3 bins from every kept peak, up to 5 peaks. Report bin-center
frequency and peak amplitude.

Spectrogram: same n and w, hop = floor(n/2), window starts 0, hop, ... while start + n
<= frames. If there are more starts than `cap = max(1, min(512, floor(2000000 /
(n//2 + 1))))`, keep `cap` starts chosen at evenly spaced indices (rounded down) from
first to last, and warn that display windows were subsampled. Each column:
`abs(rfft(y[s:s+n]*w))**2 / (rate*sum(w**2))`, interior bins doubled as above. Column time
= (first frame + s + n/2) / rate, relative to the file start. Display `10*log10` with a
-160 dB floor (display only).

Record in `analysis_parameters`: start sample, analyzed frames, full scale, RMS
definition, requested and used FFT size, FFT region ("first FFT-size frames of the
selected interval"), window, normalization, spectrum unit (peak amplitude / full scale,
or CSV units), separate channels, no detrend, no zero padding, spectrogram hop and
rendered/total windows. Always warn that RMS dBFS is digital level, not dB SPL, and
that rail samples do not prove recording-chain clipping.

Plot (optional): per channel, waveform (display-sampled to at most 4,000 points), the
first-window FFT and the spectrogram, saved per 1.8.

## 3. Label procedure (`data labels.json`)

### 3.1 Document

Read per 1.3. If `schemaVersion` is not `1`: status `unsupported`, list the available
fields only. `files` must be an object and `labels` a list, else error. Each label must
be an object with string `id` and `name`; a duplicate ID is an error. Keep the label
table `id -> name`.

### 3.2 Namespace root

Let the boundary be the supplied folder (or the lone file). The FNamDoc namespace root
is, in order: the parent of the JSON's folder when that folder is named `0. Info`
(including a renamed exported pack); else the boundary itself when it is a folder named
`FNamDoc`; else a child folder `FNamDoc` of the boundary; else none.

### 3.3 Each record in `files`

For each key and record (a non-object record is reported as malformed):

1. `label_id = labelId`; `label_name = labels[label_id]` if present. A `labelId` absent
   from the label table is a warning. When filtering, keep only records whose ID or name
   equals the filter.
2. Active scope: the namespace root when `folderKey` is `fnamdoc`; otherwise the boundary
   folder, or the JSON's own folder when the boundary is a single file. Only inventory
   files inside the active scope are candidates.
3. Link: no `relativePath` gives `unresolved` ("no explicit relativePath"). A `folderKey`
   other than none or `fnamdoc`, or `fnamdoc` without a namespace root, gives
   `unresolved` with `basis=unconfirmed_namespace`. Otherwise resolve per 1.5 in the
   active scope; a match through `fnamdoc` has `basis=fnamdoc_namespace_path`.
4. If `layoutVersion` is not 2: link status `unsupported`, no binding.
5. For a `matched` link, read the WAV header (2.1). If any of `sizeBytes`,
   `sampleRateHz`, `channels`, `sampleCount` is stored and differs from the WAV (file
   size, rate, channels, frames), or the header has issues: status `conflict`, list the
   mismatched fields, and do not apply the label.
6. `applied_label = label_name` only when the link is `matched` and the ID is in the
   table; otherwise null. Never apply `stored_label_name` alone.

### 3.4 Measurement context

`analysisVersion` must be the integer 1 or 2 and `layoutVersion` 2; otherwise
interpretation is `unknown` (version missing) or `unsupported`, with original fields
only. Numeric fields must be finite (`fftSize` and `sampleRateHz` positive integers);
invalid values are retained but not interpreted. Weighting fields accept only `None`,
`A-weighting`, `B-weighting`, `C-weighting`; never substitute one weighting field for
the other.

- Recording settings: `timeCalibrationOffsetDb`, `fftCalibrationOffsetDb`, `weighting`,
  `windowType`, `fftSize`, `sampleRateHz`, `recordedAt`. Provenance is `app_stored` when
  `sourceType` is `analyzer_mic`, else `unverified_legacy` (warn that reanalysis does
  not recover historical settings).
- Analysis: raw RMS = `overallRmsDbfs` (weighting None); for version 2 only, Time level
  = `overallTimeLevelDbfs` with `analysisWeighting` (whole-file energy mean including
  silence, no Time offset applied); `analyzedAt`. Warn when version 2 lacks
  `analysisWeighting`.
- Display projection (`unit=dB_app_display`): available only when the link is
  `matched`, version is 2, `analysisWeighting` is known, the stored Time level is
  finite and the user supplied an explicit current/scenario Time offset (zero is valid).
  Value = stored `overallTimeLevelDbfs` + that offset, status `conditional`. Record
  that current app settings and physical SPL are unverified. Never use recording or
  FFT offsets instead.

## 4. Event procedure (`EventLog_<sessionId>.json`)

### 4.1 Document

Read per 1.3. Supported only when `schemaType` is `FNam_3Tier_SessionEventAsset_JSON`,
`schemaVersion` is `1.0` and `session.mode` is `SOUND_EVENT_LOGGER`; otherwise status
`unsupported` with fields only. `events` and `assets` must be lists of objects with
string `eventId` / `assetId`; duplicate IDs within the document are an error. Warn when
`session.appVersion` is not `1.0.0` (interpretation limited to schema 1.0 fields).

A value is epoch milliseconds only if it is a finite whole number from 946684800000
(2000-01-01) up to but not including 4102444800000 (2100-01-01). Session duration =
(`endTime` - `startTime`) / 1000 seconds when both are epoch ms and end >= start.

Warn for an asset whose `eventId` matches no event (orphan).

### 4.2 Each event

- Supported kind: `eventType` = `AUDIO_EVENT` and `eventSubType` = `SPL_THRESHOLD`.
  Other kinds keep their fields with no time, SPL or clip interpretation.
- Same session: event `sessionId` equals the non-empty session ID; otherwise warn and
  disable relative time and clip binding.
- Relative time (supported, same session, start and `timestamp` both epoch ms):
  `timestamp - startTime`. Discard it with a warning if negative, after `endTime`, or
  different from a stored `relativeTimeMs`.
- `maxSplDb` of supported same-session events with a finite value is an app-stored SPL
  for ranking.
- UTC time of a supported event = `timestamp` as UTC; never assign another timezone.

### 4.3 Clip resolution for each asset of the event

Only for supported, same-session events; others stay `unresolved`.

1. An asset `sessionId` different from the session: `conflict`.
2. `assetPath` must pass 1.4, else `invalid_reference`.
3. If any path part `EventLog` is followed by a part other than the session ID:
   `conflict`. If the JSON's folder is inside a folder named `EventLog` but is not named
   after the session ID: `conflict`.
4. Export path: when the parts are `FNamDoc/Armory/EventLog/<sessionId>/...` (at least
   five parts), the JSON must sit at `<pack>/Armory/EventLog/<sessionId>/`. Then the
   target is `<pack>` joined with the parts after `FNamDoc`; it must stay inside the
   JSON's folder (else `conflict`). Record `pack_anchor` and `expected_path`. If the
   target exists in the inventory: `matched`, `basis=document_pack_namespace_path`. If
   the JSON is elsewhere, the pack anchor is unconfirmed.
5. Other paths resolve relative to the JSON's folder and must stay inside it (else
   `conflict`); an existing file is `matched`, `basis=document_relative_path`.
6. Otherwise collect suffix candidates as in 1.5 (`unresolved`, `ambiguous` or
   `missing`), never selecting one.
7. `assetType` must be `AUDIO_WAV` and `assetRole` `EVENT_CLIP`, else `unsupported`.
8. For `matched`, read the WAV header; a stored session `sampleRate` or `channels` that
   differs from the WAV makes the link `conflict`.

Record for each link: asset ID, event ID, role, JSON pointer, `wav_start_epoch_ms=null`
and `event_sample_index=null` (both unknown in this profile).

Audio state per event: `linked` when all its assets are matched, `unresolved` when any
is not, `recording_disabled` when it has no assets and `configSnapshot.wavSaveEnabled`
is false, otherwise `no_asset_reference` (warn).

### 4.4 Summary

- `event_count` = all events; `supported_event_count` and `unsupported_event_count`.
- Warn when `session.totalCount` or `summary.eventCount` differs from the event count,
  and when `summary.maxSplDb` differs from the maximum supported-event `maxSplDb`.
- Ranking: supported-event `maxSplDb`, highest first. Zero events: maximum unknown,
  never 0 dB SPL.
- Intervals: differences between consecutive sorted verified relative times, seconds.
- Bins: count of verified relative times per `floor(t / (1000 x bin_seconds))`.
- Event rate per hour = supported count x 3600 / session duration, only when the
  duration is known and every supported event has a verified relative time.
- Comparison conditions from `configSnapshot`: `thresholdDbSpl`, `weighting`,
  `minimumEventDurationMs`, `cooldownMs`, `sampleRate`, `channels`.
- Always warn that WAV start and event sample offset are unknown.

## 5. FRP procedure

### 5.1 Vector CSV

Read as UTF-8 (BOM allowed), at most 64 MiB. Skip blank lines. Comment lines (`#`) must
come before the single numeric row; more than 256 comments, a comment longer than 8,192
characters or a comment after the row is an error. Keep each comment text (after `#`,
trimmed). A comment with `:` defines a header `key: value` (key lower-cased); a
different value for the same key is an error. Exactly one non-comment row is allowed:
split on commas, at most 1,000,000 values, all finite, none above 1e100 in magnitude.

Role from comment markers (never from the filename), with `rearrangement_role` header R:

| Condition | Role |
|---|---|
| marker `AcousticModel_fir` and no R | `ACOUSTIC_MODEL_FIR` |
| R = `SHIFTED (ANALYZER RECORDING)` | `REC_SIGNAL_REARRANGED` |
| R = `REFERENCE (NOISE GENERATOR SIGNAL)` and marker `low_freq_response_preview_reference_signal` | `NOISE_SIGNAL_REARRANGED` |
| marker `rec_signal` and no R | `REC_SIGNAL` |
| marker `low_freq_response_preview_reference_signal` and no R | `NOISE_SIGNAL` |
| otherwise | error: unknown FRP CSV profile |

Headers used: `sample_rate` (positive integer), `fir_taps` (FIR) or `total_samples`
(signals), `full_scale`, `band_masking_f0_hz`/`band_masking_f1_hz` and
`band_mask_f0_hz`/`band_mask_f1_hz`, `network_latency_samples`, `session_id`,
`signal_type`. Invalid numbers are errors. Trim notes are comments exactly
`Trimmed to <n> samples to match aligned rec length.`; all such n must agree and equal
the vector length. A declared count differing from the vector length is an error,
except for `NOISE_SIGNAL_REARRANGED` with a trim note and declared >= length (warn that
`total_samples` is inherited from the original reference).

### 5.2 Signal summary (non-FIR roles)

Full scale: header `full_scale`, or an explicit value with provenance (they must agree);
it must be positive. RMS over the whole vector including silence; minimum, maximum,
mean; duration = count / rate; dBFS only with a full scale. With a rate and at least
16 values: FFT of the first min(2048, count) values with periodic Hann, normalized and
doubled as in 2.4 (in CSV units). Never infer scale from `PCM_16BIT`.

### 5.3 FIR response

FFT size = smallest power of two >= tap count unless an explicit size is given; it must
be >= taps and <= 1,048,576. `H = rfft(h, n=fft_size)` over all coefficients: no window,
no division, no doubling. Frequencies `rfftfreq(fft_size, 1/rate)`. Magnitude `abs(H)`;
dB `20*log10(abs(H))` (null when zero); phase `unwrap(angle(H))` in degrees, null where
magnitude <= 1e-12. Default output keeps at most 4,096 evenly spaced bins (unique
indices); full output keeps all.

Band: `band_masking_f0_hz/f1_hz`, falling back to `band_mask_f0_hz/f1_hz`; if both pairs
are present they must agree (else error). Both ends or neither must be present, with
f0 < f1 and f1 <= rate/2 (else error). The maximum response is searched inside the band
(whole range without a band). Peak coefficient index = argmax `abs(h)`; time = index /
rate. Record FFT size, zero padding, bin spacing rate/fft_size, reported and total bins,
and that the stored FIR is transformed, not refitted.

Plot (optional): magnitude in dB re 1, unwrapped phase, and the impulse display-sampled
to at most 4,096 points plus the largest coefficient, saved per 1.8.

### 5.4 Session JSON

Supported only when `schemaType` is `FNam_3Tier_SessionEventAsset_JSON`, `schemaVersion`
is `1.0` and `session.mode` is `FREQ_RESP_PREVIEW`; otherwise `unsupported`. Require a
non-empty string `session.sessionId`, lists `events` and `assets` with non-empty string
`eventId`/`assetId`, no duplicate IDs, and object `configSnapshot`/`summary` when
present. Asset role to type: `REC_SIGNAL`, `NOISE_SIGNAL`, `REC_SIGNAL_REARRANGED`,
`NOISE_SIGNAL_REARRANGED` are `AUDIO_CSV`; `ACOUSTIC_MODEL_FIR` is `MODEL_CSV`.

Asset resolution, for each asset:

1. An asset `sessionId` different from the session: `conflict`. `assetPath` must pass 1.4.
2. If the JSON's folder is inside `FreqRespPre` but not named after the session: `conflict`.
3. Export path `FNamDoc/Armory/FreqRespPre/...`: it needs at least five parts and part 4
   equal to the session ID (else `conflict`); the JSON must sit at
   `<pack>/Armory/FreqRespPre/<sessionId>/` (else `unresolved`). Target = `<pack>`
   joined with the parts after `FNamDoc`, `basis=document_pack_namespace_path`.
4. Other paths: target relative to the JSON's folder, `basis=document_relative_path`.
5. The target must stay inside the JSON's folder (else `conflict`). Record
   `expected_path`. An existing target is `matched`; otherwise suffix candidates as in
   1.5, never selected.
6. An unknown role or wrong type: `unsupported`. A role appearing more than once, an
   asset whose event is missing, a `rowKey` different from its `eventId`, an event in
   another session, or an event that is not `SESSION_CONTROL` with subtype
   `MEASUREMENT_START` or `MEASUREMENT_STOP`: `conflict`.

For each matched asset, read the CSV (5.1) and mark the link `conflict` if: the header
role differs from `assetRole`; `configSnapshot.channels` is set and not 1; a stored
`sampleRate` differs from the CSV rate; for `REC_SIGNAL`, `REC_SIGNAL_REARRANGED` and
`ACOUSTIC_MODEL_FIR` the CSV `session_id` differs from the session; for the FIR a stored
`firTaps` (config or summary) differs from the tap count; a stored `signalType` differs
from the CSV `signal_type`. Generator `session_id`, `local_folder` and `file_name` in
noise CSVs belong to the generator device and are not conflicts. Then summarize per 5.2
or 5.3.

Alignment checks, when both members of a pair are matched, using L =
`network_latency_samples` from the rearranged CSV:

- `REC_SIGNAL` vs `REC_SIGNAL_REARRANGED`: expected = raw[L:].
- `NOISE_SIGNAL` vs `NOISE_SIGNAL_REARRANGED`: expected = raw[:len(raw) - L].
- `consistent` when the rates match, lengths match and every value agrees within an
  absolute tolerance of 0.00000051 CSV units; otherwise `conflict`. A stored
  `summary.networkLatencySamples` different from L is also `conflict`. Without L the
  check is `unverified`.

List missing roles. The session is `partial` if a role is missing, any link is not
matched, or any alignment check is not consistent. Always warn that wall-clock strings,
`relativeTimeMs` and vector durations are separate clocks, and that `measurementValid`
is an app-stored flag, not a certification.

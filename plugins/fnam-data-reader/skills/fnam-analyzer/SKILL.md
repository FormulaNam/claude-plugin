---
name: fnam-analyzer
description: Analyze attached WAV or measurement data from FNam Analyzer, including PCM CSV, Info and labels, Sound Event Logger, and Frequency Response Preview sessions and signal/FIR CSVs. Use for RMS, FFT, spectrograms, event timing, measurement comparisons, FIR frequency response, or finding related metadata.
---

# FNam Data Reader: Analyzer

Before analyzing, read [Core rules](../fnam-core/SKILL.md). They define execution
capability, bounded input handling, provenance, linking and output semantics.
Resolve paths from the installed package, not from a developer's workspace.
Scripts are in this skill's `scripts/` directory and load the bundled Core helper.

## Attached data entry

Inventory the supplied files first. Use `analyzer_audio.py` for WAV/single-column
PCM CSV, `analyzer_labels.py` for Info/labels, `analyzer_events.py` for Sound Event
Logger and `analyzer_frp.py` for Frequency Response Preview JSON or header-identified
horizontal signal/FIR CSV vectors. A standalone WAV remains a supported entry.

### Standalone WAV

Use `python -B <this-skill>/scripts/analyzer_audio.py <wav>`.
For a plot add `--plot <new-output.png> --source-root <provided-measurement-root>`.
Use the supplied session/Data Pack folder as the protected root (the extraction
root for ZIPs). If only a single WAV/CSV was supplied, pass that file itself;
do not assume its parent directory is an entire measurement pack. The helper
requires an existing root containing the input and rejects output inside it.
Never narrow a known folder boundary to a single file merely to bypass protection.
For a chosen region use
`--start <seconds> --duration <seconds>`; `--nfft` defaults to 2048.
`--details` includes numerical spectrum arrays. Channel results stay separate.
Choose the region relevant to the request and report it; the default FFT covers
only the first FFT-size frames of that selected region, not the entire recording.
RMS covers the entire selected region. Spectrograms span that region using up to
512 selected windows (a lower cap for large FFTs); report any display subsampling.
Do not claim a short-window FFT characterizes every sound in a long recording.
For a whole-recording representative-spectrum request, explain this implementation limit and
offer explicit regional comparisons or clearly labeled unexecuted aggregation code.
Do not present an uncomputed whole-recording PSD as an available helper result.

Header facts: sample rate, channels, PCM format, frame count and duration.
Unknown unless supplied: origin app, recording vs generated signal, microphone,
input gain, label, location, calibration, weighting, actual recording timestamp.
Do not ask the app of origin for a simple FFT request. Ask for missing context
only when it changes the requested interpretation (e.g. physical SPL or machine diagnosis).
A user's statement of origin is user-provided evidence, not embedded metadata.

### Optional Info guidance

If origin is unknown, say "If this came from FNam Analyzer…" before giving these paths.
Android may display `Download` as Downloads or 다운로드. These are observed layouts,
not proof that every app/version uses them.

| Need | Additional file | Known Android location |
|---|---|---|
| Label and stored measurement conditions | `data labels.json` | `Download/FNamDoc/0. Info/data labels.json` |
| Event time, threshold and recorded maximum SPL | `EventLog_<sessionId>.json` | `Download/FNamDoc/Armory/EventLog/<sessionId>/` |
| FRP settings, reference/recorded signals, alignment and FIR | `session_<sessionId>.json` and five CSV assets | `Download/FNamDoc/Armory/FreqRespPre/<sessionId>/` |

Explain what already works, what the additional file enables, and its location.
If the file is already accessible within the provided boundary, locate it instead
of asking again. Info is optional for waveform/FFT analysis. Do not promise it has
the requested WAV record or every needed field until it is read and linked.
If metadata is absent, continue the supported analysis. Never infer calibration
from the fact that the app was Analyzer, or SPL from a filename.

## Label connection and comparisons

Run `analyzer_labels.py <data-labels.json> --root <provided-file-or-folder>`;
optionally add `--label <exact-name-or-id>`.
Supported profile: labels schema 1, record layout 2. `folderKey=fnamdoc` is the
known namespace for `FNamDoc`; preserve case elsewhere. Resolve that namespace
from the document's `0. Info` parent layout (including a renamed exported pack),
the supplied `FNamDoc` root, or its immediate `FNamDoc` child. The Info layout takes
precedence and refers to its own pack. Unknown folderKey/root mappings stay unresolved.
Without folderKey, resolve exact paths from the provided folder; for a single-file
boundary use the JSON's parent, still restricting matches to supplied files.
The helper checks explicit relative references and WAV size/rate/channel/frame
consistency. Suffix matches elsewhere are unverified candidates, even when unique;
never use them to apply a label. The result's `basis` distinguishes root-relative
paths, FNamDoc namespace paths, unverified candidates and unresolved namespaces.
Use `applied_label`
only for matched records; never treat `stored_label_name` alone as a WAV label.
Unknown layout, missing file, ambiguous candidate or header conflict stays unresolved.
`universeX/Y` have no defined acoustic meaning in this release.

For individually uploaded WAV + JSON in a flat upload directory, `folderKey=fnamdoc`
does not by itself establish that directory as FNamDoc. A matching basename,
header, stored RMS and label ID identify a candidate record, not an applied label.
Say "candidate label: tire; path relationship unconfirmed", not "confirmed label:
tire" followed by an integrity disclaimer. Missing path evidence and absence of
cryptographic authentication are different limitations. Ask for an export ZIP
preserving `FNamDoc/0. Info/` and WAV paths, or the EventLog session hierarchy.
Never fabricate folders or edit references from filename guesses to force a match.
Continue standalone WAV analysis while metadata binding remains unresolved.

Select confirmed linked WAVs and run the audio helper with comparable parameters.
Compare sample rate, channels, analyzed region, device/gain, weighting/calibration,
stored analysis version and calculation definition. Unknown conditions remain unknown.
Even equal digital RMS definitions do not establish physical loudness comparability
between different devices/input gains. State whether values are stored or recomputed.
Do not relabel stored RMS as A-weighted solely because another metadata field says A-weighting.

### Analysis version 2 metadata and offsets

The label helper returns original `stored_metadata` and an interpreted
`measurement_context`. Context on an unresolved/conflicting link describes the JSON
record only; it is not a measurement condition verified for the supplied WAV.

| Field | Meaning |
|---|---|
| `overallRmsDbfs` | App-stored raw PCM whole-file RMS dBFS; no Time/FFT offset |
| `overallTimeLevelDbfs` | Version 2 app-stored whole-file energy-mean Time level, including silence, before display Time offset |
| `analysisWeighting` | Weighting used for that reanalysis/Time level |
| `weighting` | App-stored recording-time weighting; not a substitute for analysisWeighting |
| `timeCalibrationOffsetDb` / `fftCalibrationOffsetDb` | Separate recording-time offsets; neither establishes the current display setting |
| `windowType` / `fftSize` / `sampleRateHz` | Stored recording conditions; retain separately from the Reader's calculation parameters |
| `recordedAt` / `analyzedAt` | Recording vs reanalysis timestamps; do not assign an unstated timezone |

JSON null means unknown. The string `"None"` in a weighting field means explicitly
unweighted. Missing analysisWeighting stays unknown even if recording weighting is
A/B/C. Version 1 does not inherit version 2 Time-level semantics. Unknown analysis
versions preserve raw metadata without interpreting their numeric definitions.
Reanalysis cannot recover unknown recording settings. Legacy/source-unknown records
can retain values written by older app bugs; report their stored values and
`unverified_legacy` provenance, never assert that a historical setting was recovered.
The corrected 2026-10-28 export separates the two weighting fields, but its 90
source-unknown records still contain recording `A-weighting`; retain that uncertainty.

Moa/Bora's stated display rule is whole-file weighted energy-mean level plus the
current Time offset, not an instantaneous peak or an arithmetic mean of dB values.
For an explicitly requested display calculation, pass
`--display-time-offset-db <explicit-current-or-scenario-value>` to the label helper.
Only a confirmed WAV link, version 2, known analysisWeighting and finite stored
Time level permit a `display_projection`. This is stored `overallTimeLevelDbfs`
plus the caller's offset, conditional on the stored analysis weighting. It does
not verify the current app UI settings or physical SPL. State those assumptions
and label the value `dB_app_display`, not verified dB SPL. Zero offset is valid;
unknown offset is omitted. Never substitute the recording offset, use FFT offset
for Time levels, apply an offset twice, or recompute A/B/C from a guessed filter.
The audio helper remains raw PCM RMS plus periodic-Hann FFT; it does not reproduce
the app's Time weighting implementation or FFT calibration. `Hann` in metadata
alone does not establish identical window convention/scaling to the Reader.

## Sound Event Logger

Run `analyzer_events.py <EventLog.json> --root <provided-file-or-folder>`.
Optional `--bin-seconds 60` controls session-relative bins.
Supported profile: `FNam_3Tier_SessionEventAsset_JSON`, schema `1.0`, mode
`SOUND_EVENT_LOGGER`, events `AUDIO_EVENT/SPL_THRESHOLD`, assets `AUDIO_WAV/EVENT_CLIP`.
The observed app version is retained; schema definitions drive field interpretation.
Unknown schema or event kinds must not inherit SPL/time semantics automatically.

Use actual session epoch-ms start/end for elapsed duration. Event epoch timestamps,
stored display strings, stored relative time, durationMs and WAV time are separate.
Relative times require a matching session and consistent epoch-ms relationships.
Conflicts disable relative analysis for the affected event. UTC display is explicit;
do not assign a timezone to naive strings from a filename.

Each event's assets are joined by scoped event ID and explicit assetPath. An optional
asset sessionId must agree with the document session. Resolve paths relative to
the JSON location, restricted to supplied files. The known exported path
`FNamDoc/Armory/EventLog/<sessionId>/...` requires the JSON to reside at
`<pack>/Armory/EventLog/<sessionId>/`. Map the export namespace to that document's
own `<pack>`, which may have been renamed. Report `pack_anchor` and `expected_path`;
`basis=document_pack_namespace_path` means that exact local path was found within
the supplied boundary. It does not prove origin or integrity. A matching full
export suffix and session ID in another pack never establish this relationship.
An unknown document hierarchy cannot authorize this export mapping, even if only
one candidate exists. Known EventLog folder/session mismatches and resolved paths
escaping the document/session boundary are conflicts. Single suffix candidates
remain unresolved, multiple candidates ambiguous, with no selected path.
These candidates cannot replace a missing session clip. Preserve the export hierarchy
or supply a confirmed explicit reference instead of guessing a connection. WAV
headers must match recorded channel/rate settings when supplied. Use linked files
for audio analysis, and preserve their source event IDs in the response.
WAV start epoch and event sample offset remain unknown in this profile. A 2-second
pre-trigger setting does not establish that the event occupies sample `2*rate`.
The 4-second clips and 150–210 ms stored event durations are different quantities.

Zero events is valid, with unknown event maximum, not 0 dB SPL. Recording disabled
is distinct from a missing referenced WAV. Rank `maxSplDb` as app-stored event SPL,
not a value reproduced from PCM. Compare event rates only alongside threshold,
weighting, minimum duration and cooldown. Partial clips do not support session Leq
or a continuous session noise history. Do not infer a sound source/fault from a peak alone.
`event_count` counts all stored rows. `supported_event_count` and
`unsupported_event_count` separate recognized SPL events from other kinds.
Bins, intervals, rankings and event rate cover only `AUDIO_EVENT/SPL_THRESHOLD`.
Time bins require verified session relationships; rate is unavailable if any
supported event lacks verified time, or the document contains only unsupported kinds.
Unsupported events keep their original fields, without derived times or clip binding.

## Numerical contract

- WAV input: little-endian PCM16, mono/stereo. Normalize int16 by 32768.
- RMS: `sqrt(mean(x**2))`; dBFS: `20*log10(RMS/1)`. A full-amplitude sine
  approaches −3.0103 dBFS RMS. Silence is negative infinity, serialized as null
  with `rms_dbfs_state=silence_negative_infinity`.
- RMS dBFS is digital level. Never convert it to dB SPL without sufficient explicit
  calibration and processing definitions. This release has no automatic SPL conversion.
- FFT: periodic Hann `0.5-0.5*cos(2*pi*k/N)`, no detrend, no zero padding;
  `abs(rfft(x*w))/sum(w)`, double interior bins; do not double DC or even-N Nyquist.
  Units are peak amplitude/full scale, not RMS spectrum or SPL.
- Peak picking excludes DC, uses local maxima separated by at least 3 bins, top 5.
  Frequencies are bin centers, without sub-bin estimation. Report bin spacing `rate/N`.
- Spectrogram: same Hann, hop `floor(N/2)`, one-sided PSD
  `abs(rfft(x*w))**2/(rate*sum(w**2))`, double interior bins. Display `10*log10(PSD)`
  with a −160 dB floor; floor is display-only, not measured noise level.
- PCM rails are reported as sample counts, not proof of analog/microphone clipping.

## PCM CSV

Only single-column sample rows are supported, with optional `#` comments.
Recognized definitions are `# sample_rate_hz: ...` (or `sample_rate`) and
`# full_scale: ...`. Alternatively pass `--rate` and `--full-scale` from verified
linked Info or an explicit user statement; retain that provenance in the answer.
Conflicting definitions are errors. Filename numbers alone do not establish rate.
Missing rate permits sample statistics but no seconds/Hz analysis. Missing scale
permits raw-unit RMS/spectrum but no dBFS. Do not assume all CSV values are normalized.
Horizontal FRP arrays use the dedicated FRP helper below. Separate exported FFT/Octave
tables have not been supplied or validated; do not infer a table format from the app UI.

## Frequency Response Preview (FRP)

For response, impulse, dip or phase questions, use the supplied stored FIR as the
primary model when available. Calculate from its coefficients even if only a
standalone CSV can be bound; describe other unlinked session metadata as stored
candidate context. Do not silently replace the stored model with a new H1 fit.
Separate three outputs: app-stored model/metadata, Reader-computed response of
that model, and optional independently re-estimated results. The absence of an
executable package path is an execution limitation, not FRP format non-support.

Run `python -B <this-skill>/scripts/analyzer_frp.py <session.json> --root <provided-root>`.
For a standalone signal or FIR CSV, omit `--root`. Add
`--plot <new-output.png> --source-root <provided-measurement-root>` for a stored FIR
magnitude/phase/impulse plot. The same original-file boundary protection applies
as for audio. Add `--details` only when complete arrays are needed; output can be large.

Supported JSON profile: `FNam_3Tier_SessionEventAsset_JSON`, schema `1.0`, mode
`FREQ_RESP_PREVIEW`, observed Analyzer app versions v3.6/v3.7. Unknown schemas keep
their fields without inferred relationships. CSV has no version field: recognize
the observed explicit comment-header markers, not the filename. Each CSV contains
comment headers and one horizontal numeric vector. Never flatten an arbitrary table.

| Asset role | Interpretation |
|---|---|
| `REC_SIGNAL` | Analyzer recording, including silence |
| `NOISE_SIGNAL` | Generator reference samples sent to AudioTrack, not microphone audio |
| `REC_SIGNAL_REARRANGED` | Recording after removal of initial network-latency samples |
| `NOISE_SIGNAL_REARRANGED` | Reference trimmed at the end to match the aligned recording |
| `ACOUSTIC_MODEL_FIR` | App-exported acoustic-model coefficients |

Keep JSON event/asset IDs scoped to this session. Confirm assetRole/type, event
relationships, sample rate, coefficient counts and explicit assetPath. Full export
paths require the document's own `<pack>/Armory/FreqRespPre/<sessionId>` anchor;
renaming that outer pack is allowed. Document-relative references may resolve
exactly inside the same session folder. Do not use matching names in another pack.
The app's observed session ZIP contains six files at its root, while its JSON uses
full export paths. Such a flat ZIP permits standalone CSV analysis and stored JSON
inspection, but does not establish the missing path relationship. Report candidates
as unconfirmed and request a hierarchy-preserving folder ZIP for confirmed binding.
Do not manufacture a hierarchy to make those links pass.

Generator `session_id`, `local_folder` and `file_name` describe the other device's
export. They may differ from the Analyzer session and local assetPath; this alone
is not a conflict. Do not follow those generator-local paths or assume synchronized
device clocks. Preserve wall-clock timestamps, stored relativeTimeMs, signal length,
network latency and any Welch alignment lag separately. Do not infer propagation
distance from them. Aligned files have already had their stated shift/trim applied;
never apply it a second time. Compare arrays to validate the declared operations
(absolute tolerance 0.00000051 CSV units for export rounding). The rearranged
reference retains original total_samples/total_duration headers: its explicit
trim note and actual vector count define its current length.

Signal results include sample count, duration if rate is present, raw CSV RMS,
and a periodic-Hann spectrum of the first up-to-2048 samples, with the interval
and normalization recorded. This short FFT may fall in silence; it is not a
whole-recording or transfer-function estimate. `PCM_16BIT` does not specify CSV
normalization. Compute dBFS only with explicit positive `full_scale` metadata or
`--full-scale` supplied with provenance. No automatic Time/FFT offset or SPL conversion.

For FIR, transform every stored coefficient with `rfft(h, n=fft_size)`: no window,
no division by N and no one-sided doubling. Default FFT size is the next power of
two covering all taps; `--fft-size` must include all taps. Magnitude is model
output/input ratio; `20*log10(abs(H))` is dB relative to 1, not dBFS/SPL. Report rate,
padding, bin spacing, stored band mask and estimator headers. Magnitude zero yields
null dB (negative infinity); near-zero phase is undefined. Default response arrays
retain at most 4096 bins; `--details` retains all bins. Plots sample the impulse
display but calculations use all taps. FIR coefficient peak time includes playback,
capture and acoustic delays and is not an isolated acoustic delay.

The stored FIR is transformed, not re-estimated. Preserve `masked_H1` /
`masked_Welch_H1`, Welch window/overlap/lag and masking metadata without claiming
to reproduce the app estimator, coherence, or its exact display smoothing.
`measurementValid` is an app-stored flag, not a Reader certification. For session
comparisons, use confirmed FIR assets, comparable signal/band/rate/settings and
declared frequency grids; distinguish stored settings from Reader calculations.
This Analyzer workflow reads its bundled Noise Generator reference; it does not
announce general support for every Noise Generator export.

### Dip and phase interpretation

Dip ranking, smoothing, group delay, H1 re-estimation and coherence are additional
analyses, not built-in outputs of the current FRP helper. If requested and execution
is available, compute them in clearly identified supplemental code and preserve
the method and results. If not executed, do not report numerical findings.

For dips, use the full-resolution calculated response (`--details` or an explicitly
documented transform of all coefficients), not the default display-sampled curve.
Restrict interpretation to the stated excitation/model band and identify masked
edge attenuation separately. Record the frequency grid, FFT length, exact smoothing
kernel/width (including sigma versus FWHM for Gaussian smoothing), amplitude/power/dB
averaging domain, edge exclusion, candidate separation and peak-finding criteria.
Distinguish model magnitude in dB re 1, positive dip depth = baseline minus response,
and negative response deviation = response minus baseline. Define the local baseline
and any width measure; prominence is not automatically the same as baseline depth.
Do not rank deepest local dips solely by the lowest absolute response values. If
only absolute magnitudes were calculated, call them attenuation candidates rather
than a verified ranking of local dip depth. Label centers as approximate when
smoothing/grid choices affect their position. FFT bin spacing is not resolving
power: zero padding refines the grid without adding measured information.

Before ranking a dip, check whether the actual smoothing support and baseline
neighborhood fit inside the valid excitation/model band and retained data. Record
the requested and available neighborhood, any missing/masked bins and the edge
handling method. A truncated two-sided baseline can become asymmetric; boundary
padding does not supply measured support. Mark such depths as edge-affected and
conditional. When ranking depends on them, check sensitivity using a disclosed
in-band symmetric neighborhood with sufficient samples, or leave the candidate
outside a definitive ranking. Do not compare candidates with materially different
edge support as though their depths were equally established. Changing the
baseline width changes the analysis definition and must be reported; do not
silently adjust it to preserve a preferred dip or use values outside the valid
band. This does not prevent descriptive analysis of the remaining FIR response.

Find the impulse peak directly from the absolute FIR coefficients and report the
sample index/time; it is not an FFT peak or a measured pure propagation time.
Preserve the original FIR phase. A residual/group-delay view may remove a stated
reference delay for comparison; identify its source and do not remove it twice.
For frequency in Hz and phase in radians, group delay in seconds is
`-d(unwrap(angle(H)))/df/(2*pi)`.
Report phase units, differentiation method, frequency grid, any smoothing and
low-response exclusion criteria. A negative residual delay can follow subtraction
of the reference; it does not mean sound arrives before its cause. Dip/near-zero
phase may be unreliable. Before making a precise delay or quality claim, check
sensitivity to the grid/processing where needed; unstable estimates remain
conditional. A small band median does not establish stable delay throughout the
band. To describe stability or variation, report the spread (for example, stated
quantiles/range and excluded bins) as well as the median. A plot clipped to a
display range cannot prove there are no larger excursions. If spread or numerical
sensitivity was not checked, limit the statement to the median; do not infer an
absence of local phase anomalies. Do not equate a large linear phase slope with
severe acoustic distortion.

### Optional H1 and coherence cross-check

Before each H1/coherence request, including follow-ups, check this precondition:
both signal roles must be confirmed in the same session, with the exact references
and linking basis recorded. Metadata borrowed from a FIR or JSON must also be
confirmed as applicable to those signals. A matching filename in a flat archive
is insufficient. If any required relationship is unresolved, do not execute or
report a session-specific paired-signal estimate; continue standalone FIR analysis
and explain how to supply the preserved session hierarchy. Do not run the paired
calculation first and use a disclaimer or high coherence to bypass this gate.
If such a calculation was already reported, withdraw its session-quality conclusion
and mark the paired result unverified until the required relationships are checked.
Use generator reference as x and microphone recording as y, retaining their
amplitude scales. With `Pxy = mean(conj(X)*Y)`, H1 is `Pxy/Pxx` and magnitude-squared
coherence is `abs(Pxy)**2/(Pxx*Pyy)` where denominators have adequate excitation.
Specify the exact input segments, window, segment length, overlap, averaging,
detrending, normalization, silence handling and number of averaged segments.

The app's rearranged signals remove the network offset only. Inspect whether a
residual input/output lag remains before a finite-window Welch comparison. Verify
the lag's sign, provenance and relevance to these files; never hardcode a lag
from an example or apply an already removed shift again. For positive lag L where
y follows x, compare overlapping `x[:M-L]` and `y[L:M]` after confirming equal
sample rates and the common extent M; record the discarded regions. If alignment
is uncertain, report that limitation rather than assuming it is correct. A
post-hoc phase rotation does not correct finite-window pairing bias in spectra
already estimated from misaligned records. To compare corrected H1 phase with
a stored FIR that restores L samples, restore the delay phase explicitly or
compare both at the same disclosed reference. Do not subtract device wall clocks
as a substitute for sample alignment; varying lag may require further investigation.

Distinguish a stored alignment lag, an independently estimated correlation lag and
the FIR's largest absolute coefficient index. They are different quantities. A
stored lag may be used when its applicability is confirmed, but must be described
as a stored parameter, not a fresh correlation measurement. Claim independent lag
verification only if the estimator was actually executed on the linked signals;
retain its sign convention, search range, analyzed interval and output. Numerical
closeness to an impulse peak alone is not that verification. Before/after coherence
comparisons require both executed results under disclosed, comparable conditions,
including the overlap intervals lost to alignment.

For quality judgments based on coherence, examine alignment and relevant Welch
settings first. Report the analysis band and statistic, plus local values near
features of interest. A global median alone does not validate or invalidate every
dip; a high value is not calibration or certification. Few averaged segments and
changing segment length affect the estimate. If re-estimated and stored models
disagree, present them separately and investigate processing differences without
silently overwriting one or claiming exact reproduction of the app estimator.
If a user asks about a coherence value without a source, first distinguish a
hypothetical threshold from a value observed in this session. Never call it an
app-reported value without an actual app field/output. State the statistic's
session, files and processing when known. A calculation on another session cannot
validate, refute or explain this session's value. Differences between supplemental
methods alone do not justify diagnosing the app's coherence or validity logic.

## Code generation and result presentation

For Python, show the helper command or generate standalone NumPy code that follows
the exact numerical contract above. For MATLAB/GNU Octave, use `[x,fs]=audioread(path)`
for PCM16 WAV (already normalized; do not divide again), preserve separate channels,
and express the periodic Hann explicitly to avoid toolbox-dependent defaults.
Select the exact sample interval, use the first N samples for FFT, take bins
`1:floor(N/2)+1` from `fft`, and apply the same DC/Nyquist handling. Include RMS
reference and all parameters. CSV code must take explicit rate/scale inputs.
Label generated code as executed/tested or unexecuted; code provision does not
establish automatic runtime support. Do not imply app FFT/Octave display equivalence.

Answers should state the input/region, principal findings, source pointers and
parameters, then material missing context and optional Info guidance. Present stored
and computed values separately. Save artifacts outside the measurement files.
Describe the measured/modelled chain, not a speaker alone unless its contribution
is isolated. Without a stated target response, setup and acceptance criterion,
avoid good/bad band ratings, audibility claims or defect diagnoses from dips alone.
Ask for target/setup information only when it materially affects the requested
evaluation; continue supported descriptive analysis in the meantime.

## Version and verification

This is FNam Data Reader for Claude, version 1.0.0, the initial release. Read the
actual installed manifest (`.claude-plugin/plugin.json`) when reporting the installed
version; if only this text is available, state that the installed version is unverified.

The bundled Python code passed local regression tests before release.
That result does not establish that the bundled scripts run, or that
any feature behaves as documented, in Claude or any other host environment.

Judge execution support for each environment from actual evidence in that
environment, using the Supported / Unsupported / Unverified rules in Core.
Do not describe an untested feature, platform or runtime, including generated
MATLAB/GNU Octave code, as verified.

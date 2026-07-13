# Analysis cache and data export

## Local analysis cache

Repeated analysis of large media can be expensive. Version 1.12 stores completed
`AnalysisResult` records as compressed NPZ files in the operating system's user
cache location.

A cache identity includes:

- resolved local path;
- file byte size;
- nanosecond modification timestamp;
- a SHA-256 digest of bounded samples from the beginning, middle, and end.

The content sampling avoids reading an entire multi-gigabyte source solely to
form a cache key, while still detecting common in-place replacements that keep
the same name.

A cache record contains aligned features, panel matrices, waveform, sample rate,
source/video metadata, and feature descriptions. It does not contain user
accounts, credentials, telemetry, or network identifiers.

The default cache budget is bounded. Old entries are pruned after successful
writes. **Clear cache** in the Data tab removes cached NPZ records without
touching source files, projects, presets, or exports.

## CSV export

CSV export creates one row per analysis frame. Columns include:

- source time and source-frame index;
- all extracted/imported one-dimensional features except duplicate time/frame
  aliases;
- mapped X/Y/Z/color/size values when geometry exists;
- whether the source frame survived filtering into the geometry.

Non-included mapped values are left blank. Numeric output uses a compact
round-trippable representation suitable for spreadsheets and data tools.

## NPZ export

Compressed NPZ stores the same aligned columns as arrays, plus a JSON metadata
record containing schema version, source reference, source kind, duration,
column names, and mapping formulas. It is appropriate for Python/NumPy workflows
where preserving numeric precision and load speed matters more than direct
spreadsheet readability.

Example inspection:

```python
import json
import numpy as np

with np.load("analysis.npz", allow_pickle=False) as archive:
    metadata = json.loads(str(archive["__metadata__"].item()))
    columns = metadata["columns"]
    data = {
        name: archive[f"column_{index:04d}"]
        for index, name in enumerate(columns)
    }
```

## Interpretation limits

Feature values are computational descriptors, not automatically validated
scientific measurements. Results depend on decoding, resampling, frame length,
normalization, source quality, and formulas. Preserve source files, application
version, project/preset/profile files, and relevant settings when
reproducibility matters.

# Changelog

## v1.13.0

The look, layout and workflow of v1.10.18 are unchanged. This release keeps the original
interface and brings in the best engine upgrades from the 1.12 line.

- **Audio analysis is about 6x faster** (8 s clip: 1.0 s to 0.2 s) and uses about a third of the
  memory. All 54 features are numerically identical to v1.10.18, checked against golden files made
  from the v1.10.18 code, so existing presets and projects render exactly as before.
- **Analysis cache.** Re-opening the same file with the same settings is near-instant.
- **Safer saves and exports.** Projects, presets and MP4 exports are written to a temporary file and
  moved into place only on success. A failed export no longer deletes the previous file.
- **Data export.** Mapped analysis data can be exported as CSV or NPZ (`python -m metriq_visualizer_data_export`).
- **Sturdier presets.** Legacy and BOM-prefixed preset files load reliably; your own presets are
  preferred over bundled ones with the same name.
- **Signed macOS builds stay valid.** The app no longer writes Python bytecode inside its bundle.

## v1.10.18

Original release.

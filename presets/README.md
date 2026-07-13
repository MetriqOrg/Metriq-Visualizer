# Visual behavior presets

Drop `.mvpreset` files into this directory or into `~/.metriq_visualizer/presets`.

Metriq Visualizer lists them in the **Visual style** dropdown. Selecting a style applies appearance, camera, and geometry sampling without replacing the current mapping formulas. The **Load preset file** command can still apply a complete preset, including mapping formulas. Historical v1.10 preset files are translated automatically.

Additional preset directories can be supplied through `METRIQ_PRESET_PATH` using the operating system path separator. Search precedence is environment directories, the user preset directory, then this bundled directory. Existing repository preset files are preserved by `tools/merge_into_repo.py`.

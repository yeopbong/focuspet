# Characters and sprite format

Focus Pet includes four fictional adult pixel companions. Character choice changes appearance; it does not assign a work profile or supply training labels.

| Character | Visual identity | Selection accent |
| --- | --- | --- |
| Mira | Brown side braid, round glasses, moss cardigan | Lavender |
| Jun | Navy hair, subtle stubble, rust jacket | Soft blue |
| Ada | Curly black bob, gold earrings, terracotta blazer | Mint |
| Sol | Wavy silver hair, teal overshirt, cream trousers | Warm peach |

## Packaged assets

Each `src/focuspet/assets/characters/<id>/` directory contains:

- `source.png`: a transparent source sheet for editing or replacement.
- `sprites.png`: a 256 × 640 atlas, with four columns and eight rows of 64 × 80 frames.
- `thumbnail.png`: a 64 × 80 idle portrait.
- `manifest.json`: frame sequences, timing, loop behavior, anchor, hit region, thumbnail and license.

The eight actions are `idle`, `focus`, `normal`, `distracted`, `stretch`, `rest`, `recovered` and `unknown`. Rest and unknown override the current animation immediately. Stretching uses Work Load presentation thresholds of 80 and 75; it does not alter the workload calculation. Recovered is a brief non-looping response, and unsupported actions fall back to idle.

The anchor is `(32, 77)`. Sprites render at integer scale 1–5 with nearest-neighbor scaling. Window masks follow the displayed alpha channel, allowing clicks through transparent regions. Full click-through is a separate setting with a tray recovery action; it is unavailable when no tray recovery path exists.

## Replacement and licensing

Keep the manifest's frame dimensions, action names and valid frame indices when replacing a sheet. Each action should have distinct frames and a readable silhouette against light and dark desktops. Use transparent PNGs and check the native window mask after replacement.

The included character assets use **CC0-1.0 to the extent rights can be dedicated**, as recorded in each manifest. Replacement artwork retains its own license.

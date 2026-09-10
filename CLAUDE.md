# CLAUDE.md — SBTools

In-house tools for Slow Bros. Every Blender add-on lives in its own folder under
`blender/` as a Blender 4.2+ extension with a `blender_manifest.toml`. More
add-ons are coming, so `blender/` stays a plain container and never holds add-on
code itself.

Current add-ons:

- `blender/ai_retopo` (AI Retopo), documented in [docs/ai-retopo.md](docs/ai-retopo.md)
- `blender/ai_uv_layout` (AI UV Layout), documented in [docs/ai-uv-layout.md](docs/ai-uv-layout.md)

Both use the sidebar tab `SBTools` (`bl_category`); a new add-on joins the tab
by using the same category. `bl_order` sets the order of the panels.

## Shared code between add-ons

Extensions are self-contained and Blender knows no dependencies between them,
so code that several add-ons need is an identical copy in each add-on folder.
Today that is `credentials.py`, the shared store for the Scenario API key
(`<Blender config>/sbtools/scenario_credentials.json`). The headless tests
compare the copies byte for byte; change one, change all. Add-on-specific
things (log prefix, property names) stay out of such modules.

`scenario_client.py` and `history.py` exist in both add-ons as well, adapted
in wording and property names. They are not checked for identity, but a fix in
one usually belongs in the other.

## Language

User-facing text is **English**: panel labels, property names and tooltips,
status and error messages, console output, and all documentation. Code comments
may be German.

## Porting from Phototron

The tools here reimplement steps of the Phototron pipeline, mainly
`apps/desktop/public/ipc/retopology.js`. Phototron is the reference
implementation and is not worked on from this repository.

Reproduce the original step by step, including the parts that look incidental at
first glance: the mesh cleanup that runs before decimation, the tolerance
thresholds on a correction, the exact measure a comparison uses. Dropping the
cleanup and tightening a tolerance has already produced a result that came back
displaced and wrongly scaled.

Where a deviation genuinely seems better, say so and explain the reasoning
before building it. Do not omit a step silently and mention the difference
afterwards.

## User interface

Controls expose the granularity the backend actually has. When an API accepts
only three levels, the panel offers exactly those three, never a number field
that is bucketed into them behind the scenes. A finer control promises a
precision the pipeline cannot deliver, and it adds configuration that has to be
explained and maintained.

Model parameters are data, not code: `blender/ai_retopo/models.json` holds the
endpoint ids, parameter names and ranges, so a model can be added or corrected
without touching Python.

## Build and test

Build an extension zip into `dist/`:

```powershell
.\scripts\build_addon.ps1 ai_retopo
.\scripts\build_addon.ps1 ai_uv_layout
```

Run the headless smoke tests, which need no network access. They redirect the
credentials and history files to temporary folders, so they never touch the
real key; keep that when adding tests.

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --python scripts\test_ai_retopo_headless.py
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --python scripts\test_ai_uv_layout_headless.py
```

Developed and tested against Blender 5.2. `python` is not on PATH on this
machine; use Blender's bundled interpreter when a script needs one.

## Branches

- `develop` — integration branch, all work and pull requests go here
- `main` — release state only, merged from `develop` when a version is tagged
- feature work happens on `feature/<name>` branches off `develop`

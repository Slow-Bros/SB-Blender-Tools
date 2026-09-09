# CLAUDE.md — SBTools

In-house tools for Slow Bros. Every Blender add-on lives in its own folder under
`blender/` as a Blender 4.2+ extension with a `blender_manifest.toml`. More
add-ons are coming, so `blender/` stays a plain container and never holds add-on
code itself.

Current add-on: `blender/ai_retopo` (AI Retopo), documented in
[docs/ai-retopo.md](docs/ai-retopo.md).

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
```

Run the headless smoke test, which needs no network access:

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --python scripts\test_ai_retopo_headless.py
```

Developed and tested against Blender 5.2. `python` is not on PATH on this
machine; use Blender's bundled interpreter when a script needs one.

## Branches

- `develop` — integration branch, all work and pull requests go here
- `main` — release state only, merged from `develop` when a version is tagged
- feature work happens on `feature/<name>` branches off `develop`

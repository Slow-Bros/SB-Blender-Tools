# SBTools

Repository for Slow Bros. tools, including AI Auto-Retopo etc.

The first goal is to make the AI retopology tooling from the Phototron pipeline available directly inside Blender as an add-on. Further in-house tools (Blender, Unity, pipeline scripts) live here as well.

## Layout

```
SBTools/
├── blender/            # Blender add-ons / extensions, one folder per add-on
│   └── sb_ai_retopo/   # AI retopology via Scenario API (see docs/ai-retopo.md)
├── scripts/            # Standalone helper scripts (build, packaging, batch jobs)
│   ├── build_addon.ps1             # Build an extension zip into dist/
│   └── test_ai_retopo_headless.py  # Headless smoke test (blender -b --python ...)
└── docs/               # Notes, design decisions, usage guides
```

## Add-ons

- **SB AI Retopo** (`blender/sb_ai_retopo`) — sends the active mesh to the
  Scenario API (Hunyuan PolyGen 1.5), imports the retopologized result as a new
  object at the original's position. Sidebar tab *SBTools* in the 3D viewport.
  Setup and details: [docs/ai-retopo.md](docs/ai-retopo.md).

Target: Blender 4.2+ extension format (`blender_manifest.toml`); currently developed against Blender 5.x.

## Branches

- `develop` — integration branch, all work and pull requests go here
- `main` — release state only; merged from `develop` when a version is tagged

Feature work happens on `feature/<name>` branches off `develop`.

# SB Blender Tools

Repository for Slow Bros. Blender tools, starting with AI Retopo.

The first goal is to make the AI retopology tooling from the Phototron pipeline available directly inside Blender as an add-on. Further in-house Blender add-ons and pipeline scripts live here as well.

## Layout

```
SB-Blender-Tools/
├── blender/            # Blender add-ons / extensions, one folder per add-on
│   └── ai_retopo/      # AI Retopo — retopology via Scenario API (docs/ai-retopo.md)
├── extensions/         # Published extension repository: zips + index.json (docs/releasing.md)
├── scripts/            # Standalone helper scripts (build, packaging, batch jobs)
│   ├── build_addon.ps1             # Build an extension zip into dist/
│   └── test_ai_retopo_headless.py  # Headless smoke test (blender -b --python ...)
└── docs/               # Notes, design decisions, usage guides
```

## Add-ons

- **AI Retopo** (`blender/ai_retopo`) — sends the active mesh to a retopology
  model on the Scenario API (Hunyuan PolyGen, Meshy Remesh or Tripo), imports
  the result as a new object at the original's position. Sidebar tab *SBTools* in the 3D viewport.
  Setup and details: [docs/ai-retopo.md](docs/ai-retopo.md).

Target: Blender 4.2+ extension format (`blender_manifest.toml`); currently developed against Blender 5.x.

## Installing and updating

Add-ons are published as a Blender extension repository, so updates arrive
through Blender itself. In Preferences → Get Extensions → Repositories, add a
remote repository with this URL:

```
https://raw.githubusercontent.com/Slow-Bros/SB-Blender-Tools/main/extensions/index.json
```

How a release is built and published: [docs/releasing.md](docs/releasing.md).

## Conventions

- **User-facing text is English.** Panel labels, property names and tooltips,
  status and error messages, console output and documentation. Code comments may
  be German.

## Branches

- `develop` — integration branch, all work and pull requests go here
- `main` — release state only; merged from `develop` when a version is tagged

Feature work happens on `feature/<name>` branches off `develop`.

## License

GPL-3.0-or-later, see [LICENSE](LICENSE).

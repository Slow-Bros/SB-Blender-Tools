# SBTools

Repository for Slow Bros. tools, including AI Auto-Retopo etc.

The first goal is to make the AI retopology tooling from the Phototron pipeline available directly inside Blender as an add-on. Further in-house tools (Blender, Unity, pipeline scripts) live here as well.

## Layout

```
SBTools/
├── blender/            # Blender add-ons / extensions, one folder per add-on
│   └── <addon-name>/   # blender_manifest.toml + Python package
├── scripts/            # Standalone helper scripts (build, packaging, batch jobs)
└── docs/               # Notes, design decisions, usage guides
```

Target: Blender 4.2+ extension format (`blender_manifest.toml`); currently developed against Blender 5.x.

## Branches

- `develop` — integration branch, all work and pull requests go here
- `main` — release state only; merged from `develop` when a version is tagged

Feature work happens on `feature/<name>` branches off `develop`.

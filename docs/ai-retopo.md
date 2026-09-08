# SB AI Retopo (Blender add-on)

Blender port of the AI retopology step from the Phototron desktop app
(`apps/desktop/public/ipc/retopology.js`). The active mesh is sent to the
Scenario API (Tencent Hunyuan PolyGen 1.5, model `model_tencent-smarttopology`),
retopologized, and imported back as a **new object at the exact position of the
original**. The original object is never modified.

Location: `blender/sb_ai_retopo/` — Blender 4.2+ extension (`blender_manifest.toml`),
developed and tested against Blender 5.2.

## Install

1. Build the zip (or zip the `sb_ai_retopo` folder manually):

   ```powershell
   & "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --command extension build --source-dir blender\sb_ai_retopo --output-dir dist
   ```

2. Blender → Edit → Preferences → Get Extensions → dropdown (top right) →
   *Install from Disk…* → pick `dist/sb_ai_retopo-<version>.zip`.
3. In the add-on preferences enter the Scenario **API Key** and **API Secret**
   (same credentials as in Phototron → Settings). Alternatively set the
   environment variables `SCENARIO_API_KEY` / `SCENARIO_API_SECRET` before
   starting Blender.

For development without packaging: add `blender/` to `sys.path` and enable the
module, exactly as `scripts/test_ai_retopo_headless.py` does, or symlink
`blender/sb_ai_retopo` into an extension repository directory.

## Usage

3D Viewport → Sidebar (`N`) → tab **SBTools** → panel **AI Retopo**.

| Setting | Meaning |
| --- | --- |
| Ziel-Polygone | Low / Medium / High — sent as `faceLevel`. See *Polygon density* below. |
| Polygone | Quads / Triangles — sent as `polygonType` (`quadrilateral` / `triangle`). |
| Original ausblenden | Hide (not delete) the source object after a successful import. |
| Pre-Dezimierung | Decimate the upload copy before sending (API limit 200 MB). The original is untouched. |

Requirements: Object Mode, active object is a mesh. One job at a time; the
panel shows a progress bar and a cancel button while running. Progress and
errors are also printed to the system console with the prefix `[SB-AI-RETOPO]`.

Result: a new object `<name>_retopo` in the same collection(s) as the source,
with the same parent and world matrix, smooth shaded, selected and active.

## Polygon density (API limitation)

The Scenario / Hunyuan smart-topology endpoint does **not** accept a numeric
face count. Its only density control is `faceLevel` with the values `low`,
`medium` and `high` (the same three options as the *Detail* dropdown in
Phototron), so the panel offers exactly those three and nothing else. The face
count of the result is whatever the model produces for the chosen level and the
given input mesh.

If an exact face count is needed, decimate the result manually afterwards with
Blender's Decimate modifier. On a quad result that trades quad topology for
triangles, which is why it is not part of the add-on.

## Pipeline

1. **Export** (main thread): evaluated copy of the active object (modifiers
   applied, materials / colour attributes / UVs stripped, object transform reset
   to identity) → temporary GLB.
2. **Upload** (worker thread): `POST /v1/uploads` (kind `3d`, 5 MB multipart
   parts) → `PUT` parts → `POST /v1/uploads/{id}/action {complete}` → poll until
   the upload is imported and has an asset id.
3. **Generate**: `POST /v1/generate/custom/model_tencent-smarttopology` with
   `file3d`, `polygonType`, `faceLevel`, `geometryFileFormat: "obj"` (OBJ keeps
   quads; GLB would triangulate).
4. **Poll** `GET /v1/jobs/{id}` until `success`, then `GET /v1/assets/{id}` and
   download the mesh (OBJ preferred, GLB fallback).
5. **Import** (main thread): import OBJ (Y-up, matching the glTF convention) or
   GLB, merge into one mesh object, fit its bounding box onto the source's local
   bounding box (uniform scale + translation — Hunyuan may normalise the mesh),
   apply smooth shading, optional exact-count decimation, then assign the
   source's collections, parent and world matrix.

The worker thread never touches `bpy`; it communicates via a queue that a modal
operator drains on a timer.

## Test

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --python scripts\test_ai_retopo_headless.py
```

The test needs no network access. It covers registration, export, a simulated
(normalised) result round trip with placement check, decimation and the API
response parsers. The live API path is exercised manually in Blender with real
credentials.

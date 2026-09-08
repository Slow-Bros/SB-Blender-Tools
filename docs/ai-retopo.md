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

1. **Export** (main thread): evaluated copy of the active object, modifiers
   applied, materials, colour attributes and UVs stripped, object transform
   reset to identity. The copy then gets the same cleanup Phototron runs in
   `convertObjToGlb`: merge duplicate vertices at 0.0001, delete loose geometry
   that has no faces, recalculate normals outwards. Optional pre-decimation runs
   after that, and the result is written to a temporary GLB.
2. **Upload** (worker thread): `POST /v1/uploads` (kind `3d`, 5 MB multipart
   parts) → `PUT` parts → `POST /v1/uploads/{id}/action {complete}` → poll until
   the upload is imported and has an asset id.
3. **Generate**: `POST /v1/generate/custom/model_tencent-smarttopology` with
   `file3d`, `polygonType`, `faceLevel`, `geometryFileFormat: "obj"` (OBJ keeps
   quads; GLB would triangulate).
4. **Poll** `GET /v1/jobs/{id}` until `success`, then `GET /v1/assets/{id}` and
   download the mesh (OBJ preferred, GLB fallback).
5. **Import** (main thread): import OBJ (Y-up, matching the glTF convention) or
   GLB, merge into one mesh object, run the bounding-box safety net described
   below, apply smooth shading, then assign the source's collections, parent and
   world matrix.

The worker thread never touches `bpy`; it communicates via a queue that a modal
operator drains on a timer.

## Bounding-box safety net

Hunyuan may return the mesh normalised in scale and position, so the import
compares the result against the source and corrects it if needed. The rules
follow `bakeTexturesBlender` in Phototron:

- The comparison uses the **diagonal** of the bounding box, not the longest
  single axis. The diagonal stays meaningful when proportions shift slightly and
  a different axis becomes the longest one.
- A size difference is only corrected above **one percent**, a position offset
  only above one percent of the source diagonal. A result that already sits
  correctly is left untouched.
- The measurement **ignores stray fragments**. The model sometimes produces a
  few faces outside the object; measured over all vertices they inflate the box
  and throw off both scale and position. Separate parts are therefore excluded
  when they stick out of the main part's box by more than ten percent of its
  diagonal. Parts inside that box stay in, so an object that legitimately
  consists of several pieces is measured in full. If the excluded parts would
  hold more than ten percent of the vertices, nothing is excluded, because that
  is no longer a fragment.

Every run prints the measured diagonals, the resulting factor, the offset and
the number of separate parts to the system console. When fragments are found,
the panel also shows a warning: they are ignored for the fit but stay in the
mesh, so check and delete them yourself.

## Test

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --python scripts\test_ai_retopo_headless.py
```

The test needs no network access. It covers registration, export with the
pre-upload cleanup, pre-decimation, the fit tolerances, a simulated result round
trip with a placement check, a result carrying a stray fragment, and the API
response parsers. The live API path is exercised manually in Blender with real
credentials.

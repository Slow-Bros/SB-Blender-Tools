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
| KI-Modell | Which retopology model runs the job. See *Models* below. |
| Ziel-Polygone | A face count for models that accept one, otherwise Low / Medium / High. The panel shows the selected model's allowed range. |
| Polygone | Quads / Triangles, mapped to whatever the selected model calls it. |
| Original ausblenden | Hide (not delete) the source object after a successful import. |
| Pre-Dezimierung | Decimate the upload copy before sending (API limit 200 MB). The original is untouched. |

Requirements: Object Mode, active object is a mesh. One job at a time; the
panel shows a progress bar and a cancel button while running. Progress and
errors are also printed to the system console with the prefix `[SB-AI-RETOPO]`.

Result: a new object `<name>_retopo` in the same collection(s) as the source,
with the same parent and world matrix, smooth shaded, selected and active.

## Models

Three Scenario models take an existing mesh and retopologize it. They differ in
how the polygon density is controlled, which is why the panel changes with the
selected model.

| Model | Model id | Density control | Topology |
| --- | --- | --- | --- |
| Hunyuan PolyGen 1.5 | `model_tencent-smarttopology` | `faceLevel`: low / medium / high only | `polygonType`: `quadrilateral` / `triangle` |
| Meshy Remesh | `model_meshy-remesh` | `targetPolycount`: 100 to 300000 | `topology`: `quad` / `triangle` |
| Tripo Retopology | `model_tripo-retopology` | `faceLimit`: 1000 to 20000 | `quad`: boolean |

A target face count is approximate for every model. It is what the model aims
for, not a guarantee, so the result can land somewhat above or below. Values
outside the selected model's range are clamped to the range and the panel says
so, rather than sending a value the API would reject.

Hunyuan PolyGen has no numeric control at all. Its three levels are the same
options as the *Detail* dropdown in Phototron, and the resulting face count
depends on the level and on the input mesh. Pick one of the other two models
when a specific number matters.

Two model-specific choices are worth knowing. Meshy Remesh is called with
`resizeHeight: 0` and `originAt: "empty"` so it leaves size and origin of the
input alone, which keeps the placement simple. Tripo Retopology is called with
`bake: false` because the upload carries no textures, so baking would have
nothing to project.

Only the Hunyuan path has run against the live API so far. The other two are
implemented from the documented schemas and need one real run each to confirm
their request and response shapes.

## Keeping up with the API

The Scenario catalogue changes over time. Two mechanisms keep the add-on usable
without a code change.

**The registry is a data file.** `blender/sb_ai_retopo/models.json` holds the
table above: endpoint id, parameter names, ranges and the values each model uses
for quads and triangles. Adding a model, correcting a range or dropping one that
is gone means editing JSON, not Python. *Reload Registry* in the add-on
preferences reads the file again without restarting Blender.

Editing the file inside the installed extension works but is lost on the next
install. *Export Model List* therefore writes a copy to the Blender config
folder as `sb_ai_retopo_models.json`, and that copy takes precedence over the
bundled file from then on. A malformed file never blocks the add-on: it falls
back to the bundled file, then to a single built-in entry, and reports the
problem in the preferences.

**The catalogue can be checked against the API.** *Refresh Catalogue* fetches
`GET /v1/models`, caches the result in the preferences and compares it with the
registry. The preferences then list registry models the API no longer offers,
and any model whose id or name looks like retopology but is missing from the
registry, so a new one can be added deliberately. The panel marks a selected
model that is gone, and a run is refused before the upload rather than failing
afterwards. An empty or never-fetched catalogue never blocks anything.

The fetch runs in a worker thread and is only ever triggered by that button.
Drawing a panel must not cause network traffic, because Blender redraws
constantly. The response shape of `GET /v1/models` is not contractually fixed,
so it is parsed defensively and an unexpected shape is logged rather than
raised.

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
3. **Generate**: `POST /v1/generate/custom/{model id}` with the body built by
   `models.build_request` for the selected model, carrying the uploaded asset
   id, the topology and either a face count or a level.
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

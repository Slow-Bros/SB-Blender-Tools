# SB AI Retopo (Blender add-on)

Blender port of the AI retopology step from the Phototron desktop app
(`apps/desktop/public/ipc/retopology.js`). The active mesh is sent to the
Scenario API (Tencent Hunyuan PolyGen 1.5, model `model_tencent-smarttopology`),
retopologized, and imported back as a **new object at the position of the
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
| AI Model | Which retopology model runs the job. See *Models* below. |
| Target Polygons | A face count for models that accept one, otherwise Low / Medium / High. The panel shows the selected model's allowed range. |
| Polygons | Quads / Triangles, mapped to whatever the selected model calls it. |
| Remove Stray Fragments | Delete separate parts the model placed outside the object. On by default. See *Stray fragments* below. |
| Hide Original | Hide (not delete) the source object after a successful import. |
| Pre-Decimation | Decimate the upload copy before sending (API limit 200 MB). The original is untouched. |

Requirements: Object Mode, active object is a mesh. One job at a time; the
panel shows a progress bar and a cancel button while running. Progress and
errors are also printed to the system console with the prefix `[SB-AI-RETOPO]`.

Result: a new object `<name>_retopo` in the same collection(s) as the source,
with the same parent and world matrix, smooth shaded, selected and active.

## Models

Three Scenario models are in the registry. They differ in how the polygon density
is controlled, which is why the panel changes with the selected model.

| Model | Model id | Density control | Topology | Result format |
| --- | --- | --- | --- | --- |
| Hunyuan PolyGen 1.5 | `model_tencent-smarttopology` | `faceLevel`: low / medium / high only | `polygonType`: `quadrilateral` / `triangle` | OBJ |
| Meshy Remesh | `model_meshy-remesh` | `targetPolycount`: 100 to 300000 | `topology`: `quad` / `triangle` | untested |
| Tripo Retopology | `model_tripo-retopology` | `faceLimit`: 1000 to 20000 | `quad`: boolean | FBX |

A target face count is approximate. It is what the model aims for, not a
guarantee, so the result can land somewhat above or below. Values outside the
model's range are clamped and the panel says so, rather than sending a value the
API would reject.

Hunyuan PolyGen has no numeric control at all. Its three levels are the same
options as the *Detail* dropdown in Phototron, and the resulting face count
depends on the level and on the input mesh. Pick Tripo when a specific number
matters. Tripo is called with `bake: false` because the upload carries no
textures, so baking would have nothing to project.

Tripo returns its result as FBX rather than OBJ or GLB. The download recognises
that from the file's magic bytes even when the MIME type is uninformative, and
the import goes through Blender's FBX importer. An unrecognised format is logged
with its MIME type, size and first bytes, so a new output format can be
identified instead of failing as an opaque unknown file.

**Meshy Remesh has never completed a run.** It was removed once on the
assumption that the account lacks a Meshy plan, which was never confirmed
against an API response. The per-id check reports it, so it is back in the
registry and can be tried. If a run fails, the API message in the panel finally
says why.

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

That precedence has a sharp edge worth knowing: once a user copy exists, a model
list shipped with a newer version of the add-on is ignored. The preferences say
which file is in use and offer *Reset to Bundled*, which renames the user copy
to `.bak` rather than deleting it, so hand-made edits are recoverable.

**A model id can be checked against the API.** *Check* next to the model id
field asks `GET /v1/models/{id}` for every registry model plus whatever id is
typed in, and stores the verdict: available, missing, or unknown. The panel
marks a selected model that came back missing, and a run is refused before the
upload rather than failing after it.

Every check also asks for a control id that cannot exist. If that one does not
come back missing, the endpoint is not telling models apart and the preferences
say so, because a check that answers "available" to everything is worse than no
check at all.

Only a definitive *missing* ever blocks a run. Anything inconclusive, including
a permission error or a model that was never checked, counts as unknown and
holds nobody up.

There used to be a *Refresh Catalogue* button that fetched the list at
`GET /v1/models`. It is gone. That endpoint lists the account's own trained
models, so it answered `{"models": []}` while Hunyuan demonstrably ran, and it
could never contain the platform models this add-on calls. The per-id check
replaces it entirely.

The check runs in a worker thread and only ever starts from a button press.
Drawing a panel must not cause network traffic, because Blender redraws
constantly.

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
   download the mesh, preferring OBJ, then GLB, then FBX.
5. **Import** (main thread): import OBJ (Y-up, matching the glTF convention),
   GLB or FBX, merge into one mesh object, remove stray fragments, measure size
   and position against the source, apply smooth shading, then assign the
   source's collections, parent and world matrix.

The worker thread never touches `bpy`; it communicates via a queue that a modal
operator drains on a timer.

## Placement

The result is placed by giving the new object the source object's world matrix,
and its geometry is corrected onto the source's local bounding box. The
correction always runs; there is no switch for it.

The recipe follows the alignment in Phototron's `bakeTexturesBlender`: compare
the diagonal of the bounding box rather than the longest single axis, correct
the size only beyond one percent, and re-centre only beyond one percent of the
source diagonal. Worth knowing about the original: Phototron aligns only the
throwaway copy it bakes with. The retopo file it hands the user is the raw API
result, so the alignment there is a safety net for baking, not the main path.

Every run verifies itself afterwards and writes the numbers to the system
console: the local bounding-box diagonals of source and result, their ratio, the
centre offset, and the source object's scale. Three invariants must hold, and
they are checked rather than assumed:

- the diagonal ratio is one, within one percent
- the centres coincide, within one percent of the diagonal
- the new object's world matrix equals the source's

A failure is written to the console and shown in the panel instead of being
delivered silently. The check deliberately compares in local space. A world
bounding box changes shape when an object is rotated, so two objects of equal
size but different proportions would measure differently there and the check
would raise false alarms.

## Stray fragments

The models sometimes emit a few faces outside the object. *Remove Stray
Fragments*, on by default, deletes them on import. A separate part counts as a
fragment only when it sticks out of the main part's bounding box by more than
ten percent of that box's diagonal, so an object that legitimately consists of
several pieces stays intact. Suzanne's eyes, for example, sit inside the head's
box and are kept. If the parts flagged as fragments would hold more than ten
percent of the vertices, nothing is removed, because that is no longer a
fragment but a misread of the mesh.

Switching the option off keeps everything the model returned. The panel reports
how many fragments were found either way.

Fragments that are topologically connected to the main mesh cannot be found this
way, since the detection works on separate parts. A result with attached spikes
needs manual cleanup.

## Test

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --python scripts\test_ai_retopo_headless.py
```

The test needs no network access. It covers registration, export with the
pre-upload cleanup, pre-decimation, the fit tolerances, a simulated result round
trip with a placement check, a result carrying a stray fragment, and the API
response parsers. The live API path is exercised manually in Blender with real
credentials.

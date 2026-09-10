# AI UV Layout (Blender add-on)

Blender port of the UV layout step from the Phototron desktop app
(`apps/desktop/public/ipc/retopology.js`, the `uv` phase and
`transferUVsToRetopo`). The active mesh is sent to a UV unwrapping model on the
Scenario API, and the UV coordinates that come back are written onto the
**unchanged geometry of the original**: by default onto a copy named
`<name>_uv`, on request onto the object itself. Geometry, size and position
never come from the API result.

Location: `blender/ai_uv_layout/` — Blender 4.2+ extension
(`blender_manifest.toml`), developed and tested against Blender 5.2.

The add-on is built like [AI Retopo](ai-retopo.md) and shares its sidebar tab
and its API credentials. What this document does not repeat is documented
there: the upload and polling flow, why a job cannot be cancelled, how the
model registry and the history work in detail.

## Install

1. Build the zip:

   ```powershell
   .\scripts\build_addon.ps1 ai_uv_layout
   ```

2. Blender → Edit → Preferences → Get Extensions → dropdown (top right) →
   *Install from Disk…* → pick `dist/ai_uv_layout-<version>.zip`.
3. Enter the Scenario **API Key** and **API Secret** in the add-on preferences.
   The credentials are shared by all SBTools add-ons: whoever has entered them
   for AI Retopo is done. See *Shared credentials* below.

## Usage

3D Viewport → Sidebar (`N`) → tab **SBTools** → panel **AI UV Layout**. The tab
is the same one AI Retopo uses; Blender puts every panel with the same category
into one tab, so both add-ons appear together without knowing about each other.

| Setting | Meaning |
| --- | --- |
| AI Model | Which unwrapping model runs the job. Currently one, see *Models*. |
| Apply to Original | Write the UV map onto the active object itself, overwriting its active UV map. Off by default: a copy `<name>_uv` receives the UVs and the original is untouched. |
| Hide Original | Hide (not delete) the source object after a successful transfer. Only meaningful when a copy is made. |
| History | Past jobs of this project, with *Import Again* for a result that was never imported. |

Requirements: Object Mode, active object is a mesh. One job at a time; the
panel shows a progress bar and a cancel button while running. Progress and
errors are also printed to the system console with the prefix `[SB-AI-UV]`.

The panel says which UV map will be overwritten. Without one, a map named
`UVMap` is created.

Result: the target object (copy or original) carries the model's UV layout on
the active UV map, is smooth shaded, selected and active. A copy lands in the
same collection(s) as the source, with the same parent, world matrix and
modifiers.

## Models

`blender/ai_uv_layout/models.json` lists the models. Scenario offers one UV
unwrapping model today:

| Model | Model id | Parameters | Result format |
| --- | --- | --- | --- |
| Hunyuan UV Unwrapping | `model_tencent-uv-unwrapping` | none, only the uploaded file | OBJ |

The request body is exactly what Phototron sends: the asset id under `file3d`,
nothing else. The registry exists so a second model is a JSON entry and not a
code change; the rules for the file are the ones described for AI Retopo
(*Changing the model list*), minus the density parameters that UV models do not
have. A broken `models.json` leaves the add-on registered with the Start button
disabled and the reason in the panel.

## Pipeline

1. **Export** (main thread): the active object's base mesh, without modifiers,
   without materials and without its existing UVs, written as OBJ from a
   helper object at the origin. The original is not touched.
2. **Upload** (worker thread): `POST /v1/uploads` (kind `3d`, content type
   `model/obj`, 5 MB parts) → `PUT` parts → complete → poll until the upload
   has an asset id. Same client as AI Retopo, including the 200 MB limit.
3. **Generate**: `POST /v1/generate/custom/{model id}` with `{"file3d": asset}`.
4. **Poll** `GET /v1/jobs/{id}` until `success`, then `GET /v1/assets/{id}`
   and download the mesh, preferring OBJ, then GLB, then FBX.
5. **Transfer** (main thread): import the result, join it into one mesh, copy
   its UVs onto the target (see below), apply smooth shading, delete the
   imported carrier mesh.

The worker thread never touches `bpy`; it communicates via a queue that a modal
operator drains on a timer.

## Why OBJ, and why the base mesh

Phototron's comment on the transfer is the whole reason: *the UV API normalizes
geometry (scale + center). Instead of trying to reverse that, we keep the
original geometry and only copy the UV coordinates, since both meshes share the
same topology.* Copying UVs corner for corner works only if the result has the
same faces in the same order as what was uploaded.

- **OBJ up, OBJ down.** OBJ keeps quads and writes faces in order. GLB
  triangulates on export, so a quad mesh would come back with twice the faces
  and the copy could not run. Phototron uploads the retopo OBJ as it is and
  asks for the result in OBJ (`targetFormat: 'obj'`); the add-on does the same.
- **Base mesh, not the evaluated one.** The UVs are written onto the mesh
  data block. With a Subdivision modifier on the object, the evaluated mesh has
  more faces than the data block and the copy would not line up. So the base
  mesh goes up, the UVs land on it, and the modifier interpolates them as it
  does for any hand-made UV map. That also means the model sees the coarse
  mesh, not the subdivided one.
- **Existing UVs stay home.** They are not uploaded; the model would ignore
  them, and the file is smaller without them.

## Transfer

Follows the main path of `transferUVsToRetopo` in Phototron: same number of
faces and corners → copy the UV of every corner by index. The add-on
additionally checks that every face has the same number of corners in both
meshes. Phototron compares only the two totals; two meshes can match on both
and still distribute corners differently, and a copy by index would then be
wrong without anyone noticing. Checking the sizes costs nothing.

The size and position of the returned geometry play no part in this. The model
hands back a normalised mesh, and that is fine: UVs are per corner and do not
depend on where the corner sits in space. The AI Retopo placement correction
has no counterpart here because nothing geometric is taken from the result.

**A topology mismatch is an error**, reported with both sets of numbers, and
it leaves everything as it was: no copy, no new UV map on the original. It is
not expected from the Hunyuan model, which returns the uploaded topology; it
would point at a result in a different format (GLB, triangulated) or at a mesh
edited between starting the job and importing it again from the history.

Phototron has a fallback at this point, a *Data Transfer* modifier with
*Topology* mapping, and it was deliberately not ported (September 2026).
Blender's topology mapping needs identical corner counts just like the copy by
index; with different counts it reports *'Topology' mapping cannot be used in
this case* and leaves the mesh untouched, so the fallback transfers nothing.
Phototron's own comment describes it as "nearest face interpolated", which is
a different mapping than the code sets. A fallback that would actually work
for different topologies (fit the result onto the source bounding box, then
map by nearest face) was considered and not built: the case does not occur
with the one model there is, and a nearest-face projection is unclean at UV
seams anyway. A clear message was judged more useful than a silent no-op.

## Smooth shading

Phototron's transfer script ends with *shade smooth*, and the UV step runs its
smoothing pass once more after that. The add-on applies the same to the target:
custom split normals cleared, `sharp_face` and `sharp_edge` removed. A copy
gets it anyway; with *Apply to Original* it also changes the original's
shading, which is Phototron's behaviour and is documented here for that reason.

## History

The same history as AI Retopo, kept in the add-on's own `history.json`: an
entry per job, written the moment the API returns the job id, with *Import
Again* to fetch a result once more or pick up a job that was still running
when Blender stopped. Project filter, handover on first save and the 100-entry
limit are described in [ai-retopo.md](ai-retopo.md#history).

*Import Again* needs a mesh to put the UVs on. It uses the source object by
name. If that is gone, the active mesh serves as the target, but only if its
topology matches the result; UVs onto a different mesh would be nonsense.
Without a match the result is kept as its own object `<name>_uv`, with the
normalised geometry the model returned, and the panel says so.

## Shared credentials

Both add-ons are separate extensions, and Blender has no way for one extension
to read another's preferences. The API key and secret therefore live in one
file that both read and write:

```
<Blender config>/sbtools/scenario_credentials.json
```

On Windows that is
`%APPDATA%\Blender Foundation\Blender\<version>\config\sbtools\`. The fields in
either add-on's preferences are views onto this file; entering the key in one
shows it in the other. The environment variables `SCENARIO_API_KEY` and
`SCENARIO_API_SECRET` still work as a fallback when the file is empty.

The file is written through a temporary file and `os.replace`, so a crash
mid-write leaves the previous content whole. It is plain JSON, as readable as
the `userpref.blend` entry it replaces.

The module behind it, `credentials.py`, is an identical copy in every add-on
folder, because an extension must be self-contained. The headless tests of
both add-ons fail if the copies drift apart. AI Retopo 0.1.0 stored the key in
its own preferences; on the first access after the update those values move
into the shared file and the old fields are emptied.

## Test

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --python scripts\test_ai_uv_layout_headless.py
```

The test needs no network access. It enables both add-ons side by side and
covers: the shared tab and shared credentials, the model registry, OBJ export
of the base mesh, the transfer by index onto a copy and onto the original, the
clean rejection of a topology mismatch, the standalone fallback, the API
response parsers, and the history. The live API path is exercised manually in
Blender with real credentials.

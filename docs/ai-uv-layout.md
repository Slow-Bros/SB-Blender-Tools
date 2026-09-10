# AI UV Layout (Blender add-on)

Blender port of the UV layout step from the Phototron desktop app
(`apps/desktop/public/ipc/retopology.js`, the `uv` phase and
`transferUVsToRetopo`). The active mesh is sent to a UV unwrapping model on the
Scenario API, and the UV coordinates that come back are added to the
**original object as a new UV map** named `AI_UV_1`, `AI_UV_2` and so on.
Existing UV maps are kept, geometry, size and position never come from the
API result.

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
| History | Past jobs of this project, with *Import Again* for a result that was never imported. |

Requirements: Object Mode, active object is a mesh. One job at a time; the
panel shows a progress bar and a cancel button while running. Progress and
errors are also printed to the system console with the prefix `[SB-AI-UV]`.

Result: the object gets a new UV map with the model's layout, set active for
editing and rendering, and is smooth shaded. Nothing else about the object
changes: no copy is made, and the UV maps it had stay as they were.

The panel shows how many UV maps the object has and the name the result will
get. Maps are numbered `AI_UV_1`, `AI_UV_2`, ... over the highest number
already present, so deleting `AI_UV_1` does not make the next result reuse the
name, and maps with other names (`UVMap`, a lightmap) are left alone and not
counted. Blender allows eight UV maps per mesh; with eight present the panel
says so and the job is refused before anything is uploaded.

## Models

`blender/ai_uv_layout/models.json` lists the models. Scenario offers one UV
unwrapping model today:

| Model | Model id | Parameters | Result format |
| --- | --- | --- | --- |
| Hunyuan UV Unwrapping | `model_tencent-uv-unwrapping` | none, only the uploaded file | OBJ |

The request body is exactly what Phototron sends: the asset id under `file3d`,
nothing else. Besides the OBJ the job also returns an FBX of the same mesh
and two PNG images; the add-on takes the OBJ and ignores the rest.

Not every mesh is accepted. Suzanne fails after a few seconds with *An
internal error occurred* and the hint that the file format may be invalid for
this generation type; a closed retopo mesh and a cube run fine. The likely
reason is her open geometry, the eye sockets are holes and the eyes separate
shells. A failed job shows the API message in the panel. The registry exists so a second model is a JSON entry and not a
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
5. **Transfer** (main thread): import the result as one mesh, copy its UVs
   onto the object (see below), apply smooth shading, delete the imported
   carrier mesh.

The worker thread never touches `bpy`; it communicates via a queue that a modal
operator drains on a timer.

## Why OBJ, and why the base mesh

Phototron's comment on the transfer is the whole reason: *the UV API normalizes
geometry (scale + center). Instead of trying to reverse that, we keep the
original geometry and only copy the UV coordinates, since both meshes share the
same topology.* The result must therefore carry the same faces with the same
corners as what was uploaded.

- **OBJ up, OBJ down.** OBJ keeps quads. GLB triangulates on export, so a
  quad mesh would come back with twice the faces and nothing would match.
  Phototron uploads the retopo OBJ as it is and asks for the result in OBJ
  (`targetFormat: 'obj'`); the add-on does the same.
- **Base mesh, not the evaluated one.** The UVs are written onto the mesh
  data block. With a Subdivision modifier on the object, the evaluated mesh has
  more faces than the data block and the copy would not line up. So the base
  mesh goes up, the UVs land on it, and the modifier interpolates them as it
  does for any hand-made UV map. That also means the model sees the coarse
  mesh, not the subdivided one.
- **Existing UVs stay home.** They are not uploaded; the model would ignore
  them, and the file is smaller without them.
- **The result is read as one mesh.** The model's OBJ holds one `o` block per
  connected part (`part_00000001`, `part_00000002`, ...). Blender's importer
  would turn those into separate objects; the add-on imports with object and
  group splitting off and never joins anything.

## Transfer

Phototron copies the UV of every corner by index: same number of faces and
corners, corner *i* of the result goes to corner *i* of the original. That
assumes the model returns the faces in the order they were uploaded, and **it
does not**. Measured on a real job (September 2026, a retopo mesh of 1,755
faces in two connected parts): the result is written by a Blender 3.6 on
Scenario's side with one `o` block per connected part, and faces of one part
that sat between faces of the other in the original move to the end of their
block. Same 1,755 faces, same 6,547 corners, but from face 448 on everything
sits one or two positions off. A copy by index would give those faces another
face's UVs, and with an all-quad mesh nobody would notice until the texture
comes out wrong. Phototron's transfer only ever worked for meshes in one
piece; a cube passes, Suzanne's head with two eyes does not.

What the model does keep is the geometry. On that job every vertex came back
at exactly its uploaded position, not even scaled. The add-on therefore
matches by geometry instead of by index:

1. The result is fitted onto the original's bounding box with the AI Retopo
   recipe (compare the diagonals, correct beyond one percent). On the job
   above nothing had to be corrected; the fit is the safety net in case the
   model does normalise, as Phototron's comment says it may.
2. Each result face is matched to the original face with the nearest centroid,
   within 0.1 % of the bounding-box diagonal.
3. Within that face, corners are paired by position, each corner exclusively,
   nearest first. Where several original faces share a centroid, the corners
   decide: two quads crossing each other have the same centre and different
   corners, and a real retopo result contained such a pair. Two corners of
   one face on the same position (a collapsed quad) each still get their own
   partner.

The matching is deliberately not vertex-to-vertex across the whole mesh:
Suzanne has two pairs of vertices on identical positions, and a mapping by
vertex set becomes ambiguous there. Inside a single face no two corners
coincide, so the per-face pairing is unambiguous.

The console reports how many faces were matched and how many of them were
still at their original index, so an index copy would have worked. The
number is informational; the transfer does not depend on it.

**A mismatch is an error** and leaves everything as it was, no new UV map on
the original. Different face or corner counts are reported with both sets of
numbers; that points at a result in a different format (GLB, triangulated).
Equal counts but faces without a counterpart are reported with the count of
faces left over; that points at a mesh edited between starting the job and
importing it again from the history, or at a model that moved vertices.

Phototron has a fallback for the count mismatch, a *Data Transfer* modifier
with *Topology* mapping, and it was deliberately not ported. Blender's
topology mapping needs identical corner counts and otherwise reports
*'Topology' mapping cannot be used in this case* and leaves the mesh
untouched, so the fallback transfers nothing. A clear message was judged more
useful than a silent no-op.

## Smooth shading

Phototron's transfer script ends with *shade smooth*, and the UV step runs its
smoothing pass once more after that. The add-on applies the same to the
object: custom split normals cleared, `sharp_face` and `sharp_edge` removed.
That changes the original's shading, which is Phototron's behaviour and is
documented here for that reason.

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
of the base mesh, the transfer as a new numbered UV map with existing maps
untouched, the eight-map limit, the geometric matching on a result with
shuffled faces, renumbered vertices, rotated corners and normalised geometry,
crossing quads with a shared centroid, a result split into several `o`
blocks, the clean rejection of a topology or geometry mismatch, the
standalone fallback, the API
response parsers, and the history. The live API path is exercised manually in
Blender with real credentials.

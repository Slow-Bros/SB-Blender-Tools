# AI Retopo (Blender add-on)

Blender port of the AI retopology step from the Phototron desktop app
(`apps/desktop/public/ipc/retopology.js`). The active mesh is sent to one of the
retopology models on the Scenario API (see *Models* below), retopologized, and
imported back as a **new object at the position of the original**. The original object is never modified.

Location: `blender/ai_retopo/` — Blender 4.2+ extension (`blender_manifest.toml`),
developed and tested against Blender 5.2.

## Install

1. Build the zip (or zip the `ai_retopo` folder manually):

   ```powershell
   & "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --command extension build --source-dir blender\ai_retopo --output-dir dist
   ```

2. Blender → Edit → Preferences → Get Extensions → dropdown (top right) →
   *Install from Disk…* → pick `dist/ai_retopo-<version>.zip`.
3. In the add-on preferences enter the Scenario **API Key** and **API Secret**
   (same credentials as in Phototron → Settings). Alternatively set the
   environment variables `SCENARIO_API_KEY` / `SCENARIO_API_SECRET` before
   starting Blender. The credentials are shared by all SBTools add-ons, so
   they need entering once; where they live and how the entry from version
   0.1.0 is taken over is described in
   [ai-uv-layout.md](ai-uv-layout.md#shared-credentials).

For development without packaging: add `blender/` to `sys.path` and enable the
module, exactly as `scripts/test_ai_retopo_headless.py` does, or symlink
`blender/ai_retopo` into an extension repository directory.

## Usage

3D Viewport → Sidebar (`N`) → tab **SBTools** → panel **AI Retopo**. The tab
is shared with the other SBTools add-ons; [AI UV Layout](ai-uv-layout.md) sits
below this panel and takes the retopo result on to the next step.

| Setting | Meaning |
| --- | --- |
| AI Model | Which retopology model runs the job. See *Models* below. |
| Target Polygons | A face count for models that accept one, otherwise Low / Medium / High. The panel shows the selected model's allowed range. |
| Polygons | Quads / Triangles, mapped to whatever the selected model calls it. |
| Remove Stray Fragments | Delete separate parts the model placed outside the object. On by default. See *Stray fragments* below. |
| Hide Original | Hide (not delete) the source object after a successful import. |
| History | Past jobs of this project, with *Import Again* for a result that was never imported. See *History* below. |
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
against an API response. It is in the registry and can be tried. If a run fails,
the API message in the panel finally says why.

## History

Because a started job cannot be cancelled, losing Blender means losing a job
that is already paid for. The panel therefore keeps a *History* sub-panel, and
its point is not the files — it is the job ids.

An entry is written the moment the API returns the job id, long before anything
can go wrong: name (the one the result carries in the outliner, `Scan_retopo`),
time, model, status and, once it arrives, the size of the result. Selecting an
entry shows those details and *Import Again*, which asks Scenario for the job
once more. A job that was still running when Blender stopped is picked up there
and finishes normally; a finished one is downloaded again. Nothing is cached
locally — the result lives in the project after the import, and at Scenario as
an asset.

The list lives in `history.json` in the add-on's user folder, written through a
temporary file and `os.replace`. That exchange is atomic, so a crash during the
write leaves the previous file whole instead of half a new one. Old entries drop
out at 100.

*This Project Only* filters by the `.blend` the job belongs to. Jobs started in
a file that was never saved have no project yet; saving the file for the first
time hands them over to it. Only the jobs of that same document are handed over
— a second Blender instance holds a different key for its own unsaved jobs and
keeps them. Leaving an unsaved file without saving leaves its jobs without a
project for good: they stay in the list under *all projects*, importable by job
id, but they belong to a project that never came to exist. *Save As*, renaming
or moving a project does not move entries either; that would be guesswork.

If the source object is gone when a job is imported again, the active mesh
serves as the reference for size and position. Without one the result comes in
uncorrected, and the panel says so.

## Changing the model list

`blender/ai_retopo/models.json` holds the table above: endpoint id, parameter
names, ranges and the values each model uses for quads and triangles. Adding a
model, correcting a range or dropping one that is gone means editing that file
and restarting Blender, not editing Python.

There is deliberately no interface around this. An earlier version had buttons
to refresh a catalogue from the API, check single model ids, export the list for
editing and reload it at runtime. All of it served hand-editing the file or a
question that only came up once, so it was removed. Two findings from that
detour are worth keeping:

- `GET /v1/models` lists the account's own trained models, not the platform
  models that `/v1/generate/custom/{id}` addresses. It answered `{"models": []}`
  on an account where Hunyuan runs fine, so it cannot be used to check whether a
  model is available.
- Exporting the list to the Blender config folder created a copy that silently
  shadowed the one shipped with the add-on, so an updated model list never took
  effect. The add-on now reads only its own file. A leftover
  `sb_ai_retopo_models.json` in the Blender config folder is ignored and can be
  deleted.

A broken `models.json` does not break the add-on: it still registers, the panel
shows the reason, and the Start button stays disabled until the file is fixed
and Blender restarted.

The model dropdown stores its choice in the `.blend` file as a number derived
from the model key, not as the position in the list. Reordering or removing
entries therefore never silently switches a saved scene to a different model.

Whether a model actually works for an account is answered by running it. A
failed job shows the API message verbatim in the panel and the console, which is
the information that matters when something goes wrong.

## Pipeline

1. **Export** (main thread): evaluated copy of the active object, modifiers
   applied, materials and colour attributes stripped, object transform reset
   to identity. The copy then gets the same cleanup Phototron runs in
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

## Cancelling

Cancel stops the add-on, not the job. Before the job starts it is a real abort,
there is nothing running yet. Afterwards the job runs to the end at Scenario and
spends its credits either way; the add-on only stops collecting the result.

That is not for lack of an endpoint. Scenario has one — `POST
/v1/jobs/{id}/action` with `{"action": "cancel"}`, the same action pattern the
upload uses for `complete` — and the reference notes that "Today only cancel on
inference jobs is supported". A retopology job is not one of those. Tried in
September 2026 against a running Hunyuan job, the API answered:

```
400 {"reason": "Cannot cancel this type of job. Action not permitted."}
```

All three models go through `/v1/generate/custom/{id}`, so this is not specific
to Hunyuan. The wording of both the docs and the error suggests the job type may
be allowed later. If it is, sending that one request from the cancel path is the
whole change — worth retesting before building anything larger around it.

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

Every run measures the corrected mesh afterwards, on its vertex data rather than
on the numbers the correction was computed from, and writes the result to the
system console: the local bounding-box diagonals of source and result, their
ratio, the centre offset, and the source object's scale. Two invariants must
hold:

- the diagonal ratio is one, within one percent
- the centres coincide, within one percent of the diagonal

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

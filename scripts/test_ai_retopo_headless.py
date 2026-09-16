# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless smoke test for the ai_retopo add-on (no network access).

Run:  blender -b --python scripts/test_ai_retopo_headless.py

Covers: registration, pre-upload cleanup, GLB export of a transformed object,
import of a simulated (normalized) result, the bounding-box safety net with its
one percent tolerance, stray fragments being ignored during measurement,
placement on the original, the pure-python API response parsers, the job
history with its project handover, and the job pump that delivers a result
only into the file the job belongs to.
"""
import os
import sys
import tempfile
import threading

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "blender"))

import addon_utils  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
mod = addon_utils.enable("ai_retopo", default_set=True, persistent=False)
assert mod is not None, "add-on failed to enable"
from ai_retopo import (credentials, history, mesh_io, models, operators, panel,  # noqa: E402
                       preferences, scenario_client)

ctx = bpy.context
scene = ctx.scene
assert hasattr(scene, "sb_ai_retopo"), "scene settings missing"
prefs = preferences.get_prefs(ctx)

# --- shared credentials: one file for every SBTools add-on. Redirected to a
# temp folder first thing, so the test never touches the real key.
cred_dir = tempfile.mkdtemp(prefix="sb_credentials_")
credentials.directory = lambda: cred_dir
credentials._cache = None
assert credentials.load() == {"api_key": "", "api_secret": ""}
prefs.scenario_api_key = " key-1 "
prefs.scenario_api_secret = "secret-1"
assert credentials.load() == {"api_key": "key-1", "api_secret": "secret-1"}
assert prefs.scenario_api_key == "key-1"
assert os.path.exists(os.path.join(cred_dir, credentials.FILE_NAME))
# Version 0.1.0 kept the key in the preferences as api_key/api_secret. Those
# move into the shared file the first time they are read, and the old entry
# is emptied so the secret is not stored twice.
credentials.save(api_key="", api_secret="")
prefs.api_key = "legacy-key"
prefs.api_secret = "legacy-secret"
assert preferences.get_credentials(ctx) == ("legacy-key", "legacy-secret")
assert prefs.api_key == "" and prefs.api_secret == "", (prefs.api_key, prefs.api_secret)
assert credentials.load()["api_key"] == "legacy-key"
# a legacy entry never overrides shared credentials that already exist
prefs.api_key = "older-key"
assert preferences.get_credentials(ctx) == ("legacy-key", "legacy-secret")
assert prefs.api_key == ""
# environment variables fill in when the file is empty
credentials.save(api_key="", api_secret="")
os.environ["SCENARIO_API_KEY"] = "env-key"
os.environ["SCENARIO_API_SECRET"] = "env-secret"
assert preferences.get_credentials(ctx) == ("env-key", "env-secret")
os.environ.pop("SCENARIO_API_KEY")
os.environ.pop("SCENARIO_API_SECRET")
# the module is a copy in every add-on, and the copies must not drift apart
with open(os.path.join(ROOT, "blender", "ai_retopo", "credentials.py"), "rb") as f:
    _copy_a = f.read()
with open(os.path.join(ROOT, "blender", "ai_uv_layout", "credentials.py"), "rb") as f:
    _copy_b = f.read()
assert _copy_a == _copy_b, "credentials.py differs between ai_retopo and ai_uv_layout"
print("[TEST] shared credentials ok")
levels = [i.identifier for i in scene.sb_ai_retopo.bl_rna.properties["face_level"].enum_items]
assert levels == ["low", "medium", "high"], levels
assert scene.sb_ai_retopo.face_level == "medium"
poly = [i.identifier for i in scene.sb_ai_retopo.bl_rna.properties["polygon_type"].enum_items]
assert poly == [models.QUADS, models.TRIS], poly
# The level values must be exactly what the level-based API expects
assert set(levels) == set(scenario_client.FACE_LEVELS), (levels, scenario_client.FACE_LEVELS)
# The model enum is built from the registry at draw time, so its items are not
# exposed through bl_rna. Check it functionally instead: every registry key must
# be assignable, anything else must be rejected.
first_key = models.MODELS[0]["key"]
assert scene.sb_ai_retopo.model == first_key, scene.sb_ai_retopo.model
for spec in models.MODELS:
    scene.sb_ai_retopo.model = spec["key"]
    assert scene.sb_ai_retopo.model == spec["key"], spec["key"]
try:
    scene.sb_ai_retopo.model = "not-a-model"
    raise AssertionError("unknown model key must be rejected")
except TypeError:
    pass
scene.sb_ai_retopo.model = first_key
model_keys = [m["key"] for m in models.MODELS]
print(f"[TEST] registration + settings ok, models: {model_keys}")

# --- model registry: every model must build a complete, valid request body
for spec in models.MODELS:
    assert spec["id"].startswith("model_"), spec
    for pk in (models.QUADS, models.TRIS):
        body = models.build_request(spec, "asset123", pk, face_level="low", target_faces=12345)
        assert body[spec["file_param"]] == "asset123", body
        assert body[spec["polygon_param"]] == spec["polygon_values"][pk], body
        for k, v in spec.get("extra", {}).items():
            assert body[k] == v, body
        if models.uses_count(spec):
            lo, hi = models.count_range(spec, pk)
            got = body[spec["count_param"]]
            assert lo <= got <= hi, (spec["key"], pk, got)
            # a value outside the model's range must be clamped, never sent raw
            low = models.build_request(spec, "a", pk, target_faces=1)
            high = models.build_request(spec, "a", pk, target_faces=10 ** 9)
            assert low[spec["count_param"]] == lo, low
            assert high[spec["count_param"]] == hi, high
            assert models.count_range_label(spec, pk) == f"{lo:,} to {hi:,}"
        else:
            assert body[spec["level_param"]] == "low", body
            assert "count_param" not in spec, spec
            assert models.count_range(spec, pk) is None
            assert models.count_range_label(spec, pk) == ""
            assert models.clamp_count(spec, 7, pk) == 7
try:
    models.build_request(models.MODELS[0], "a", "bogus")
    raise AssertionError("unknown polygon key must raise")
except ValueError:
    pass
assert models.get("does-not-exist")["key"] == first_key
# Tripo takes at most 10,000 faces for quads but 20,000 for triangles; a job
# with 20,000 quads went through once and failed at Scenario after the upload
tripo = models.get("tripo_retopology")
assert tripo["id"] == "model_tripo-retopology", tripo
assert models.count_range(tripo, models.QUADS) == (500, 10000), tripo["count_range"]
assert models.count_range(tripo, models.TRIS) == (500, 20000), tripo["count_range"]
assert models.build_request(tripo, "a", models.QUADS, target_faces=20000)["faceLimit"] == 10000
assert models.build_request(tripo, "a", models.TRIS, target_faces=20000)["faceLimit"] == 20000
try:
    models.count_range(tripo, "bogus")
    raise AssertionError("unknown polygon key must raise")
except ValueError:
    pass
# Upload limits are per model, straight from each model's documentation:
# Tripo takes 150 MB, Hunyuan 200 MB. Meshy documents none and gets the default.
assert models.upload_limit_bytes(tripo) == 150 * 1024 * 1024
assert models.upload_limit_bytes(models.get("hunyuan_polygen")) == 200 * 1024 * 1024
assert models.get("meshy_remesh")["upload_limit_mb"] == models.DEFAULT_UPLOAD_LIMIT_MB
assert all(models.upload_limit_bytes(m) > 0 for m in models.FALLBACK_MODELS)
assert not models.LOAD_ERROR, models.LOAD_ERROR
model_ids = [m["id"] for m in models.MODELS]
assert "model_meshy-remesh" in model_ids, "Meshy is back in the registry"
print(f"[TEST] model registry ok: {model_ids}")

# --- registry is data: a JSON file drives it, and a broken file cannot brick it
import json as _json  # noqa: E402
reg_tmp = tempfile.mkdtemp(prefix="sb_registry_")
good = os.path.join(reg_tmp, "good.json")
with open(good, "w", encoding="utf-8") as f:
    _json.dump({"models": [{
        "key": "custom", "id": "model_custom-x", "label": "Custom",
        "density": "count", "file_param": "model", "polygon_param": "topology",
        "polygon_values": {"quads": "quad", "tris": "triangle"},
        "count_param": "n", "count_range": [10, 20],
    }]}, f)
loaded = models._read(good)
assert loaded[0]["extra"] == {}, loaded                  # optional keys defaulted
assert loaded[0]["description"] == "Custom", loaded
assert loaded[0]["upload_limit_mb"] == models.DEFAULT_UPLOAD_LIMIT_MB, loaded
assert models._validate({**loaded[0], "upload_limit_mb": 50})["upload_limit_mb"] == 50
# a plain pair applies to both polygon types
assert loaded[0]["count_range"] == {"quads": (10, 20), "tris": (10, 20)}, loaded
split = models._validate({**loaded[0], "count_range": {"quads": [1, 2], "tris": [3, 4]}})
assert split["count_range"] == {"quads": (1, 2), "tris": (3, 4)}, split
for broken in ({"models": []},
               {"models": [{"key": "a"}]},
               {"models": [{**loaded[0], "count_range": [99, 1]}]},
               {"models": [{**loaded[0], "count_range": [10]}]},
               {"models": [{**loaded[0], "count_range": [10, "20"]}]},
               {"models": [{**loaded[0], "count_range": {"quads": [1, 2]}}]},
               {"models": [{**loaded[0], "count_range": {"quads": [1, 2], "tris": [4, 3]}}]},
               {"models": [{**loaded[0], "upload_limit_mb": 0}]},
               {"models": [{**loaded[0], "upload_limit_mb": "150"}]},
               {"models": [{**loaded[0], "upload_limit_mb": True}]},
               {"models": [loaded[0], loaded[0]]}):
    bad = os.path.join(reg_tmp, "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        _json.dump(broken, f)
    try:
        models._read(bad)
        raise AssertionError(f"invalid registry accepted: {broken}")
    except ValueError:
        pass
print("[TEST] registry file validation ok")
# --- source object: Suzanne, subdivided, transformed, with material/vertex color
bpy.ops.mesh.primitive_monkey_add()
src = ctx.active_object
src.name = "Scan"
src.modifiers.new("Subsurf", "SUBSURF").levels = 2
src.data.materials.append(bpy.data.materials.new("Mat"))
src.data.color_attributes.new("Col", "BYTE_COLOR", "CORNER")
src.location = Vector((3.0, -2.0, 1.5))
src.rotation_euler = Euler((0.3, 0.7, 1.1))
src.scale = Vector((2.0, 2.0, 2.0))
coll = bpy.data.collections.new("Scans")
scene.collection.children.link(coll)
coll.objects.link(src)
scene.collection.objects.unlink(src)
ctx.view_layer.update()

tmp = tempfile.mkdtemp(prefix="sb_test_")
glb = os.path.join(tmp, "upload.glb")
info = mesh_io.export_object_for_upload(ctx, src, glb, decimate_target=0)
assert os.path.getsize(glb) > 1000, "glb too small"
assert info["faces"] > 500, info
assert "Scan_sb_upload" not in bpy.data.objects, "temp object not cleaned up"
assert src.select_get() and ctx.view_layer.objects.active == src, "selection not restored"
print(f"[TEST] export ok: {info}")

# The panel's size warning rests on an estimate from the mesh counters alone;
# it has to match what the exporter actually writes, or the warning lies.
eval_mesh = src.evaluated_get(ctx.evaluated_depsgraph_get()).data
counts = (len(eval_mesh.vertices), len(eval_mesh.loops), len(eval_mesh.polygons))
est = mesh_io.estimate_upload_bytes(*counts)
real = os.path.getsize(glb)
assert abs(est - real) / real < 0.02, (est, real, counts)
# a mesh that fits keeps its face count, one that does not gets a smaller
# target with some headroom, rounded to the thousands the field counts in
assert mesh_io.faces_within_upload_limit(*counts, real * 2) == counts[2]
fit = mesh_io.faces_within_upload_limit(*counts, real // 2)
assert 1000 <= fit < counts[2] and fit % 1000 == 0, (fit, counts)
assert mesh_io.estimate_upload_bytes(*counts, decimate_target=fit) <= real // 2, (fit, counts)
# Tripo's 150 MB is roughly 8 million triangles of a closed mesh; a bigger
# mesh must get a target below that limit, not above the mesh itself
big = (5_000_000, 30_000_000, 10_000_000)
assert mesh_io.estimate_upload_bytes(*big) > 150 * 1024 * 1024
big_fit = mesh_io.faces_within_upload_limit(*big, 150 * 1024 * 1024)
assert 1000 <= big_fit < 10_000_000 and big_fit % 1000 == 0, big_fit
assert mesh_io.estimate_upload_bytes(*big, decimate_target=big_fit) <= 150 * 1024 * 1024
assert mesh_io.estimate_upload_bytes(*big, decimate_target=12_000_000) == mesh_io.estimate_upload_bytes(*big)
print(f"[TEST] upload size estimate ok: {est} vs {real} bytes, fit {fit} of {counts[2]}")

# The panel warns under the face count only when the mesh is over the limit of
# the selected model, and names the face count that fits
class _Box:
    def __init__(self):
        self.lines = []

    def label(self, text="", icon="NONE"):
        self.lines.append((text, icon))

settings = scene.sb_ai_retopo
settings.pre_decimate = False
box = _Box()
panel._draw_upload_size(box, eval_mesh, tripo, settings)
assert box.lines == [], box.lines
tiny = {**tripo, "upload_limit_mb": real / 2 / 1024 / 1024}
panel._draw_upload_size(box, eval_mesh, tiny, settings)
assert len(box.lines) == 2 and box.lines[0][1] == "ERROR", box.lines
assert "limit is 0 MB" in box.lines[0][0], box.lines
assert f"Reduce to at least {fit:,} faces" in box.lines[1][0], box.lines
# a pre-decimation target that fits silences the warning, one that does not keeps it
settings.pre_decimate = True
settings.pre_decimate_target = fit
box = _Box()
panel._draw_upload_size(box, eval_mesh, tiny, settings)
assert box.lines == [], box.lines
settings.pre_decimate_target = counts[2] + 1000
panel._draw_upload_size(box, eval_mesh, tiny, settings)
assert len(box.lines) == 2, box.lines
settings.pre_decimate = False
print("[TEST] panel upload warning ok")

# Recommended upload size: 5 to 10 % of the source, never below the floor,
# capped by what fits the limit, nothing for a mesh that is small already
rec = mesh_io.recommended_upload_faces
assert rec(10_000_000) == (500_000, 1_000_000), rec(10_000_000)
assert rec(1_500_000) == (100_000, 150_000), rec(1_500_000)      # floor lifts the low end
assert rec(900_000) == (100_000, 100_000), rec(900_000)          # floor lifts both
assert rec(100_000) is None and rec(7_872) is None
assert rec(10_000_000, max_faces=750_000) == (500_000, 750_000)  # capped by the limit
assert rec(10_000_000, max_faces=300_000) is None                 # limit already forces less
assert rec(10_000_000, max_faces=500_000) is None
assert rec(7_872, max_faces=3_000) is None
assert rec(10_000_000, max_faces=20_000_000) == (500_000, 1_000_000)
assert rec(123_456_789)[0] % 1000 == 0 and rec(123_456_789)[1] % 1000 == 0

# In the panel the recommendation sits under the limit warning; the small test
# mesh gets none, a scan-sized mesh gets the range, and a pre-decimation
# target inside the range turns the info icon into a check mark
class _Counts:
    def __init__(self, v, l, f):
        self.vertices, self.loops, self.polygons = range(v), range(l), range(f)

scan = _Counts(*big)                       # 10 million faces, over Tripo's 150 MB
box = _Box()
panel._draw_upload_size(box, scan, tripo, settings)
assert len(box.lines) == 4 and box.lines[0][1] == "ERROR", box.lines
assert box.lines[2] == ("Recommended upload: 500,000 to 1,000,000 faces", "INFO"), box.lines
assert box.lines[3][0].startswith("5 to 10 % of the source"), box.lines
# a limit inside the range caps its upper end to what fits
cramped = {**tripo, "upload_limit_mb": 15}
cramped_fit = mesh_io.faces_within_upload_limit(*big, 15 * 1024 * 1024)
assert 500_000 < cramped_fit < 1_000_000, cramped_fit
box = _Box()
panel._draw_upload_size(box, scan, cramped, settings)
assert box.lines[2][0] == f"Recommended upload: 500,000 to {cramped_fit:,} faces", box.lines
settings.pre_decimate = True
settings.pre_decimate_target = 600_000
box = _Box()
panel._draw_upload_size(box, scan, tripo, settings)
assert box.lines[0][1] == "CHECKMARK" and box.lines[0][0].startswith("Recommended"), box.lines
settings.pre_decimate_target = 200_000
box = _Box()
panel._draw_upload_size(box, scan, tripo, settings)
assert box.lines[0][1] == "INFO", box.lines
settings.pre_decimate = False
mid = _Counts(450_000, 2_700_000, 900_000)  # fits the limit, floor makes one number
box = _Box()
panel._draw_upload_size(box, mid, tripo, settings)
assert box.lines[0] == ("Recommended upload: about 100,000 faces", "INFO"), box.lines
assert box.lines[1][0].startswith("11 % of the source"), box.lines
print("[TEST] upload recommendation ok")

# Pre-decimate export
glb2 = os.path.join(tmp, "upload_dec.glb")
info2 = mesh_io.export_object_for_upload(ctx, src, glb2, decimate_target=500)
assert os.path.getsize(glb2) < os.path.getsize(glb), "pre-decimate did not shrink file"
# the estimate scales with the decimation ratio, like the exporter does
est2 = mesh_io.estimate_upload_bytes(*counts, decimate_target=500)
assert abs(est2 - os.path.getsize(glb2)) / os.path.getsize(glb2) < 0.1, (est2, os.path.getsize(glb2))
print(f"[TEST] pre-decimate export ok: {info2}")

# --- cleanup before upload (duplicate + loose vertices), as Phototron does
bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
dirty = ctx.active_object
dirty.name = "Dirty"
bm = bmesh.new()
bm.from_mesh(dirty.data)
bm.verts.ensure_lookup_table()
bm.verts.new(bm.verts[0].co)      # duplicate on top of an existing vertex
bm.verts.new(Vector((5, 5, 5)))   # loose vertex far away
bm.to_mesh(dirty.data)
bm.free()
assert len(dirty.data.vertices) == 10
glb3 = os.path.join(tmp, "dirty.glb")
info3 = mesh_io.export_object_for_upload(ctx, dirty, glb3)
assert info3["cleanup"]["verts_removed"] == 1, info3
assert info3["cleanup"]["loose_removed"] == 1, info3
assert info3["faces"] == info3["faces_clean"] == 6, info3
assert len(dirty.data.vertices) == 10, "cleanup must not touch the source object"
bpy.data.objects.remove(dirty, do_unlink=True)
print(f"[TEST] pre-upload cleanup ok: {info3['cleanup']}")

# --- fit_matrix: Phototron's one percent tolerance, measured on the diagonal
lo, hi = Vector((0, 0, 0)), Vector((2, 1, 1))
m, i = mesh_io.fit_matrix(lo, hi, lo, hi)
assert m is None and not i["scaled"] and not i["moved"], i
m, i = mesh_io.fit_matrix(lo, hi, lo, hi * 0.995)  # 0.5 % off, inside tolerance
assert m is None, i
m, i = mesh_io.fit_matrix(lo, hi, lo, hi * 0.5)    # 100 % off, must be corrected
assert m is not None and i["scaled"] and i["moved"], i
assert (m @ lo - lo).length < 1e-6 and (m @ (hi * 0.5) - hi).length < 1e-6
m, i = mesh_io.fit_matrix(lo, hi, lo * 0.95, hi * 0.95)  # 5 % off, must be corrected
assert m is not None and i["scaled"], i
shift = Vector((0.5, 0, 0))
m, i = mesh_io.fit_matrix(lo, hi, lo + shift, hi + shift)  # moved, not scaled
assert m is not None and i["moved"] and not i["scaled"], i
print("[TEST] fit_matrix tolerance ok")

# --- simulate API result: re-import our own GLB, normalize to unit cube (as
# Hunyuan may do), export as Y-up OBJ, then run the import/placement path.
before = set(bpy.data.objects)
bpy.ops.import_scene.gltf(filepath=glb)
sim = [o for o in bpy.data.objects if o not in before and o.type == "MESH"][0]
me = sim.data
lo = Vector([min(v.co[i] for v in me.vertices) for i in range(3)])
hi = Vector([max(v.co[i] for v in me.vertices) for i in range(3)])
center = (lo + hi) / 2
scale = 1.0 / max(hi - lo)
me.transform(Matrix.Translation(Vector((0.123, -0.4, 0.05))) @ Matrix.Scale(scale, 4) @ Matrix.Translation(-center))
for o in bpy.data.objects:
    o.select_set(o == sim)
obj_path = os.path.join(tmp, "retopo_result.obj")
bpy.ops.wm.obj_export(filepath=obj_path, export_selected_objects=True, export_materials=False)
bpy.data.objects.remove(sim, do_unlink=True)

new, stats = mesh_io.import_result(ctx, obj_path, src)
ctx.view_layer.update()
assert new.name == "Scan_retopo", new.name
assert stats["fitted"], "bbox fit should have been applied to the normalized result"
assert new.users_collection[0] == coll, [c.name for c in new.users_collection]
assert all(p.use_smooth for p in new.data.polygons)
assert "Scan" in bpy.data.objects and not src.hide_get(), "original must remain"


def world_bbox(o):
    lo, hi = mesh_io.local_bbox(ctx, o)
    pts = [o.matrix_world @ Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    return (Vector([min(p[i] for p in pts) for i in range(3)]),
            Vector([max(p[i] for p in pts) for i in range(3)]))


def bbox_without_outliers(mesh):
    """Bounding box of the main part only, plus the part statistics."""
    a = mesh_io.analyze_parts(mesh)
    return a["lo"], a["hi"], {k: a[k] for k in ("parts", "outlier_parts", "outlier_fraction", "filtered")}


slo, shi = world_bbox(src)
nlo, nhi = world_bbox(new)
err = max((slo - nlo).length, (shi - nhi).length)
assert err < 1e-3, f"placement mismatch: {err}"
# Suzanne is legitimately 3 parts (head + two eyes) that sit inside the head's
# box, so nothing may be treated as an outlier here
assert stats["parts"] == 3 and stats["outlier_parts"] == 0 and not stats["filtered"], stats
print(f"[TEST] import + placement ok, bbox error {err:.2e}, parts {stats['parts']}")

# Second import with an explicit name, source hidden afterwards
new2, stats2 = mesh_io.import_result(ctx, obj_path, src, name="Scan_retopo_2",
                                    hide_source=True)
assert new2.name == "Scan_retopo_2", new2.name
assert src.hide_get() and "Scan" in bpy.data.objects, "source must be hidden, not deleted"
src.hide_set(False)
print(f"[TEST] named import + hide source ok: {stats2}")

# --- result with a stray island far outside the object: the placement must be
# measured on the main part only, otherwise scale and position are corrupted
before = set(bpy.data.objects)
bpy.ops.wm.obj_import(filepath=obj_path)
main_part = [o for o in bpy.data.objects if o not in before and o.type == "MESH"][0]
bpy.ops.mesh.primitive_cube_add(size=0.05, location=(0.0, 0.0, 3.0))
stray = ctx.active_object
for o in bpy.data.objects:
    o.select_set(o in (main_part, stray))
stray_path = os.path.join(tmp, "retopo_stray.obj")
bpy.ops.wm.obj_export(filepath=stray_path, export_selected_objects=True, export_materials=False)
for o in (main_part, stray):
    bpy.data.objects.remove(o, do_unlink=True)

new3, stats3 = mesh_io.import_result(ctx, stray_path, src, name="Scan_retopo_stray",
                                    remove_fragments=False)
ctx.view_layer.update()
# Suzanne itself is 3 parts (head + two eyes), the stray cube is the 4th.
# Only the cube sticks out of the head's box, so only it may be excluded.
assert stats3["parts"] == 4, stats3
assert stats3["outlier_parts"] == 1 and stats3["filtered"], stats3
mlo, mhi, _ = bbox_without_outliers(new3.data)
pts = [new3.matrix_world @ Vector((x, y, z))
       for x in (mlo.x, mhi.x) for y in (mlo.y, mhi.y) for z in (mlo.z, mhi.z)]
mlo_w = Vector([min(p[i] for p in pts) for i in range(3)])
mhi_w = Vector([max(p[i] for p in pts) for i in range(3)])
err3 = max((slo - mlo_w).length, (shi - mhi_w).length)
assert err3 < 1e-3, f"stray island corrupted the placement: {err3}"
print(f"[TEST] stray island ignored, placement ok: bbox error {err3:.2e}")

# --- cleanup_mesh: merge doubles, drop loose vertices (parity with Phototron)
cm = bpy.data.meshes.new("cleanup_test")
cm.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 0), (5, 5, 5)], [], [(0, 1, 2)])
cm.update()
cleanup = mesh_io.cleanup_mesh(cm)
assert len(cm.vertices) == 3 and len(cm.polygons) == 1, (len(cm.vertices), len(cm.polygons))
assert cleanup["verts_removed"] == 1 and cleanup["loose_removed"] == 1, cleanup
bpy.data.meshes.remove(cm)
print(f"[TEST] cleanup_mesh ok: {cleanup}")

# --- analyze_parts: a few stray faces must not inflate the measurement
bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, location=(0, 0, 0))
sphere = ctx.active_object
bpy.ops.mesh.primitive_cube_add(size=0.05, location=(10, 0, 0))
fragment = ctx.active_object
with ctx.temp_override(object=sphere, active_object=sphere, selected_objects=[sphere, fragment],
                       selected_editable_objects=[sphere, fragment]):
    bpy.ops.object.join()
full_lo, full_hi = mesh_io._mesh_bbox(sphere.data)
main_lo, main_hi, pinfo = bbox_without_outliers(sphere.data)
assert pinfo["parts"] == 2 and pinfo["filtered"], pinfo
assert (full_hi - full_lo).length > 9.0, "full bbox should be inflated by the fragment"
assert (main_hi - main_lo).length < 3.6, (main_lo, main_hi)
print(f"[TEST] main-part bbox ok: {pinfo}")

# --- regression: a result with stray faces must still land on the original
#     (this is the Medium failure Amalia hit in Blender)
bpy.ops.object.select_all(action="DESELECT")
sphere.select_set(True)
ctx.view_layer.objects.active = sphere
stray_path = os.path.join(tmp, "retopo_stray.obj")
sm = sphere.data
s_lo, s_hi = bbox_without_outliers(sm)[:2]
s_center = (s_lo + s_hi) * 0.5
sm.transform(Matrix.Scale(1.0 / max(s_hi - s_lo), 4) @ Matrix.Translation(-s_center))
bpy.ops.wm.obj_export(filepath=stray_path, export_selected_objects=True, export_materials=False)

bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
src2 = ctx.active_object
src2.name = "Ball"
src2.location = Vector((-4.0, 1.0, 2.0))
src2.rotation_euler = Euler((0.2, 1.3, 0.5))
src2.scale = Vector((3.0, 3.0, 3.0))
ctx.view_layer.update()

fixed, fstats = mesh_io.import_result(ctx, stray_path, src2, name="Ball_retopo",
                                      remove_fragments=False)
ctx.view_layer.update()
assert fstats["parts"] == 2 and fstats["filtered"], fstats
b_lo, b_hi = world_bbox(src2)
f_lo, f_hi = bbox_without_outliers(fixed.data)[:2]
f_pts = [fixed.matrix_world @ Vector((x, y, z))
         for x in (f_lo.x, f_hi.x) for y in (f_lo.y, f_hi.y) for z in (f_lo.z, f_hi.z)]
fw_lo = Vector([min(p[i] for p in f_pts) for i in range(3)])
fw_hi = Vector([max(p[i] for p in f_pts) for i in range(3)])
stray_err = max((b_lo - fw_lo).length, (b_hi - fw_hi).length)
assert stray_err < 1e-3, f"stray faces threw off the placement: {stray_err}"
print(f"[TEST] stray-fragment placement ok: error {stray_err:.2e}")

bpy.data.objects.remove(fixed, do_unlink=True)
bpy.data.objects.remove(src2, do_unlink=True)
bpy.data.objects.remove(sphere, do_unlink=True)

# --- FBX result path: Tripo returns quad results as FBX, which used to be
# rejected as an unknown ".bin" format
bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12)
fbx_source = ctx.active_object
bpy.ops.object.select_all(action="DESELECT")
fbx_source.select_set(True)
ctx.view_layer.objects.active = fbx_source
fbx_path = os.path.join(tmp, "retopo_result.fbx")
bpy.ops.export_scene.fbx(filepath=fbx_path, use_selection=True)
bpy.data.objects.remove(fbx_source, do_unlink=True)

with open(fbx_path, "rb") as f:
    assert scenario_client.detect_extension(f.read(256)) == ".fbx", "real FBX not detected"

bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12)
fbx_target = ctx.active_object
fbx_target.name = "FbxBall"
fbx_target.location = Vector((6.0, -3.0, 0.5))
fbx_target.rotation_euler = Euler((0.9, 0.1, 0.4))
fbx_target.scale = Vector((1.5, 1.5, 1.5))
ctx.view_layer.update()

fbx_obj, fbx_stats = mesh_io.import_result(ctx, fbx_path, fbx_target, name="FbxBall_retopo")
ctx.view_layer.update()
assert fbx_obj.name == "FbxBall_retopo", fbx_obj.name
assert fbx_stats["faces"] > 100, fbx_stats
t_lo, t_hi = world_bbox(fbx_target)
r_lo, r_hi = world_bbox(fbx_obj)
fbx_err = max((t_lo - r_lo).length, (t_hi - r_hi).length)
assert fbx_err < 1e-3, f"FBX placement mismatch: {fbx_err}"
print(f"[TEST] FBX result imported and placed ok: {fbx_stats['faces']} faces, error {fbx_err:.2e}")
bpy.data.objects.remove(fbx_obj, do_unlink=True)
bpy.data.objects.remove(fbx_target, do_unlink=True)

# --- a result already in the right space must survive the correction unchanged,
# and the world check must confirm it
bpy.ops.mesh.primitive_monkey_add()
plain = ctx.active_object
plain.name = "PlainSource"
plain.location = Vector((-2.0, 4.0, 1.0))
plain.rotation_euler = Euler((0.4, 0.2, 1.7))
plain.scale = Vector((2.5, 2.5, 2.5))
ctx.view_layer.update()

plain_mesh_copy = plain.data.copy()
carrier = bpy.data.objects.new("carrier", plain_mesh_copy)
scene.collection.objects.link(carrier)
bpy.ops.object.select_all(action="DESELECT")
carrier.select_set(True)
ctx.view_layer.objects.active = carrier
plain_path = os.path.join(tmp, "plain_result.obj")
bpy.ops.wm.obj_export(filepath=plain_path, export_selected_objects=True, export_materials=False)
bpy.data.objects.remove(carrier, do_unlink=True)

before_co = [tuple(v.co) for v in plain.data.vertices[:20]]
plain_obj, plain_stats = mesh_io.import_result(ctx, plain_path, plain, name="Plain_retopo")
ctx.view_layer.update()
assert not plain_stats["fitted"], "an identical result needs no correction"
after_co = [tuple(v.co) for v in plain_obj.data.vertices[:20]]
assert all(abs(a[i] - b[i]) < 1e-5 for a, b in zip(before_co, after_co) for i in range(3)),     "geometry must come through unchanged"
assert plain_stats["world_ok"], plain_stats
assert plain_stats["world_residual"] < 1e-4, plain_stats
print(f"[TEST] identical result untouched, world residual {plain_stats['world_residual']:.2e}")
bpy.data.objects.remove(plain_obj, do_unlink=True)

# --- a normalized result must be corrected and pass the world check. This is
# the case that came back much too large in Blender.
bpy.ops.mesh.primitive_monkey_add()
norm_src = ctx.active_object
norm_src.name = "NormSource"
norm_src.location = Vector((5.0, 1.0, -2.0))
norm_src.rotation_euler = Euler((0.1, 0.8, 0.3))
norm_src.scale = Vector((4.0, 4.0, 4.0))
ctx.view_layer.update()

norm_copy = norm_src.data.copy()
n_lo = Vector([min(v.co[i] for v in norm_copy.vertices) for i in range(3)])
n_hi = Vector([max(v.co[i] for v in norm_copy.vertices) for i in range(3)])
norm_copy.transform(Matrix.Scale(1.0 / max(n_hi - n_lo), 4)
                    @ Matrix.Translation(-(n_lo + n_hi) * 0.5))
carrier = bpy.data.objects.new("carrier2", norm_copy)
scene.collection.objects.link(carrier)
bpy.ops.object.select_all(action="DESELECT")
carrier.select_set(True)
ctx.view_layer.objects.active = carrier
norm_path = os.path.join(tmp, "normalized_result.obj")
bpy.ops.wm.obj_export(filepath=norm_path, export_selected_objects=True, export_materials=False)
bpy.data.objects.remove(carrier, do_unlink=True)

norm_obj, norm_stats = mesh_io.import_result(ctx, norm_path, norm_src, name="Norm_retopo")
ctx.view_layer.update()
assert norm_stats["fitted"], "a normalized result must be corrected"
assert norm_stats["world_ok"], norm_stats
assert norm_stats["world_residual"] < 1e-3, norm_stats
# the object scale must not be counted twice: world size follows the original
s_lo, s_hi = world_bbox(norm_src)
n2_lo, n2_hi = world_bbox(norm_obj)
assert (s_hi - s_lo - (n2_hi - n2_lo)).length < 1e-3, ((s_hi - s_lo), (n2_hi - n2_lo))
print(f"[TEST] normalized result corrected, world residual {norm_stats['world_residual']:.2e}")
bpy.data.objects.remove(norm_obj, do_unlink=True)
bpy.data.objects.remove(norm_src, do_unlink=True)

# --- stray fragments are deleted by default, and the mesh still lands right
frag_obj, frag_stats = mesh_io.import_result(ctx, stray_path, src, name="Frag_retopo")
assert frag_stats["outlier_parts"] >= 1, frag_stats
assert frag_stats["fragments_removed"] > 0, frag_stats
assert frag_stats["world_ok"], frag_stats
# The reported counts describe what was found, so check the mesh itself
post = mesh_io.analyze_parts(frag_obj.data)
assert post["outlier_parts"] == 0, post
print(f"[TEST] stray fragments removed by default: {frag_stats['fragments_removed']} vertices")
bpy.data.objects.remove(frag_obj, do_unlink=True)

# --- keeping them is still possible, and then nothing is deleted
kept_obj, kept_stats = mesh_io.import_result(ctx, stray_path, src, name="Kept_retopo",
                                             remove_fragments=False)
assert kept_stats["fragments_removed"] == 0, kept_stats
assert kept_stats["outlier_parts"] >= 1, kept_stats
assert kept_stats["faces"] > frag_stats["faces"], (kept_stats["faces"], frag_stats["faces"])
print("[TEST] fragments can be kept on request")
bpy.data.objects.remove(kept_obj, do_unlink=True)
bpy.data.objects.remove(plain, do_unlink=True)

# Pure-python API parsers
# the client refuses a file over the model's limit before any request goes out
_client = scenario_client.ScenarioClient("k", "s")
try:
    _client.upload_3d(b"x" * 2 * 1024 * 1024, "big.glb", "model/gltf-binary", max_bytes=1024 * 1024)
    raise AssertionError("oversized upload accepted")
except scenario_client.ScenarioError as e:
    assert "limit is 1 MB" in str(e), e
assert scenario_client.extract_job_id({"job": {"jobId": "j1"}}) == "j1"
assert scenario_client.extract_job_id({"id": "j2"}) == "j2"
assert scenario_client.extract_asset_ids({"job": {"metadata": {"assetIds": ["a", "b"]}}}) == ["a", "b"]
assert scenario_client.extract_asset_ids({"job": {"result": {"assetId": "x"}}}) == ["x"]
# A failed job carries its reason under metadata: the hint says what to change,
# the error is generic plus a support id. Before, neither was read and the
# console only said "Job failed: failure".
failed = {"job": {"status": "failure", "metadata": {
    "error": "An internal error occurred. Please contact support and provide this id: error_X",
    "hint": "Reduce the face_limit parameter to a value between 500 and 10000 for this model.",
}}}
reason, detail = scenario_client.job_failure_reason(failed)
assert reason.startswith("Reduce the face_limit"), reason
assert "error_X" in detail, detail
reason, detail = scenario_client.job_failure_reason(
    {"job": {"status": "failure", "metadata": {"error": "boom", "hint": None}}})
assert (reason, detail) == ("boom", ""), (reason, detail)
assert scenario_client.job_failure_reason({"job": {"status": "failure"}}) == ("", "")
assert scenario_client.job_failure_reason({"status": "failure", "metadata": {"hint": " h "}}) == ("h", "")
assert scenario_client.detect_extension(b"# Blender\nv 1 2 3\n") == ".obj"
assert scenario_client.detect_extension(b"glTF\x02\x00\x00\x00") == ".glb"
# Tripo returns quad results as FBX; before, this fell through as ".bin"
assert scenario_client.detect_extension(b"Kaydara FBX Binary  \x00\x1a\x00") == ".fbx"
assert scenario_client.detect_extension(b"; FBX 7.4.0 project file\n") == ".fbx"
assert scenario_client.detect_extension(b"\x00\x01\x02\x03nonsense") == ".bin"
described = scenario_client.describe_bytes(b"\x00\x01ABC", "application/octet-stream")
assert "application/octet-stream" in described and "0001414243" in described, described
assert scenario_client.MIME_TO_EXT["model/fbx"] == ".fbx"
# an octet-stream mime must not win over the magic bytes
assert scenario_client.MIME_TO_EXT["application/octet-stream"] is None
fbx_pick = scenario_client.pick_mesh_asset([
    ("x", {"mimeType": "image/png", "url": "u0", "kind": "image"}),
    ("f", {"mimeType": "model/fbx", "url": "u1", "kind": "3d"}),
])
assert fbx_pick[0] == "f", fbx_pick
picked = scenario_client.pick_mesh_asset([
    ("g", {"mimeType": "model/gltf-binary", "url": "u1", "kind": "3d"}),
    ("o", {"mimeType": "model/obj", "url": "u2", "kind": "3d"}),
])
assert picked[0] == "o"
print("[TEST] client parsers ok")

# --- history: written the moment the job id exists, because that is the only
# thing that survives a crash. Redirected to a temp folder so the test never
# touches the real history.
hist_dir = tempfile.mkdtemp(prefix="sb_history_")
history.directory = lambda: hist_dir
history.new_session()
assert history.entries(force=True) == []

history.add("job_1", name="Scan_retopo", model="Hunyuan PolyGen 1.5",
            source_object="Scan", blend_file="")
entry = history.get("job_1")
assert entry["status"] == history.STATUS_RUNNING, entry
assert entry["size_mb"] == 0.0, entry
# no project yet, so the entry remembers which document it belongs to
assert entry["session"] == history.SESSION, entry
history.update("job_1", status=history.STATUS_FINISHED, size_mb=1.25)
assert history.get("job_1")["status"] == history.STATUS_FINISHED
assert history.get("job_1")["size_mb"] == 1.25
# A small result must not round down to zero, that is the "not downloaded" value
history.update("job_1", size_mb=round(5000 / 1024 / 1024, 6))
assert history.get("job_1")["size_mb"] > 0.0, history.get("job_1")
assert panel._pretty_size(history.get("job_1")["size_mb"]) == "5 KB"
assert panel._pretty_size(0.0) == "size unknown"
assert panel._pretty_size(1.5) == "1.50 MB"
history.update("job_1", size_mb=1.25)

# Saving claims the unsaved jobs of this document only. A second document
# (File > New, or another Blender instance) must not adopt them.
history.add("job_2", name="Other_retopo", model="Tripo Retopology",
            source_object="Other", blend_file="")
history.new_session()
history.add("job_3", name="Third_retopo", model="Tripo Retopology",
            source_object="Third", blend_file="")
assert history.claim_unsaved("C:/projects/haus.blend") == 1
assert history.get("job_3")["blend_file"] == "C:/projects/haus.blend"
assert history.get("job_3")["session"] == ""
assert history.get("job_1")["blend_file"] == "", "another document must not be claimed"
assert len(history.for_project("C:/projects/haus.blend")) == 1
assert len(history.for_project("")) == 2

# The panel list is built from the file, filtered by project
scene.sb_ai_retopo.history_this_project = False
assert history.sync(ctx) == 3
assert {i.name for i in ctx.window_manager.sb_ai_retopo_history} == {
    "Scan_retopo", "Other_retopo", "Third_retopo"}
assert {i.model for i in ctx.window_manager.sb_ai_retopo_history} == {
    "Hunyuan PolyGen 1.5", "Tripo Retopology"}
scene.sb_ai_retopo.history_this_project = True
# this file was never saved, so only the two jobs without a project show
assert history.sync(ctx) == 2, [i.name for i in ctx.window_manager.sb_ai_retopo_history]

# Oldest entries fall out instead of growing without end
for n in range(history.MAX_ENTRIES + 5):
    history.add(f"bulk_{n}", name=f"Bulk_{n}", model="Tripo Retopology",
                source_object="Bulk", blend_file="C:/projects/haus.blend")
assert len(history.entries(force=True)) == history.MAX_ENTRIES

# A damaged file must not raise, the add-on starts a new history instead
with open(history.path(), "w", encoding="utf-8") as f:
    f.write("{ this is not json")
assert history.entries(force=True) == []
print("[TEST] history ok")

# Operator poll / credentials guard. Earlier blocks deleted their objects, so
# make the source mesh active again first.
bpy.ops.object.select_all(action="DESELECT")
src.select_set(True)
ctx.view_layer.objects.active = src
assert bpy.ops.sb.ai_retopo.poll(), "operator should be available for active mesh in object mode"
prefs.scenario_api_key = ""
prefs.scenario_api_secret = ""
os.environ.pop("SCENARIO_API_KEY", None)
os.environ.pop("SCENARIO_API_SECRET", None)
try:
    res = bpy.ops.sb.ai_retopo()
except RuntimeError as e:
    # In background mode an operator ERROR report is raised as RuntimeError
    assert "API key" in str(e), e
    res = {"CANCELLED"}
assert res == {"CANCELLED"}, res
assert not operators.is_running()
print("[TEST] operator credential guard ok")

# --- job pump: an app timer drains the worker's events in the main thread, so
# a job outlives the window and the file it was started in. Simulated with
# jobs whose thread is a stand-in; the events are put in by hand.
history.entries(force=True)
history.new_session()


class _FakeThread:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


def fake_job(source, job_id=None, alive=True):
    job = operators._Job(source_name=source, model_label="Tripo Retopology", job_id=job_id)
    job.temp_dir = tempfile.mkdtemp(prefix="sb_pump_")
    job.thread = _FakeThread(alive)
    operators._jobs.append(job)
    return job


def result_copy(job):
    dst = os.path.join(job.temp_dir, "retopo_result.obj")
    with open(obj_path, "rb") as fi, open(dst, "wb") as fo:
        fo.write(fi.read())
    return dst


assert operators._pump() is None, "no jobs, the timer must end"
settings = scene.sb_ai_retopo

# A job of this (unsaved) file: the id goes into the history with this
# document's key, the result is imported here.
job = fake_job("Scan")
assert job.belongs_to_open_file()
job.events.put(("job", {"job_id": "pump_1"}))
job.events.put(("progress", {"value": 0.5, "message": "Retopology running ..."}))
assert operators._pump() == operators._TICK
assert job.progress == 0.5 and job.status == "Retopology running ..."
entry = history.get("pump_1")
assert entry["status"] == history.STATUS_RUNNING and entry["session"] == history.SESSION, entry
assert operators.jobs_for_open_file() == [job] and operators.other_jobs_count() == 0
assert operators.is_fetching("pump_1") and operators.find_job(job.token) is job
before = set(bpy.data.objects)
job.events.put(("done", {"path": result_copy(job), "size_mb": 0.5}))
assert operators._pump() is None
imported = [o for o in bpy.data.objects if o not in before]
assert len(imported) == 1 and imported[0].type == "MESH", imported
assert settings.last_result.startswith(imported[0].name), settings.last_result
assert history.get("pump_1")["status"] == history.STATUS_FINISHED
assert history.get("pump_1")["size_mb"] == 0.5
assert not operators._jobs and not os.path.exists(job.temp_dir)

# A job of another file: finished in the history, nothing imported here.
job = fake_job("Scan", job_id="pump_2")
job.blend_file = "C:/projects/other.blend"
assert not job.belongs_to_open_file()
history.add("pump_2", name="Scan_retopo", model=job.model_label, source_object="Scan",
            blend_file=job.blend_file)
assert operators.jobs_for_open_file() == [] and operators.other_jobs_count() == 1
settings.last_result = ""
before = set(bpy.data.objects)
job.events.put(("done", {"path": result_copy(job), "size_mb": 0.5}))
assert operators._pump() is None
assert set(bpy.data.objects) == before, "a result must not land in a foreign project"
assert settings.last_result == ""
assert history.get("pump_2")["status"] == history.STATUS_FINISHED
assert not operators._jobs and not os.path.exists(job.temp_dir)

# A job started in an unsaved file that was then left (File > New): the
# document key no longer matches, so it counts as another project.
job = fake_job("Scan", job_id="pump_3")
history.new_session()
assert not job.belongs_to_open_file()
operators._end(job)

# Errors and cancels reach the history, the error text with them; the panel
# of the job's own file shows the error as well.
job = fake_job("Scan", job_id="pump_4")
history.add("pump_4", name="Scan_retopo", model=job.model_label, source_object="Scan",
            blend_file="")
job.events.put(("error", {"message": "Job failed: out of credits"}))
assert operators._pump() is None
assert history.get("pump_4")["status"] == history.STATUS_FAILED
assert history.get("pump_4")["error"] == "Job failed: out of credits"
assert settings.last_error == "Job failed: out of credits"
history.sync(ctx)
assert any(i.error == "Job failed: out of credits" for i in ctx.window_manager.sb_ai_retopo_history)

job = fake_job("Scan", job_id="pump_5")
history.add("pump_5", name="Scan_retopo", model=job.model_label, source_object="Scan",
            blend_file="")
job.events.put(("cancelled", {}))
assert operators._pump() is None
assert history.get("pump_5")["status"] == history.STATUS_CANCELLED

# A thread that died without a word is reported, not waited for
job = fake_job("Scan", job_id="pump_6", alive=False)
assert operators._pump() is None
assert "stopped unexpectedly" in settings.last_error, settings.last_error

# The cancel operator finds its job by token
job = fake_job("Scan")
job.thread = threading.Thread(target=lambda: None)
job.thread.start()
assert bpy.ops.sb.ai_retopo_cancel(token=job.token) == {"FINISHED"}
assert job.cancel.is_set() and job.status == "Cancelling ..."
operators._end(job)

# Saving the unsaved file for the first time gives its jobs their project,
# exactly like the history entries get it
job = fake_job("Scan", job_id="pump_7")
assert job.blend_file == ""
saved = os.path.join(tmp, "pump.blend")
bpy.ops.wm.save_as_mainfile(filepath=saved)
assert bpy.data.filepath == saved
assert job.blend_file == saved and job.belongs_to_open_file(), job.blend_file
operators._end(job)
assert not operators._jobs
print("[TEST] job pump ok")
addon_utils.disable("ai_retopo", default_set=True)
assert "sb_ai_retopo" not in bpy.types.Scene.bl_rna.properties
print("[TEST] ALL OK")

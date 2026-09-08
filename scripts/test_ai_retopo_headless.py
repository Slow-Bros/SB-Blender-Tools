# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless smoke test for the sb_ai_retopo add-on (no network access).

Run:  blender -b --python scripts/test_ai_retopo_headless.py

Covers: registration, pre-upload cleanup, GLB export of a transformed object,
import of a simulated (normalized) result, the bounding-box safety net with its
one percent tolerance, stray fragments being ignored during measurement,
placement on the original, and the pure-python API response parsers.
"""
import os
import sys
import tempfile

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "blender"))

import addon_utils  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
mod = addon_utils.enable("sb_ai_retopo", default_set=True, persistent=False)
assert mod is not None, "add-on failed to enable"
from sb_ai_retopo import mesh_io, scenario_client, preferences  # noqa: E402

ctx = bpy.context
scene = ctx.scene
assert hasattr(scene, "sb_ai_retopo"), "scene settings missing"
prefs = preferences.get_prefs(ctx)
levels = [i.identifier for i in scene.sb_ai_retopo.bl_rna.properties["face_level"].enum_items]
assert levels == ["low", "medium", "high"], levels
assert scene.sb_ai_retopo.face_level == "medium"
poly = [i.identifier for i in scene.sb_ai_retopo.bl_rna.properties["polygon_type"].enum_items]
from sb_ai_retopo import models, scenario_client as _sc  # noqa: E402

# A user copy in the Blender config folder takes precedence over the bundled
# registry. This machine may have one, so force the bundled file to keep the
# test deterministic; the precedence itself is tested explicitly further down.
_real_user_file_path = models.user_file_path
models.user_file_path = lambda: ""
models.load()
assert models.LOADED_FROM == models.BUNDLED_FILE, models.LOADED_FROM
assert poly == [models.QUADS, models.TRIS], poly
# The level values must be exactly what the level-based API expects
assert set(levels) == set(_sc.FACE_LEVELS), (levels, _sc.FACE_LEVELS)
# The model enum is built from the registry at draw time, so its items are not
# exposed through bl_rna. Check it functionally instead: every registry key must
# be assignable, anything else must be rejected.
assert scene.sb_ai_retopo.model == models.default_key(), scene.sb_ai_retopo.model
for spec in models.MODELS:
    scene.sb_ai_retopo.model = spec["key"]
    assert scene.sb_ai_retopo.model == spec["key"], spec["key"]
try:
    scene.sb_ai_retopo.model = "not-a-model"
    raise AssertionError("unknown model key must be rejected")
except TypeError:
    pass
scene.sb_ai_retopo.model = models.default_key()
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
            got = body[spec["count_param"]]
            assert spec["count_min"] <= got <= spec["count_max"], (spec["key"], got)
            # a value outside the model's range must be clamped, never sent raw
            low = models.build_request(spec, "a", pk, target_faces=1)
            high = models.build_request(spec, "a", pk, target_faces=10 ** 9)
            assert low[spec["count_param"]] == spec["count_min"], low
            assert high[spec["count_param"]] == spec["count_max"], high
        else:
            assert body[spec["level_param"]] == "low", body
            assert "count_param" not in spec, spec
try:
    models.build_request(models.MODELS[0], "a", "bogus")
    raise AssertionError("unknown polygon key must raise")
except ValueError:
    pass
assert models.get("does-not-exist")["key"] == models.default_key()
assert models.LOADED_FROM == models.BUNDLED_FILE, models.LOADED_FROM
assert not models.LOAD_ERROR, models.LOAD_ERROR
assert "model_meshy-remesh" not in models.known_ids(), "Meshy was removed from the registry"
print(f"[TEST] model registry ok from {models.LOADED_FROM}: {[m['id'] for m in models.MODELS]}")

# --- registry is data: a JSON file drives it, and a broken file cannot brick it
import json as _json  # noqa: E402
reg_tmp = tempfile.mkdtemp(prefix="sb_registry_")
good = os.path.join(reg_tmp, "good.json")
with open(good, "w", encoding="utf-8") as f:
    _json.dump({"models": [{
        "key": "custom", "id": "model_custom-x", "label": "Custom",
        "density": "count", "file_param": "model", "polygon_param": "topology",
        "polygon_values": {"quads": "quad", "tris": "triangle"},
        "count_param": "n", "count_min": 10, "count_max": 20,
    }]}, f)
loaded = models._read(good)
assert loaded[0]["count_default"] == 10, loaded          # filled in from count_min
assert loaded[0]["extra"] == {}, loaded                  # optional keys defaulted
assert loaded[0]["description"] == "Custom", loaded
for broken in ({"models": []},
               {"models": [{"key": "a"}]},
               {"models": [{**loaded[0], "count_min": 99, "count_max": 1}]},
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

# --- catalogue parsing must survive the response shape being different
from sb_ai_retopo.scenario_client import extract_models, extract_cursor  # noqa: E402
assert extract_models({"models": [{"id": "a", "name": "A"}]}) == [("a", "A")]
assert extract_models({"data": [{"modelId": "b", "displayName": "B"}]}) == [("b", "B")]
assert extract_models([{"id": "c"}]) == [("c", "")]
assert extract_models({"unexpected": 1}) == []
assert extract_models("nonsense") == []
assert extract_cursor({"nextPaginationToken": "t"}) == "t"
assert extract_cursor({"models": []}) is None

# --- comparing the registry against a catalogue
cat = [(m["id"], m["label"]) for m in models.MODELS]
assert models.classify({i for i, _ in cat})["missing"] == []
assert models.classify(set())["missing"] == []          # unknown catalogue accuses nobody
gone = models.classify({models.MODELS[0]["id"]})["missing"]
assert gone == [m["id"] for m in models.MODELS[1:]], gone
unknown = models.unknown_candidates(cat + [("model_acme-retopo", "Acme Retopo"),
                                           ("model_acme-texture", "Acme Texture")])
assert unknown == [("model_acme-retopo", "Acme Retopo")], unknown
print("[TEST] catalogue parsing + comparison ok")

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

# Pre-decimate export
glb2 = os.path.join(tmp, "upload_dec.glb")
info2 = mesh_io.export_object_for_upload(ctx, src, glb2, decimate_target=500)
assert os.path.getsize(glb2) < os.path.getsize(glb), "pre-decimate did not shrink file"
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

# --- fit_matrix: diagonal comparison with a one percent tolerance
lo, hi = Vector((0, 0, 0)), Vector((2, 1, 1))
m, i = mesh_io.fit_matrix(lo, hi, lo, hi)
assert m is None and not i["scaled"] and not i["moved"], i
m, i = mesh_io.fit_matrix(lo, hi, lo, hi * 0.995)  # 0.5 % off, inside tolerance
assert m is None, i
m, i = mesh_io.fit_matrix(lo, hi, lo, hi * 0.5)    # 100 % off, must be corrected
assert m is not None and i["scaled"] and i["moved"], i
assert (m @ lo - lo).length < 1e-6 and (m @ (hi * 0.5) - hi).length < 1e-6
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

settings = scene.sb_ai_retopo
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


slo, shi = world_bbox(src)
nlo, nhi = world_bbox(new)
err = max((slo - nlo).length, (shi - nhi).length)
assert err < 1e-3, f"placement mismatch: {err}"
# Suzanne is legitimately 3 parts (head + two eyes) that sit inside the head's
# box, so nothing may be treated as an outlier here
assert stats["parts"] == 3 and stats["outlier_parts"] == 0 and not stats["filtered"], stats
print(f"[TEST] import + placement ok, bbox error {err:.2e}, parts {stats['parts']}")

# Second import with an explicit name, source hidden afterwards
new2, stats2 = mesh_io.import_result(ctx, obj_path, src, name="Scan_retopo_2", hide_source=True)
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

new3, stats3 = mesh_io.import_result(ctx, stray_path, src, name="Scan_retopo_stray")
ctx.view_layer.update()
# Suzanne itself is 3 parts (head + two eyes), the stray cube is the 4th.
# Only the cube sticks out of the head's box, so only it may be excluded.
assert stats3["parts"] == 4, stats3
assert stats3["outlier_parts"] == 1 and stats3["filtered"], stats3
mlo, mhi, _ = mesh_io.bbox_without_outliers(new3.data)
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

# --- fit_matrix: Phototron's one percent tolerance, measured on the diagonal
lo0, hi0 = Vector((-1, -1, -1)), Vector((1, 1, 1))
m, fi = mesh_io.fit_matrix(lo0, hi0, lo0, hi0)
assert m is None and not fi["scaled"] and not fi["moved"], fi
m, fi = mesh_io.fit_matrix(lo0, hi0, lo0 * 0.995, hi0 * 0.995)
assert m is None, f"0.5 percent must stay untouched: {fi}"
m, fi = mesh_io.fit_matrix(lo0, hi0, lo0 * 0.95, hi0 * 0.95)
assert m is not None and fi["scaled"], f"5 percent must be corrected: {fi}"
m, fi = mesh_io.fit_matrix(lo0, hi0, lo0 + Vector((0.5, 0, 0)), hi0 + Vector((0.5, 0, 0)))
assert m is not None and fi["moved"] and not fi["scaled"], fi
print("[TEST] fit_matrix tolerances ok")

# --- bbox_without_outliers: a few stray faces must not inflate the measurement
bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, location=(0, 0, 0))
sphere = ctx.active_object
bpy.ops.mesh.primitive_cube_add(size=0.05, location=(10, 0, 0))
fragment = ctx.active_object
with ctx.temp_override(object=sphere, active_object=sphere, selected_objects=[sphere, fragment],
                       selected_editable_objects=[sphere, fragment]):
    bpy.ops.object.join()
full_lo, full_hi = mesh_io._mesh_bbox(sphere.data)
main_lo, main_hi, pinfo = mesh_io.bbox_without_outliers(sphere.data)
assert pinfo["parts"] == 2 and pinfo["filtered"], pinfo
assert (full_hi - full_lo).length > 9.0, "full bbox should be inflated by the fragment"
assert (main_hi - main_lo).length < 3.6, (main_lo, main_hi)
print(f"[TEST] bbox_without_outliers ok: {pinfo}")

# --- regression: a result with stray faces must still land on the original
#     (this is the Medium failure Amalia hit in Blender)
bpy.ops.object.select_all(action="DESELECT")
sphere.select_set(True)
ctx.view_layer.objects.active = sphere
stray_path = os.path.join(tmp, "retopo_stray.obj")
sm = sphere.data
s_lo, s_hi = mesh_io.bbox_without_outliers(sm)[:2]
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

fixed, fstats = mesh_io.import_result(ctx, stray_path, src2, name="Ball_retopo")
ctx.view_layer.update()
assert fstats["parts"] == 2 and fstats["filtered"], fstats
b_lo, b_hi = world_bbox(src2)
f_lo, f_hi = mesh_io.bbox_without_outliers(fixed.data)[:2]
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

# Pure-python API parsers
assert scenario_client.extract_job_id({"job": {"jobId": "j1"}}) == "j1"
assert scenario_client.extract_job_id({"id": "j2"}) == "j2"
assert scenario_client.extract_asset_ids({"job": {"metadata": {"assetIds": ["a", "b"]}}}) == ["a", "b"]
assert scenario_client.extract_asset_ids({"job": {"result": {"assetId": "x"}}}) == ["x"]
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
try:
    scenario_client.ScenarioClient("", "")
    raise AssertionError("missing credentials must raise")
except scenario_client.ScenarioError:
    pass
print("[TEST] client parsers ok")

# Operator poll / credentials guard. Earlier blocks deleted their objects, so
# make the source mesh active again first.
bpy.ops.object.select_all(action="DESELECT")
src.select_set(True)
ctx.view_layer.objects.active = src
assert bpy.ops.sb.ai_retopo.poll(), "operator should be available for active mesh in object mode"
prefs.api_key = ""
prefs.api_secret = ""
os.environ.pop("SCENARIO_API_KEY", None)
os.environ.pop("SCENARIO_API_SECRET", None)
try:
    res = bpy.ops.sb.ai_retopo()
except RuntimeError as e:
    # In background mode an operator ERROR report is raised as RuntimeError
    assert "API key" in str(e), e
    res = {"CANCELLED"}
assert res == {"CANCELLED"}, res
assert not scene.sb_ai_retopo.running
print("[TEST] operator credential guard ok")

# --- a model the catalogue no longer offers must be refused before uploading
prefs.api_key = "dummy-key"
prefs.api_secret = "dummy-secret"
prefs.set_catalogue([("model_something-else", "Other")])
assert prefs.available_ids() == {"model_something-else"}
for o in bpy.data.objects:
    o.select_set(False)
bpy.ops.mesh.primitive_cube_add()
guard_obj = ctx.active_object
try:
    res = bpy.ops.sb.ai_retopo()
except RuntimeError as e:
    assert "not offered by the API any more" in str(e), e
    res = {"CANCELLED"}
assert res == {"CANCELLED"}, res
assert "not offered" in scene.sb_ai_retopo.last_error, scene.sb_ai_retopo.last_error
assert not scene.sb_ai_retopo.running
# an empty catalogue must never block a run
prefs.set_catalogue([])
assert prefs.available_ids() == set()
bpy.data.objects.remove(guard_obj, do_unlink=True)
prefs.api_key = ""
prefs.api_secret = ""
print("[TEST] unavailable-model guard ok")

# --- the registry can be exported and reloaded at runtime
exported = models.save_user_file(os.path.join(reg_tmp, "user_copy.json"))
assert os.path.exists(exported)
before_reload = [m["key"] for m in models.MODELS]
models.load()
assert [m["key"] for m in models.MODELS] == before_reload
print("[TEST] registry export + reload ok")

# --- a user copy shadows the bundled registry, and can be reset again
shadow = os.path.join(reg_tmp, "shadow.json")
with open(shadow, "w", encoding="utf-8") as f:
    _json.dump({"models": [{
        "key": "only_one", "id": "model_only-one", "label": "Only One",
        "density": "level", "file_param": "file3d", "polygon_param": "polygonType",
        "polygon_values": {"quads": "quadrilateral", "tris": "triangle"},
    }]}, f)
models.user_file_path = lambda: shadow
models.load()
assert [m["key"] for m in models.MODELS] == ["only_one"], models.MODELS
assert models.LOADED_FROM == shadow, models.LOADED_FROM
backup = models.reset_user_file()
assert backup and os.path.exists(backup) and not os.path.exists(shadow)
models.load()
assert models.LOADED_FROM == models.BUNDLED_FILE, models.LOADED_FROM
assert models.reset_user_file() is None, "resetting twice must be a no-op"
# a broken user copy must fall back instead of breaking the add-on
with open(shadow, "w", encoding="utf-8") as f:
    f.write("{ not json")
models.load()
assert models.LOADED_FROM == models.BUNDLED_FILE, models.LOADED_FROM
assert models.LOAD_ERROR, "a broken user copy must be reported"
os.remove(shadow)
models.user_file_path = _real_user_file_path
models.load()
print("[TEST] user copy precedence + reset ok")

addon_utils.disable("sb_ai_retopo", default_set=True)
assert "sb_ai_retopo" not in bpy.types.Scene.bl_rna.properties
print("[TEST] ALL OK")

# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless smoke test for the sb_ai_retopo add-on (no network access).

Run:  blender -b --python scripts/test_ai_retopo_headless.py

Covers: registration, GLB export of a transformed object, import of a
simulated (normalized) result, bounding-box fit + placement on the original,
exact-count decimation, and the pure-python API response parsers.
"""
import os
import sys
import tempfile

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
assert poly == ["quadrilateral", "triangle"], poly
# Panel values must be exactly what the API accepts, no client-side mapping
from sb_ai_retopo import scenario_client as _sc  # noqa: E402
assert set(levels) == set(_sc.FACE_LEVELS) and set(poly) == set(_sc.POLYGON_TYPES)
print("[TEST] registration + settings ok")

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
print(f"[TEST] import + placement ok: {stats}, bbox error {err:.2e}")

# Second import with an explicit name, source hidden afterwards
new2, stats2 = mesh_io.import_result(ctx, obj_path, src, name="Scan_retopo_2", hide_source=True)
assert new2.name == "Scan_retopo_2", new2.name
assert src.hide_get() and "Scan" in bpy.data.objects, "source must be hidden, not deleted"
src.hide_set(False)
print(f"[TEST] named import + hide source ok: {stats2}")

# Pure-python API parsers
assert scenario_client.extract_job_id({"job": {"jobId": "j1"}}) == "j1"
assert scenario_client.extract_job_id({"id": "j2"}) == "j2"
assert scenario_client.extract_asset_ids({"job": {"metadata": {"assetIds": ["a", "b"]}}}) == ["a", "b"]
assert scenario_client.extract_asset_ids({"job": {"result": {"assetId": "x"}}}) == ["x"]
assert scenario_client.detect_extension(b"# Blender\nv 1 2 3\n") == ".obj"
assert scenario_client.detect_extension(b"glTF\x02\x00\x00\x00") == ".glb"
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

# Operator poll / credentials guard
assert bpy.ops.sb.ai_retopo.poll(), "operator should be available for active mesh in object mode"
prefs.api_key = ""
prefs.api_secret = ""
os.environ.pop("SCENARIO_API_KEY", None)
os.environ.pop("SCENARIO_API_SECRET", None)
try:
    res = bpy.ops.sb.ai_retopo()
except RuntimeError as e:
    # In background mode an operator ERROR report is raised as RuntimeError
    assert "API Key" in str(e), e
    res = {"CANCELLED"}
assert res == {"CANCELLED"}, res
assert not scene.sb_ai_retopo.running
print("[TEST] operator credential guard ok")

addon_utils.disable("sb_ai_retopo", default_set=True)
assert "sb_ai_retopo" not in bpy.types.Scene.bl_rna.properties
print("[TEST] ALL OK")

# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless smoke test for the ai_uv_layout add-on (no network access).

Run:  blender -b --python scripts/test_ai_uv_layout_headless.py

Covers: registration next to ai_retopo in the shared SBTools tab, credentials
shared between both add-ons, the model registry, OBJ export of the base mesh,
the UV transfer by loop index onto a copy and onto the original, the clean
rejection of a topology mismatch, the standalone fallback, the API response
parsers, and the job history.
"""
import json
import os
import sys
import tempfile

import bpy
import numpy as np
from mathutils import Euler, Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "blender"))

import addon_utils  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
assert addon_utils.enable("ai_retopo", default_set=True, persistent=False) is not None, "ai_retopo failed to enable"
assert addon_utils.enable("ai_uv_layout", default_set=True, persistent=False) is not None, "ai_uv_layout failed to enable"
from ai_uv_layout import credentials, history, mesh_io, models, panel, preferences, scenario_client  # noqa: E402
from ai_retopo import credentials as retopo_credentials  # noqa: E402
from ai_retopo import panel as retopo_panel  # noqa: E402
from ai_retopo import preferences as retopo_preferences  # noqa: E402

ctx = bpy.context
scene = ctx.scene
assert hasattr(scene, "sb_ai_uv"), "scene settings missing"
assert hasattr(scene, "sb_ai_retopo"), "both add-ons must coexist"
prefs = preferences.get_prefs(ctx)
retopo_prefs = retopo_preferences.get_prefs(ctx)

# --- shared sidebar tab: both panels live in SBTools, UV Layout below AI Retopo
assert panel.VIEW3D_PT_sb_ai_uv_layout.bl_category == "SBTools"
assert retopo_panel.VIEW3D_PT_sb_ai_retopo.bl_category == "SBTools"
assert panel.VIEW3D_PT_sb_ai_uv_layout.bl_order > retopo_panel.VIEW3D_PT_sb_ai_retopo.bl_order

# --- shared credentials: entered in one add-on, visible in the other.
# Redirected to a temp folder first thing, so the test never touches the real key.
cred_dir = tempfile.mkdtemp(prefix="sb_credentials_")
for mod in (credentials, retopo_credentials):
    mod.directory = lambda: cred_dir
    mod._cache = None
retopo_prefs.scenario_api_key = "shared-key"
retopo_prefs.scenario_api_secret = "shared-secret"
assert prefs.scenario_api_key == "shared-key"
assert preferences.get_credentials(ctx) == ("shared-key", "shared-secret")
prefs.scenario_api_secret = "changed"
assert retopo_prefs.scenario_api_secret == "changed"
assert retopo_preferences.get_credentials(ctx) == ("shared-key", "changed")
with open(os.path.join(ROOT, "blender", "ai_retopo", "credentials.py"), "rb") as f:
    _copy_a = f.read()
with open(os.path.join(ROOT, "blender", "ai_uv_layout", "credentials.py"), "rb") as f:
    _copy_b = f.read()
assert _copy_a == _copy_b, "credentials.py differs between ai_retopo and ai_uv_layout"
print("[TEST] shared SBTools tab and credentials ok")

# --- model registry: currently one model, and its request body is exactly
# what Phototron sends
assert not models.LOAD_ERROR, models.LOAD_ERROR
assert [m["id"] for m in models.MODELS] == ["model_tencent-uv-unwrapping"], models.MODELS
spec = models.MODELS[0]
assert models.build_request(spec, "asset1") == {"file3d": "asset1"}
assert scene.sb_ai_uv.model == spec["key"]
try:
    scene.sb_ai_uv.model = "not-a-model"
    raise AssertionError("unknown model key must be rejected")
except TypeError:
    pass
assert models.get("does-not-exist")["key"] == spec["key"]

reg_tmp = tempfile.mkdtemp(prefix="sb_registry_")
good = os.path.join(reg_tmp, "good.json")
with open(good, "w", encoding="utf-8") as f:
    json.dump({"models": [
        {"key": "a", "id": "model_a", "label": "A", "file_param": "file3d"},
        {"key": "b", "id": "model_b", "label": "B", "file_param": "model", "extra": {"flag": True}},
    ]}, f)
loaded = models._read(good)
assert loaded[0]["extra"] == {} and loaded[0]["description"] == "A", loaded
assert models.build_request(loaded[1], "x") == {"model": "x", "flag": True}
for broken in ({"models": []},
               {"models": [{"key": "a"}]},
               {"models": [loaded[0], loaded[0]]},
               {"models": [{**loaded[0], "extra": "nope"}]}):
    bad = os.path.join(reg_tmp, "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        json.dump(broken, f)
    try:
        models._read(bad)
        raise AssertionError(f"invalid registry accepted: {broken}")
    except ValueError:
        pass
print("[TEST] model registry ok")

# --- source object: a cube (6 quads) with a subsurf modifier, transformed, in
# its own collection, with a material. The primitive comes with a UV map, so
# this one also covers overwriting an existing map.
bpy.ops.mesh.primitive_cube_add()
src = ctx.active_object
src.name = "Retopo"
src.modifiers.new("Subsurf", "SUBSURF").levels = 2
src.data.materials.append(bpy.data.materials.new("Mat"))
src.location = Vector((3.0, -2.0, 1.5))
src.rotation_euler = Euler((0.3, 0.7, 1.1))
src.scale = Vector((2.0, 2.0, 2.0))
coll = bpy.data.collections.new("Retopo")
scene.collection.children.link(coll)
coll.objects.link(src)
scene.collection.objects.unlink(src)
ctx.view_layer.update()
assert src.data.uv_layers.active is not None, "the cube primitive should carry a UV map"
src_uv_before = np.empty(len(src.data.loops) * 2, dtype=np.float32)
src.data.uv_layers.active.data.foreach_get("uv", src_uv_before)

tmp = tempfile.mkdtemp(prefix="sb_uv_test_")
obj_path = os.path.join(tmp, "upload.obj")
info = mesh_io.export_object_for_upload(ctx, src, obj_path)
assert info["faces"] == 6 and info["loops"] == 24, info      # the base mesh, not the subdivided one
assert "Retopo_sb_uv_upload" not in bpy.data.objects, "temp object not cleaned up"
assert src.select_get() and ctx.view_layer.objects.active == src, "selection not restored"
with open(obj_path, encoding="utf-8") as f:
    lines = f.read().splitlines()
assert sum(l.startswith("f ") for l in lines) == 6, "OBJ must carry the 6 base faces"
assert not any(l.startswith("vt ") for l in lines), "existing UVs must not be uploaded"
assert not any(l.startswith("mtllib") for l in lines), "no material file"
print(f"[TEST] export ok: {info}")

# --- simulate the API result: re-import the upload, give it a distinct UV
# layout, normalise the geometry as the model does, and export as OBJ
before = set(bpy.data.objects)
bpy.ops.wm.obj_import(filepath=obj_path)
sim = [o for o in bpy.data.objects if o not in before and o.type == "MESH"][0]
me = sim.data
n_loops = len(me.loops)
assert n_loops == 24, n_loops
layer = me.uv_layers.new(name="UVMap")
idx = np.arange(n_loops, dtype=np.float32)
expected = np.stack([idx / n_loops + 0.01, (idx % 7) / 7.0 + 0.02], axis=1).astype(np.float32)
layer.data.foreach_set("uv", expected.ravel())
lo = Vector([min(v.co[i] for v in me.vertices) for i in range(3)])
hi = Vector([max(v.co[i] for v in me.vertices) for i in range(3)])
me.transform(Matrix.Translation(Vector((0.2, -0.1, 0.05))) @ Matrix.Scale(1.0 / max(hi - lo), 4)
             @ Matrix.Translation(-(lo + hi) * 0.5))
for o in bpy.data.objects:
    o.select_set(o == sim)
result_path = os.path.join(tmp, "uv_result.obj")
bpy.ops.wm.obj_export(filepath=result_path, export_selected_objects=True, export_materials=False)
bpy.data.objects.remove(sim, do_unlink=True)


def read_uv(obj):
    buf = np.empty(len(obj.data.loops) * 2, dtype=np.float32)
    obj.data.uv_layers.active.data.foreach_get("uv", buf)
    return buf.reshape(-1, 2)


def read_co(obj):
    buf = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", buf)
    return buf.reshape(-1, 3)


# --- default path: UVs onto a copy, geometry and placement of the original
mesh_objects_before = {o.name for o in bpy.data.objects}
uv_obj = mesh_io.import_uv_mesh(ctx, result_path)
assert uv_obj.data.uv_layers.active is not None
assert mesh_io.topology_matches(src.data, uv_obj.data)
new, stats = mesh_io.apply_uvs(ctx, uv_obj, src, name="Retopo_uv")
ctx.view_layer.update()
assert new.name == "Retopo_uv" and new is not src, new.name
assert stats["method"] == mesh_io.METHOD_INDEX, stats
assert stats["coverage"] > 0.99 and stats["faces"] == 6 and stats["quads"] == 6, stats
assert not stats["applied_to_source"]
assert {o.name for o in bpy.data.objects} == mesh_objects_before | {"Retopo_uv"}, "UV carrier must be removed"
assert new.matrix_world == src.matrix_world and new.parent == src.parent
assert [c.name for c in new.users_collection] == ["Retopo"], [c.name for c in new.users_collection]
assert np.allclose(read_co(new), read_co(src)), "geometry must stay the original's"
assert np.allclose(read_uv(new), expected, atol=1e-5), "UVs must be the model's, corner for corner"
assert np.allclose(read_uv(src).ravel(), src_uv_before), "the original must keep its own UVs"
assert [m.type for m in new.modifiers] == ["SUBSURF"], "modifiers travel with the copy"
assert "sharp_face" not in new.data.attributes, "smooth shading"
assert ctx.view_layer.objects.active == new and new.select_get() and not src.select_get()
assert not src.hide_get()
print(f"[TEST] UV transfer onto a copy ok: {stats}")

# --- apply to the original: a mesh without any UV map gets 'UVMap'
uv_obj = mesh_io.import_uv_mesh(ctx, result_path)
bpy.ops.mesh.primitive_cube_add(calc_uvs=False)
plain = ctx.active_object
plain.name = "Plain"
assert not plain.data.uv_layers
same, stats2 = mesh_io.apply_uvs(ctx, uv_obj, plain, name="Plain_uv",
                                 apply_to_source=True, hide_source=True)
assert same is plain and stats2["applied_to_source"], stats2
assert stats2["layer"] == "UVMap" and stats2["method"] == mesh_io.METHOD_INDEX, stats2
assert "Plain_uv" not in bpy.data.objects
assert not plain.hide_get(), "hide_source means nothing when the original is the target"
assert np.allclose(read_uv(plain), expected, atol=1e-5)
print(f"[TEST] UV transfer onto the original ok: {stats2}")

# --- hide the original on the copy path
uv_obj = mesh_io.import_uv_mesh(ctx, result_path)
new2, _ = mesh_io.apply_uvs(ctx, uv_obj, src, name="Retopo_uv_2", hide_source=True)
assert new2.name == "Retopo_uv_2"
assert src.hide_get() and "Retopo" in bpy.data.objects, "source must be hidden, not deleted"
src.hide_set(False)
bpy.data.objects.remove(new2, do_unlink=True)
print("[TEST] hide original ok")

# --- topology mismatch is an error, not a fallback. Phototron's Data Transfer
# fallback with topology mapping cannot transfer anything between different
# topologies, so it was dropped in favour of a message that names the numbers.
# Nothing may be left behind: no copy, no carrier, no new UV map on the target.
bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=4)
ball = ctx.active_object
ball.name = "Ball"
ball_uv_before = read_uv(ball)      # the primitive has its own UVs, they must survive untouched
objects_before = {o.name for o in bpy.data.objects}
uv_obj = mesh_io.import_uv_mesh(ctx, result_path)
assert not mesh_io.topology_matches(ball.data, uv_obj.data)
try:
    mesh_io.apply_uvs(ctx, uv_obj, ball, name="Ball_uv")
    raise AssertionError("a topology mismatch must be rejected")
except mesh_io.MeshIOError as e:
    assert "6 faces / 24 corners" in str(e) and "32 faces / 112 corners" in str(e), e
assert {o.name for o in bpy.data.objects} == objects_before, "copy and carrier must be cleaned up"
assert np.array_equal(read_uv(ball), ball_uv_before), "the original must be untouched"
# the same onto the original itself, without a UV map: none may be created
bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=4, calc_uvs=False)
bare = ctx.active_object
bare.name = "Bare"
uv_obj = mesh_io.import_uv_mesh(ctx, result_path)
try:
    mesh_io.apply_uvs(ctx, uv_obj, bare, name="Bare_uv", apply_to_source=True)
    raise AssertionError("a topology mismatch must be rejected")
except mesh_io.MeshIOError:
    pass
assert not bare.data.uv_layers, "a failed transfer must not leave a UV map behind"
assert not bare.modifiers
print("[TEST] topology mismatch rejected cleanly")

# --- same face and corner count but different face sizes is not a match
# (stricter than Phototron, which compares only the two counts)
ma = bpy.data.meshes.new("two_quads")
ma.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0), (2, 1, 0)], [],
               [(0, 1, 2, 3), (1, 4, 5, 2)])
mb = bpy.data.meshes.new("tri_penta")
mb.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0), (2, 0, 0), (3, 0, 0), (3, 1, 0), (2, 1, 0)], [],
               [(0, 1, 2), (1, 3, 4, 5, 6)])
assert len(ma.polygons) == len(mb.polygons) == 2 and len(ma.loops) == len(mb.loops) == 8
assert not mesh_io.topology_matches(ma, mb)
assert mesh_io.topology_matches(ma, ma)
bpy.data.meshes.remove(ma)
bpy.data.meshes.remove(mb)
print("[TEST] topology check ok")

# --- standalone: history import when no mesh with matching topology is left
uv_obj = mesh_io.import_uv_mesh(ctx, result_path)
alone, stats4 = mesh_io.keep_standalone(ctx, uv_obj, "Gone_uv")
assert alone.name == "Gone_uv" and alone.data.name == "Gone_uv"
assert stats4["method"] == mesh_io.METHOD_STANDALONE and stats4["coverage"] > 0.99, stats4
assert ctx.view_layer.objects.active == alone
bpy.data.objects.remove(alone, do_unlink=True)
print("[TEST] standalone fallback ok")

# --- a result without UVs is rejected and leaves nothing behind
count_before = len(bpy.data.objects)
try:
    mesh_io.import_uv_mesh(ctx, obj_path)   # the upload OBJ carries no vt lines
    raise AssertionError("a result without UVs must be rejected")
except mesh_io.MeshIOError as e:
    assert "no UV map" in str(e), e
assert len(bpy.data.objects) == count_before, "rejected import must be cleaned up"
print("[TEST] result without UVs rejected")

# --- pure-python API parsers
assert scenario_client.extract_job_id({"job": {"jobId": "j1"}}) == "j1"
assert scenario_client.extract_job_id({"id": "j2"}) == "j2"
assert scenario_client.extract_asset_ids({"job": {"metadata": {"assetIds": ["a", "b"]}}}) == ["a", "b"]
assert scenario_client.extract_asset_ids({"job": {"result": {"assetId": "x"}}}) == ["x"]
assert scenario_client.detect_extension(b"# Blender\nv 1 2 3\nvt 0.5 0.5\n") == ".obj"
assert scenario_client.detect_extension(b"glTF\x02\x00\x00\x00") == ".glb"
assert scenario_client.detect_extension(b"\x00\x01\x02\x03nonsense") == ".bin"
# OBJ wins over GLB: it keeps quads and face order, which the index copy needs
picked = scenario_client.pick_mesh_asset([
    ("g", {"mimeType": "model/gltf-binary", "url": "u1", "kind": "3d"}),
    ("o", {"mimeType": "model/obj", "url": "u2", "kind": "3d"}),
])
assert picked[0] == "o", picked
assert scenario_client.PREFERRED_MIMES[0] == "model/obj"
assert mesh_io.UPLOAD_CONTENT_TYPE == "model/obj"
print("[TEST] client parsers ok")

# --- history: written the moment the job id exists. Redirected to a temp
# folder so the test never touches the real history.
hist_dir = tempfile.mkdtemp(prefix="sb_uv_history_")
history.directory = lambda: hist_dir
history.new_session()
assert history.entries(force=True) == []
history.add("job_1", name="Retopo_uv", model="Hunyuan UV Unwrapping",
            source_object="Retopo", blend_file="")
entry = history.get("job_1")
assert entry["status"] == history.STATUS_RUNNING and entry["session"] == history.SESSION, entry
history.update("job_1", status=history.STATUS_FINISHED, size_mb=round(5000 / 1024 / 1024, 6))
assert history.get("job_1")["status"] == history.STATUS_FINISHED
assert panel._pretty_size(history.get("job_1")["size_mb"]) == "5 KB"
history.new_session()
history.add("job_2", name="Other_uv", model="Hunyuan UV Unwrapping",
            source_object="Other", blend_file="")
assert history.claim_unsaved("C:/projects/haus.blend") == 1
assert history.get("job_2")["blend_file"] == "C:/projects/haus.blend"
assert history.get("job_1")["blend_file"] == "", "another document must not be claimed"
scene.sb_ai_uv.history_this_project = False
assert history.sync(ctx) == 2
assert {i.name for i in ctx.window_manager.sb_ai_uv_history} == {"Retopo_uv", "Other_uv"}
scene.sb_ai_uv.history_this_project = True
assert history.sync(ctx) == 1, [i.name for i in ctx.window_manager.sb_ai_uv_history]
# the two add-ons keep separate histories
from ai_retopo import history as retopo_history  # noqa: E402
assert retopo_history.path() != history.path()
with open(history.path(), "w", encoding="utf-8") as f:
    f.write("{ this is not json")
assert history.entries(force=True) == []
print("[TEST] history ok")

# --- operator poll / credentials guard
bpy.ops.object.select_all(action="DESELECT")
src.select_set(True)
ctx.view_layer.objects.active = src
assert bpy.ops.sb.ai_uv_layout.poll(), "operator should be available for active mesh in object mode"
prefs.scenario_api_key = ""
prefs.scenario_api_secret = ""
os.environ.pop("SCENARIO_API_KEY", None)
os.environ.pop("SCENARIO_API_SECRET", None)
try:
    res = bpy.ops.sb.ai_uv_layout()
except RuntimeError as e:
    # In background mode an operator ERROR report is raised as RuntimeError
    assert "API key" in str(e), e
    res = {"CANCELLED"}
assert res == {"CANCELLED"}, res
assert not scene.sb_ai_uv.running
# emptied here, empty there: it is the same store
assert retopo_preferences.get_credentials(ctx) == ("", "")
print("[TEST] operator credential guard ok")

addon_utils.disable("ai_uv_layout", default_set=True)
assert "sb_ai_uv" not in bpy.types.Scene.bl_rna.properties
assert "sb_ai_retopo" in bpy.types.Scene.bl_rna.properties, "disabling one add-on must not touch the other"
addon_utils.disable("ai_retopo", default_set=True)
print("[TEST] ALL OK")

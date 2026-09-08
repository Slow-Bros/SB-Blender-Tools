# SPDX-License-Identifier: GPL-3.0-or-later
"""Mesh-Export fuer den Upload und Import/Platzierung des Ergebnisses.

Laeuft ausschliesslich im Blender-Hauptthread (bpy-Zugriff).

Koordinaten: Das Quellobjekt wird im lokalen Raum (Objekttransform = Identity)
als GLB exportiert. Das Ergebnis wird importiert, per Bounding-Box auf die
lokale Bounding-Box des Originals eingepasst (Hunyuan normalisiert das Mesh
u.U. auf einen Einheitswuerfel) und bekommt anschliessend die Welt-Matrix des
Originals. Damit landet es exakt an der urspruenglichen Position.
"""

import os

import bpy
import numpy as np
from mathutils import Matrix, Vector


class MeshIOError(Exception):
    pass


# -- Export ---------------------------------------------------------------

def export_object_for_upload(context, obj, glb_path, decimate_target=0):
    """Exportiert eine bereinigte Kopie von *obj* (Modifier angewendet, ohne
    Materialien/Farb-Attribute, lokaler Raum) als GLB.

    Returns: dict mit Statistik (faces, faces_exported, bytes)
    """
    if obj is None or obj.type != "MESH":
        raise MeshIOError("Bitte ein Mesh-Objekt auswaehlen.")

    depsgraph = context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(eval_obj, preserve_all_data_layers=False, depsgraph=depsgraph)
    if len(mesh.polygons) == 0:
        bpy.data.meshes.remove(mesh)
        raise MeshIOError(f"'{obj.name}' enthaelt keine Faces.")

    mesh.name = f"{obj.name}_sb_upload"
    faces_before = len(mesh.polygons)

    # Farb-Attribute und Materialien entfernen (nur Geometrie wird gebraucht,
    # eingebettete Texturen wuerden die GLB sonst riesig machen)
    for attr in list(mesh.color_attributes):
        mesh.color_attributes.remove(attr)
    for uv in list(mesh.uv_layers):
        mesh.uv_layers.remove(uv)
    mesh.materials.clear()

    temp = bpy.data.objects.new(mesh.name, mesh)
    temp.matrix_world = Matrix.Identity(4)
    context.scene.collection.objects.link(temp)

    if decimate_target and faces_before > decimate_target:
        mod = temp.modifiers.new("SB_PreDecimate", "DECIMATE")
        mod.ratio = decimate_target / faces_before

    prev_active = context.view_layer.objects.active
    prev_selected = [o for o in context.view_layer.objects if o.select_get()]
    try:
        for o in prev_selected:
            o.select_set(False)
        temp.select_set(True)
        context.view_layer.objects.active = temp

        kwargs = dict(
            filepath=glb_path,
            export_format="GLB",
            use_selection=True,
            export_apply=True,
            export_materials="NONE",
            export_yup=True,
            export_normals=False,
            export_texcoords=False,
            export_animations=False,
            export_skins=False,
            export_morph=False,
        )
        # Versionsabhaengige Optionen defensiv setzen
        props = bpy.ops.export_scene.gltf.get_rna_type().properties.keys()
        for key, value in (
            ("export_vertex_color", "NONE"),
            ("export_image_format", "NONE"),
            ("export_extras", False),
            ("export_lights", False),
            ("export_cameras", False),
        ):
            if key in props:
                kwargs[key] = value

        result = bpy.ops.export_scene.gltf(**kwargs)
        if "FINISHED" not in result:
            raise MeshIOError(f"glTF-Export fehlgeschlagen: {result}")

        faces_exported = min(faces_before, decimate_target) if decimate_target else faces_before
    finally:
        for o in prev_selected:
            try:
                o.select_set(True)
            except ReferenceError:
                pass
        if prev_active is not None:
            try:
                context.view_layer.objects.active = prev_active
            except ReferenceError:
                pass
        bpy.data.objects.remove(temp, do_unlink=True)
        bpy.data.meshes.remove(mesh)

    if not os.path.exists(glb_path):
        raise MeshIOError("GLB-Datei wurde nicht erstellt.")

    return {
        "faces": faces_before,
        "faces_exported": faces_exported,
        "bytes": os.path.getsize(glb_path),
    }


# -- Import ---------------------------------------------------------------

def _mesh_bbox(mesh):
    """Bounding-Box (min, max) aus den Vertex-Koordinaten eines Meshes.

    Bewusst nicht Object.bound_box: das liefert bei Subdivision-Modifiern nur
    die Bounds des Kontroll-Cages, nicht der evaluierten Geometrie.
    """
    n = len(mesh.vertices)
    if n == 0:
        raise MeshIOError("Mesh hat keine Vertices.")
    co = np.empty(n * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    return Vector(co.min(axis=0).tolist()), Vector(co.max(axis=0).tolist())


def local_bbox(context, obj):
    """Lokale Bounding-Box (min, max) des evaluierten Objekts (Modifier angewendet)."""
    depsgraph = context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    try:
        return _mesh_bbox(mesh)
    finally:
        eval_obj.to_mesh_clear()


def fit_matrix(src_lo, src_hi, res_lo, res_hi):
    """Matrix, die die Ergebnis-Bounding-Box uniform auf die Quell-Box abbildet.

    Rueckgabe None, wenn beide Boxen bereits (nahezu) uebereinstimmen.
    """
    src_size = src_hi - src_lo
    res_size = res_hi - res_lo
    src_max = max(src_size)
    res_max = max(res_size)
    if res_max <= 1e-12:
        raise MeshIOError("Ergebnis-Mesh ist degeneriert (Ausdehnung 0).")
    scale = src_max / res_max
    src_center = (src_lo + src_hi) * 0.5
    res_center = (res_lo + res_hi) * 0.5

    tol = max(src_max, 1.0) * 1e-4
    if abs(scale - 1.0) < 1e-4 and (src_center - res_center).length < tol:
        return None
    return Matrix.Translation(src_center) @ Matrix.Scale(scale, 4) @ Matrix.Translation(-res_center)


def _import_file(context, path):
    """Importiert OBJ/GLB und gibt die neu erzeugten Objekte zurueck."""
    ext = os.path.splitext(path)[1].lower()
    before = set(bpy.data.objects)
    if ext == ".obj":
        # Scenario liefert Y-up (glTF-Konvention); Blender-Default fuer OBJ ist
        # forward -Z / up Y und passt dazu.
        result = bpy.ops.wm.obj_import(filepath=path, forward_axis="NEGATIVE_Z", up_axis="Y")
    elif ext in (".glb", ".gltf"):
        result = bpy.ops.import_scene.gltf(filepath=path)
    else:
        raise MeshIOError(f"Unbekanntes Ergebnisformat: {ext}")
    if "FINISHED" not in result:
        raise MeshIOError(f"Import fehlgeschlagen: {result}")
    return [o for o in bpy.data.objects if o not in before]


def _consolidate(context, new_objects):
    """Reduziert das Importergebnis auf genau ein Mesh-Objekt (Welt-Transformen
    werden in die Mesh-Daten gebacken, Hilfsobjekte entfernt)."""
    meshes = [o for o in new_objects if o.type == "MESH"]
    others = [o for o in new_objects if o.type != "MESH"]
    if not meshes:
        for o in new_objects:
            bpy.data.objects.remove(o, do_unlink=True)
        raise MeshIOError("Import enthielt kein Mesh.")

    for o in meshes:
        if o.matrix_world != Matrix.Identity(4):
            o.data.transform(o.matrix_world)
        o.parent = None
        o.matrix_world = Matrix.Identity(4)

    main = meshes[0]
    if len(meshes) > 1:
        with context.temp_override(
            object=main, active_object=main, selected_objects=meshes,
            selected_editable_objects=meshes,
        ):
            bpy.ops.object.join()
    for o in others:
        bpy.data.objects.remove(o, do_unlink=True)
    return main


def _apply_smooth_shading(context, obj):
    mesh = obj.data
    try:
        with context.temp_override(object=obj, active_object=obj, selected_objects=[obj],
                                   selected_editable_objects=[obj]):
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
    except Exception:
        pass
    for p in mesh.polygons:
        p.use_smooth = True
    # OBJ-Import setzt Smooth-Groups als scharfe Kanten -> entfernen
    sharp = mesh.attributes.get("sharp_edge")
    if sharp is not None:
        mesh.attributes.remove(sharp)
    sharp_face = mesh.attributes.get("sharp_face")
    if sharp_face is not None:
        mesh.attributes.remove(sharp_face)


def _decimate_to(context, obj, target_faces, triangulate):
    faces = len(obj.data.polygons)
    if target_faces <= 0 or faces <= target_faces:
        return
    mod = obj.modifiers.new("SB_ExactCount", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = target_faces / faces
    mod.use_collapse_triangulate = triangulate
    with context.temp_override(object=obj, active_object=obj, selected_objects=[obj],
                               selected_editable_objects=[obj]):
        bpy.ops.object.modifier_apply(modifier=mod.name)


def face_stats(mesh):
    quads = tris = ngons = 0
    for p in mesh.polygons:
        n = len(p.vertices)
        if n == 4:
            quads += 1
        elif n == 3:
            tris += 1
        else:
            ngons += 1
    return {"faces": len(mesh.polygons), "quads": quads, "tris": tris, "ngons": ngons}


def import_result(context, path, source_obj, *, name=None, target_faces=0,
                  force_exact=False, polygon_type="quadrilateral", hide_source=False):
    """Importiert das Retopo-Ergebnis, passt es auf das Original ein und legt
    es als neues Objekt neben dem Original ab.

    Returns: (new_object, stats_dict)
    """
    src_lo, src_hi = local_bbox(context, source_obj)

    new_objects = _import_file(context, path)
    obj = _consolidate(context, new_objects)
    mesh = obj.data

    res_lo, res_hi = _mesh_bbox(mesh)
    fit = fit_matrix(src_lo, src_hi, res_lo, res_hi)
    if fit is not None:
        mesh.transform(fit)

    _apply_smooth_shading(context, obj)

    if force_exact and target_faces > 0:
        _decimate_to(context, obj, target_faces, triangulate=(polygon_type == "triangle"))

    # Benennen, in die Collections des Originals einsortieren, Transform uebernehmen
    base_name = name or f"{source_obj.name}_retopo"
    obj.name = base_name
    mesh.name = base_name
    mesh.materials.clear()

    for coll in list(obj.users_collection):
        coll.objects.unlink(obj)
    targets = list(source_obj.users_collection) or [context.scene.collection]
    for coll in targets:
        coll.objects.link(obj)

    obj.parent = source_obj.parent
    obj.matrix_parent_inverse = source_obj.matrix_parent_inverse.copy()
    obj.matrix_world = source_obj.matrix_world.copy()

    if hide_source:
        source_obj.hide_set(True)

    # Auswahl auf das neue Objekt setzen
    for o in context.view_layer.objects:
        if o.select_get():
            o.select_set(False)
    try:
        obj.select_set(True)
        context.view_layer.objects.active = obj
    except RuntimeError:
        pass

    stats = face_stats(mesh)
    stats["fitted"] = fit is not None
    return obj, stats

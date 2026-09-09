# SPDX-License-Identifier: GPL-3.0-or-later
"""Mesh-Export fuer den Upload und Import/Platzierung des Ergebnisses.

Laeuft ausschliesslich im Blender-Hauptthread (bpy-Zugriff).

Koordinaten: Das Quellobjekt wird im lokalen Raum (Objekttransform = Identity)
als GLB exportiert. Das Ergebnis wird auf die lokale Bounding-Box des Originals
korrigiert und bekommt dessen Welt-Matrix.

Die Korrektur folgt Phototron (bakeTexturesBlender in
apps/desktop/public/ipc/retopology.js): verglichen wird die Diagonale der Box,
korrigiert wird ab einem Prozent Abweichung. Am Ende wird auf den Vertex-Daten
nachgemessen, ob die Korrektur auch angekommen ist; weicht sie ab, steht das
im Log und im Panel, statt still ausgeliefert zu werden.
"""

import os

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

from .log import log

# Toleranzen der Bounding-Box-Pruefung, uebernommen aus Phototron
SCALE_TOLERANCE = 0.01   # 1 % Abweichung der Box-Diagonale
OFFSET_TOLERANCE = 0.01  # 1 % der Quell-Diagonale als Versatz

# Ab dieser Groesse wird auf die Analyse zusammenhaengender Teile verzichtet
MAX_VERTS_FOR_PART_ANALYSIS = 500000
# Ein getrenntes Teil gilt als Fragment, wenn es weiter als dieser Anteil der
# Hauptteil-Diagonale aus dessen Bounding-Box herausragt
OUTLIER_MARGIN = 0.1
# Sicherung: stellen die aussortierten Teile mehr als diesen Anteil der
# Geometrie, ist das kein Fragment mehr und es wird alles gemessen
MAX_OUTLIER_FRACTION = 0.1


class MeshIOError(Exception):
    pass


# -- Export ---------------------------------------------------------------

def cleanup_mesh(mesh, merge_distance=0.0001):
    """Mesh-Cleanup vor dem Upload, identisch zu Phototron (convertObjToGlb):
    doppelte Vertices verschmelzen, lose Geometrie ohne Faces loeschen,
    Normalen nach aussen vereinheitlichen.

    Returns: dict mit Statistik (verts_removed, loose_removed)
    """
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        verts_before = len(bm.verts)

        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=merge_distance)
        merged = verts_before - len(bm.verts)

        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")

        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    return {"verts_removed": merged, "loose_removed": len(loose)}


def export_object_for_upload(context, obj, glb_path, decimate_target=0):
    """Exportiert eine bereinigte Kopie von *obj* (Modifier angewendet, ohne
    Materialien/Farb-Attribute, lokaler Raum) als GLB.

    Returns: dict mit Statistik (faces, faces_clean, faces_exported, bytes, cleanup)
    """
    if obj is None or obj.type != "MESH":
        raise MeshIOError("Please select a mesh object.")

    depsgraph = context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(eval_obj, preserve_all_data_layers=False, depsgraph=depsgraph)
    if len(mesh.polygons) == 0:
        bpy.data.meshes.remove(mesh)
        raise MeshIOError(f"'{obj.name}' has no faces.")

    mesh.name = f"{obj.name}_sb_upload"
    faces_before = len(mesh.polygons)

    # Farb-Attribute und Materialien am Mesh entfernen statt ueber Exporter-
    # Optionen: die Optionsnamen dafuer haben sich zwischen Blender-Versionen
    # geaendert, ein Mesh ohne die Daten braucht sie nicht. UVs und Normalen
    # laesst der Exporter ueber stabile Optionen weg.
    for attr in list(mesh.color_attributes):
        mesh.color_attributes.remove(attr)
    mesh.materials.clear()

    # Cleanup vor der Dezimierung, Reihenfolge wie in Phototron
    cleanup = cleanup_mesh(mesh)
    faces_clean = len(mesh.polygons)
    if faces_clean == 0:
        bpy.data.meshes.remove(mesh)
        raise MeshIOError(f"'{obj.name}' has no faces left after the cleanup.")
    log(
        f"Cleanup: {faces_before} -> {faces_clean} faces, removed "
        f"{cleanup['verts_removed']} duplicate and {cleanup['loose_removed']} loose vertices"
    )

    temp = bpy.data.objects.new(mesh.name, mesh)
    temp.matrix_world = Matrix.Identity(4)
    context.scene.collection.objects.link(temp)

    if decimate_target and faces_clean > decimate_target:
        mod = temp.modifiers.new("SB_PreDecimate", "DECIMATE")
        mod.ratio = decimate_target / faces_clean

    prev_active = context.view_layer.objects.active
    prev_selected = [o for o in context.view_layer.objects if o.select_get()]
    try:
        for o in prev_selected:
            o.select_set(False)
        temp.select_set(True)
        context.view_layer.objects.active = temp

        result = bpy.ops.export_scene.gltf(
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
        if "FINISHED" not in result:
            raise MeshIOError(f"glTF export failed: {result}")

        # Was nach der Dezimierung tatsaechlich rausging, messen statt schaetzen
        depsgraph = context.evaluated_depsgraph_get()
        faces_exported = len(temp.evaluated_get(depsgraph).data.polygons)
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
        raise MeshIOError("The GLB file was not created.")

    return {
        "faces": faces_before,
        "faces_clean": faces_clean,
        "faces_exported": faces_exported,
        "bytes": os.path.getsize(glb_path),
        "cleanup": cleanup,
    }


# -- Import ---------------------------------------------------------------

def _mesh_bbox(mesh, exclude=None):
    """Bounding-Box (min, max) aus den Vertex-Koordinaten eines Meshes.

    Bewusst nicht Object.bound_box: das liefert bei Subdivision-Modifiern nur
    die Bounds des Kontroll-Cages, nicht der evaluierten Geometrie.
    exclude: optionales bool-Array ueber die Vertices, die nicht mitzaehlen.
    """
    n = len(mesh.vertices)
    if n == 0:
        raise MeshIOError("The mesh has no vertices.")
    co = np.empty(n * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    if exclude is not None:
        co = co[~exclude]
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


def connected_components(mesh):
    """Labelt jeden Vertex mit der Wurzel seiner Zusammenhangskomponente.

    Vektorisiert statt einer Python-Schleife ueber jede Kante: pro Runde
    haengt sich die groessere Wurzel jeder Kante an die kleinere (Hooking),
    danach werden die Ketten plattgedrueckt, bis jeder Vertex direkt auf seine
    Wurzel zeigt (Pointer-Jumping). Das konvergiert in wenigen Runden.
    """
    n = len(mesh.vertices)
    edges = np.empty(len(mesh.edges) * 2, dtype=np.int64)
    mesh.edges.foreach_get("vertices", edges)
    a, b = edges[0::2], edges[1::2]

    parent = np.arange(n, dtype=np.int64)
    while True:
        ra, rb = parent[a], parent[b]
        differ = ra != rb
        if not differ.any():
            return parent
        lo = np.minimum(ra[differ], rb[differ])
        hi = np.maximum(ra[differ], rb[differ])
        # Bei mehreren Kanten an derselben Wurzel gewinnt irgendeine kleinere;
        # jede ist gueltig, da immer nach unten gehaengt wird (kein Zyklus).
        parent[hi] = lo
        while True:
            grand = parent[parent]
            if np.array_equal(grand, parent):
                break
            parent = grand


def analyze_parts(mesh):
    """Zerlegt das Mesh in zusammenhaengende Teile und trennt Fragmente ab.

    Returns: dict mit full_lo/full_hi (Box ueber alles), lo/hi (Box ohne
    Fragmente), outlier_mask (bool-Array ueber die Vertices oder None) sowie
    parts, outlier_parts, outlier_fraction und filtered.
    """
    n = len(mesh.vertices)
    if n == 0:
        raise MeshIOError("The mesh has no vertices.")

    co = np.empty(n * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)

    full_lo = Vector(co.min(axis=0).tolist())
    full_hi = Vector(co.max(axis=0).tolist())
    result = {
        "full_lo": full_lo, "full_hi": full_hi, "lo": full_lo, "hi": full_hi,
        "outlier_mask": None,
        "parts": 1, "outlier_parts": 0, "outlier_fraction": 0.0, "filtered": False,
    }

    if n > MAX_VERTS_FOR_PART_ANALYSIS or len(mesh.edges) == 0:
        return result

    labels = connected_components(mesh)
    counts = np.bincount(labels, minlength=n)
    roots = [int(r) for r in np.nonzero(counts)[0]]
    result["parts"] = len(roots)
    if len(roots) == 1:
        return result

    part_lo, part_hi = {}, {}
    for r in roots:
        pts = co[labels == r]
        part_lo[r] = pts.min(axis=0)
        part_hi[r] = pts.max(axis=0)

    main = max(roots, key=lambda r: counts[r])
    lo = part_lo[main].copy()
    hi = part_hi[main].copy()
    margin = float(np.linalg.norm(hi - lo)) * OUTLIER_MARGIN

    # Teile innerhalb der (leicht vergroesserten) Hauptbox gehoeren dazu.
    # Wiederholen, bis nichts mehr hinzukommt, damit auch Ketten erfasst werden.
    kept = {main}
    changed = True
    while changed:
        changed = False
        for r in roots:
            if r in kept:
                continue
            if np.all(part_lo[r] >= lo - margin) and np.all(part_hi[r] <= hi + margin):
                kept.add(r)
                lo = np.minimum(lo, part_lo[r])
                hi = np.maximum(hi, part_hi[r])
                changed = True

    outliers = [r for r in roots if r not in kept]
    if not outliers:
        return result

    result["outlier_parts"] = len(outliers)
    result["outlier_fraction"] = float(sum(counts[r] for r in outliers)) / n
    if result["outlier_fraction"] > MAX_OUTLIER_FRACTION:
        # So viel Geometrie ist kein Fragment mehr; nichts aussortieren
        return result

    result["filtered"] = True
    result["lo"] = Vector(lo.tolist())
    result["hi"] = Vector(hi.tolist())
    result["outlier_mask"] = np.isin(labels, outliers)
    return result


def remove_vertices(mesh, mask):
    """Loescht die durch die Maske markierten Vertices samt ihrer Faces."""
    indices = np.nonzero(mask)[0]
    if len(indices) == 0:
        return 0
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        doomed = [bm.verts[int(i)] for i in indices]
        bmesh.ops.delete(bm, geom=doomed, context="VERTS")
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    return len(indices)


def fit_matrix(src_lo, src_hi, res_lo, res_hi):
    """Sicherheitsnetz-Korrektur des Ergebnisses auf die Quell-Bounding-Box.

    Verglichen wird wie in Phototron die Diagonale der Box, nicht die laengste
    Einzelachse: die Diagonale bleibt aussagekraeftig, auch wenn sich die
    Proportionen leicht verschieben und eine andere Achse die laengste wird.
    Korrigiert wird erst ab einem Prozent Abweichung, damit ein bereits korrekt
    sitzendes Ergebnis unangetastet bleibt.

    Returns: (matrix oder None, info)
    """
    src_diag = (src_hi - src_lo).length
    res_diag = (res_hi - res_lo).length
    if res_diag <= 1e-12:
        raise MeshIOError("The result mesh is degenerate, its size is zero.")

    scale = src_diag / res_diag
    src_center = (src_lo + src_hi) * 0.5
    res_center = (res_lo + res_hi) * 0.5
    offset = (src_center - res_center).length

    info = {
        "src_diag": src_diag,
        "res_diag": res_diag,
        "scale": scale,
        "offset": offset,
        "scaled": abs(scale - 1.0) > SCALE_TOLERANCE,
        "moved": offset > max(src_diag, 1e-9) * OFFSET_TOLERANCE,
    }
    if not info["scaled"] and not info["moved"]:
        return None, info

    factor = scale if info["scaled"] else 1.0
    target_center = src_center if info["moved"] else res_center
    matrix = Matrix.Translation(target_center) @ Matrix.Scale(factor, 4) @ Matrix.Translation(-res_center)
    return matrix, info


def _import_file(path):
    """Importiert OBJ/GLB und gibt die neu erzeugten Objekte zurueck."""
    ext = os.path.splitext(path)[1].lower()
    before = set(bpy.data.objects)
    if ext == ".obj":
        # Scenario liefert Y-up (glTF-Konvention); Blender-Default fuer OBJ ist
        # forward -Z / up Y und passt dazu.
        result = bpy.ops.wm.obj_import(filepath=path, forward_axis="NEGATIVE_Z", up_axis="Y")
    elif ext in (".glb", ".gltf"):
        result = bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        # Tripo liefert Quad-Ergebnisse als FBX. Der Importer bringt seine
        # eigene Achsen- und Massstabsumrechnung mit; was daneben liegt, faengt
        # die Bounding-Box-Pruefung ab.
        if not hasattr(bpy.ops.import_scene, "fbx"):
            raise MeshIOError(
                "The result is an FBX file, but the FBX importer is not enabled. "
                "Enable 'Import-Export: FBX format' in the Blender preferences."
            )
        result = bpy.ops.import_scene.fbx(filepath=path)
    else:
        raise MeshIOError(
            f"Unknown result format '{ext}'. The model returned a file this "
            "add-on cannot read; the system console shows what was received."
        )
    if "FINISHED" not in result:
        raise MeshIOError(f"Import failed: {result}")
    return [o for o in bpy.data.objects if o not in before]


def _consolidate(context, new_objects):
    """Reduziert das Importergebnis auf genau ein Mesh-Objekt (Welt-Transformen
    werden in die Mesh-Daten gebacken, Hilfsobjekte entfernt)."""
    meshes = [o for o in new_objects if o.type == "MESH"]
    others = [o for o in new_objects if o.type != "MESH"]
    if not meshes:
        for o in new_objects:
            bpy.data.objects.remove(o, do_unlink=True)
        raise MeshIOError("The import contained no mesh.")

    # Nach dem Import muss der Depsgraph aktualisiert sein, sonst liefert
    # matrix_world bei verschachtelten Objekten veraltete Werte
    context.view_layer.update()

    for o in meshes:
        if o.data.users > 1:
            # Instanzen teilen sich ein Mesh; ohne Kopie wuerde die Transform
            # mehrfach auf dieselben Daten angewendet
            o.data = o.data.copy()
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
    """Alles glatt schattieren.

    Seit Blender 4.1 sind Flat-Faces das Attribut sharp_face und harte Kanten
    sharp_edge; ohne die Attribute gilt alles als glatt. Der OBJ-Import legt
    Smooth-Groups als sharp_edge ab, deshalb muss auch das weg.
    """
    mesh = obj.data
    try:
        with context.temp_override(object=obj, active_object=obj, selected_objects=[obj],
                                   selected_editable_objects=[obj]):
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
    except Exception:
        pass
    for name in ("sharp_face", "sharp_edge"):
        attr = mesh.attributes.get(name)
        if attr is not None:
            mesh.attributes.remove(attr)


def face_stats(mesh):
    n = len(mesh.polygons)
    sizes = np.empty(n, dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", sizes)
    quads = int(np.count_nonzero(sizes == 4))
    tris = int(np.count_nonzero(sizes == 3))
    return {"faces": n, "quads": quads, "tris": tris, "ngons": n - quads - tris}


def import_result(context, path, source_obj, *, name=None, hide_source=False,
                  remove_fragments=True):
    """Importiert das Retopo-Ergebnis und legt es als neues Objekt neben dem
    Original ab.

    Groesse und Lage werden immer gegen das Original geprueft und korrigiert,
    nach dem Rezept aus Phototron (bakeTexturesBlender): verglichen wird die
    Diagonale der Bounding-Box, korrigiert wird ab einem Prozent Abweichung.
    Zum Schluss wird auf den Vertex-Daten nachgemessen und protokolliert, damit
    eine falsche Korrektur auffaellt statt still auszuliefern.

    Returns: (new_object, stats) mit stats = faces, quads, tris, ngons, fitted,
    parts, outlier_parts, filtered, fragments_removed, world_ok,
    world_residual, world_centre_offset
    """
    src_lo, src_hi = local_bbox(context, source_obj)

    new_objects = _import_file(path)
    obj = _consolidate(context, new_objects)
    mesh = obj.data

    # part_info beschreibt, was gefunden wurde, auch wenn danach entfernt wird
    analysis = analyze_parts(mesh)
    part_info = {k: analysis[k] for k in ("parts", "outlier_parts", "outlier_fraction", "filtered")}
    if part_info["parts"] > 1:
        log(
            f"Result consists of {part_info['parts']} separate parts, "
            f"{part_info['outlier_parts']} of them outliers "
            f"({part_info['outlier_fraction'] * 100:.2f}% of the vertices)"
        )

    removed = 0
    if remove_fragments and analysis["outlier_mask"] is not None:
        removed = remove_vertices(mesh, analysis["outlier_mask"])
        log(f"Removed {removed} vertices of {part_info['outlier_parts']} stray fragment(s)")
        analysis = analyze_parts(mesh)

    res_lo, res_hi = analysis["lo"], analysis["hi"]
    fit, fit_info = fit_matrix(src_lo, src_hi, res_lo, res_hi)
    log(
        f"Bounding box: source {fit_info['src_diag']:.4f}, result {fit_info['res_diag']:.4f}, "
        f"factor {fit_info['scale']:.6f}, offset {fit_info['offset']:.4f}"
    )

    applied = False
    if fit is None:
        log("Size and position match the original, nothing to correct")
    else:
        parts = []
        if fit_info["scaled"]:
            parts.append(f"scaled by {fit_info['scale']:.4f}")
        if fit_info["moved"]:
            parts.append(f"moved by {fit_info['offset']:.4f}")
        log("Correction: " + " and ".join(parts))
        mesh.transform(fit)
        applied = True

    _apply_smooth_shading(context, obj)

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

    # Nachmessen auf den Vertex-Daten, nicht auf dem, was fit_matrix
    # ausgerechnet hat: das bestaetigt, dass die Korrektur im Mesh angekommen
    # ist. Bewusst im lokalen Raum: eine Welt-Box aendert bei gedrehten
    # Objekten mit der Form ihre Ausdehnung. Behaltene Ausreisser zaehlen
    # nicht mit, sonst waere die Messung dieselbe Luege wie ohne Filter.
    lo, hi = _mesh_bbox(mesh, exclude=analysis["outlier_mask"])
    res_diag = (hi - lo).length
    src_diag = (src_hi - src_lo).length
    size_ratio = res_diag / src_diag if src_diag > 1e-12 else 1.0
    centre_off = ((src_lo + src_hi) - (lo + hi)).length * 0.5
    size_ok = abs(size_ratio - 1.0) <= SCALE_TOLERANCE
    centre_ok = centre_off <= src_diag * OFFSET_TOLERANCE

    log(
        f"Check: local diagonal original {src_diag:.4f}, result {res_diag:.4f} "
        f"(ratio {size_ratio:.6f}), centre off {centre_off:.4f}, "
        f"object scale {tuple(round(v, 4) for v in source_obj.scale)}"
    )
    if not (size_ok and centre_ok):
        reasons = []
        if not size_ok:
            reasons.append(f"size ratio {size_ratio:.4f}")
        if not centre_ok:
            reasons.append(f"centre off {centre_off:.4f}")
        log("Check FAILED: " + ", ".join(reasons))

    stats = face_stats(mesh)
    stats.update({
        "fitted": applied,
        "parts": part_info["parts"],
        "outlier_parts": part_info["outlier_parts"],
        "filtered": part_info["filtered"],
        "fragments_removed": removed,
        "world_ok": size_ok and centre_ok,
        "world_residual": abs(size_ratio - 1.0),
        "world_centre_offset": centre_off,
    })
    return obj, stats

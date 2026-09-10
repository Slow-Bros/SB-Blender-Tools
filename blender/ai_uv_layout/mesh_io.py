# SPDX-License-Identifier: GPL-3.0-or-later
"""Mesh-Export fuer den Upload und Uebernahme der UVs aus dem Ergebnis.

Laeuft ausschliesslich im Blender-Hauptthread (bpy-Zugriff).

Das Rezept folgt transferUVsToRetopo in Phototron
(apps/desktop/public/ipc/retopology.js): das UV-Modell normalisiert die
Geometrie (Groesse und Lage), also wird nicht versucht, das rueckgaengig zu
machen. Die Geometrie bleibt die des Originals, uebernommen werden nur die
UV-Koordinaten, Face fuer Face und Ecke fuer Ecke nach Index. Dafuer muss die
Topologie des Ergebnisses der des Originals entsprechen; deshalb geht der
Upload als OBJ hinaus (Quads und Reihenfolge der Faces bleiben erhalten) und
kommt bevorzugt als OBJ zurueck.

Stimmen die Zahlen nicht, ist der Transfer ein Fehler. Phototron faellt an
dieser Stelle auf einen Data-Transfer-Modifier mit Topologie-Abbildung
zurueck, aber der setzt dieselbe gleiche Ecken-Zahl voraus und laesst das Mesh
bei verschiedener Topologie unveraendert, ein Leerlauf. Der ist bewusst nicht
uebernommen (Entscheidung September 2026): eine klare Meldung, woran es liegt,
ist mehr wert als ein Ergebnis, das nach einem aussieht.
"""

import os
import re

import bpy
import numpy as np
from mathutils import Matrix, Vector

from .log import log

UPLOAD_CONTENT_TYPE = "model/obj"

METHOD_GEOMETRY = "geometry"      # Zuordnung ueber die Geometrie, der Weg auf ein Original
METHOD_STANDALONE = "standalone"  # Ergebnis als eigenes Objekt, ohne Original


class MeshIOError(Exception):
    pass


# -- Export ---------------------------------------------------------------

def export_object_for_upload(context, obj, obj_path):
    """Exportiert das Basis-Mesh von *obj* (ohne Modifier) als OBJ.

    Ohne Modifier, weil die UVs spaeter auf genau dieses Mesh geschrieben
    werden: ein Subdivision-Modifier bliebe erhalten und interpoliert die UVs
    dann selbst. Ohne Materialien und ohne vorhandene UVs, beides braucht das
    Modell nicht. Das Original wird nicht veraendert.

    Returns: dict mit Statistik (faces, loops, verts, bytes)
    """
    if obj is None or obj.type != "MESH":
        raise MeshIOError("Please select a mesh object.")
    mesh = obj.data
    if len(mesh.polygons) == 0:
        raise MeshIOError(f"'{obj.name}' has no faces.")

    # Ein Hilfsobjekt am Ursprung mit denselben Mesh-Daten: so geht nur dieses
    # Mesh hinaus, und die Objekt-Transformation spielt keine Rolle
    temp = bpy.data.objects.new(f"{obj.name}_sb_uv_upload", mesh)
    temp.matrix_world = Matrix.Identity(4)
    context.scene.collection.objects.link(temp)

    prev_active = context.view_layer.objects.active
    prev_selected = [o for o in context.view_layer.objects if o.select_get()]
    try:
        for o in prev_selected:
            o.select_set(False)
        temp.select_set(True)
        context.view_layer.objects.active = temp

        result = bpy.ops.wm.obj_export(
            filepath=obj_path,
            export_selected_objects=True,
            apply_modifiers=False,
            export_uv=False,
            export_materials=False,
            export_triangulated_mesh=False,
            export_smooth_groups=False,
        )
        if "FINISHED" not in result:
            raise MeshIOError(f"OBJ export failed: {result}")
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

    if not os.path.exists(obj_path):
        raise MeshIOError("The OBJ file was not created.")

    return {
        "faces": len(mesh.polygons),
        "loops": len(mesh.loops),
        "verts": len(mesh.vertices),
        "bytes": os.path.getsize(obj_path),
    }


# -- Import ---------------------------------------------------------------

def _import_file(path):
    """Importiert OBJ/GLB/FBX und gibt die neu erzeugten Objekte zurueck."""
    ext = os.path.splitext(path)[1].lower()
    before = set(bpy.data.objects)
    if ext == ".obj":
        # Als EIN Mesh lesen, ohne Aufteilung nach 'o'- oder 'g'-Bloecken. Der
        # UV-Transfer kopiert nach Face-Index und braucht die Dateireihenfolge;
        # mehrere Objekte wieder zusammenzufuegen wuerde sie in Blenders
        # (alphabetischer) Objektreihenfolge neu sortieren, und die Faces
        # saessen verschoben, obwohl die Summen stimmen.
        result = bpy.ops.wm.obj_import(filepath=path, use_split_objects=False,
                                       use_split_groups=False)
    elif ext in (".glb", ".gltf"):
        result = bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
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
    """Reduziert das Importergebnis auf genau ein Mesh-Objekt."""
    meshes = [o for o in new_objects if o.type == "MESH"]
    others = [o for o in new_objects if o.type != "MESH"]
    if not meshes:
        for o in new_objects:
            bpy.data.objects.remove(o, do_unlink=True)
        raise MeshIOError("The import contained no mesh.")

    context.view_layer.update()
    for o in meshes:
        if o.data.users > 1:
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


def import_uv_mesh(context, path):
    """Importiert das UV-Ergebnis als ein Mesh-Objekt und gibt es zurueck.

    Die Geometrie ist die normalisierte des Modells und wird nicht korrigiert;
    gebraucht werden nur die UVs. Der Aufrufer entscheidet, wohin sie gehen.
    """
    obj = _consolidate(context, _import_file(path))
    if obj.data.uv_layers.active is None:
        remove_object(obj)
        raise MeshIOError("The result has no UV map. The model returned a mesh without UVs.")
    return obj


def remove_object(obj):
    """Entfernt ein Objekt samt seinem Mesh, wenn niemand sonst es benutzt."""
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


# -- Topologie und Zuordnung ------------------------------------------------
#
# Das Modell gibt die Faces nicht in der hochgeladenen Reihenfolge zurueck.
# Sein Export (ein Blender 3.6 auf Scenarios Seite) schreibt einen o-Block pro
# zusammenhaengendem Teil, und Faces eines Teils, die im Original zwischen
# denen eines anderen lagen, ruecken ans Ende ihres Blocks. Die Vertices
# liegen dagegen exakt an ihren Positionen (nachgemessen an einem echten Job,
# September 2026: 1755 Faces, Abstand null). Also werden die Faces ueber ihre
# Geometrie zugeordnet: Face zu Face ueber den Schwerpunkt, Ecke zu Ecke ueber
# die Position innerhalb des Faces. Phototrons Kopie nach Index ist
# der Sonderfall, in dem diese Zuordnung die Identitaet ist; sie geht nur bei
# Meshes aus einem Stueck auf.

# Toleranzen, relativ zur Diagonale der Bounding-Box des Ziels
FIT_TOLERANCE = 0.01     # ab so viel Abweichung wird das Ergebnis eingepasst, wie in AI Retopo
MATCH_TOLERANCE = 0.001  # so nah muessen Schwerpunkt und Ecken am Original liegen


def _loop_totals(mesh):
    sizes = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", sizes)
    return sizes


def _coords(mesh):
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def counts_match(mesh_a, mesh_b):
    """Gleiche Zahl an Faces und Ecken: die Voraussetzung, bevor zugeordnet wird."""
    return (len(mesh_a.polygons) == len(mesh_b.polygons) > 0
            and len(mesh_a.loops) == len(mesh_b.loops))


def _fit_coords(dst_co, src_co):
    """Passt die Ergebnis-Koordinaten auf die Bounding-Box des Ziels ein.

    Rezept aus AI Retopo: Diagonale vergleichen, ab einem Prozent Abweichung
    skalieren beziehungsweise verschieben. Das UV-Modell hat die Geometrie
    bisher unveraendert gelassen; die Einpassung ist das Sicherheitsnetz fuer
    den Fall, dass es sie doch normalisiert.

    Returns: (coords, info) mit scale, offset, scaled, moved
    """
    lo_d, hi_d = dst_co.min(axis=0), dst_co.max(axis=0)
    lo_s, hi_s = src_co.min(axis=0), src_co.max(axis=0)
    dst_diag = float(np.linalg.norm(hi_d - lo_d))
    src_diag = float(np.linalg.norm(hi_s - lo_s))
    if src_diag <= 1e-12:
        raise MeshIOError("The result mesh is degenerate, its size is zero.")
    scale = dst_diag / src_diag
    dst_c = (lo_d + hi_d) * 0.5
    src_c = (lo_s + hi_s) * 0.5
    offset = float(np.linalg.norm(dst_c - src_c))
    info = {
        "scale": scale,
        "offset": offset,
        "scaled": abs(scale - 1.0) > FIT_TOLERANCE,
        "moved": offset > max(dst_diag, 1e-9) * FIT_TOLERANCE,
    }
    if not info["scaled"] and not info["moved"]:
        return src_co, info
    factor = scale if info["scaled"] else 1.0
    centre = dst_c if info["moved"] else src_c
    return (src_co - src_c) * factor + centre, info


def _centroids(mesh, co):
    """Schwerpunkt jedes Faces aus den Vertex-Koordinaten *co*."""
    n = len(mesh.polygons)
    starts = np.empty(n, dtype=np.int64)
    totals = np.empty(n, dtype=np.int64)
    mesh.polygons.foreach_get("loop_start", starts)
    mesh.polygons.foreach_get("loop_total", totals)
    loop_verts = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", loop_verts)
    sums = np.add.reduceat(co[loop_verts], starts, axis=0)
    return sums / totals[:, None]


def match_faces(dst_mesh, src_mesh):
    """Ordnet jede Ecke des Ziels einer Ecke des Ergebnisses zu.

    Face zu Face ueber den Schwerpunkt, dann Ecke zu Ecke ueber die Position
    innerhalb des Faces. Bewusst kein globales Vertex-zu-Vertex: Meshes haben
    gelegentlich zwei Vertices an derselben Stelle (Suzanne hat welche), und
    dann waere die Zuordnung ueber die Vertexmenge mehrdeutig. Innerhalb eines
    Faces liegen keine zwei Ecken aufeinander.

    Returns: (loop_map, info). loop_map ist ein Array ueber die Ecken des
    Ziels mit dem Index der Ergebnis-Ecke, -1 wo keine gefunden wurde. info:
    faces, faces_matched, faces_unmatched, faces_in_place (gleicher Index wie
    im Ziel), max_dist (groesster Abstand einer zugeordneten Ecke), fit.
    """
    from mathutils.kdtree import KDTree

    dst_co = _coords(dst_mesh)
    src_co, fit = _fit_coords(dst_co, _coords(src_mesh))
    diag = float(np.linalg.norm(dst_co.max(axis=0) - dst_co.min(axis=0)))
    tolerance = max(diag, 1e-9) * MATCH_TOLERANCE

    dst_centres = _centroids(dst_mesh, dst_co)
    src_centres = _centroids(src_mesh, src_co)
    tree = KDTree(len(dst_centres))
    for i, centre in enumerate(dst_centres):
        tree.insert(Vector(centre), i)
    tree.balance()

    dst_polys = dst_mesh.polygons
    loop_map = np.full(len(dst_mesh.loops), -1, dtype=np.int64)
    claimed = np.zeros(len(dst_polys), dtype=bool)
    matched = 0
    in_place = 0
    max_dist = 0.0
    def corner_pairs(dpoly, spoly):
        """Ecke zu Ecke, exklusiv nach aufsteigendem Abstand, oder None.

        Exklusiv, damit zwei Ecken eines Faces auf derselben Position (ein
        entartetes Quad) je eine eigene Ergebnis-Ecke bekommen. Ist eine Ecke
        nicht innerhalb der Toleranz zu finden, passt das Face nicht.
        Returns: (pairs, largest distance) oder None
        """
        d_loops = list(range(dpoly.loop_start, dpoly.loop_start + dpoly.loop_total))
        s_loops = list(range(spoly.loop_start, spoly.loop_start + spoly.loop_total))
        d_pos = dst_co[[dst_mesh.loops[l].vertex_index for l in d_loops]]
        s_pos = src_co[[src_mesh.loops[l].vertex_index for l in s_loops]]
        dist = np.linalg.norm(d_pos[:, None, :] - s_pos[None, :, :], axis=2)
        pairs = []
        d_used = set()
        s_used = set()
        largest = 0.0
        for flat in np.argsort(dist, axis=None):
            di, si = divmod(int(flat), len(s_loops))
            if di in d_used or si in s_used:
                continue
            if dist[di, si] > tolerance:
                break
            pairs.append((d_loops[di], s_loops[si]))
            d_used.add(di)
            s_used.add(si)
            largest = max(largest, float(dist[di, si]))
        if len(pairs) != len(d_loops):
            return None
        return pairs, largest

    for spoly in src_mesh.polygons:
        # Mehrere Ziel-Faces koennen denselben Schwerpunkt haben: doppelte
        # Faces, oder zwei Quads, die sich ueberkreuzen (kommt in Retopo-
        # Ergebnissen vor). Erst die Ecken entscheiden, welches gemeint ist.
        found = None
        for _, index, dist in tree.find_n(Vector(src_centres[spoly.index]), 4):
            if dist > tolerance:
                break
            dpoly = dst_polys[index]
            if claimed[index] or dpoly.loop_total != spoly.loop_total:
                continue
            result = corner_pairs(dpoly, spoly)
            if result is not None:
                found = (dpoly, result)
                break
        if found is None:
            continue
        dpoly, (pairs, largest) = found
        for d_loop, s_loop in pairs:
            loop_map[d_loop] = s_loop
        claimed[dpoly.index] = True
        max_dist = max(max_dist, largest)
        matched += 1
        if dpoly.index == spoly.index:
            in_place += 1

    info = {
        "faces": len(dst_polys),
        "faces_matched": matched,
        "faces_unmatched": len(dst_polys) - matched,
        "faces_in_place": in_place,
        "max_dist": max_dist,
        "fit": fit,
    }
    return loop_map, info


def face_stats(mesh):
    sizes = _loop_totals(mesh)
    n = len(sizes)
    quads = int(np.count_nonzero(sizes == 4))
    tris = int(np.count_nonzero(sizes == 3))
    return {"faces": n, "quads": quads, "tris": tris, "ngons": n - quads - tris}


def _uv_array(uv_layer):
    """Alle UV-Koordinaten eines Layers als (n, 2)-Array."""
    n = len(uv_layer.data)
    buf = np.empty(n * 2, dtype=np.float32)
    if n:
        uv_layer.data.foreach_get("uv", buf)
    return buf.reshape(-1, 2)


def uv_coverage(uv_layer):
    """Anteil der Ecken, deren UV nicht (0, 0) ist.

    Eine frisch angelegte UV-Map steht ueberall auf (0, 0). Der Wert steht im
    Log als Anhaltspunkt, ob eine Map gefuellt ist.
    """
    uv = _uv_array(uv_layer)
    if len(uv) == 0:
        return 0.0
    nonzero = np.count_nonzero(np.any(uv != 0.0, axis=1))
    return float(nonzero / len(uv))


# -- Transfer -------------------------------------------------------------

UV_LAYER_PREFIX = "AI_UV"
MAX_UV_LAYERS = 8  # Blenders Grenze pro Mesh
_NUMBERED = re.compile(re.escape(UV_LAYER_PREFIX) + r"_(\d+)$")


def next_uv_layer_name(mesh):
    """Name der naechsten Ergebnis-Map: AI_UV_1, AI_UV_2, ...

    Vorhandene Maps werden nie ueberschrieben, jedes Ergebnis kommt als
    weitere Map hinzu. Gezaehlt wird ueber die hoechste vergebene Nummer,
    damit eine geloeschte Map keine Nummer doppelt vergibt.
    """
    highest = 0
    for layer in mesh.uv_layers:
        m = _NUMBERED.match(layer.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return f"{UV_LAYER_PREFIX}_{highest + 1}"


def _new_uv_layer(mesh):
    """Legt die Ergebnis-Map an und macht sie aktiv (Bearbeitung und Render)."""
    if len(mesh.uv_layers) >= MAX_UV_LAYERS:
        raise MeshIOError(
            f"'{mesh.name}' already has {MAX_UV_LAYERS} UV maps, Blender's maximum. "
            "Delete one before adding another result."
        )
    # do_init=False: Blender wuerde die neue Map sonst mit einer
    # Standardprojektion fuellen, die die Kopie gleich ueberschreibt
    layer = mesh.uv_layers.new(name=next_uv_layer_name(mesh), do_init=False)
    if layer is None:
        raise MeshIOError("Could not add a UV map.")
    layer.active = True
    layer.active_render = True
    return layer


def transfer_uvs(target, uv_obj):
    """Schreibt die UVs von *uv_obj* in eine neue UV-Map von *target*.

    Voraussetzung sind dieselbe Zahl an Faces und Ecken und dieselbe
    Geometrie; die Faces werden ueber ihre Vertices zugeordnet (match_faces),
    nicht ueber ihren Index. Sonst ist das ein Fehler, und die Meldung nennt
    die Zahlen. Geprueft wird vor dem Anlegen der Map, damit ein Fehler das
    Ziel unveraendert laesst. Vorhandene Maps bleiben, wie sie sind.

    Returns: dict mit method, layer, faces, uv_faces, loops, uv_loops,
    faces_in_place, max_dist, fitted, coverage
    """
    src_mesh = uv_obj.data
    dst_mesh = target.data
    src_layer = src_mesh.uv_layers.active
    if src_layer is None:
        raise MeshIOError("The result has no UV map.")

    if not counts_match(dst_mesh, src_mesh):
        raise MeshIOError(
            f"The result does not match the mesh: {len(src_mesh.polygons)} faces / "
            f"{len(src_mesh.loops)} corners came back for {len(dst_mesh.polygons)} faces / "
            f"{len(dst_mesh.loops)} corners. UVs can only be transferred onto the same topology. "
            "Expect this when the result is not OBJ, or when the mesh was edited after the job started."
        )

    loop_map, info = match_faces(dst_mesh, src_mesh)
    fit = info["fit"]
    if fit["scaled"] or fit["moved"]:
        log(f"Result fitted onto the mesh: scale {fit['scale']:.4f}, offset {fit['offset']:.4f}")
    if info["faces_unmatched"]:
        raise MeshIOError(
            f"{info['faces_unmatched']} of {info['faces']} faces have no counterpart in the result "
            f"(faces are matched by position, within {MATCH_TOLERANCE * 100:.1f}% of the bounding-box "
            "diagonal). UVs can only be transferred onto the same geometry. Expect this when the mesh "
            "was edited after the job started."
        )

    dst_layer = _new_uv_layer(dst_mesh)
    src_uv = _uv_array(src_layer)
    dst_layer.data.foreach_set("uv", src_uv[loop_map].ravel())
    dst_mesh.update()
    stats = {
        "method": METHOD_GEOMETRY,
        "layer": dst_layer.name,
        "faces": len(dst_mesh.polygons),
        "uv_faces": len(src_mesh.polygons),
        "loops": len(dst_mesh.loops),
        "uv_loops": len(src_mesh.loops),
        "faces_in_place": info["faces_in_place"],
        "max_dist": info["max_dist"],
        "fitted": fit["scaled"] or fit["moved"],
        "coverage": uv_coverage(dst_layer),
    }
    log(
        f"Matched {info['faces_matched']} faces by geometry, {info['faces_in_place']} of them at "
        f"their original index; copied {len(loop_map)} UV coordinates into new map '{dst_layer.name}'"
    )
    log(f"UV coverage: {stats['coverage'] * 100:.1f}% of the corners carry a UV")
    return stats


# -- Ergebnis ablegen -------------------------------------------------------

def _apply_smooth_shading(context, obj):
    """Alles glatt schattieren, wie der Smooth-Schritt in Phototron.

    Seit Blender 4.1 sind Flat-Faces das Attribut sharp_face und harte Kanten
    sharp_edge; ohne die Attribute gilt alles als glatt.
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


def _select_only(context, obj):
    for o in context.view_layer.objects:
        if o.select_get():
            o.select_set(False)
    try:
        obj.select_set(True)
        context.view_layer.objects.active = obj
    except RuntimeError:
        pass


def apply_uvs(context, uv_obj, target):
    """Uebertraegt die UVs des Ergebnisses als neue UV-Map auf *target*.

    Das UV-Objekt wird danach entfernt; es war nur der Traeger der Koordinaten.
    Schlaegt der Transfer fehl, bleibt das Ziel unveraendert.

    Returns: (target, stats) mit stats aus transfer_uvs plus quads, tris, ngons
    """
    if target is None or target.type != "MESH":
        raise MeshIOError("The target is not a mesh object.")
    try:
        stats = transfer_uvs(target, uv_obj)
    finally:
        remove_object(uv_obj)
    _apply_smooth_shading(context, target)
    _select_only(context, target)
    stats.update(face_stats(target.data))
    return target, stats


def keep_standalone(context, uv_obj, name):
    """Behaelt das Ergebnis als eigenes Objekt, mit der Geometrie des Modells.

    Nur fuer den Nachimport aus der Historie, wenn kein Mesh mit passender
    Topologie mehr da ist, auf das die UVs koennten. Groesse und Lage sind
    dann die normalisierten des Modells, und der Aufrufer sagt das im Panel.
    """
    _apply_smooth_shading(context, uv_obj)
    uv_obj.name = name
    uv_obj.data.name = name
    uv_obj.data.materials.clear()
    _select_only(context, uv_obj)
    layer = uv_obj.data.uv_layers.active
    stats = {
        "method": METHOD_STANDALONE,
        "layer": layer.name if layer else "",
        "uv_faces": len(uv_obj.data.polygons),
        "loops": len(uv_obj.data.loops),
        "uv_loops": len(uv_obj.data.loops),
        "coverage": uv_coverage(layer) if layer else 0.0,
    }
    stats.update(face_stats(uv_obj.data))
    return uv_obj, stats

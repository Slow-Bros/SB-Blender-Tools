# SPDX-License-Identifier: GPL-3.0-or-later
"""Szenen-Einstellungen und Laufzeitstatus fuer das Panel."""

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, PointerProperty, StringProperty)

from . import history, models


class SBAIRetopoHistoryItem(bpy.types.PropertyGroup):
    """Ein Eintrag der Historie, wie ihn das Panel zeigt.

    Nur die Anzeige: die Wahrheit steht in history.json, diese Liste wird
    daraus aufgebaut (history.sync).
    """
    name: StringProperty(name="Name")
    job_id: StringProperty(name="Job")
    started: StringProperty(name="Started")
    status: StringProperty(name="Status")
    model: StringProperty(name="Model")
    size_mb: FloatProperty(name="Size (MB)")
    source_object: StringProperty(name="Source Object")
    blend_file: StringProperty(name="Project")


def _sync_history(self, context):
    history.sync(context)


def _model_items(self, context):
    # Callback statt fester Liste, weil die Items aus models.json kommen.
    # Blender braucht eine Referenz auf die Strings, sonst werden sie
    # freigegeben, deshalb der Cache am Funktionsobjekt.
    items = models.enum_items()
    _model_items._cache = items
    return items


class SBAIRetopoSettings(bpy.types.PropertyGroup):
    model: EnumProperty(
        name="AI Model",
        description="Retopology model of the Scenario API",
        items=_model_items,
        # Dynamische Enums brauchen den Default als Nummer; ohne ihn steht die
        # Property auf 0, und das ist keine der Modell-Nummern.
        default=models.enum_number(models.MODELS[0]["key"]),
    )
    target_faces: IntProperty(
        name="Target Polygons",
        description=(
            "Wanted number of polygons. The allowed range depends on the "
            "model and is shown in the panel; values outside it are clamped"
        ),
        default=10000,
        min=100,
        max=300000,
        step=100,
    )
    face_level: EnumProperty(
        name="Target Polygons",
        description=(
            "Polygon density of the result. Used by models that have no "
            "target count, only these three levels"
        ),
        items=(
            ("low", "Low", "Strong reduction, fewest polygons"),
            ("medium", "Medium", "Balanced reduction"),
            ("high", "High", "Light reduction, most polygons"),
        ),
        default="medium",
    )
    polygon_type: EnumProperty(
        name="Polygons",
        description="Topology type of the result",
        items=(
            (models.QUADS, "Quads", "Quad topology (loaded as OBJ so the quads survive)"),
            (models.TRIS, "Triangles", "Triangle topology"),
        ),
        default=models.QUADS,
    )
    pre_decimate: BoolProperty(
        name="Pre-Decimation",
        description="Reduce very dense meshes before uploading (the API limit is 200 MB)",
        default=False,
    )
    pre_decimate_target: IntProperty(
        name="Upload Faces",
        description="Target face count for the pre-decimation",
        default=200000,
        min=1000,
        soft_max=2000000,
        step=1000,
    )
    remove_fragments: BoolProperty(
        name="Remove Stray Fragments",
        description=(
            "Delete separate parts the model placed outside the object. They "
            "are artefacts, and they distort any measurement of size and position"
        ),
        default=True,
    )
    hide_source: BoolProperty(
        name="Hide Original",
        description="Hide the source object in the viewport after a successful import; it is kept",
        default=False,
    )
    history_this_project: BoolProperty(
        name="This Project Only",
        description=(
            "Show only the jobs of the current project. Jobs from a file that "
            "was never saved have no project until it is saved the first time"
        ),
        default=True,
        update=_sync_history,
    )

    # Laufzeitstatus, nur zur Anzeige
    running: BoolProperty(default=False, options={"SKIP_SAVE"})
    progress: FloatProperty(default=0.0, min=0.0, max=1.0, subtype="FACTOR", options={"SKIP_SAVE"})
    status: StringProperty(default="", options={"SKIP_SAVE"})
    last_result: StringProperty(default="", options={"SKIP_SAVE"})
    last_warning: StringProperty(default="", options={"SKIP_SAVE"})
    last_error: StringProperty(default="", options={"SKIP_SAVE"})


def register():
    bpy.utils.register_class(SBAIRetopoHistoryItem)
    bpy.utils.register_class(SBAIRetopoSettings)
    bpy.types.Scene.sb_ai_retopo = PointerProperty(type=SBAIRetopoSettings)
    # Am WindowManager, nicht an der Szene: die Liste ist die Anzeige einer
    # Datei und gehoert nicht in die .blend-Datei.
    bpy.types.WindowManager.sb_ai_retopo_history = CollectionProperty(type=SBAIRetopoHistoryItem)
    bpy.types.WindowManager.sb_ai_retopo_history_index = IntProperty(default=0)


def unregister():
    del bpy.types.WindowManager.sb_ai_retopo_history_index
    del bpy.types.WindowManager.sb_ai_retopo_history
    del bpy.types.Scene.sb_ai_retopo
    bpy.utils.unregister_class(SBAIRetopoSettings)
    bpy.utils.unregister_class(SBAIRetopoHistoryItem)

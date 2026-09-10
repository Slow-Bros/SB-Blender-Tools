# SPDX-License-Identifier: GPL-3.0-or-later
"""Szenen-Einstellungen und Laufzeitstatus fuer das Panel."""

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, PointerProperty, StringProperty)

from . import history, models


class SBAIUVHistoryItem(bpy.types.PropertyGroup):
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


class SBAIUVSettings(bpy.types.PropertyGroup):
    model: EnumProperty(
        name="AI Model",
        description="UV unwrapping model of the Scenario API",
        items=_model_items,
        # Dynamische Enums brauchen den Default als Nummer; ohne ihn steht die
        # Property auf 0, und das ist keine der Modell-Nummern.
        default=models.enum_number(models.MODELS[0]["key"]),
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
    bpy.utils.register_class(SBAIUVHistoryItem)
    bpy.utils.register_class(SBAIUVSettings)
    bpy.types.Scene.sb_ai_uv = PointerProperty(type=SBAIUVSettings)
    # Am WindowManager, nicht an der Szene: die Liste ist die Anzeige einer
    # Datei und gehoert nicht in die .blend-Datei.
    bpy.types.WindowManager.sb_ai_uv_history = CollectionProperty(type=SBAIUVHistoryItem)
    bpy.types.WindowManager.sb_ai_uv_history_index = IntProperty(default=0)


def unregister():
    del bpy.types.WindowManager.sb_ai_uv_history_index
    del bpy.types.WindowManager.sb_ai_uv_history
    del bpy.types.Scene.sb_ai_uv
    bpy.utils.unregister_class(SBAIUVSettings)
    bpy.utils.unregister_class(SBAIUVHistoryItem)

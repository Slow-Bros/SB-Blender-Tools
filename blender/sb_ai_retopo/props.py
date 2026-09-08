# SPDX-License-Identifier: GPL-3.0-or-later
"""Szenen-Einstellungen und Laufzeitstatus fuer das Panel."""

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty


class SBAIRetopoSettings(bpy.types.PropertyGroup):
    face_level: EnumProperty(
        name="Ziel-Polygone",
        description=(
            "Polygondichte des Ergebnisses. Entspricht dem faceLevel der "
            "Scenario API, die nur diese drei Stufen kennt"
        ),
        items=(
            ("low", "Low", "Starke Reduktion, wenigste Polygone"),
            ("medium", "Medium", "Ausgewogene Reduktion"),
            ("high", "High", "Geringe Reduktion, meiste Polygone"),
        ),
        default="medium",
    )
    polygon_type: EnumProperty(
        name="Polygone",
        description="Topologie-Typ des Ergebnisses",
        items=(
            ("quadrilateral", "Quads", "Viereck-Topologie (Ergebnis wird als OBJ geladen, Quads bleiben erhalten)"),
            ("triangle", "Triangles", "Dreieck-Topologie"),
        ),
        default="quadrilateral",
    )
    pre_decimate: BoolProperty(
        name="Pre-Dezimierung",
        description="Sehr dichte Meshes vor dem Upload reduzieren (Upload-Limit 200 MB)",
        default=False,
    )
    pre_decimate_target: IntProperty(
        name="Upload-Faces",
        description="Ziel-Faces fuer die Pre-Dezimierung",
        default=200000,
        min=1000,
        soft_max=2000000,
        step=1000,
    )
    hide_source: BoolProperty(
        name="Original ausblenden",
        description="Original nach erfolgreichem Import im Viewport ausblenden (bleibt erhalten)",
        default=False,
    )

    # Laufzeitstatus (nicht gespeichert relevant, nur Anzeige)
    running: BoolProperty(default=False, options={"SKIP_SAVE"})
    progress: FloatProperty(default=0.0, min=0.0, max=1.0, subtype="FACTOR", options={"SKIP_SAVE"})
    status: StringProperty(default="", options={"SKIP_SAVE"})
    last_result: StringProperty(default="", options={"SKIP_SAVE"})
    last_error: StringProperty(default="", options={"SKIP_SAVE"})


def register():
    bpy.utils.register_class(SBAIRetopoSettings)
    bpy.types.Scene.sb_ai_retopo = PointerProperty(type=SBAIRetopoSettings)


def unregister():
    del bpy.types.Scene.sb_ai_retopo
    bpy.utils.unregister_class(SBAIRetopoSettings)

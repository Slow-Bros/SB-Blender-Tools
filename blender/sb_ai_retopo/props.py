# SPDX-License-Identifier: GPL-3.0-or-later
"""Szenen-Einstellungen und Laufzeitstatus fuer das Panel."""

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty


class SBAIRetopoSettings(bpy.types.PropertyGroup):
    target_faces: IntProperty(
        name="Ziel-Polygone",
        description=(
            "Gewuenschte Anzahl Faces. Die Scenario API kennt nur die Stufen "
            "low/medium/high; die Zahl wird ueber die Schwellen in den Add-on-"
            "Einstellungen auf eine Stufe abgebildet"
        ),
        default=10000,
        min=100,
        soft_max=200000,
        step=100,
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
    force_exact_count: BoolProperty(
        name="Exakte Anzahl (Decimate)",
        description=(
            "Reduziert das KI-Ergebnis nach dem Import per Decimate auf die Ziel-"
            "Polygonzahl, falls es mehr Faces hat. Bei Quads zerstoert das die "
            "Quad-Topologie teilweise"
        ),
        default=False,
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

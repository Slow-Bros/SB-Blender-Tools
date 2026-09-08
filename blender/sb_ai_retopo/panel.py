# SPDX-License-Identifier: GPL-3.0-or-later
"""N-Panel (Sidebar) im 3D-Viewport, Tab 'SBTools'."""

import bpy

from . import models, preferences
from .operators import is_running


class VIEW3D_PT_sb_ai_retopo(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SBTools"
    bl_label = "AI Retopo"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sb_ai_retopo
        obj = context.active_object
        running = is_running()

        api_key, api_secret = preferences.get_credentials(context)
        if not api_key or not api_secret:
            box = layout.box()
            box.label(text="Scenario API Key fehlt", icon="ERROR")
            box.operator("preferences.addon_show", text="Add-on-Einstellungen").module = __package__

        # -- Objekt -------------------------------------------------------
        box = layout.box()
        if obj is not None and obj.type == "MESH":
            box.label(text=obj.name, icon="MESH_DATA")
            box.label(text=f"{len(obj.data.polygons):,} Faces".replace(",", "."))
        else:
            box.label(text="Kein Mesh ausgewaehlt", icon="INFO")
        if context.mode != "OBJECT":
            box.label(text="Nur im Object Mode", icon="ERROR")

        # -- Einstellungen ------------------------------------------------
        col = layout.column(align=True)
        col.enabled = not running
        col.label(text="KI-Modell")
        col.prop(settings, "model", text="")
        col.separator()

        spec = models.get(settings.model)
        col.label(text="Ziel-Polygone")
        if models.uses_count(spec):
            col.prop(settings, "target_faces", text="")
            limited = models.clamp_count(spec, settings.target_faces) != settings.target_faces
            col.label(
                text=f"Modell erlaubt {models.count_range_label(spec)}",
                icon="ERROR" if limited else "NONE",
            )
        else:
            col.prop(settings, "face_level", expand=True)
            col.label(text="Modell kennt keine Zielzahl, nur Stufen")
        col.separator()
        col.label(text="Polygone")
        col.prop(settings, "polygon_type", expand=True)
        col.separator()
        col.prop(settings, "hide_source")

        row = col.row(align=True)
        row.prop(settings, "pre_decimate")
        sub = row.row(align=True)
        sub.enabled = settings.pre_decimate
        sub.prop(settings, "pre_decimate_target", text="")

        layout.separator()

        # -- Aktion / Status ----------------------------------------------
        if running:
            layout.prop(settings, "progress", text=settings.status or "Laeuft ...", slider=True)
            layout.operator("sb.ai_retopo_cancel", icon="CANCEL")
        else:
            layout.operator("sb.ai_retopo", icon="MOD_REMESH", text="KI-Retopologie starten")

        if settings.last_result:
            layout.label(text=settings.last_result, icon="CHECKMARK")
        if settings.last_warning:
            box = layout.box()
            for i, line in enumerate(_wrap(settings.last_warning, 42)):
                box.label(text=line, icon="ERROR" if i == 0 else "BLANK1")
        if settings.last_error:
            box = layout.box()
            box.alert = True
            for i, line in enumerate(_wrap(settings.last_error, 42)):
                box.label(text=line, icon="ERROR" if i == 0 else "BLANK1")


def _wrap(text, width):
    words = text.split()
    lines, current = [], ""
    for w in words:
        if len(current) + len(w) + 1 > width and current:
            lines.append(current)
            current = w
        else:
            current = f"{current} {w}".strip()
    if current:
        lines.append(current)
    return lines or [text]


def register():
    bpy.utils.register_class(VIEW3D_PT_sb_ai_retopo)


def unregister():
    bpy.utils.unregister_class(VIEW3D_PT_sb_ai_retopo)

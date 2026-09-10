# SPDX-License-Identifier: GPL-3.0-or-later
"""N-Panel (Sidebar) im 3D-Viewport, Tab 'SBTools'.

Der Tab ist derselbe wie beim Retopo-Add-on: Blender legt alle Panels mit
derselben bl_category in einen Tab, dafuer braucht es keine Verbindung
zwischen den Add-ons. bl_order haelt UV Layout unter AI Retopo.
"""

import textwrap

import bpy

from . import history, mesh_io, models, preferences
from .operators import is_running

STATUS_ICONS = {
    history.STATUS_RUNNING: "SORTTIME",
    history.STATUS_FINISHED: "CHECKMARK",
    history.STATUS_FAILED: "ERROR",
    history.STATUS_CANCELLED: "X",
}


def _pretty_time(started):
    """'2026-09-09T15:11:34' als '2026-09-09 15:11'."""
    return started.replace("T", " ")[:16] if started else "unknown"


def _pretty_size(size_mb):
    """Groesse des Ergebnisses. Unter einem MB in KB, sonst waere ein kleines
    Mesh dieselbe '0.00 MB' wie ein nie heruntergeladenes."""
    if size_mb >= 1.0:
        return f"{size_mb:.2f} MB"
    if size_mb > 0.0:
        return f"{size_mb * 1024:.0f} KB"
    return "size unknown"


class VIEW3D_PT_sb_ai_uv_layout(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SBTools"
    bl_label = "AI UV Layout"
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sb_ai_uv
        obj = context.active_object
        running = is_running()

        if models.LOAD_ERROR:
            box = layout.box()
            box.alert = True
            box.label(text="Model list unusable, fix models.json and restart", icon="ERROR")
            box.label(text=models.LOAD_ERROR[:60], icon="BLANK1")

        api_key, api_secret = preferences.get_credentials(context)
        if not api_key or not api_secret:
            box = layout.box()
            box.label(text="Scenario API key is missing", icon="ERROR")
            box.operator("preferences.addon_show", text="Add-on Preferences").module = __package__

        # -- Object -------------------------------------------------------
        box = layout.box()
        if obj is not None and obj.type == "MESH":
            box.label(text=obj.name, icon="MESH_DATA")
            box.label(text=f"{len(obj.data.polygons):,} faces")
            count = len(obj.data.uv_layers)
            if count >= mesh_io.MAX_UV_LAYERS:
                box.label(text=f"{count} UV maps, Blender's maximum. Delete one first", icon="ERROR")
            else:
                existing = f"{count} UV map{'s' if count != 1 else ''}, " if count else ""
                box.label(text=f"{existing}result becomes '{mesh_io.next_uv_layer_name(obj.data)}'",
                          icon="UV")
        else:
            box.label(text="No mesh selected", icon="INFO")
        if context.mode != "OBJECT":
            box.label(text="Object Mode only", icon="ERROR")

        # -- Settings -----------------------------------------------------
        col = layout.column(align=True)
        col.enabled = not running
        col.label(text="AI Model")
        col.prop(settings, "model", text="")

        layout.separator()

        # -- Action and status --------------------------------------------
        if running:
            layout.progress(text=settings.status or "Running ...", factor=settings.progress, type="BAR")
            layout.operator("sb.ai_uv_layout_cancel", icon="CANCEL")
        else:
            layout.operator("sb.ai_uv_layout", icon="UV", text="Generate UV Layout")

        if settings.last_result:
            layout.label(text=settings.last_result, icon="CHECKMARK")
        if settings.last_warning:
            box = layout.box()
            for i, line in enumerate(textwrap.wrap(settings.last_warning, 42)):
                box.label(text=line, icon="ERROR" if i == 0 else "BLANK1")
        if settings.last_error:
            box = layout.box()
            box.alert = True
            for i, line in enumerate(textwrap.wrap(settings.last_error, 42)):
                box.label(text=line, icon="ERROR" if i == 0 else "BLANK1")


class VIEW3D_UL_sb_ai_uv_history(bpy.types.UIList):
    """Eine Zeile pro Job: Name wie im Outliner, Modell, Status als Icon."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        row.label(text=item.name or item.job_id,
                  icon=STATUS_ICONS.get(item.status, "MESH_DATA"))
        sub = row.row()
        sub.alignment = "RIGHT"
        sub.label(text=item.model)


class VIEW3D_PT_sb_ai_uv_history(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SBTools"
    bl_parent_id = "VIEW3D_PT_sb_ai_uv_layout"
    bl_label = "History"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sb_ai_uv
        wm = context.window_manager

        row = layout.row(align=True)
        row.prop(settings, "history_this_project")
        row.operator("sb.ai_uv_layout_history_refresh", text="", icon="FILE_REFRESH")

        if not wm.sb_ai_uv_history:
            if settings.history_this_project and not bpy.data.filepath:
                layout.label(text="No jobs in this unsaved file yet", icon="INFO")
            else:
                layout.label(text="No jobs yet", icon="INFO")
            return

        layout.template_list("VIEW3D_UL_sb_ai_uv_history", "",
                             wm, "sb_ai_uv_history",
                             wm, "sb_ai_uv_history_index", rows=4)

        item = history.selected(context)
        if item is None:
            return

        box = layout.box()
        box.label(text=_pretty_time(item.started), icon="TIME")
        box.label(text=item.model or "unknown model", icon="UV")
        box.label(text=item.status or "unknown",
                  icon=STATUS_ICONS.get(item.status, "QUESTION"))
        box.label(text=_pretty_size(item.size_mb), icon="FILE_3D")
        box.label(text=item.job_id, icon="COPY_ID")
        if not item.blend_file:
            box.label(text="No project, the file was never saved", icon="INFO")

        col = layout.column()
        col.enabled = not is_running()
        col.operator("sb.ai_uv_layout_history_import", icon="IMPORT")


classes = (VIEW3D_PT_sb_ai_uv_layout, VIEW3D_UL_sb_ai_uv_history,
           VIEW3D_PT_sb_ai_uv_history)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

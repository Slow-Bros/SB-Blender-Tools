# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on-Einstellungen: Zugangsdaten, Verbindung und Cache des Modellkatalogs.

Der Katalog wird bewusst nur auf Knopfdruck geholt und dann in den Preferences
zwischengespeichert. Das Zeichnen eines Panels darf niemals Netzwerkverkehr
ausloesen, weil Blender staendig neu zeichnet.
"""

import json
import os
import time

import bpy
from bpy.props import IntProperty, StringProperty

from . import models

ENV_KEY = "SCENARIO_API_KEY"
ENV_SECRET = "SCENARIO_API_SECRET"


class SBAIRetopoPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    api_key: StringProperty(
        name="API Key",
        description=f"Scenario API key (or set the environment variable {ENV_KEY})",
        subtype="PASSWORD",
    )
    api_secret: StringProperty(
        name="API Secret",
        description=f"Scenario API secret (or set the environment variable {ENV_SECRET})",
        subtype="PASSWORD",
    )

    poll_interval: IntProperty(
        name="Poll Interval (s)",
        description="Seconds between two status requests to the Scenario API",
        default=5,
        min=1,
        max=60,
    )
    job_timeout_minutes: IntProperty(
        name="Job Timeout (min)",
        description="How long to wait for a retopology job before giving up",
        default=15,
        min=1,
        max=120,
    )

    # Cache des Modellkatalogs als JSON-Text, damit er die Sitzung ueberlebt
    catalogue_json: StringProperty(default="", options={"HIDDEN"})
    catalogue_fetched: StringProperty(default="", options={"HIDDEN"})

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Scenario API", icon="WORLD")
        box.prop(self, "api_key")
        box.prop(self, "api_secret")
        box.label(text=f"Or use the environment variables {ENV_KEY} / {ENV_SECRET}", icon="INFO")

        box = layout.box()
        box.label(text="Connection", icon="TIME")
        row = box.row(align=True)
        row.prop(self, "poll_interval")
        row.prop(self, "job_timeout_minutes")

        box = layout.box()
        box.label(text="Models", icon="PRESET")
        box.label(text=f"Registry: {len(models.MODELS)} models from {_short(models.LOADED_FROM)}")
        if models.LOAD_ERROR:
            sub = box.box()
            sub.alert = True
            sub.label(text=f"Registry problem: {models.LOAD_ERROR}", icon="ERROR")

        if models.user_file_in_use():
            sub = box.box()
            sub.label(text="An edited user copy is in use, not the bundled list.", icon="INFO")
            sub.label(text="Models added with the add-on will not show up until it is reset.",
                      icon="BLANK1")
            sub.operator("sb.ai_retopo_reset_registry", icon="LOOP_BACK")

        row = box.row(align=True)
        row.operator("sb.ai_retopo_refresh_models", icon="FILE_REFRESH")
        row.operator("sb.ai_retopo_reload_registry", icon="FILE_REFRESH")
        row.operator("sb.ai_retopo_export_registry", icon="EXPORT")

        catalogue = self.catalogue()
        if catalogue:
            box.label(text=f"Catalogue: {len(catalogue)} models, checked {self.catalogue_fetched}")
            missing = models.classify({m[0] for m in catalogue})["missing"]
            if missing:
                sub = box.box()
                sub.alert = True
                sub.label(text="No longer offered by the API:", icon="ERROR")
                for model_id in missing:
                    sub.label(text=model_id, icon="BLANK1")
            unknown = models.unknown_candidates(catalogue)
            if unknown:
                sub = box.box()
                sub.label(text="Retopology models missing from the registry:", icon="INFO")
                for model_id, name in unknown[:8]:
                    sub.label(text=f"{model_id}  {name}".strip(), icon="BLANK1")
                sub.label(text="Add them to the registry file to make them selectable.", icon="BLANK1")
        else:
            box.label(text="Catalogue not fetched yet.", icon="INFO")

        user_path = models.user_file_path()
        if user_path:
            box.label(text=f"Registry file: {user_path}")

    # -- Cache-Zugriff ---------------------------------------------------

    def catalogue(self):
        """Zwischengespeicherter Katalog als Liste von (id, name)."""
        if not self.catalogue_json:
            return []
        try:
            data = json.loads(self.catalogue_json)
        except (json.JSONDecodeError, TypeError):
            return []
        return [(str(e[0]), str(e[1])) for e in data if isinstance(e, (list, tuple)) and e]

    def set_catalogue(self, entries):
        self.catalogue_json = json.dumps([[i, n] for i, n in entries])
        self.catalogue_fetched = time.strftime("%Y-%m-%d %H:%M")

    def available_ids(self):
        return {model_id for model_id, _ in self.catalogue()}


def _short(path):
    if not path:
        return "unknown"
    return os.path.basename(path) if os.sep in path else path


def get_prefs(context=None) -> SBAIRetopoPreferences:
    context = context or bpy.context
    return context.preferences.addons[__package__].preferences


def get_credentials(context=None):
    """Zugangsdaten aus den Einstellungen, Fallback auf Umgebungsvariablen."""
    prefs = get_prefs(context)
    key = (prefs.api_key or os.environ.get(ENV_KEY, "")).strip()
    secret = (prefs.api_secret or os.environ.get(ENV_SECRET, "")).strip()
    return key, secret


def register():
    bpy.utils.register_class(SBAIRetopoPreferences)


def unregister():
    bpy.utils.unregister_class(SBAIRetopoPreferences)

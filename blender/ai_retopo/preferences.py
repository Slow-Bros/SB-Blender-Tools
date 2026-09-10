# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on-Einstellungen: Zugangsdaten und Verbindungsparameter.

Die Modell-Liste hat hier bewusst keine Oberflaeche, siehe docs/ai-retopo.md
("Changing the model list").
"""

import os

import bpy
from bpy.props import IntProperty, StringProperty

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

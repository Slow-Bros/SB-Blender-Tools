# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on-Einstellungen: Scenario-Zugangsdaten und Verbindungsparameter."""

import os

import bpy
from bpy.props import IntProperty, StringProperty

ENV_KEY = "SCENARIO_API_KEY"
ENV_SECRET = "SCENARIO_API_SECRET"


class SBAIRetopoPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    api_key: StringProperty(
        name="API Key",
        description=f"Scenario API key (alternativ Umgebungsvariable {ENV_KEY})",
        subtype="PASSWORD",
    )
    api_secret: StringProperty(
        name="API Secret",
        description=f"Scenario API secret (alternativ Umgebungsvariable {ENV_SECRET})",
        subtype="PASSWORD",
    )

    poll_interval: IntProperty(
        name="Poll-Intervall (s)",
        description="Abstand zwischen zwei Status-Abfragen an die Scenario API",
        default=5,
        min=1,
        max=60,
    )
    job_timeout_minutes: IntProperty(
        name="Job-Timeout (min)",
        description="Maximale Wartezeit auf den Retopologie-Job",
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
        box.label(
            text=f"Alternativ: Umgebungsvariablen {ENV_KEY} / {ENV_SECRET}",
            icon="INFO",
        )

        box = layout.box()
        box.label(text="Verbindung", icon="TIME")
        row = box.row(align=True)
        row.prop(self, "poll_interval")
        row.prop(self, "job_timeout_minutes")


def get_prefs(context=None) -> SBAIRetopoPreferences:
    context = context or bpy.context
    return context.preferences.addons[__package__].preferences


def get_credentials(context=None):
    """Zugangsdaten aus den Add-on-Einstellungen, Fallback auf Umgebungsvariablen."""
    prefs = get_prefs(context)
    key = (prefs.api_key or os.environ.get(ENV_KEY, "")).strip()
    secret = (prefs.api_secret or os.environ.get(ENV_SECRET, "")).strip()
    return key, secret


def register():
    bpy.utils.register_class(SBAIRetopoPreferences)


def unregister():
    bpy.utils.unregister_class(SBAIRetopoPreferences)

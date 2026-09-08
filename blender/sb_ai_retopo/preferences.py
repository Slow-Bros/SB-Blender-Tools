# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on-Einstellungen: Scenario-Zugangsdaten und Face-Level-Schwellen."""

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

    # Die Scenario API kennt nur drei Stufen (low/medium/high). Die im Panel
    # eingegebene Ziel-Polygonzahl wird über diese Schwellen auf eine Stufe
    # abgebildet. Werte sind Erfahrungswerte und dürfen angepasst werden.
    face_level_low_max: IntProperty(
        name="Low bis",
        description="Ziel-Faces bis einschließlich dieses Werts → faceLevel 'low'",
        default=5000,
        min=1,
    )
    face_level_medium_max: IntProperty(
        name="Medium bis",
        description="Ziel-Faces bis einschließlich dieses Werts → faceLevel 'medium', darüber 'high'",
        default=20000,
        min=1,
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
        box.label(text="Ziel-Faces → Scenario faceLevel", icon="MESH_DATA")
        row = box.row(align=True)
        row.prop(self, "face_level_low_max")
        row.prop(self, "face_level_medium_max")
        box.label(text="Die API akzeptiert nur low / medium / high, keine exakte Zahl.", icon="INFO")

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


def face_level_for_target(target_faces: int, context=None) -> str:
    prefs = get_prefs(context)
    if target_faces <= prefs.face_level_low_max:
        return "low"
    if target_faces <= prefs.face_level_medium_max:
        return "medium"
    return "high"


def register():
    bpy.utils.register_class(SBAIRetopoPreferences)


def unregister():
    bpy.utils.unregister_class(SBAIRetopoPreferences)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on-Einstellungen: Zugangsdaten und Verbindungsparameter.

Die Zugangsdaten liegen nicht hier, sondern im gemeinsamen Speicher aller
SBTools-Add-ons (credentials.py); die Properties sind nur eine Ansicht darauf.
Wer den Key im Retopo-Add-on eingetragen hat, sieht ihn hier wieder.
"""

import bpy
from bpy.props import IntProperty

from . import credentials


class SBAIUVLayoutPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    scenario_api_key: credentials.key_property()
    scenario_api_secret: credentials.secret_property()

    poll_interval: IntProperty(
        name="Poll Interval (s)",
        description="Seconds between two status requests to the Scenario API",
        default=5,
        min=1,
        max=60,
    )
    job_timeout_minutes: IntProperty(
        name="Job Timeout (min)",
        description="How long to wait for a UV unwrapping job before giving up",
        default=15,
        min=1,
        max=120,
    )

    def draw(self, context):
        layout = self.layout
        credentials.draw_preferences(layout, self)

        box = layout.box()
        box.label(text="Connection", icon="TIME")
        row = box.row(align=True)
        row.prop(self, "poll_interval")
        row.prop(self, "job_timeout_minutes")


def get_prefs(context=None) -> SBAIUVLayoutPreferences:
    context = context or bpy.context
    return context.preferences.addons[__package__].preferences


def get_credentials(context=None):
    """Zugangsdaten aus dem gemeinsamen Speicher, Fallback auf Umgebungsvariablen."""
    return credentials.resolve()


def register():
    bpy.utils.register_class(SBAIUVLayoutPreferences)


def unregister():
    bpy.utils.unregister_class(SBAIUVLayoutPreferences)

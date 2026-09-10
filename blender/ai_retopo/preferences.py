# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on-Einstellungen: Zugangsdaten und Verbindungsparameter.

Die Zugangsdaten liegen nicht hier, sondern im gemeinsamen Speicher aller
SBTools-Add-ons (credentials.py); die Properties sind nur eine Ansicht darauf.
Die Modell-Liste hat hier bewusst keine Oberflaeche, siehe docs/ai-retopo.md
("Changing the model list").
"""

import bpy
from bpy.props import IntProperty, StringProperty

from . import credentials
from .log import log

# Version 0.1.0 hielt die Zugangsdaten in den Properties api_key/api_secret der
# Einstellungen selbst. Sie bleiben definiert, sonst kaeme Blender an die in
# userpref.blend gespeicherten Werte nicht mehr heran; beim ersten Zugriff
# wandern sie in den gemeinsamen Speicher und werden hier geleert.
LEGACY_FIELDS = ("api_key", "api_secret")


class SBAIRetopoPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    scenario_api_key: credentials.key_property()
    scenario_api_secret: credentials.secret_property()

    # Nur noch fuer die Uebernahme aus 0.1.0, nicht mehr in der Oberflaeche
    api_key: StringProperty(name="API Key (version 0.1.0)", subtype="PASSWORD", options={"HIDDEN"})
    api_secret: StringProperty(name="API Secret (version 0.1.0)", subtype="PASSWORD", options={"HIDDEN"})

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
        migrate_legacy(self)
        credentials.draw_preferences(layout, self)

        box = layout.box()
        box.label(text="Connection", icon="TIME")
        row = box.row(align=True)
        row.prop(self, "poll_interval")
        row.prop(self, "job_timeout_minutes")


def get_prefs(context=None) -> SBAIRetopoPreferences:
    context = context or bpy.context
    return context.preferences.addons[__package__].preferences


def migrate_legacy(prefs):
    """Uebernimmt Zugangsdaten aus Version 0.1.0 in den gemeinsamen Speicher.

    Der alte Eintrag wird danach geloescht, damit das Geheimnis nicht doppelt
    liegt. Steht im gemeinsamen Speicher schon etwas, gewinnt das, und der alte
    Eintrag faellt weg.
    Returns: True, wenn ein alter Eintrag gefunden wurde.
    """
    legacy = {name: getattr(prefs, name).strip() for name in LEGACY_FIELDS}
    if not any(legacy.values()):
        return False
    stored = credentials.load()
    if not any(stored.values()):
        if not credentials.save(**legacy):
            # Datei nicht schreibbar: alten Eintrag behalten, spaeter erneut
            return True
        log("Moved the Scenario credentials into the shared SBTools store")
    else:
        log("Shared SBTools credentials already exist, dropped the old add-on entry")
    for name in LEGACY_FIELDS:
        setattr(prefs, name, "")
    try:
        bpy.context.preferences.is_dirty = True
    except AttributeError:
        pass
    return True


def get_credentials(context=None):
    """Zugangsdaten aus dem gemeinsamen Speicher, Fallback auf Umgebungsvariablen."""
    try:
        migrate_legacy(get_prefs(context))
    except (KeyError, AttributeError):
        # Beim Aktivieren ist der Kontext eingeschraenkt
        pass
    return credentials.resolve()


def register():
    bpy.utils.register_class(SBAIRetopoPreferences)


def unregister():
    bpy.utils.unregister_class(SBAIRetopoPreferences)

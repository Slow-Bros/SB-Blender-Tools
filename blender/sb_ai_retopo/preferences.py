# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on-Einstellungen: Zugangsdaten, Verbindung und Modellpruefung.

Geprueft wird nur auf Knopfdruck, das Ergebnis liegt danach in den Preferences.
Das Zeichnen eines Panels darf niemals Netzwerkverkehr ausloesen, weil Blender
staendig neu zeichnet.

Frueher stand hier ein Abgleich gegen GET /v1/models. Dieser Endpunkt listet
die selbst trainierten Modelle des Accounts und antwortete mit einer leeren
Liste, obwohl die Plattform-Modelle nachweislich laufen. Er ist deshalb
entfernt; geprueft wird jede Modell-ID einzeln.
"""

import json
import os

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

    # Ergebnis der Modellpruefung, damit es die Sitzung ueberlebt
    probe_error: StringProperty(default="", options={"HIDDEN"})
    probe_control: StringProperty(default="", options={"HIDDEN"})
    # Ergebnis der gezielten Abfragen: {model_id: [status, erlaeuterung]}
    probe_json: StringProperty(default="", options={"HIDDEN"})
    probe_id: StringProperty(
        name="Model Id",
        description="Any model id to check against the API, for example one that was removed",
        default="model_meshy-remesh",
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
        row.operator("sb.ai_retopo_reload_registry", icon="FILE_REFRESH")
        row.operator("sb.ai_retopo_export_registry", icon="EXPORT")

        probes = self.probes()
        if self.probe_error:
            sub = box.box()
            sub.alert = True
            sub.label(text="Last model check failed:", icon="ERROR")
            for line in _wrap(self.probe_error, 70):
                sub.label(text=line, icon="BLANK1")

        if self.probe_control and self.probe_control != "missing":
            sub = box.box()
            sub.alert = True
            sub.label(text="The check cannot tell models apart", icon="ERROR")
            sub.label(text="A deliberately invalid id also came back as "
                           f"'{self.probe_control}',", icon="BLANK1")
            sub.label(text="so treat every verdict below as meaningless.", icon="BLANK1")

        if probes:
            sub = box.box()
            sub.label(text="Checked against the API:", icon="CHECKMARK")
            icons = {"available": "CHECKMARK", "missing": "CANCEL", "unknown": "QUESTION"}
            known = models.known_ids()
            for model_id, (status, note) in sorted(probes.items()):
                row = sub.row()
                row.alert = status == "missing"
                suffix = "" if model_id in known else "  not in the registry"
                row.label(text=f"{model_id}: {status} ({note}){suffix}",
                          icon=icons.get(status, "DOT"))
            if any(m not in known for m in probes):
                sub.label(text="To use one that is not in the registry, add it to the "
                               "registry file with its parameters.", icon="INFO")

        row = box.row(align=True)
        row.prop(self, "probe_id", text="")
        row.operator("sb.ai_retopo_probe_model", icon="VIEWZOOM")

        user_path = models.user_file_path()
        if user_path:
            box.label(text=f"Registry file: {user_path}")

    # -- Cache-Zugriff ---------------------------------------------------

    def probes(self):
        """Ergebnisse der gezielten Abfragen als {model_id: (status, note)}."""
        if not self.probe_json:
            return {}
        try:
            data = json.loads(self.probe_json)
        except (json.JSONDecodeError, TypeError):
            return {}
        return {k: (v[0], v[1]) for k, v in data.items() if isinstance(v, list) and len(v) == 2}

    def set_probes(self, results):
        merged = self.probes()
        merged.update(results)
        self.probe_json = json.dumps({k: [s, n] for k, (s, n) in merged.items()})

    def probe_status(self, model_id):
        """'available', 'missing' oder 'unknown' fuer ein Modell."""
        return self.probes().get(model_id, ("unknown", "not checked"))[0]


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

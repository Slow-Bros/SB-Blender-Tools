# SPDX-License-Identifier: GPL-3.0-or-later
"""Gemeinsamer Speicher der Scenario-Zugangsdaten fuer alle SBTools-Add-ons.

Jedes Add-on ist eine eigene Extension mit eigenen Einstellungen, und Blender
kennt keine Abhaengigkeiten zwischen Extensions, ueber die eines die
Einstellungen des anderen lesen koennte. Der API-Key liegt deshalb in einer
Datei im Config-Ordner von Blender, und die Einstellungsseite jedes Add-ons ist
nur eine Ansicht darauf: einmal eingetragen, gilt er fuer alle.

Dieses Modul liegt als identische Kopie in jedem Add-on unter blender/. Die
Headless-Tests pruefen, dass die Kopien uebereinstimmen; eine Aenderung gehoert
in alle. Deshalb steht hier nichts Add-on-Spezifisches, auch kein Log-Prefix.
"""

import json
import os
import tempfile

import bpy
from bpy.props import StringProperty

ENV_KEY = "SCENARIO_API_KEY"
ENV_SECRET = "SCENARIO_API_SECRET"

DIR_NAME = "sbtools"
FILE_NAME = "scenario_credentials.json"
FIELDS = ("api_key", "api_secret")

_cache = None
_cache_stamp = None


def directory():
    """Ordner der gemeinsamen Datei: <Blender-Config>/sbtools.

    Der Config-Ordner gehoert zur Blender-Version, nicht zur Extension, und ist
    damit der einzige Ort, den alle Add-ons gleich finden.
    """
    return bpy.utils.user_resource("CONFIG", path=DIR_NAME)


def path():
    return os.path.join(directory(), FILE_NAME)


def _stamp(file_path):
    try:
        st = os.stat(file_path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def load():
    """Gespeicherte Zugangsdaten als dict mit api_key und api_secret, leer wenn keine.

    Gelesen wird nur, wenn sich die Datei geaendert hat: die Getter der
    Einstellungen rufen das bei jedem Zeichnen auf.
    """
    global _cache, _cache_stamp
    file_path = path()
    stamp = _stamp(file_path)
    if _cache is not None and stamp == _cache_stamp:
        return dict(_cache)

    data = {}
    if stamp is not None:
        try:
            with open(file_path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception as e:  # noqa: BLE001 - eine kaputte Datei darf nichts blockieren
            print(f"[SBTOOLS] Credentials file unreadable ({e}): {file_path}")

    _cache = {k: str(data.get(k) or "").strip() for k in FIELDS}
    _cache_stamp = stamp
    return dict(_cache)


def save(**fields):
    """Schreibt die angegebenen Felder, die anderen bleiben stehen.

    Geschrieben wird ueber eine temporaere Datei und os.replace, damit ein
    Absturz mitten im Schreiben die alte Datei ganz laesst.
    Returns: True, wenn die Datei geschrieben wurde.
    """
    global _cache, _cache_stamp
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise ValueError(f"unknown credential field(s): {sorted(unknown)}")

    data = load()
    data.update({k: str(v or "").strip() for k, v in fields.items()})
    file_path = path()
    folder = os.path.dirname(file_path)
    tmp = None
    try:
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".credentials_", suffix=".json", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, file_path)
    except Exception as e:  # noqa: BLE001 - ohne die Datei laeuft das Add-on weiter
        print(f"[SBTOOLS] Could not write the credentials file {file_path}: {e}")
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
        return False
    _cache = data
    _cache_stamp = _stamp(file_path)
    return True


def resolve():
    """(key, secret) fuer einen API-Aufruf: die Datei, sonst die Umgebungsvariablen."""
    data = load()
    key = (data["api_key"] or os.environ.get(ENV_KEY, "")).strip()
    secret = (data["api_secret"] or os.environ.get(ENV_SECRET, "")).strip()
    return key, secret


# -- Anbindung an die AddonPreferences ------------------------------------
#
# Die Properties speichern nichts selbst: Getter und Setter gehen direkt auf
# die Datei. So zeigt jedes Add-on denselben Stand, und Blender legt keine
# zweite Kopie des Geheimnisses in userpref.blend ab.

def _getter(field):
    return lambda self: load()[field]


def _setter(field):
    # Blender verlangt None als Rueckgabe eines Setters
    def set_value(self, value):
        save(**{field: value})
    return set_value


def key_property():
    return StringProperty(
        name="API Key",
        description=(
            "Scenario API key, shared by all SBTools add-ons "
            f"(or set the environment variable {ENV_KEY})"
        ),
        subtype="PASSWORD",
        get=_getter("api_key"),
        set=_setter("api_key"),
    )


def secret_property():
    return StringProperty(
        name="API Secret",
        description=(
            "Scenario API secret, shared by all SBTools add-ons "
            f"(or set the environment variable {ENV_SECRET})"
        ),
        subtype="PASSWORD",
        get=_getter("api_secret"),
        set=_setter("api_secret"),
    )


def draw_preferences(layout, prefs, key_attr="scenario_api_key", secret_attr="scenario_api_secret"):
    """Der gemeinsame Block in den Einstellungen eines Add-ons."""
    box = layout.box()
    box.label(text="Scenario API (shared by all SBTools add-ons)", icon="WORLD")
    box.prop(prefs, key_attr)
    box.prop(prefs, secret_attr)
    box.label(text=f"Or use the environment variables {ENV_KEY} / {ENV_SECRET}", icon="INFO")
    box.label(text=f"Stored in {path()}", icon="FILE_FOLDER")

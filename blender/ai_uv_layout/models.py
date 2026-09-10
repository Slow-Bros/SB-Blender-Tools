# SPDX-License-Identifier: GPL-3.0-or-later
"""Registry der UV-Unwrapping-Modelle der Scenario API.

Die Tabelle steht in models.json neben diesem Modul, damit ein Modell ohne
Code-Aenderung ergaenzt oder entfernt werden kann. Zurzeit gibt es bei
Scenario genau eines, und es nimmt ausser der Datei keine Parameter; die
Registry ist trotzdem da, damit ein zweites nur ein JSON-Eintrag ist.

Quelle: docs.scenario.com, Endpunkt /v1/generate/custom/{model_id}.
"""

import json
import os
import zlib

from .log import log

BUNDLED_FILE = os.path.join(os.path.dirname(__file__), "models.json")

REQUIRED_KEYS = ("key", "id", "label", "file_param")

# Platzhalter, falls models.json fehlt oder unbrauchbar ist: Panel und Enum
# haben damit etwas anzuzeigen, das Add-on registriert sich trotzdem. Einen
# Job startet der Operator damit nicht (poll prueft LOAD_ERROR), die id ist
# bewusst leer.
FALLBACK_MODELS = [
    {
        "key": "registry_broken",
        "id": "",
        "label": "Model list unusable",
        "description": "models.json could not be read, see the panel",
        "file_param": "file3d",
        "extra": {},
    },
]

MODELS = []
LOAD_ERROR = ""


def _validate(entry):
    for key in REQUIRED_KEYS:
        if key not in entry:
            raise ValueError(f"model entry is missing '{key}'")
    entry.setdefault("description", entry["label"])
    entry.setdefault("extra", {})
    if not isinstance(entry["extra"], dict):
        raise ValueError(f"'{entry['key']}' has an 'extra' that is not an object")
    return entry


def _read(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("models") if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entries:
        raise ValueError("no 'models' list in file")
    result = []
    seen = set()
    numbers = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("model entry is not an object")
        entry = _validate(dict(entry))
        if entry["key"] in seen:
            raise ValueError(f"duplicate model key '{entry['key']}'")
        seen.add(entry["key"])
        number = enum_number(entry["key"])
        if number in numbers:
            raise ValueError(f"enum number of '{entry['key']}' collides with '{numbers[number]}'")
        numbers[number] = entry["key"]
        result.append(entry)
    return result


def load():
    """Laedt die Registry aus models.json, mit eingebautem Notnagel."""
    global MODELS, LOAD_ERROR
    try:
        MODELS = _read(BUNDLED_FILE)
        LOAD_ERROR = ""
        return MODELS
    except Exception as e:  # noqa: BLE001 - eine kaputte Datei darf nichts blockieren
        MODELS = [dict(m) for m in FALLBACK_MODELS]
        LOAD_ERROR = str(e)
        log(f"Model registry unusable: {e}")
        return MODELS


def get(key):
    """Modell-Spezifikation zu einem Schluessel, Fallback auf das erste Modell.

    MODELS ist nie leer: _read lehnt eine leere Liste ab und load() setzt
    sonst den Notnagel ein.
    """
    for spec in MODELS:
        if spec["key"] == key:
            return spec
    return MODELS[0]


def enum_number(key):
    """Stabile Nummer eines Modells fuer die EnumProperty.

    Blender speichert die Auswahl als Integer in der .blend-Datei. Ohne feste
    Nummer waere das der Listenindex, und ein Umsortieren von models.json
    wuerde in gespeicherten Szenen still ein anderes Modell auswaehlen.
    """
    return zlib.crc32(key.encode()) & 0x7FFFFFFF


def enum_items():
    """Items fuer die EnumProperty im Panel: (id, name, description, icon, number)."""
    return tuple((m["key"], m["label"], m["description"], "NONE", enum_number(m["key"]))
                 for m in MODELS)


def build_request(spec, asset_id):
    """Baut den Request-Body fuer /v1/generate/custom/{id}.

    Wie in Phototron besteht er nur aus der hochgeladenen Datei; extra aus der
    Registry geht unveraendert mit.
    """
    body = {spec["file_param"]: asset_id}
    body.update(spec.get("extra", {}))
    return body


# Am Ende, weil _read auf enum_number zugreift
load()

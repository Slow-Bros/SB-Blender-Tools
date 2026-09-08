# SPDX-License-Identifier: GPL-3.0-or-later
"""Registry der Retopologie-Modelle der Scenario API.

Die Tabelle steht in models.json neben diesem Modul, damit ein Modell ohne
Code-Aenderung ergaenzt oder korrigiert werden kann. Eine Benutzerkopie unter
<Blender-Config>/sb_ai_retopo_models.json hat Vorrang und ueberlebt das
Neuinstallieren des Add-ons.

Jedes Modell hat eigene Parameternamen und eine eigene Art, die Polygondichte
zu steuern. Zwei Modelle nehmen eine echte Zielzahl entgegen, eines kennt nur
drei Stufen. Das Panel richtet sich nach `density` des gewaehlten Modells.

Quelle: docs.scenario.com, Endpunkt /v1/generate/custom/{model_id}.
"""

import json
import os

# Art der Dichtesteuerung
DENSITY_LEVEL = "level"  # nur low / medium / high
DENSITY_COUNT = "count"  # echte Zielzahl an Faces

# Interne, modellunabhaengige Werte fuer den Topologie-Typ
QUADS = "quads"
TRIS = "tris"

BUNDLED_FILE = os.path.join(os.path.dirname(__file__), "models.json")
USER_FILE_NAME = "sb_ai_retopo_models.json"

REQUIRED_KEYS = ("key", "id", "label", "density", "file_param",
                 "polygon_param", "polygon_values")

# Notnagel, falls models.json fehlt oder unbrauchbar ist. Das Add-on bleibt
# damit bedienbar, statt beim Registrieren auszusteigen.
FALLBACK_MODELS = [
    {
        "key": "hunyuan_polygen",
        "id": "model_tencent-smarttopology",
        "label": "Hunyuan PolyGen 1.5",
        "description": "Tencent Hunyuan PolyGen 1.5, three density levels",
        "density": DENSITY_LEVEL,
        "file_param": "file3d",
        "polygon_param": "polygonType",
        "polygon_values": {QUADS: "quadrilateral", TRIS: "triangle"},
        "level_param": "faceLevel",
        "extra": {"geometryFileFormat": "obj"},
    },
]

MODELS = []
LOAD_ERROR = ""
LOADED_FROM = ""


def user_file_path():
    """Pfad der Benutzerkopie im Blender-Konfigurationsordner."""
    try:
        import bpy
        base = bpy.utils.user_resource("CONFIG")
    except Exception:
        return ""
    return os.path.join(base, USER_FILE_NAME) if base else ""


def _validate(entry):
    for key in REQUIRED_KEYS:
        if key not in entry:
            raise ValueError(f"model entry is missing '{key}'")
    if entry["density"] not in (DENSITY_LEVEL, DENSITY_COUNT):
        raise ValueError(f"unknown density '{entry['density']}' in '{entry['key']}'")

    values = entry["polygon_values"]
    for pk in (QUADS, TRIS):
        if pk not in values:
            raise ValueError(f"'{entry['key']}' has no polygon value for '{pk}'")

    if entry["density"] == DENSITY_COUNT:
        for key in ("count_param", "count_min", "count_max"):
            if key not in entry:
                raise ValueError(f"'{entry['key']}' is missing '{key}'")
        if entry["count_min"] > entry["count_max"]:
            raise ValueError(f"'{entry['key']}' has count_min above count_max")
        entry.setdefault("count_default", entry["count_min"])
    else:
        entry.setdefault("level_param", "faceLevel")

    entry.setdefault("description", entry["label"])
    entry.setdefault("extra", {})
    return entry


def _read(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("models") if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entries:
        raise ValueError("no 'models' list in file")
    result = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("model entry is not an object")
        entry = _validate(dict(entry))
        if entry["key"] in seen:
            raise ValueError(f"duplicate model key '{entry['key']}'")
        seen.add(entry["key"])
        result.append(entry)
    return result


def load():
    """Laedt die Registry: Benutzerkopie vor mitgelieferter Datei vor Notnagel."""
    global MODELS, LOAD_ERROR, LOADED_FROM
    errors = []
    for label, path in (("user file", user_file_path()), ("bundled file", BUNDLED_FILE)):
        if not path or not os.path.exists(path):
            continue
        try:
            MODELS = _read(path)
            LOAD_ERROR = "; ".join(errors)
            LOADED_FROM = path
            return MODELS
        except Exception as e:  # noqa: BLE001 - ein kaputtes File darf nichts blockieren
            errors.append(f"{label}: {e}")

    MODELS = [dict(m) for m in FALLBACK_MODELS]
    LOADED_FROM = "built-in fallback"
    LOAD_ERROR = "; ".join(errors) or "models.json not found"
    return MODELS


def save_user_file(path=None):
    """Schreibt die aktuelle Registry als Benutzerkopie zum Bearbeiten."""
    path = path or user_file_path()
    if not path:
        raise RuntimeError("Blender config directory is not available")
    payload = {"version": 1, "models": MODELS}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def user_file_in_use():
    """True, wenn gerade die Benutzerkopie und nicht die mitgelieferte Datei gilt."""
    path = user_file_path()
    return bool(path) and LOADED_FROM == path


def reset_user_file():
    """Benennt die Benutzerkopie um, damit wieder die mitgelieferte Datei gilt.

    Bewusst kein Loeschen: die Datei kann von Hand bearbeitet worden sein.

    Returns: Pfad der Sicherung, oder None wenn es keine Benutzerkopie gab.
    """
    path = user_file_path()
    if not path or not os.path.exists(path):
        return None
    backup = path + ".bak"
    if os.path.exists(backup):
        os.remove(backup)
    os.replace(path, backup)
    return backup


load()


def default_key():
    return MODELS[0]["key"] if MODELS else ""


def get(key):
    """Modell-Spezifikation zu einem Schluessel, Fallback auf das erste Modell."""
    for spec in MODELS:
        if spec["key"] == key:
            return spec
    return MODELS[0] if MODELS else dict(FALLBACK_MODELS[0])


def known_ids():
    return {spec["id"] for spec in MODELS}


def enum_items():
    """Items fuer die EnumProperty im Panel."""
    return tuple((m["key"], m["label"], m["description"]) for m in MODELS)


def uses_count(spec):
    return spec["density"] == DENSITY_COUNT


def clamp_count(spec, value):
    """Zielzahl in den vom Modell erlaubten Bereich zwingen."""
    if not uses_count(spec):
        return value
    return max(spec["count_min"], min(spec["count_max"], int(value)))


def count_range_label(spec):
    if not uses_count(spec):
        return ""
    return f"{spec['count_min']:,} to {spec['count_max']:,}"


def build_request(spec, asset_id, polygon_key, *, face_level=None, target_faces=None):
    """Baut den Request-Body fuer /v1/generate/custom/{id}.

    polygon_key ist QUADS oder TRIS und wird auf den Wert des jeweiligen
    Modells abgebildet. Je nach Modell wird entweder face_level oder
    target_faces verwendet.
    """
    if polygon_key not in (QUADS, TRIS):
        raise ValueError(f"unknown topology key: {polygon_key}")

    body = {spec["file_param"]: asset_id}
    body[spec["polygon_param"]] = spec["polygon_values"][polygon_key]

    if uses_count(spec):
        if target_faces is None:
            target_faces = spec["count_default"]
        body[spec["count_param"]] = clamp_count(spec, target_faces)
    else:
        body[spec["level_param"]] = face_level or "medium"

    body.update(spec.get("extra", {}))
    return body

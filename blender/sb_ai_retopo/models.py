# SPDX-License-Identifier: GPL-3.0-or-later
"""Registry der Retopologie-Modelle der Scenario API.

Jedes Modell hat eigene Parameternamen und eine eigene Art, die Polygondichte
zu steuern. Zwei Modelle nehmen eine echte Zielzahl entgegen, eines kennt nur
drei Stufen. Das Panel richtet sich nach `density` des gewaehlten Modells.

Quellen: docs.scenario.com, Endpunkt /v1/generate/custom/{model_id}.
Nur das Hunyuan-Modell ist bisher gegen die echte API gelaufen; die
Parameterschemata der beiden anderen stammen aus der Dokumentation.
"""

# Art der Dichtesteuerung
DENSITY_LEVEL = "level"  # nur low / medium / high
DENSITY_COUNT = "count"  # echte Zielzahl an Faces

# Interne, modellunabhaengige Werte fuer den Topologie-Typ
QUADS = "quads"
TRIS = "tris"


MODELS = (
    {
        "key": "hunyuan_polygen",
        "id": "model_tencent-smarttopology",
        "label": "Hunyuan PolyGen 1.5",
        "description": (
            "Tencent Hunyuan PolyGen 1.5. Art-Grade-Quads, aber nur drei "
            "Dichtestufen, keine Zielzahl"
        ),
        "density": DENSITY_LEVEL,
        "file_param": "file3d",
        "polygon_param": "polygonType",
        "polygon_values": {QUADS: "quadrilateral", TRIS: "triangle"},
        "level_param": "faceLevel",
        "extra": {"geometryFileFormat": "obj"},  # OBJ behaelt Quads
    },
    {
        "key": "meshy_remesh",
        "id": "model_meshy-remesh",
        "label": "Meshy Remesh",
        "description": "Meshy Remesh. Zielzahl von 100 bis 300.000 Polygonen",
        "density": DENSITY_COUNT,
        "file_param": "model",
        "polygon_param": "topology",
        "polygon_values": {QUADS: "quad", TRIS: "triangle"},
        "count_param": "targetPolycount",
        "count_min": 100,
        "count_max": 300000,
        "count_default": 30000,
        # resizeHeight 0 und originAt "empty" lassen Groesse und Ursprung des
        # Eingangsmeshes unangetastet, was die Platzierung erheblich erleichtert
        "extra": {"resizeHeight": 0, "originAt": "empty"},
    },
    {
        "key": "tripo_retopology",
        "id": "model_tripo-retopology",
        "label": "Tripo Retopology",
        "description": "Tripo Retopology. Zielzahl von 1.000 bis 20.000 Faces",
        "density": DENSITY_COUNT,
        "file_param": "model",
        "polygon_param": "quad",
        "polygon_values": {QUADS: True, TRIS: False},
        "count_param": "faceLimit",
        "count_min": 1000,
        "count_max": 20000,
        "count_default": 10000,
        # Texturen werden nicht mit hochgeladen, Baking waere sinnlos
        "extra": {"bake": False},
    },
)

_BY_KEY = {m["key"]: m for m in MODELS}
DEFAULT_KEY = MODELS[0]["key"]


def get(key):
    """Modell-Spezifikation zu einem Schluessel, Fallback auf das Standardmodell."""
    return _BY_KEY.get(key, _BY_KEY[DEFAULT_KEY])


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
    return f"{spec['count_min']:,} bis {spec['count_max']:,}".replace(",", ".")


def build_request(spec, asset_id, polygon_key, *, face_level=None, target_faces=None):
    """Baut den Request-Body fuer /v1/generate/custom/{id}.

    polygon_key ist QUADS oder TRIS und wird auf den Wert des jeweiligen
    Modells abgebildet. Je nach Modell wird entweder face_level oder
    target_faces verwendet.
    """
    if polygon_key not in (QUADS, TRIS):
        raise ValueError(f"Unbekannter Topologie-Typ: {polygon_key}")

    body = {spec["file_param"]: asset_id}
    body[spec["polygon_param"]] = spec["polygon_values"][polygon_key]

    if uses_count(spec):
        if target_faces is None:
            target_faces = spec["count_default"]
        body[spec["count_param"]] = clamp_count(spec, target_faces)
    else:
        if face_level is None:
            face_level = "medium"
        body[spec["level_param"]] = face_level

    body.update(spec.get("extra", {}))
    return body

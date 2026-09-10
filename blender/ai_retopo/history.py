# SPDX-License-Identifier: GPL-3.0-or-later
"""Historie der Retopologie-Jobs.

Gespeichert wird nur, was einen Job spaeter wieder auffindbar macht: seine
Job-Id, ein Name, Zeit, Status und die Groesse des Ergebnisses. Die Mesh-Datei
selbst wird nicht aufbewahrt, das Ergebnis liegt nach dem Import im Projekt und
bei Scenario als Asset.

Der Sinn ist der Absturz: Ein gestarteter Job laeuft bei Scenario weiter und
laesst sich nicht abbrechen (siehe docs/ai-retopo.md). Stirbt Blender waehrend
er laeuft, ist das Ergebnis nur dann noch erreichbar, wenn die Job-Id die
Sitzung ueberlebt hat. Deshalb steht der Eintrag hier, sobald die Id da ist,
und nicht erst nach dem Download.

Ablage: eine JSON-Liste im Extension-User-Ordner, geschrieben ueber eine
temporaere Datei und os.replace. Der Austausch ist atomar, ein Absturz mitten
im Schreiben laesst also die alte Datei vollstaendig stehen statt eine halbe zu
hinterlassen.

Projektbezug: 'blend_file' ordnet einen Eintrag einem Projekt zu, der Filter im
Panel richtet sich danach. Jobs aus einer noch nie gespeicherten Datei haben
kein Projekt; sie bekommen es beim ersten Speichern nachgetragen. Damit dabei
nicht die ungespeicherten Jobs einer zweiten Blender-Instanz mit adoptiert
werden, traegt jeder solche Eintrag die Kennung seines Dokuments (siehe
SESSION).
"""

import json
import os
import tempfile
import uuid
from datetime import datetime

import bpy

from .log import log

FILE_NAME = "history.json"
# Aeltere Eintraege fallen heraus. Bewusst fest: eine Einstellung dafuer waere
# eine Frage, die niemand beantworten will.
MAX_ENTRIES = 100

STATUS_RUNNING = "running"
STATUS_FINISHED = "finished"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

# Kennung des aktuell geladenen Dokuments, nur im Speicher. Sie wird beim
# Registrieren und bei jedem Datei-Wechsel neu gezogen, denn Blender startet
# beim Oeffnen einer anderen Datei nicht neu: An den Prozess gebunden wuerde
# das erste Speichern einer neuen Datei die Jobs der vorherigen adoptieren.
SESSION = ""

_cache = None
_cache_stamp = None


def new_session():
    global SESSION
    SESSION = uuid.uuid4().hex
    return SESSION


def directory():
    """Ordner der Historie im User-Bereich der Extension.

    extension_path_user gibt es nur, wenn das Add-on als Extension laeuft; als
    einfaches Add-on (so laeuft der Headless-Test) faellt es auf den
    Config-Ordner zurueck.
    """
    try:
        return bpy.utils.extension_path_user(__package__, create=True)
    except (AttributeError, ValueError):
        path = bpy.utils.user_resource("CONFIG", path="sb_ai_retopo", create=True)
        return path


def path():
    return os.path.join(directory(), FILE_NAME)


def _stamp(file_path):
    try:
        st = os.stat(file_path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def entries(force=False):
    """Alle Eintraege, neueste zuerst.

    Gelesen wird nur, wenn sich die Datei geaendert hat: die Liste wird beim
    Zeichnen des Panels abgefragt.
    """
    global _cache, _cache_stamp
    file_path = path()
    stamp = _stamp(file_path)
    if not force and _cache is not None and stamp == _cache_stamp:
        return _cache

    data = []
    if stamp is not None:
        try:
            with open(file_path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                data = [e for e in loaded if isinstance(e, dict) and e.get("job_id")]
        except Exception as e:  # noqa: BLE001 - eine kaputte Historie darf nichts blockieren
            log(f"History unreadable ({e}), starting a new one")
            data = []

    data.sort(key=lambda e: e.get("started") or "", reverse=True)
    _cache = data
    _cache_stamp = stamp
    return _cache


def _write(data):
    global _cache, _cache_stamp
    data = data[:MAX_ENTRIES]
    file_path = path()
    fd, tmp = tempfile.mkstemp(prefix=".history_", suffix=".json", dir=os.path.dirname(file_path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, file_path)
    except Exception as e:  # noqa: BLE001 - ohne Historie laeuft das Add-on weiter
        log(f"Could not write the history: {e}")
        if os.path.exists(tmp):
            os.remove(tmp)
        return data
    _cache = data
    _cache_stamp = _stamp(file_path)
    return data


def add(job_id, *, name, model, source_object, blend_file):
    """Legt den Eintrag an, sobald die Job-Id bekannt ist."""
    entry = {
        "job_id": job_id,
        "name": name,
        "started": datetime.now().isoformat(timespec="seconds"),
        "status": STATUS_RUNNING,
        "model": model,
        "size_mb": 0.0,
        "source_object": source_object,
        "blend_file": blend_file,
        # Nur solange der Eintrag noch kein Projekt hat
        "session": "" if blend_file else SESSION,
    }
    data = [e for e in entries(force=True) if e.get("job_id") != job_id]
    data.insert(0, entry)
    _write(data)
    return entry


def update(job_id, **fields):
    """Aendert einen Eintrag, etwa auf 'finished' mit der Groesse des Ergebnisses."""
    data = entries(force=True)
    for entry in data:
        if entry.get("job_id") == job_id:
            entry.update(fields)
            _write(data)
            return entry
    return None


def claim_unsaved(blend_file):
    """Traegt das Projekt in die Eintraege dieses Dokuments nach.

    Aufgerufen beim Speichern. Betroffen sind nur Eintraege ohne Projekt, die
    aus genau diesem Dokument stammen; die einer zweiten Blender-Instanz tragen
    eine andere Kennung und bleiben unberuehrt.
    """
    if not blend_file:
        return 0
    data = entries(force=True)
    claimed = 0
    for entry in data:
        if not entry.get("blend_file") and entry.get("session") == SESSION:
            entry["blend_file"] = blend_file
            entry["session"] = ""
            claimed += 1
    if claimed:
        _write(data)
        log(f"History: {claimed} job(s) now belong to {blend_file}")
    return claimed


def for_project(blend_file):
    """Eintraege eines Projekts. Ein leerer Pfad meint die ungespeicherte Datei."""
    return [e for e in entries() if (e.get("blend_file") or "") == (blend_file or "")]


def get(job_id):
    for entry in entries():
        if entry.get("job_id") == job_id:
            return entry
    return None


def sync(context=None):
    """Spiegelt die Historie in die Liste, die das Panel zeichnet.

    Die UIList braucht eine Collection-Property; die Datei bleibt die Quelle,
    diese Kopie nur ihre Anzeige. Deshalb wird sie neu aufgebaut statt gepflegt.
    """
    context = context or bpy.context
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "sb_ai_retopo_history"):
        return 0

    settings = getattr(context.scene, "sb_ai_retopo", None) if context.scene else None
    only_project = settings.history_this_project if settings else False
    data = for_project(bpy.data.filepath) if only_project else entries()

    wm.sb_ai_retopo_history.clear()
    for entry in data:
        item = wm.sb_ai_retopo_history.add()
        item.job_id = entry.get("job_id", "")
        item.name = entry.get("name") or entry.get("job_id", "")
        item.started = entry.get("started", "")
        item.status = entry.get("status", "")
        item.model = entry.get("model", "")
        item.size_mb = float(entry.get("size_mb") or 0.0)
        item.source_object = entry.get("source_object", "")
        item.blend_file = entry.get("blend_file", "")
    wm.sb_ai_retopo_history_index = min(
        wm.sb_ai_retopo_history_index, max(0, len(wm.sb_ai_retopo_history) - 1)
    )
    return len(data)


def selected(context):
    """Der im Panel gewaehlte Eintrag, oder None."""
    wm = context.window_manager
    items = getattr(wm, "sb_ai_retopo_history", None)
    if not items:
        return None
    index = wm.sb_ai_retopo_history_index
    if 0 <= index < len(items):
        return items[index]
    return None


# -- Handler ---------------------------------------------------------------

@bpy.app.handlers.persistent
def _on_load(_dummy):
    # Neues Dokument, neue Kennung: die ungespeicherten Jobs der vorherigen
    # Datei duerfen nicht mitwandern, wenn diese hier gespeichert wird.
    new_session()
    sync()


@bpy.app.handlers.persistent
def _on_save(_dummy):
    claim_unsaved(bpy.data.filepath)
    sync()


def register():
    new_session()
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    if _on_save not in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.append(_on_save)
    try:
        sync()
    except Exception as e:  # noqa: BLE001 - beim Aktivieren ist der Kontext eingeschraenkt
        log(f"History not shown yet ({e}), use Refresh in the panel")


def unregister():
    for handlers, fn in ((bpy.app.handlers.load_post, _on_load),
                         (bpy.app.handlers.save_post, _on_save)):
        if fn in handlers:
            handlers.remove(fn)

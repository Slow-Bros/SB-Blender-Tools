# SPDX-License-Identifier: GPL-3.0-or-later
"""Jobs: Export -> Scenario API (Worker-Thread) -> Import.

Ein Job besteht aus zwei Haelften. Der Worker-Thread redet mit Scenario und
darf bpy nicht anfassen; er legt nur Nachrichten in job.events. Die Pumpe
(_pump, ein App-Timer) laeuft im Hauptthread, liest die Nachrichten, fuehrt
die Historie und importiert am Ende.

Die Pumpe haengt bewusst an bpy.app.timers und nicht an einem modalen
Operator: ein modaler Operator gehoert zum Fenster und stirbt beim Oeffnen
einer anderen Datei, waehrend der Thread weiterlaeuft — sein Ergebnis kaeme
dann nie an. Der App-Timer ueberlebt den Dateiwechsel (persistent=True) und
liefert das Ergebnis in die Datei, zu der der Job gehoert: ist sie offen, wird
importiert, sonst steht der Job in der Historie als fertig und wartet dort auf
Import Again.

Es duerfen mehrere Jobs gleichzeitig laufen. Die Arbeit passiert bei Scenario,
lokal wartet nur ein Thread pro Job; eine Warteschlange wuerde die Jobs nur
verzoegern.
"""

import os
import queue
import shutil
import tempfile
import threading
import time
import traceback
import uuid

import bpy
from bpy.props import StringProperty

from . import history, mesh_io, models, preferences
from .log import log
from .scenario_client import Cancelled, ScenarioClient, ScenarioError

# Takt der Pumpe in Sekunden
_TICK = 0.25


class _Job:
    """Zustand eines laufenden Jobs.

    Anzeige (progress, status) liegt hier und nicht in Szenen-Properties: die
    Szene wechselt mit der Datei, der Job nicht.
    """

    def __init__(self, *, source_name, model_label, job_id=None):
        # Griff fuer Panel und Cancel, bevor die API eine Job-Id gemeldet hat
        self.token = uuid.uuid4().hex
        self.thread = None
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.temp_dir = None
        self.source_name = source_name
        self.model_label = model_label
        self.job_id = job_id
        self.result_path = None
        self.progress = 0.0
        self.status = ""
        # Projekt des Jobs. Eine ungespeicherte Datei hat keinen Pfad, dann
        # zaehlt die Dokument-Kennung der Historie; beim ersten Speichern
        # traegt _on_save den Pfad nach.
        self.blend_file = bpy.data.filepath
        self.session = history.SESSION

    def belongs_to_open_file(self):
        if self.blend_file:
            return self.blend_file == bpy.data.filepath
        return self.session == history.SESSION


_jobs = []


def jobs_for_open_file():
    return [job for job in _jobs if job.belongs_to_open_file()]


def other_jobs_count():
    return sum(1 for job in _jobs if not job.belongs_to_open_file())


def is_running():
    return bool(_jobs)


def is_fetching(job_id):
    """Ob ein Job mit dieser Id gerade laeuft oder geholt wird."""
    return any(job.job_id == job_id for job in _jobs)


def find_job(token):
    for job in _jobs:
        if job.token == token:
            return job
    return None


# -- Worker (Thread, kein bpy) ------------------------------------------------

def _worker(job, api_key, api_secret, glb_path, model_spec, polygon_key, face_level,
            target_faces, poll_interval):
    """Laeuft im Thread. Kommuniziert nur ueber job.events, kein bpy."""
    def emit(kind, **data):
        job.events.put((kind, data))

    try:
        client = ScenarioClient(
            api_key, api_secret, cancel_event=job.cancel, log=log,
            poll_interval=poll_interval,
        )
        with open(glb_path, "rb") as f:
            data = f.read()

        emit("progress", value=0.08, message="Uploading mesh ...")
        asset_id = client.upload_3d(
            data, os.path.basename(glb_path), "model/gltf-binary",
            on_progress=lambda p: emit("progress", value=0.08 + p * 0.10,
                                       message=f"Uploading ... {int(p * 100)}%"),
            max_bytes=models.upload_limit_bytes(model_spec),
        )
        log(f"Asset: {asset_id}")

        emit("progress", value=0.22, message=f"Starting job ({model_spec['label']}) ...")
        body = models.build_request(model_spec, asset_id, polygon_key,
                                    face_level=face_level, target_faces=target_faces)
        job_id = client.start_generation(model_spec["id"], body)
        log(f"Job: {job_id}")
        # Sofort melden: der Hauptthread schreibt die Id in die Historie, und
        # nur dadurch ist der Job nach einem Absturz noch erreichbar.
        emit("job", job_id=job_id)

        started = time.monotonic()

        def on_poll(count, status, progress):
            # Die API meldet selten einen Fortschritt. Ohne ihn bleibt der
            # Balken stehen und die Laufzeit steht im Text.
            elapsed = int(time.monotonic() - started)
            text = f"Retopology running ... {elapsed // 60}:{elapsed % 60:02d} ({status})"
            value = 0.25
            if isinstance(progress, (int, float)) and 0 <= progress <= 1:
                value += progress * 0.60
            emit("progress", value=value, message=text)

        result = client.wait_for_job(job_id, on_poll=on_poll)

        emit("progress", value=0.88, message="Downloading result ...")
        mesh_bytes, ext = client.download_job_mesh(result)
        out_path = os.path.join(job.temp_dir, f"retopo_result{ext}")
        with open(out_path, "wb") as f:
            f.write(mesh_bytes)
        log(f"Result: {out_path} ({len(mesh_bytes) / 1024:.0f} KB)")
        emit("done", path=out_path, size_mb=len(mesh_bytes) / 1024 / 1024)
    except Cancelled:
        emit("cancelled")
    except ScenarioError as e:
        emit("error", message=str(e))
    except Exception as e:  # noqa: BLE001 - alles melden, Thread darf nicht still sterben
        log(traceback.format_exc())
        emit("error", message=f"{type(e).__name__}: {e}")


def _download_worker(job, api_key, api_secret, job_id, poll_interval):
    """Holt das Ergebnis eines Jobs, der schon laeuft oder fertig ist.

    Der Weg aus der Historie: kein Export, kein Upload, keine neue Generierung
    — nur warten, herunterladen, importieren. Ein Job aus einer abgestuerzten
    Sitzung kommt hier wieder herein.
    """
    def emit(kind, **data):
        job.events.put((kind, data))

    try:
        client = ScenarioClient(
            api_key, api_secret, cancel_event=job.cancel, log=log,
            poll_interval=poll_interval,
        )
        started = time.monotonic()

        def on_poll(count, status, progress):
            elapsed = int(time.monotonic() - started)
            emit("progress", value=0.3,
                 message=f"Waiting for the job ... {elapsed // 60}:{elapsed % 60:02d} ({status})")

        emit("progress", value=0.1, message="Checking the job ...")
        result = client.wait_for_job(job_id, on_poll=on_poll)

        emit("progress", value=0.88, message="Downloading result ...")
        mesh_bytes, ext = client.download_job_mesh(result)
        out_path = os.path.join(job.temp_dir, f"retopo_result{ext}")
        with open(out_path, "wb") as f:
            f.write(mesh_bytes)
        log(f"Result: {out_path} ({len(mesh_bytes) / 1024:.0f} KB)")
        emit("done", path=out_path, size_mb=len(mesh_bytes) / 1024 / 1024)
    except Cancelled:
        emit("cancelled")
    except ScenarioError as e:
        emit("error", message=str(e))
    except Exception as e:  # noqa: BLE001
        log(traceback.format_exc())
        emit("error", message=f"{type(e).__name__}: {e}")


# -- Pumpe (Hauptthread) ------------------------------------------------------

def _start(job, target, args, name):
    job.thread = threading.Thread(target=target, args=args, daemon=True, name=name)
    _jobs.append(job)
    job.thread.start()
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=_TICK, persistent=True)


def _pump():
    """App-Timer: Nachrichten aller Jobs abarbeiten. Endet mit dem letzten Job."""
    for job in list(_jobs):
        try:
            _pump_job(job)
        except Exception as e:  # noqa: BLE001 - ein Fehler darf die anderen Jobs nicht stoppen
            log(traceback.format_exc())
            _end(job, error=f"{type(e).__name__}: {e}")
    _redraw()
    return _TICK if _jobs else None


def _pump_job(job):
    while True:
        try:
            kind, data = job.events.get_nowait()
        except queue.Empty:
            break

        if kind == "progress":
            job.progress = data["value"]
            job.status = data["message"]
        elif kind == "job":
            # Der einzige Zeitpunkt, an dem die Job-Id festgehalten werden
            # kann, bevor irgendetwas schiefgehen kann. Projekt und Kennung
            # kommen vom Job, nicht von der gerade offenen Datei.
            job.job_id = data["job_id"]
            history.add(
                job.job_id,
                name=f"{job.source_name}_retopo",
                model=job.model_label,
                source_object=job.source_name,
                blend_file=job.blend_file,
                session=job.session,
            )
            history.sync()
        elif kind == "done":
            job.result_path = data["path"]
            # Fein genug runden, dass ein kleines Ergebnis nicht auf 0.0
            # faellt: die Null steht fuer 'nicht heruntergeladen'
            history.update(job.job_id, status=history.STATUS_FINISHED,
                           size_mb=round(data.get("size_mb") or 0.0, 6), error="")
            history.sync()
            _deliver(job)
            return
        elif kind == "cancelled":
            history.update(job.job_id, status=history.STATUS_CANCELLED)
            history.sync()
            log(f"{job.source_name}: cancelled")
            _end(job, error="Cancelled.")
            return
        elif kind == "error":
            history.update(job.job_id, status=history.STATUS_FAILED, error=data["message"])
            history.sync()
            log(f"{job.source_name}: {data['message']}")
            _end(job, error=data["message"])
            return

    if job.result_path:
        # Ergebnis liegt vor, der Import wartet auf den Object Mode
        _deliver(job)
    elif not job.thread.is_alive() and job.events.empty():
        # Thread endete ohne Meldung -> als Fehler behandeln
        _end(job, error="The worker thread stopped unexpectedly.")


def _deliver(job):
    """Bringt das Ergebnis in die Datei des Jobs, wenn sie offen ist.

    Ist eine andere Datei offen, bleibt der Job in der Historie seines
    Projekts als fertig stehen; Import Again holt ihn dort. So landet ein
    Mesh nie in einem fremden Projekt.
    """
    if not job.belongs_to_open_file():
        project = job.blend_file or "an unsaved file"
        log(f"{job.source_name}: finished, but {project} is not open. "
            "Use Import Again in that project's history.")
        _end(job)
        return
    with bpy.context.temp_override(**_override()):
        if bpy.context.mode != "OBJECT":
            job.progress = 0.9
            job.status = "Finished, waiting for Object Mode ..."
            return
        _import(bpy.context, job)


def _import(context, job):
    settings = context.scene.sb_ai_retopo
    job.progress = 0.92
    job.status = "Importing result ..."
    # Das Quellobjekt kann fehlen, wenn ein Job aus der Historie in ein
    # umgebautes Projekt importiert wird. Dann dient das aktive Mesh als
    # Bezug; ohne eines gibt es keine Box zum Vergleichen, und das
    # Ergebnis kommt unkorrigiert herein.
    source = bpy.data.objects.get(job.source_name)
    placement_note = ""
    if source is None:
        active = context.active_object
        if active is not None and active.type == "MESH":
            source = active
            placement_note = (
                f"'{job.source_name}' no longer exists, so '{source.name}' was used "
                "for size and position."
            )
        else:
            placement_note = (
                f"'{job.source_name}' no longer exists and no mesh is active, so the "
                "result was imported without correcting size and position."
            )
    try:
        if source is None:
            obj, stats = mesh_io.import_unplaced(
                context, job.result_path, f"{job.source_name}_retopo")
        else:
            obj, stats = mesh_io.import_result(
                context, job.result_path, source,
                hide_source=settings.hide_source,
                remove_fragments=settings.remove_fragments,
            )
    except Exception as e:  # noqa: BLE001
        log(traceback.format_exc())
        _end(job, error=f"Import failed: {e}", settings=settings)
        return

    summary = f"{obj.name}: {stats['faces']} faces, {stats['quads']} quads, {stats['tris']} tris"
    if stats["ngons"]:
        summary += f", {stats['ngons']} n-gons"
    settings.last_result = summary
    log(summary)

    notes = []
    if placement_note:
        notes.append(placement_note)
    if stats["fragments_removed"]:
        notes.append(
            f"Removed {stats['outlier_parts']} stray fragment(s), "
            f"{stats['fragments_removed']} vertices."
        )
    elif stats["outlier_parts"]:
        notes.append(
            f"The result has {stats['outlier_parts']} fragment(s) outside the object, "
            "kept because removal is switched off."
        )
    if not stats["world_ok"]:
        notes.append(
            f"After correction the result is still off: size by "
            f"{stats['world_residual'] * 100:.1f}%, centre by "
            f"{stats['world_centre_offset']:.3f}. The system console has the "
            "measured sizes."
        )
    if notes:
        warning = " ".join(notes)
        settings.last_warning = warning
        log(warning)
    else:
        settings.last_warning = ""
    settings.last_error = ""
    _end(job, settings=settings)


def _end(job, error=None, settings=None):
    """Job aus der Liste nehmen und aufraeumen.

    Ein Fehler steht im Panel der Datei, zu der der Job gehoert, sofern sie
    offen ist; die Historie hat ihn in jedem Fall.
    """
    job.cancel.set()
    if job.temp_dir:
        shutil.rmtree(job.temp_dir, ignore_errors=True)
    if job in _jobs:
        _jobs.remove(job)
    if error and settings is None and job.belongs_to_open_file():
        settings = _scene_settings()
    if error and settings is not None:
        settings.last_error = error


def _scene_settings():
    with bpy.context.temp_override(**_override()):
        scene = bpy.context.scene
    return getattr(scene, "sb_ai_retopo", None) if scene else None


def _override():
    """Kontext fuer Import und Anzeige aus dem Timer heraus.

    Ein App-Timer hat kein Fenster; mit dem ersten Fenster kommen Szene und
    View-Layer, die der Import braucht. Im Hintergrundmodus gibt es keines,
    dann reicht der Kontext, wie er ist.
    """
    wm = bpy.context.window_manager
    windows = wm.windows if wm else ()
    return {"window": windows[0]} if windows else {}


def _redraw():
    wm = bpy.context.window_manager
    if wm is None:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


# -- Operatoren -----------------------------------------------------------------

class SB_OT_ai_retopo(bpy.types.Operator):
    bl_idname = "sb.ai_retopo"
    bl_label = "AI Retopology"
    bl_description = "Retopologize the active mesh through the Scenario API and import the result as a new object"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None and obj.type == "MESH"
            and context.mode == "OBJECT"
            and not models.LOAD_ERROR
        )

    def execute(self, context):
        settings = context.scene.sb_ai_retopo
        prefs = preferences.get_prefs(context)
        api_key, api_secret = preferences.get_credentials(context)
        if not api_key or not api_secret:
            self.report({"ERROR"}, "Scenario API key and secret are missing (add-on preferences).")
            return {"CANCELLED"}

        source = context.active_object
        spec = models.get(settings.model)

        job = _Job(source_name=source.name, model_label=spec["label"])
        job.temp_dir = tempfile.mkdtemp(prefix="sb_ai_retopo_", dir=bpy.app.tempdir or None)
        glb_path = os.path.join(job.temp_dir, "upload.glb")

        job.progress = 0.02
        job.status = "Exporting mesh ..."
        settings.last_error = ""
        settings.last_result = ""
        settings.last_warning = ""

        try:
            decimate = settings.pre_decimate_target if settings.pre_decimate else 0
            info = mesh_io.export_object_for_upload(context, source, glb_path, decimate_target=decimate)
        except Exception as e:  # noqa: BLE001
            settings.last_error = str(e)
            shutil.rmtree(job.temp_dir, ignore_errors=True)
            self.report({"ERROR"}, f"Export failed: {e}")
            return {"CANCELLED"}

        if models.uses_count(spec):
            wanted = settings.target_faces
            actual = models.clamp_count(spec, wanted, settings.polygon_type)
            density = f"target {actual} faces"
            if actual != wanted:
                density += f" (clamped from {wanted} to the model range)"
        else:
            density = f"level {settings.face_level}"

        log(
            f"Export: {info['faces']} faces raw, {info['faces_clean']} after cleanup, "
            f"{info['faces_exported']} uploaded, {info['bytes'] / 1024 / 1024:.1f} MB | "
            f"{spec['label']}, {density}, {settings.polygon_type}"
        )

        job.status = "Connecting to the Scenario API ..."
        # Der Body entsteht erst im Worker, wenn die Asset-ID da ist; hier
        # gehen nur einfache Werte mit, kein bpy-Zustand.
        _start(
            job, _worker,
            (job, api_key, api_secret, glb_path, spec,
             settings.polygon_type, settings.face_level, settings.target_faces,
             float(prefs.poll_interval)),
            "sb_ai_retopo",
        )
        return {"FINISHED"}


class SB_OT_ai_retopo_history_import(bpy.types.Operator):
    """Holt das Ergebnis eines Jobs aus der Historie erneut.

    Kein Export, kein Upload; sonst derselbe Weg durch die Pumpe wie ein
    neuer Job.
    """
    bl_idname = "sb.ai_retopo_history_import"
    bl_label = "Import Again"
    bl_description = (
        "Fetch this job from Scenario again and import it. A job that was still "
        "running when Blender stopped is picked up here"
    )

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and history.selected(context) is not None

    def execute(self, context):
        settings = context.scene.sb_ai_retopo
        prefs = preferences.get_prefs(context)
        api_key, api_secret = preferences.get_credentials(context)
        if not api_key or not api_secret:
            self.report({"ERROR"}, "Scenario API key and secret are missing (add-on preferences).")
            return {"CANCELLED"}

        item = history.selected(context)
        if item is None:
            self.report({"ERROR"}, "No job selected.")
            return {"CANCELLED"}
        if is_fetching(item.job_id):
            self.report({"WARNING"}, "This job is already running.")
            return {"CANCELLED"}

        job = _Job(source_name=item.source_object or item.name, model_label=item.model,
                   job_id=item.job_id)
        job.temp_dir = tempfile.mkdtemp(prefix="sb_ai_retopo_", dir=bpy.app.tempdir or None)
        job.progress = 0.02
        job.status = "Checking the job ..."
        settings.last_error = ""
        settings.last_result = ""
        settings.last_warning = ""

        log(f"History: fetching job {item.job_id} ({item.model or 'unknown model'})")
        _start(
            job, _download_worker,
            (job, api_key, api_secret, item.job_id, float(prefs.poll_interval)),
            "sb_ai_retopo_history",
        )
        return {"FINISHED"}


class SB_OT_ai_retopo_history_refresh(bpy.types.Operator):
    bl_idname = "sb.ai_retopo_history_refresh"
    bl_label = "Refresh"
    bl_description = "Read the history file again, for jobs that another Blender instance added"

    def execute(self, context):
        history.entries(force=True)
        count = history.sync(context)
        self.report({"INFO"}, f"{count} job(s) in the history.")
        return {"FINISHED"}


class SB_OT_ai_retopo_cancel(bpy.types.Operator):
    bl_idname = "sb.ai_retopo_cancel"
    bl_label = "Cancel"
    bl_description = "Cancel this AI retopology job"

    token: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return is_running()

    def execute(self, context):
        job = find_job(self.token)
        if job is None:
            return {"CANCELLED"}
        job.cancel.set()
        job.status = "Cancelling ..."
        return {"FINISHED"}


# -- Handler --------------------------------------------------------------------

@bpy.app.handlers.persistent
def _on_save(_dummy):
    # Das erste Speichern gibt den Jobs der ungespeicherten Datei ihr Projekt,
    # so wie history.claim_unsaved es fuer die Eintraege tut
    for job in _jobs:
        if not job.blend_file and job.session == history.SESSION:
            job.blend_file = bpy.data.filepath


classes = (SB_OT_ai_retopo, SB_OT_ai_retopo_history_import,
           SB_OT_ai_retopo_history_refresh, SB_OT_ai_retopo_cancel)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    if _on_save not in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.append(_on_save)


def unregister():
    if _on_save in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(_on_save)
    for job in list(_jobs):
        _end(job)
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

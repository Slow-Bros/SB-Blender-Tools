# SPDX-License-Identifier: GPL-3.0-or-later
"""Modal-Operator: Export -> Scenario API (Worker-Thread) -> Import."""

import os
import queue
import shutil
import tempfile
import threading
import time
import traceback

import bpy

from . import history, mesh_io, models, preferences
from .log import log
from .scenario_client import Cancelled, ScenarioClient, ScenarioError


class _Job:
    """Zustand eines laufenden Retopologie-Jobs (Modul-global, ein Job gleichzeitig)."""

    def __init__(self):
        self.thread = None
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.temp_dir = None
        self.source_name = None
        self.result_path = None
        # Fuer die Historie: Job-Id, sobald die API sie gemeldet hat, und der
        # Name des Modells, das den Job faehrt
        self.job_id = None
        self.model_label = ""


_job = None


def is_running():
    return _job is not None and _job.thread is not None and _job.thread.is_alive()


def _worker(job, api_key, api_secret, glb_path, model_spec, polygon_key, face_level,
            target_faces, poll_interval, job_timeout):
    """Laeuft im Thread. Kommuniziert nur ueber job.events, kein bpy."""
    def emit(kind, **data):
        job.events.put((kind, data))

    try:
        client = ScenarioClient(
            api_key, api_secret, cancel_event=job.cancel, log=log,
            poll_interval=poll_interval, job_timeout=job_timeout,
        )
        with open(glb_path, "rb") as f:
            data = f.read()

        emit("progress", value=0.08, message="Uploading mesh ...")
        asset_id = client.upload_3d(
            data, os.path.basename(glb_path), "model/gltf-binary",
            on_progress=lambda p: emit("progress", value=0.08 + p * 0.10,
                                       message=f"Uploading ... {int(p * 100)}%"),
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
            # Balken stehen und die Laufzeit steht im Text, statt aus dem
            # Timeout einen Fortschritt zu erfinden.
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


def _download_worker(job, api_key, api_secret, job_id, poll_interval, job_timeout):
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
            poll_interval=poll_interval, job_timeout=job_timeout,
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


class _RetopoModal:
    """Gemeinsamer Teil beider Wege: Ereignisse einsammeln, importieren, aufraeumen.

    Bewusst kein Operator und keine Vererbung zwischen den beiden Operatoren:
    registriert Blender eine abgeleitete Operator-Klasse, ueberschreibt deren
    poll die der Basis, und der Haupt-Operator waere nicht mehr ausfuehrbar.
    """

    _timer = None

    def _start_modal(self, context):
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.25, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        settings = context.scene.sb_ai_retopo
        job = _job
        if job is None:
            return self._finish(context)

        while True:
            try:
                kind, data = job.events.get_nowait()
            except queue.Empty:
                break

            if kind == "progress":
                settings.progress = data["value"]
                settings.status = data["message"]
            elif kind == "job":
                # Der einzige Zeitpunkt, an dem die Job-Id festgehalten werden
                # kann, bevor irgendetwas schiefgehen kann
                job.job_id = data["job_id"]
                history.add(
                    job.job_id,
                    name=f"{job.source_name}_retopo",
                    model=job.model_label,
                    source_object=job.source_name,
                    blend_file=bpy.data.filepath,
                )
                history.sync(context)
            elif kind == "done":
                job.result_path = data["path"]
                # Fein genug runden, dass ein kleines Ergebnis nicht auf 0.0
                # faellt: die Null steht fuer 'nicht heruntergeladen'
                history.update(job.job_id, status=history.STATUS_FINISHED,
                               size_mb=round(data.get("size_mb") or 0.0, 6))
                return self._import(context, job)
            elif kind == "cancelled":
                history.update(job.job_id, status=history.STATUS_CANCELLED)
                settings.last_error = "Cancelled."
                self.report({"WARNING"}, "AI retopology cancelled.")
                return self._finish(context)
            elif kind == "error":
                history.update(job.job_id, status=history.STATUS_FAILED)
                settings.last_error = data["message"]
                self.report({"ERROR"}, data["message"])
                return self._finish(context)

        if not job.thread.is_alive() and job.events.empty():
            # Thread endete ohne Meldung -> als Fehler behandeln
            settings.last_error = "The worker thread stopped unexpectedly."
            return self._finish(context)

        _redraw(context)
        return {"RUNNING_MODAL"}

    def _import(self, context, job):
        settings = context.scene.sb_ai_retopo
        settings.progress = 0.92
        settings.status = "Importing result ..."
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
            settings.last_error = f"Import failed: {e}"
            self.report({"ERROR"}, settings.last_error)
            return self._finish(context)

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
            self.report({"WARNING"}, warning)
        else:
            settings.last_warning = ""

        self.report({"INFO"}, f"AI retopology finished: {summary}")
        return self._finish(context, success=True)

    def _finish(self, context, success=False):
        global _job
        settings = context.scene.sb_ai_retopo
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        job = _job
        if job is not None:
            job.cancel.set()
            if job.temp_dir:
                shutil.rmtree(job.temp_dir, ignore_errors=True)
        _job = None
        settings.running = False
        settings.progress = 1.0 if success else 0.0
        settings.status = "Done." if success else ""
        _redraw(context)
        return {"FINISHED"} if success else {"CANCELLED"}


class SB_OT_ai_retopo(_RetopoModal, bpy.types.Operator):
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
            and not is_running()
            and not models.LOAD_ERROR
        )

    def execute(self, context):
        global _job
        settings = context.scene.sb_ai_retopo
        prefs = preferences.get_prefs(context)
        api_key, api_secret = preferences.get_credentials(context)
        if not api_key or not api_secret:
            self.report({"ERROR"}, "Scenario API key and secret are missing (add-on preferences).")
            return {"CANCELLED"}

        source = context.active_object
        spec = models.get(settings.model)

        job = _Job()
        job.temp_dir = tempfile.mkdtemp(prefix="sb_ai_retopo_", dir=bpy.app.tempdir or None)
        job.source_name = source.name
        job.model_label = spec["label"]
        glb_path = os.path.join(job.temp_dir, "upload.glb")

        settings.running = True
        settings.progress = 0.02
        settings.status = "Exporting mesh ..."
        settings.last_error = ""
        settings.last_result = ""
        settings.last_warning = ""

        try:
            decimate = settings.pre_decimate_target if settings.pre_decimate else 0
            info = mesh_io.export_object_for_upload(context, source, glb_path, decimate_target=decimate)
        except Exception as e:  # noqa: BLE001
            settings.running = False
            settings.status = ""
            settings.last_error = str(e)
            shutil.rmtree(job.temp_dir, ignore_errors=True)
            self.report({"ERROR"}, f"Export failed: {e}")
            return {"CANCELLED"}

        if models.uses_count(spec):
            wanted = settings.target_faces
            actual = models.clamp_count(spec, wanted)
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

        # Der Body entsteht erst im Worker, wenn die Asset-ID da ist; hier
        # gehen nur einfache Werte mit, kein bpy-Zustand.
        job.thread = threading.Thread(
            target=_worker,
            args=(job, api_key, api_secret, glb_path, spec,
                  settings.polygon_type, settings.face_level, settings.target_faces,
                  float(prefs.poll_interval), prefs.job_timeout_minutes * 60.0),
            daemon=True,
            name="sb_ai_retopo",
        )
        _job = job
        job.thread.start()

        settings.status = "Connecting to the Scenario API ..."
        return self._start_modal(context)

class SB_OT_ai_retopo_history_import(_RetopoModal, bpy.types.Operator):
    """Holt das Ergebnis eines Jobs aus der Historie erneut.

    Teilt sich Modal-Schleife, Import und Aufraeumen mit dem Haupt-Operator;
    nur der Anfang unterscheidet sich, weil nichts exportiert und hochgeladen
    wird.
    """
    bl_idname = "sb.ai_retopo_history_import"
    bl_label = "Import Again"
    bl_description = (
        "Fetch this job from Scenario again and import it. A job that was still "
        "running when Blender stopped is picked up here"
    )

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and not is_running() and history.selected(context) is not None

    def execute(self, context):
        global _job
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

        job = _Job()
        job.temp_dir = tempfile.mkdtemp(prefix="sb_ai_retopo_", dir=bpy.app.tempdir or None)
        job.source_name = item.source_object or item.name
        job.model_label = item.model
        job.job_id = item.job_id

        settings.running = True
        settings.progress = 0.02
        settings.status = "Checking the job ..."
        settings.last_error = ""
        settings.last_result = ""
        settings.last_warning = ""

        log(f"History: fetching job {item.job_id} ({item.model or 'unknown model'})")
        job.thread = threading.Thread(
            target=_download_worker,
            args=(job, api_key, api_secret, item.job_id,
                  float(prefs.poll_interval), prefs.job_timeout_minutes * 60.0),
            daemon=True,
            name="sb_ai_retopo_history",
        )
        _job = job
        job.thread.start()

        return self._start_modal(context)


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
    bl_description = "Cancel the running AI retopology job"

    @classmethod
    def poll(cls, context):
        return is_running()

    def execute(self, context):
        if _job is not None:
            _job.cancel.set()
            context.scene.sb_ai_retopo.status = "Cancelling ..."
        return {"FINISHED"}


def _redraw(context):
    screen = context.screen if context.screen else None
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


classes = (SB_OT_ai_retopo, SB_OT_ai_retopo_history_import,
           SB_OT_ai_retopo_history_refresh, SB_OT_ai_retopo_cancel)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    global _job
    if _job is not None:
        _job.cancel.set()
        _job = None
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

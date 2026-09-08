# SPDX-License-Identifier: GPL-3.0-or-later
"""Modal-Operator: Export -> Scenario API (Worker-Thread) -> Import."""

import os
import queue
import shutil
import tempfile
import threading
import traceback

import bpy

from . import mesh_io, preferences
from .scenario_client import Cancelled, ScenarioClient, ScenarioError

LOG_PREFIX = "[SB-AI-RETOPO]"


def _log(msg):
    print(f"{LOG_PREFIX} {msg}")


class _Job:
    """Zustand eines laufenden Retopologie-Jobs (Modul-global, ein Job gleichzeitig)."""

    def __init__(self):
        self.thread = None
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.temp_dir = None
        self.source_name = None
        self.result_path = None


_job = None


def is_running():
    return _job is not None and _job.thread is not None and _job.thread.is_alive()


def _worker(job, api_key, api_secret, glb_path, polygon_type, face_level, poll_interval, job_timeout):
    """Laeuft im Thread. Kommuniziert nur ueber job.events, kein bpy."""
    def emit(kind, **data):
        job.events.put((kind, data))

    def log(msg):
        _log(msg)
        emit("log", message=msg)

    try:
        client = ScenarioClient(
            api_key, api_secret, cancel_event=job.cancel, log=log,
            poll_interval=poll_interval, job_timeout=job_timeout,
        )
        with open(glb_path, "rb") as f:
            data = f.read()

        emit("progress", value=0.08, message="Mesh wird hochgeladen ...")
        asset_id = client.upload_3d(
            data, os.path.basename(glb_path), "model/gltf-binary",
            on_progress=lambda p: emit("progress", value=0.08 + p * 0.10,
                                       message=f"Hochladen ... {int(p * 100)}%"),
        )
        log(f"Asset: {asset_id}")

        emit("progress", value=0.22, message="Retopologie-Job wird gestartet ...")
        job_id = client.start_retopology(asset_id, polygon_type, face_level)
        log(f"Job: {job_id}")

        def on_poll(count, status, progress):
            frac = progress if isinstance(progress, (int, float)) and 0 <= progress <= 1 else None
            if frac is None:
                frac = min(count * poll_interval / job_timeout, 0.95)
            emit("progress", value=0.25 + frac * 0.60, message=f"Retopologie laeuft ... ({status})")

        result = client.wait_for_job(job_id, on_poll=on_poll)

        emit("progress", value=0.88, message="Ergebnis wird heruntergeladen ...")
        mesh_bytes, ext = client.download_job_mesh(result)
        out_path = os.path.join(job.temp_dir, f"retopo_result{ext}")
        with open(out_path, "wb") as f:
            f.write(mesh_bytes)
        log(f"Ergebnis: {out_path} ({len(mesh_bytes) / 1024:.0f} KB)")
        emit("done", path=out_path)
    except Cancelled:
        emit("cancelled")
    except ScenarioError as e:
        emit("error", message=str(e))
    except Exception as e:  # noqa: BLE001 - alles melden, Thread darf nicht still sterben
        _log(traceback.format_exc())
        emit("error", message=f"{type(e).__name__}: {e}")


class SB_OT_ai_retopo(bpy.types.Operator):
    bl_idname = "sb.ai_retopo"
    bl_label = "KI-Retopologie"
    bl_description = "Aktives Mesh per Scenario API (Hunyuan PolyGen) retopologisieren und als neues Objekt importieren"
    bl_options = {"REGISTER"}

    _timer = None

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None and obj.type == "MESH"
            and context.mode == "OBJECT"
            and not is_running()
        )

    def execute(self, context):
        global _job
        settings = context.scene.sb_ai_retopo
        prefs = preferences.get_prefs(context)
        api_key, api_secret = preferences.get_credentials(context)
        if not api_key or not api_secret:
            self.report({"ERROR"}, "Scenario API Key/Secret fehlen (Add-on-Einstellungen).")
            return {"CANCELLED"}

        source = context.active_object
        face_level = settings.face_level

        job = _Job()
        job.temp_dir = tempfile.mkdtemp(prefix="sb_ai_retopo_", dir=bpy.app.tempdir or None)
        job.source_name = source.name
        glb_path = os.path.join(job.temp_dir, "upload.glb")

        settings.running = True
        settings.progress = 0.02
        settings.status = "Mesh wird exportiert ..."
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
            self.report({"ERROR"}, f"Export fehlgeschlagen: {e}")
            return {"CANCELLED"}

        _log(
            f"Export: {info['faces']} Faces roh, {info['faces_clean']} nach Cleanup, "
            f"{info['faces_exported']} hochgeladen, {info['bytes'] / 1024 / 1024:.1f} MB | "
            f"faceLevel {face_level}, {settings.polygon_type}"
        )

        job.thread = threading.Thread(
            target=_worker,
            args=(job, api_key, api_secret, glb_path, settings.polygon_type, face_level,
                  float(prefs.poll_interval), prefs.job_timeout_minutes * 60.0),
            daemon=True,
            name="sb_ai_retopo",
        )
        _job = job
        job.thread.start()

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.25, window=context.window)
        wm.modal_handler_add(self)
        settings.status = "Verbindung zur Scenario API ..."
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
            elif kind == "log":
                pass
            elif kind == "done":
                job.result_path = data["path"]
                return self._import(context, job)
            elif kind == "cancelled":
                settings.last_error = "Abgebrochen."
                self.report({"WARNING"}, "KI-Retopologie abgebrochen.")
                return self._finish(context)
            elif kind == "error":
                settings.last_error = data["message"]
                self.report({"ERROR"}, data["message"])
                return self._finish(context)

        if not job.thread.is_alive() and job.events.empty():
            # Thread endete ohne Meldung -> als Fehler behandeln
            settings.last_error = "Worker-Thread unerwartet beendet."
            return self._finish(context)

        _redraw(context)
        return {"RUNNING_MODAL"}

    def _import(self, context, job):
        settings = context.scene.sb_ai_retopo
        settings.progress = 0.92
        settings.status = "Ergebnis wird importiert ..."
        source = bpy.data.objects.get(job.source_name)
        if source is None:
            settings.last_error = f"Originalobjekt '{job.source_name}' existiert nicht mehr."
            self.report({"ERROR"}, settings.last_error)
            return self._finish(context)
        try:
            obj, stats = mesh_io.import_result(
                context, job.result_path, source,
                hide_source=settings.hide_source,
            )
        except Exception as e:  # noqa: BLE001
            _log(traceback.format_exc())
            settings.last_error = f"Import fehlgeschlagen: {e}"
            self.report({"ERROR"}, settings.last_error)
            return self._finish(context)

        summary = f"{obj.name}: {stats['faces']} Faces, {stats['quads']} Quads, {stats['tris']} Tris"
        if stats["ngons"]:
            summary += f", {stats['ngons']} N-Gons"
        settings.last_result = summary
        _log(summary)

        if stats["outlier_parts"]:
            warning = (
                f"KI-Ergebnis hat {stats['outlier_parts']} Fragment(e) ausserhalb des Objekts. "
                "Bei der Einpassung ignoriert, im Mesh pruefen und ggf. loeschen."
            )
            settings.last_warning = warning
            _log(warning)
            self.report({"WARNING"}, warning)
        else:
            settings.last_warning = ""

        self.report({"INFO"}, f"KI-Retopologie fertig: {summary}")
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
        settings.status = "Fertig." if success else ""
        _redraw(context)
        return {"FINISHED"} if success else {"CANCELLED"}


class SB_OT_ai_retopo_cancel(bpy.types.Operator):
    bl_idname = "sb.ai_retopo_cancel"
    bl_label = "Abbrechen"
    bl_description = "Laufende KI-Retopologie abbrechen"

    @classmethod
    def poll(cls, context):
        return is_running()

    def execute(self, context):
        if _job is not None:
            _job.cancel.set()
            context.scene.sb_ai_retopo.status = "Wird abgebrochen ..."
        return {"FINISHED"}


def _redraw(context):
    screen = context.screen if context.screen else None
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


classes = (SB_OT_ai_retopo, SB_OT_ai_retopo_cancel)


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

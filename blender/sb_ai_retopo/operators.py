# SPDX-License-Identifier: GPL-3.0-or-later
"""Modal-Operator: Export -> Scenario API (Worker-Thread) -> Import."""

import os
import queue
import shutil
import tempfile
import threading
import traceback

import bpy

from . import mesh_io, models, preferences
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


def _worker(job, api_key, api_secret, glb_path, model_spec, request_body, poll_interval, job_timeout):
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

        emit("progress", value=0.08, message="Uploading mesh ...")
        asset_id = client.upload_3d(
            data, os.path.basename(glb_path), "model/gltf-binary",
            on_progress=lambda p: emit("progress", value=0.08 + p * 0.10,
                                       message=f"Uploading ... {int(p * 100)}%"),
        )
        log(f"Asset: {asset_id}")

        emit("progress", value=0.22, message=f"Starting job ({model_spec['label']}) ...")
        body = dict(request_body)
        body[model_spec["file_param"]] = asset_id
        job_id = client.start_generation(model_spec["id"], body)
        log(f"Job: {job_id}")

        def on_poll(count, status, progress):
            frac = progress if isinstance(progress, (int, float)) and 0 <= progress <= 1 else None
            if frac is None:
                frac = min(count * poll_interval / job_timeout, 0.95)
            emit("progress", value=0.25 + frac * 0.60, message=f"Retopology running ... ({status})")

        result = client.wait_for_job(job_id, on_poll=on_poll)

        emit("progress", value=0.88, message="Downloading result ...")
        mesh_bytes, ext = client.download_job_mesh(result)
        out_path = os.path.join(job.temp_dir, f"retopo_result{ext}")
        with open(out_path, "wb") as f:
            f.write(mesh_bytes)
        log(f"Result: {out_path} ({len(mesh_bytes) / 1024:.0f} KB)")
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
    bl_label = "AI Retopology"
    bl_description = "Retopologize the active mesh through the Scenario API and import the result as a new object"
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
            self.report({"ERROR"}, "Scenario API key and secret are missing (add-on preferences).")
            return {"CANCELLED"}

        source = context.active_object
        spec = models.get(settings.model)

        # Nur bei einer eindeutigen Absage blockieren. Der Katalog unter
        # /v1/models fuehrt die Plattform-Modelle nicht, deshalb zaehlt hier
        # allein die gezielte Abfrage, und "unbekannt" haelt niemanden auf.
        if prefs.probe_status(spec["id"]) == "missing":
            msg = (
                f"'{spec['label']}' ({spec['id']}) does not exist for this account. "
                "Pick another model, or check the id again in the add-on preferences."
            )
            settings.last_error = msg
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        # asset_id wird erst nach dem Upload im Worker eingesetzt
        request_body = models.build_request(
            spec, "", settings.polygon_type,
            face_level=settings.face_level,
            target_faces=settings.target_faces,
        )

        job = _Job()
        job.temp_dir = tempfile.mkdtemp(prefix="sb_ai_retopo_", dir=bpy.app.tempdir or None)
        job.source_name = source.name
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

        _log(
            f"Export: {info['faces']} faces raw, {info['faces_clean']} after cleanup, "
            f"{info['faces_exported']} uploaded, {info['bytes'] / 1024 / 1024:.1f} MB | "
            f"{spec['label']}, {density}, {settings.polygon_type}"
        )

        job.thread = threading.Thread(
            target=_worker,
            args=(job, api_key, api_secret, glb_path, spec, request_body,
                  float(prefs.poll_interval), prefs.job_timeout_minutes * 60.0),
            daemon=True,
            name="sb_ai_retopo",
        )
        _job = job
        job.thread.start()

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.25, window=context.window)
        wm.modal_handler_add(self)
        settings.status = "Connecting to the Scenario API ..."
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
                settings.last_error = "Cancelled."
                self.report({"WARNING"}, "AI retopology cancelled.")
                return self._finish(context)
            elif kind == "error":
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
        source = bpy.data.objects.get(job.source_name)
        if source is None:
            settings.last_error = f"The source object '{job.source_name}' no longer exists."
            self.report({"ERROR"}, settings.last_error)
            return self._finish(context)
        try:
            obj, stats = mesh_io.import_result(
                context, job.result_path, source,
                hide_source=settings.hide_source,
                remove_fragments=settings.remove_fragments,
                fit_to_original=settings.fit_to_original,
            )
        except Exception as e:  # noqa: BLE001
            _log(traceback.format_exc())
            settings.last_error = f"Import failed: {e}"
            self.report({"ERROR"}, settings.last_error)
            return self._finish(context)

        summary = f"{obj.name}: {stats['faces']} faces, {stats['quads']} quads, {stats['tris']} tris"
        if stats["ngons"]:
            summary += f", {stats['ngons']} n-gons"
        settings.last_result = summary
        _log(summary)

        notes = []
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
        if stats["deviates"]:
            notes.append(
                f"Size differs by factor {stats['scale']:.3f} and the centre by "
                f"{stats['offset']:.3f} from the original. Left as returned; "
                "switch on 'Fit to Original' if the object really sits wrong."
            )
        if notes:
            warning = " ".join(notes)
            settings.last_warning = warning
            _log(warning)
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


class SB_OT_refresh_models(bpy.types.Operator):
    bl_idname = "sb.ai_retopo_refresh_models"
    bl_label = "Refresh Catalogue"
    bl_description = "Fetch the model catalogue from the Scenario API and cache it"

    # Der Abruf laeuft im Thread, ein bpy.app.timer holt das Ergebnis ab.
    # Panels duerfen niemals selbst Netzwerkverkehr ausloesen.
    _pending = None

    @classmethod
    def poll(cls, context):
        return cls._pending is None

    def execute(self, context):
        api_key, api_secret = preferences.get_credentials(context)
        if not api_key or not api_secret:
            self.report({"ERROR"}, "Scenario API key and secret are missing (add-on preferences).")
            return {"CANCELLED"}

        result = {}
        cls = SB_OT_refresh_models

        def fetch():
            try:
                client = ScenarioClient(api_key, api_secret, log=_log)
                result["models"] = client.list_models()
            except ScenarioError as e:
                result["error"] = str(e)
            except Exception as e:  # noqa: BLE001
                _log(traceback.format_exc())
                result["error"] = f"{type(e).__name__}: {e}"

        thread = threading.Thread(target=fetch, daemon=True, name="sb_ai_retopo_models")
        thread.start()
        cls._pending = thread

        def collect():
            if thread.is_alive():
                return 0.2
            cls._pending = None
            try:
                prefs = preferences.get_prefs()
            except Exception:  # noqa: BLE001
                _log(traceback.format_exc())
                return None

            if "error" in result:
                # Auch in der Oberflaeche melden, nicht nur in der Konsole:
                # ein stiller Knopf ist als Diagnose wertlos
                prefs.catalogue_error = result["error"]
                _log(f"Catalogue refresh failed: {result['error']}")
            else:
                entries = result.get("models", [])
                prefs.set_catalogue(entries)
                missing = models.classify({m[0] for m in entries})["missing"]
                unknown = models.unknown_candidates(entries)
                found = len(models.known_ids() & {m[0] for m in entries})
                _log(
                    f"Catalogue: {len(entries)} models, {found} of our "
                    f"{len(models.MODELS)} registry models present, "
                    f"{len(unknown)} unknown retopology candidates"
                )
                if entries and found == 0:
                    _log(
                        "None of the registry models appear in this list, so it is "
                        "probably not the catalogue of usable generation models"
                    )
                for model_id, name in unknown:
                    _log(f"  not in registry: {model_id} {name}".rstrip())
            _redraw_all()
            return None

        bpy.app.timers.register(collect, first_interval=0.2)
        self.report({"INFO"}, "Fetching the model catalogue ...")
        return {"FINISHED"}


def _run_in_thread(work, done):
    """Fuehrt work() im Thread aus und ruft done(result) im Hauptthread auf.

    Netzwerkaufrufe duerfen den Hauptthread nicht blockieren, und ein Panel
    darf sie nicht ausloesen. Ein Timer holt das Ergebnis ab.
    """
    result = {}

    def run():
        try:
            result["value"] = work()
        except ScenarioError as e:
            result["error"] = str(e)
        except Exception as e:  # noqa: BLE001
            _log(traceback.format_exc())
            result["error"] = f"{type(e).__name__}: {e}"

    thread = threading.Thread(target=run, daemon=True, name="sb_ai_retopo_net")
    thread.start()

    def collect():
        if thread.is_alive():
            return 0.2
        done(result)
        _redraw_all()
        return None

    bpy.app.timers.register(collect, first_interval=0.2)
    return thread


class SB_OT_probe_model(bpy.types.Operator):
    bl_idname = "sb.ai_retopo_probe_model"
    bl_label = "Check"
    bl_description = (
        "Ask the API directly whether this model id exists for your account. "
        "More reliable than the catalogue, which does not list platform models"
    )

    def execute(self, context):
        api_key, api_secret = preferences.get_credentials(context)
        if not api_key or not api_secret:
            self.report({"ERROR"}, "Scenario API key and secret are missing (add-on preferences).")
            return {"CANCELLED"}

        prefs = preferences.get_prefs(context)
        wanted = [spec["id"] for spec in models.MODELS]
        extra = prefs.probe_id.strip()
        if extra and extra not in wanted:
            wanted.append(extra)

        def work():
            client = ScenarioClient(api_key, api_secret, log=_log)
            return {model_id: client.probe_model(model_id) for model_id in wanted}

        def done(result):
            if "error" in result:
                preferences.get_prefs().catalogue_error = result["error"]
                _log(f"Model check failed: {result['error']}")
                return
            found = result["value"]
            preferences.get_prefs().set_probes(found)
            for model_id, (status, note) in found.items():
                _log(f"Model {model_id}: {status} ({note})")

        _run_in_thread(work, done)
        self.report({"INFO"}, f"Checking {len(wanted)} model id(s) ...")
        return {"FINISHED"}


class SB_OT_reload_registry(bpy.types.Operator):
    bl_idname = "sb.ai_retopo_reload_registry"
    bl_label = "Reload Registry"
    bl_description = "Read the model registry file again, without restarting Blender"

    def execute(self, context):
        models.load()
        _redraw_all()
        if models.LOAD_ERROR:
            self.report({"WARNING"}, f"Registry: {models.LOAD_ERROR}")
        else:
            self.report({"INFO"}, f"Registry: {len(models.MODELS)} models loaded")
        return {"FINISHED"}


class SB_OT_export_registry(bpy.types.Operator):
    bl_idname = "sb.ai_retopo_export_registry"
    bl_label = "Export Model List"
    bl_description = (
        "Write the model registry to the Blender config folder so it can be "
        "edited and survives reinstalling the add-on"
    )

    def execute(self, context):
        try:
            path = models.save_user_file()
        except Exception as e:  # noqa: BLE001
            self.report({"ERROR"}, f"Could not write the registry: {e}")
            return {"CANCELLED"}
        models.load()
        _redraw_all()
        self.report({"INFO"}, f"Registry written to {path}")
        return {"FINISHED"}


class SB_OT_reset_registry(bpy.types.Operator):
    bl_idname = "sb.ai_retopo_reset_registry"
    bl_label = "Reset to Bundled"
    bl_description = (
        "Set the edited user copy of the model registry aside so the list "
        "shipped with the add-on applies again. The copy is renamed, not deleted"
    )

    @classmethod
    def poll(cls, context):
        return models.user_file_in_use()

    def execute(self, context):
        try:
            backup = models.reset_user_file()
        except OSError as e:
            self.report({"ERROR"}, f"Could not move the user copy aside: {e}")
            return {"CANCELLED"}
        models.load()
        _redraw_all()
        if backup:
            self.report({"INFO"}, f"User copy renamed to {os.path.basename(backup)}")
        else:
            self.report({"INFO"}, "There was no user copy")
        return {"FINISHED"}


def _redraw_all():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in ("VIEW_3D", "PREFERENCES"):
                area.tag_redraw()


def _redraw(context):
    screen = context.screen if context.screen else None
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


classes = (
    SB_OT_ai_retopo,
    SB_OT_ai_retopo_cancel,
    SB_OT_refresh_models,
    SB_OT_reload_registry,
    SB_OT_export_registry,
    SB_OT_reset_registry,
    SB_OT_probe_model,
)


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

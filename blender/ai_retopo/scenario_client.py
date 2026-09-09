# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimaler Scenario-API-Client (nur Standardbibliothek).

Ablauf wie in Phototron (apps/desktop/public/ipc/retopology.js):
  1. POST /v1/uploads (kind "3d") -> presigned Part-URLs
  2. PUT der Datei-Teile
  3. POST /v1/uploads/{id}/action {complete} -> auf entityId (Asset) warten
  4. POST /v1/generate/custom/model_tencent-smarttopology
  5. GET /v1/jobs/{id} bis success
  6. GET /v1/assets/{id} -> url -> Download

Alle Netzwerkaufrufe laufen in einem Worker-Thread; hier wird KEIN bpy verwendet.
"""

import base64
import json
import threading
import time
import urllib.error
import urllib.request

API_BASE = "https://api.cloud.scenario.com"
# Modelle und ihre Parameter stehen in models.py
PART_SIZE = 5 * 1024 * 1024
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # Limit der Hunyuan-Modelle
REQUEST_TIMEOUT = 60

# Stufen der level-basierten Modelle; das Panel-Enum muss genau diese kennen
FACE_LEVELS = ("low", "medium", "high")

MIME_TO_EXT = {
    "model/obj": ".obj",
    "model/gltf-binary": ".glb",
    "model/gltf+json": ".gltf",
    "model/fbx": ".fbx",
    "application/octet-stream": None,  # nichtssagend, dann entscheiden die Magic Bytes
}

# Reihenfolge der Bevorzugung beim Herunterladen: OBJ behaelt Quads, GLB
# trianguliert, FBX liefert Tripo fuer Quad-Ausgaben.
PREFERRED_MIMES = ("model/obj", "model/gltf-binary", "model/fbx")


class ScenarioError(Exception):
    pass


class Cancelled(ScenarioError):
    pass


class ScenarioClient:
    def __init__(self, api_key, api_secret, *, cancel_event=None, log=None,
                 poll_interval=5.0, job_timeout=900.0):
        token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        self._auth = f"Basic {token}"
        self._cancel = cancel_event or threading.Event()
        self._log = log or (lambda msg: None)
        self.poll_interval = poll_interval
        self.job_timeout = job_timeout

    # -- Basis ----------------------------------------------------------

    def _check_cancel(self):
        if self._cancel.is_set():
            raise Cancelled("Cancelled")

    def _sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._check_cancel()
            time.sleep(max(0.0, min(0.25, end - time.monotonic())))

    def _request(self, method, path, body=None):
        self._check_cancel()
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API_BASE + path, data=data, method=method)
        req.add_header("Authorization", self._auth)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as res:
                raw = res.read()
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                parsed = json.loads(raw)
                msg = parsed.get("message") or parsed.get("error") or json.dumps(parsed)
            except Exception:
                msg = raw[:500].decode(errors="replace")
            raise ScenarioError(f"API {e.code} ({method} {path}): {msg}") from None
        except urllib.error.URLError as e:
            raise ScenarioError(f"Connection failed: {e.reason}") from None
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise ScenarioError(f"Invalid API response: {raw[:300]!r}") from None

    def _put(self, url, chunk):
        self._check_cancel()
        req = urllib.request.Request(url, data=chunk, method="PUT")
        req.add_header("Content-Length", str(len(chunk)))
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT * 5) as res:
                res.read()
        except urllib.error.HTTPError as e:
            raise ScenarioError(f"Upload PUT {e.code}: {e.read()[:200]!r}") from None
        except urllib.error.URLError as e:
            raise ScenarioError(f"Upload failed: {e.reason}") from None

    def _download(self, url):
        self._check_cancel()
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT * 5) as res:
                chunks = []
                while True:
                    self._check_cancel()
                    block = res.read(1024 * 1024)
                    if not block:
                        break
                    chunks.append(block)
                return b"".join(chunks)
        except urllib.error.HTTPError as e:
            raise ScenarioError(f"Download failed: HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise ScenarioError(f"Download failed: {e.reason}") from None

    # -- Upload ---------------------------------------------------------

    def upload_3d(self, data, file_name, content_type, on_progress=None):
        """Laedt eine 3D-Datei hoch und gibt die Asset-ID zurueck."""
        if len(data) > MAX_UPLOAD_BYTES:
            raise ScenarioError(
                f"File too large ({len(data) / 1024 / 1024:.0f} MB, limit is 200 MB). "
                "Enable pre-decimation."
            )
        parts_count = max(1, -(-len(data) // PART_SIZE))
        self._log(f"Upload {file_name}: {len(data) / 1024 / 1024:.1f} MB in {parts_count} part(s)")

        init = self._request("POST", "/v1/uploads", {
            "fileName": file_name,
            "contentType": content_type,
            "kind": "3d",
            "parts": parts_count,
            "fileSize": len(data),
        })
        upload = init.get("upload") or init
        upload_id = upload.get("id")
        parts = upload.get("parts") or []
        if not upload_id or not parts:
            raise ScenarioError(f"Upload init failed: {json.dumps(init)[:300]}")

        for i, part in enumerate(parts):
            start = i * PART_SIZE
            self._put(part["url"], data[start:start + PART_SIZE])
            if on_progress:
                on_progress((i + 1) / len(parts))

        self._request("POST", f"/v1/uploads/{upload_id}/action", {"action": "complete"})

        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            self._sleep(self.poll_interval)
            res = self._request("GET", f"/v1/uploads/{upload_id}")
            upload = res.get("upload") or res
            status = upload.get("status")
            asset_id = upload.get("entityId") or upload.get("assetId") or upload.get("asset_id")
            self._log(f"Upload status: {status}")
            if status in ("imported", "validated", "completed") and asset_id:
                return asset_id
            if status in ("failed", "error"):
                raise ScenarioError(f"Upload validation failed: {json.dumps(upload)[:300]}")
        raise ScenarioError("Upload import timed out after 5 minutes.")

    # -- Retopologie-Job -------------------------------------------------

    def start_generation(self, model_id, body):
        """Startet einen Job auf /v1/generate/custom/{model_id}.

        Der Body wird von models.build_request erzeugt, weil jedes Modell
        eigene Parameternamen hat.
        """
        if not model_id:
            raise ScenarioError("No model given.")
        self._log(f"Generate {model_id}: {json.dumps(body)}")
        res = self._request("POST", f"/v1/generate/custom/{model_id}", body)
        job_id = extract_job_id(res)
        if not job_id:
            raise ScenarioError(f"No job id in response: {json.dumps(res)[:300]}")
        return job_id

    def wait_for_job(self, job_id, on_poll=None):
        deadline = time.monotonic() + self.job_timeout
        count = 0
        while time.monotonic() < deadline:
            self._sleep(self.poll_interval)
            count += 1
            res = self._request("GET", f"/v1/jobs/{job_id}")
            job = res.get("job") or res
            status = job.get("status")
            if on_poll:
                on_poll(count, status, job.get("progress"))
            if status in ("success", "completed"):
                return res
            if status in ("failed", "failure", "error", "canceled"):
                history = job.get("statusHistory") or []
                last = history[-1] if history and isinstance(history[-1], dict) else {}
                reason = job.get("error") or job.get("message") or last.get("reason") or status
                raise ScenarioError(f"Job failed: {reason}")
        raise ScenarioError(f"Timed out: job did not finish within {self.job_timeout / 60:.0f} minutes")

    def download_job_mesh(self, job_result):
        """Laedt das 3D-Ergebnis eines Jobs. Bevorzugt OBJ (Quads), sonst GLB.

        Returns: (bytes, extension)
        """
        asset_ids = extract_asset_ids(job_result)
        if not asset_ids:
            raise ScenarioError(f"No result asset id: {json.dumps(job_result)[:400]}")

        infos = []
        for aid in asset_ids:
            info = self._request("GET", f"/v1/assets/{aid}")
            asset = info.get("asset") or info
            infos.append((aid, asset))

        chosen = pick_mesh_asset(infos)
        if chosen is None:
            raise ScenarioError("No 3D asset found in the job result")

        aid, asset = chosen
        mime = asset.get("mimeType") or asset.get("contentType") or ""
        self._log(f"Downloading asset {aid} ({mime or 'unknown'})")
        data = self._download(asset["url"])
        ext = MIME_TO_EXT.get(mime) or detect_extension(data)
        if ext == ".bin":
            # Kein bekanntes Format: die Bytes beschreiben, damit ein neues
            # Ausgabeformat nachvollziehbar ist statt nur '.bin unbekannt'
            self._log(f"Unrecognised result format: {describe_bytes(data, mime)}")
        else:
            self._log(f"Result format: {ext}")
        return data, ext


# -- Hilfsfunktionen (reine Datenverarbeitung, testbar ohne Netz) --------

def extract_job_id(res):
    job = res.get("job") or {}
    return job.get("jobId") or job.get("id") or res.get("jobId") or res.get("id")


def extract_asset_ids(job_result):
    job = job_result.get("job") or job_result
    meta = job.get("metadata") or {}
    result = job.get("result") or {}
    ids = meta.get("assetIds") or (meta.get("output") or {}).get("assetIds") or result.get("assetIds") or []
    ids = list(ids)
    if not ids:
        single = result.get("assetId") or result.get("asset_id")
        if not single and job.get("assets"):
            single = job["assets"][0].get("id")
        if single:
            ids.append(single)
    return ids


def pick_mesh_asset(infos):
    """Waehlt aus [(id, asset)] das 3D-Asset nach PREFERRED_MIMES, sonst kind=3d."""
    def by_mime(mime):
        for aid, asset in infos:
            if (asset.get("mimeType") or asset.get("contentType") or "") == mime and asset.get("url"):
                return aid, asset
        return None

    chosen = None
    for mime in PREFERRED_MIMES:
        chosen = by_mime(mime)
        if chosen is not None:
            break
    if chosen is None:
        for aid, asset in infos:
            if asset.get("kind") == "3d" and asset.get("url"):
                return aid, asset
    return chosen


def detect_extension(data):
    """Ermittelt das Format an den Magic Bytes, wenn der MIME-Typ nichts sagt."""
    if data[:4] == b"glTF":
        return ".glb"
    # Tripo liefert Quad-Ergebnisse als FBX
    if data[:18] == b"Kaydara FBX Binary":
        return ".fbx"
    if data[:3] == b"ply":
        return ".ply"
    head = data[:200].lstrip().decode("utf-8", errors="replace")
    if head.startswith("; FBX"):
        return ".fbx"
    for prefix in ("v ", "vt ", "vn ", "f ", "o ", "g ", "s ", "usemtl ", "mtllib "):
        if head.startswith(prefix):
            return ".obj"
    if head.startswith("#"):
        # OBJ-Kommentar, aber nur wenn irgendwo echte OBJ-Daten folgen
        sample = data[:4096].decode("utf-8", errors="replace")
        if any(f"\n{p}" in sample for p in ("v ", "f ", "vn ", "vt ")):
            return ".obj"
    return ".bin"


def describe_bytes(data, mime=""):
    """Kurzbeschreibung fuer Fehlermeldungen bei unbekanntem Format."""
    head = bytes(data[:16])
    printable = "".join(chr(b) if 32 <= b < 127 else "." for b in head)
    return f"mime={mime or 'unknown'}, {len(data)} bytes, starts with {head.hex()} '{printable}'"

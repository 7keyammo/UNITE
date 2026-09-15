from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


MOBILE_HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DaQauntum Device Bridge</title><style>
:root{color-scheme:dark}body{font-family:system-ui,-apple-system,sans-serif;background:#03050b;color:#edf8ff;margin:0;min-height:100vh;display:grid;place-items:center}
main{width:min(92vw,560px);background:rgba(8,14,28,.92);border:1px solid #24415f;border-radius:24px;padding:24px;box-shadow:0 20px 80px #0008}h1{margin:0 0 6px;font-size:1.55rem}p{color:#9ab1c8}.atom{width:72px;height:72px;border:2px solid #61e8ff;border-radius:50%;display:grid;place-items:center;margin:0 auto 18px;box-shadow:0 0 36px #32cfff55;color:#9bf5ff;font-weight:800}
.card{border:1px solid #1d344d;border-radius:16px;padding:16px;margin-top:14px;background:#070c17}input,textarea,button{box-sizing:border-box;width:100%;font:inherit;border-radius:12px;border:1px solid #284765;background:#0a1422;color:#eefaff;padding:12px;margin-top:9px}button{background:#0e6379;border-color:#39ddff;font-weight:700}button.secondary{background:#121a2a}.ok{color:#77f5ba}.bad{color:#ff8d9b}.small{font-size:.85rem;color:#7892aa}</style></head>
<body><main><div class="atom">DQ</div><h1>DaQauntum Device Bridge</h1><p>Pair this phone with the DaQauntum running on your computer. Files and notes are sent only to that local machine.</p>
<div id="pairCard" class="card"><b>Pair device</b><input id="code" inputmode="numeric" maxlength="6" placeholder="6-digit code shown on computer"><input id="device" placeholder="Device name (e.g. Louis iPhone)"><button onclick="pair()">Pair</button></div>
<div id="sendCard" class="card" style="display:none"><b>Send knowledge</b><input id="project" placeholder="Project (optional)"><input id="file" type="file"><button onclick="upload()">Send file</button><textarea id="note" rows="5" placeholder="Paste a note, link, idea, or observation..."></textarea><button class="secondary" onclick="sendText()">Send note</button><div id="result" class="small"></div></div>
<script>
let token=sessionStorage.getItem('dq_bridge_token')||'';let device=sessionStorage.getItem('dq_bridge_device')||'';
const $=id=>document.getElementById(id);function show(msg,ok=true){$('result').innerHTML=`<p class="${ok?'ok':'bad'}">${String(msg).replace(/[<>&]/g,'')}</p>`}
function paired(){if(token){$('pairCard').style.display='none';$('sendCard').style.display='block'}}paired();
async function pair(){try{device=$('device').value.trim()||'Phone';let r=await fetch('/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:$('code').value.trim(),device})});let d=await r.json();if(!r.ok)throw Error(d.error||'Pairing failed');token=d.token;sessionStorage.setItem('dq_bridge_token',token);sessionStorage.setItem('dq_bridge_device',device);paired();show('Paired with DaQauntum.')}catch(e){alert(e.message)}}
async function upload(){let f=$('file').files[0];if(!f)return show('Choose a file first.',false);show('Sending…');try{let r=await fetch('/upload',{method:'POST',headers:{'Authorization':'Bearer '+token,'X-Filename':encodeURIComponent(f.name),'X-Project':encodeURIComponent($('project').value.trim()),'X-Device':encodeURIComponent(device)},body:f});let d=await r.json();if(!r.ok)throw Error(d.error||'Upload failed');show(`Indexed as source #${d.source.id}: ${d.source.title}`)}catch(e){show(e.message,false)}}
async function sendText(){let text=$('note').value.trim();if(!text)return show('Enter a note first.',false);show('Sending…');try{let r=await fetch('/text',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify({text,project:$('project').value.trim(),device})});let d=await r.json();if(!r.ok)throw Error(d.error||'Send failed');$('note').value='';show(`Note indexed as source #${d.source.id}`)}catch(e){show(e.message,false)}}
</script></main></body></html>'''


class DeviceBridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, manager, config: dict[str, Any] | None = None):
        super().__init__(address, DeviceBridgeHandler)
        cfg = config or {}
        self.manager = manager
        self.max_upload_bytes = int(cfg.get("max_upload_bytes", 20_000_000))
        self.session_ttl = int(cfg.get("session_ttl_seconds", 86400))
        self.pairing_ttl = int(cfg.get("pairing_ttl_seconds", 900))
        self.pairing_code = f"{secrets.randbelow(900000)+100000:06d}"
        self.pairing_created = time.time()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.pair_failures: dict[str, list[float]] = {}

    def new_pairing_code(self) -> str:
        self.pairing_code = f"{secrets.randbelow(900000)+100000:06d}"
        self.pairing_created = time.time()
        return self.pairing_code

    def validate_pair_code(self, code: str, remote: str) -> bool:
        now = time.time()
        recent = [x for x in self.pair_failures.get(remote, []) if now - x < 300]
        self.pair_failures[remote] = recent
        if len(recent) >= 8:
            return False
        if now - self.pairing_created > self.pairing_ttl:
            return False
        ok = secrets.compare_digest(str(code), self.pairing_code)
        if not ok:
            recent.append(now)
        return ok

    def create_session(self, device: str, remote: str) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = {"device": device[:120], "remote": remote, "created": time.time(), "last": time.time()}
        return token

    def session(self, token: str) -> dict[str, Any] | None:
        item = self.sessions.get(token)
        if not item:
            return None
        if time.time() - float(item["last"]) > self.session_ttl:
            self.sessions.pop(token, None)
            return None
        item["last"] = time.time()
        return item


class DeviceBridgeHandler(BaseHTTPRequestHandler):
    server: DeviceBridgeHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.path not in {"/", "/favicon.ico"}:
            super().log_message(fmt, *args)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/mobile"}:
            data = MOBILE_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(data); return
        if path == "/status":
            self._json({"ok": True, "service": "DaQauntum Device Bridge", "paired_sessions": len(self.server.sessions)})
            return
        self._json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/pair":
                payload = self._read_json(20_000)
                remote = self.client_address[0]
                if not self.server.validate_pair_code(str(payload.get("code", "")), remote):
                    self._json({"ok": False, "error": "Invalid/expired pairing code or too many attempts"}, HTTPStatus.UNAUTHORIZED); return
                device = str(payload.get("device", "Phone")).strip() or "Phone"
                token = self.server.create_session(device, remote)
                self._json({"ok": True, "token": token, "expires_in": self.server.session_ttl}); return
            session = self._authorize()
            if path == "/upload":
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0 or length > self.server.max_upload_bytes:
                    raise ValueError(f"Upload must be 1..{self.server.max_upload_bytes} bytes")
                raw_name = unquote(self.headers.get("X-Filename", "upload.bin"))
                project = unquote(self.headers.get("X-Project", "")).strip() or None
                device = unquote(self.headers.get("X-Device", "")).strip() or session.get("device", "Phone")
                filename = self._safe_filename(raw_name)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                folder = self.server.manager.inbox_dir / datetime.now().strftime("%Y-%m-%d")
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / f"{stamp}-{secrets.token_hex(3)}-{filename}"
                target.write_bytes(self.rfile.read(length))
                source = self.server.manager.ingest_device_file(target, project=project, device_name=device)
                self._json({"ok": True, "source": source}); return
            if path == "/text":
                payload = self._read_json(min(self.server.max_upload_bytes, 2_000_000))
                text = str(payload.get("text", "")).strip()
                if not text:
                    raise ValueError("Text is empty")
                project = str(payload.get("project", "")).strip() or None
                device = str(payload.get("device", "")).strip() or session.get("device", "Phone")
                folder = self.server.manager.inbox_dir / datetime.now().strftime("%Y-%m-%d")
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}-phone-note.md"
                target.write_text(f"# Device note\n\nDevice: {device}\n\n{text}\n", encoding="utf-8")
                source = self.server.manager.ingest_device_file(target, project=project, device_name=device)
                self._json({"ok": True, "source": source}); return
            self._json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)
        except PermissionError as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.UNAUTHORIZED)
        except ValueError as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _authorize(self) -> dict[str, Any]:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise PermissionError("Pair this device first")
        item = self.server.session(auth[7:].strip())
        if not item:
            raise PermissionError("Pairing session expired; pair again")
        return item

    def _read_json(self, max_bytes: int) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > max_bytes:
            raise ValueError("Invalid request size")
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    @staticmethod
    def _safe_filename(name: str) -> str:
        name = Path(name).name.replace("\x00", "")
        cleaned = "".join(ch if ch.isalnum() or ch in "._- ()" else "_" for ch in name).strip(" .")
        return (cleaned or "upload.bin")[:180]

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(data)


class DeviceBridgeThread:
    def __init__(self, manager, host: str, port: int, config: dict[str, Any] | None = None):
        self.manager = manager
        self.host = host
        self.port = int(port)
        self.config = config or {}
        self.server: DeviceBridgeHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> "DeviceBridgeThread":
        self.server = DeviceBridgeHTTPServer((self.host, self.port), self.manager, self.config)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, name="daqauntum-device-bridge", daemon=True)
        self.thread.start()
        return self

    @property
    def pairing_code(self) -> str | None:
        return self.server.pairing_code if self.server else None

    def rotate_pairing_code(self) -> str:
        if not self.server:
            raise RuntimeError("Device bridge is not running")
        return self.server.new_pairing_code()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown(); self.server.server_close()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        self.server = None; self.thread = None

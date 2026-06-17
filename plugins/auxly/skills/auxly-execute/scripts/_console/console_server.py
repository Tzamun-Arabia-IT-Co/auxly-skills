#!/usr/bin/env python3
"""Auxly Execute Dashboard — live execution monitor server.

A tiny, dependency-free HTTP + SSE server that holds the execution state for one
plan run and streams updates to the browser dashboard. It is mutated by the
`dash.py` CLI (which Claude calls as it executes plan slices) and serves the
embedded Auxly-themed dashboard UI.

Endpoints:
  GET  /                      -> dashboard (ui/index.html)
  GET  /api/state             -> current state JSON
  GET  /events?token=...      -> Server-Sent Events stream of state mutations
  POST /api/event             -> mutate state  (X-Dash-Token header)  [executor]
  POST /api/resolve           -> resolve blocker / dismiss warning    [browser]
  GET  /api/actions?token=... -> drain queued browser actions         [executor]

State is held in memory and snapshotted to a JSON file so a fresh server (or the
CLI) can recover it. This server is intentionally single-run and localhost-only.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DashState:
    """Thread-safe execution state with file snapshotting and an SSE fan-out."""

    def __init__(self, snapshot_path: Path, initial: Optional[Dict[str, Any]] = None) -> None:
        self._lock = threading.Lock()
        self._snapshot_path = snapshot_path
        self._subscribers: List[queue.Queue] = []
        self._actions: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        if initial is not None:
            self._state = deepcopy(initial)
        elif snapshot_path.exists():
            self._state = json.loads(snapshot_path.read_text(encoding="utf-8"))
        else:
            self._state = _empty_state()
        self._write()

    # ---- snapshot ----
    def _write(self) -> None:
        try:
            tmp = self._snapshot_path.with_suffix(self._snapshot_path.suffix + ".tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self._state, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._snapshot_path)
        except OSError:
            pass

    # ---- SSE fan-out ----
    def subscribe(self) -> "queue.Queue":
        q: "queue.Queue" = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: "queue.Queue") -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _broadcast(self) -> None:
        snapshot = deepcopy(self._state)
        for q in list(self._subscribers):
            try:
                q.put_nowait(snapshot)
            except queue.Full:
                pass

    # ---- reads ----
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    # ---- browser action queue ----
    def push_action(self, action: Dict[str, Any]) -> None:
        self._actions.put(action)

    def drain_actions(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        while True:
            try:
                out.append(self._actions.get_nowait())
            except queue.Empty:
                break
        return out

    # ---- mutation ----
    def apply_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a single executor event. Returns the new state."""
        etype = event.get("type")
        with self._lock:
            s = self._state
            s["updated_at"] = now_iso()
            if etype == "set_run":
                for k in ("title", "status", "run_id"):
                    if k in event and event[k] is not None:
                        s[k] = event[k]
            elif etype == "set_plan":
                s["phases"] = event.get("phases") or []
                if event.get("title"):
                    s["title"] = event["title"]
            elif etype == "phase":
                _upsert_phase(s, event)
            elif etype == "slice":
                _upsert_slice(s, event)
            elif etype == "blocker":
                _add_blocker(s, event)
                s["status"] = "blocked"
            elif etype == "resolve_blocker":
                _resolve_blocker(s, event.get("id"), event.get("resolution", ""))
            elif etype == "agent":
                _upsert_agent(s, event)
            elif etype == "warning":
                _add_warning(s, event)
            elif etype == "dismiss_warning":
                _dismiss_warning(s, event.get("id"))
            elif etype == "log":
                s.setdefault("log", []).append({"ts": now_iso(), "msg": event.get("msg", "")})
                s["log"] = s["log"][-200:]
            _recompute(s)
            self._write()
            self._broadcast()
            return deepcopy(s)


def _empty_state() -> Dict[str, Any]:
    return {
        "run_id": "",
        "title": "",
        "status": "running",
        "started_at": now_iso(),
        "updated_at": now_iso(),
        "phases": [],
        "agents": [],
        "blockers": [],
        "warnings": [],
        "log": [],
        "progress": {"done": 0, "total": 0, "pct": 0},
    }


def _upsert_phase(s: Dict[str, Any], event: Dict[str, Any]) -> None:
    pid = str(event.get("id"))
    for p in s["phases"]:
        if str(p.get("id")) == pid:
            if event.get("status"):
                p["status"] = event["status"]
            if event.get("name"):
                p["name"] = event["name"]
            return
    s["phases"].append({"id": pid, "name": event.get("name") or pid,
                        "status": event.get("status") or "pending", "slices": []})


def _upsert_slice(s: Dict[str, Any], event: Dict[str, Any]) -> None:
    sid = str(event.get("id"))
    pid = sid.split(".")[0] if "." in sid else str(event.get("phase") or "")
    phase = None
    for p in s["phases"]:
        if str(p.get("id")) == pid:
            phase = p
            break
    if phase is None:
        phase = {"id": pid, "name": pid, "status": "active", "slices": []}
        s["phases"].append(phase)
    for sl in phase["slices"]:
        if str(sl.get("id")) == sid:
            if event.get("status"):
                sl["status"] = event["status"]
            if event.get("name"):
                sl["name"] = event["name"]
            if event.get("note") is not None:
                sl["note"] = event["note"]
            return
    phase["slices"].append({"id": sid, "name": event.get("name") or sid,
                            "status": event.get("status") or "pending", "note": event.get("note") or ""})


def _add_blocker(s: Dict[str, Any], event: Dict[str, Any]) -> None:
    bid = event.get("id") or f"b{len(s['blockers']) + 1}"
    for b in s["blockers"]:
        if b["id"] == bid:
            b.update({"subject": event.get("subject", b["subject"]),
                      "detail": event.get("detail", b.get("detail", "")), "status": "open"})
            return
    s["blockers"].append({"id": bid, "subject": event.get("subject", ""),
                          "detail": event.get("detail", ""), "slice": event.get("slice", ""),
                          "status": "open", "resolution": "", "ts": now_iso()})


def _resolve_blocker(s: Dict[str, Any], bid: Optional[str], resolution: str) -> None:
    for b in s["blockers"]:
        if b["id"] == bid:
            b["status"] = "resolved"
            b["resolution"] = resolution
            b["resolved_at"] = now_iso()
    if all(b["status"] == "resolved" for b in s["blockers"]):
        if s["status"] == "blocked":
            s["status"] = "running"


def _upsert_agent(s: Dict[str, Any], event: Dict[str, Any]) -> None:
    aid = str(event.get("id") or event.get("name") or f"agent{len(s.get('agents', [])) + 1}")
    agents = s.setdefault("agents", [])
    fields = ("name", "kind", "model", "role", "status", "current")
    for a in agents:
        if a["id"] == aid:
            for f in fields:
                if event.get(f) is not None:
                    a[f] = event[f]
            a["updated_at"] = now_iso()
            # When an agent goes idle/done, it is no longer on a task.
            if event.get("status") in ("idle", "done") and event.get("current") is None:
                a["current"] = ""
            return
    agents.append({
        "id": aid,
        "name": event.get("name") or aid,
        "kind": event.get("kind") or "claude",
        "model": event.get("model") or "",
        "role": event.get("role") or "executor",
        "status": event.get("status") or "idle",
        "current": event.get("current") or "",
        "updated_at": now_iso(),
    })


def _add_warning(s: Dict[str, Any], event: Dict[str, Any]) -> None:
    wid = event.get("id") or f"w{len(s['warnings']) + 1}"
    for w in s["warnings"]:
        if w["id"] == wid:
            return
    s["warnings"].append({"id": wid, "subject": event.get("subject", ""),
                          "detail": event.get("detail", ""), "slice": event.get("slice", ""),
                          "status": "active", "ts": now_iso()})


def _dismiss_warning(s: Dict[str, Any], wid: Optional[str]) -> None:
    for w in s["warnings"]:
        if w["id"] == wid:
            w["status"] = "dismissed"


def _recompute(s: Dict[str, Any]) -> None:
    total = 0
    done = 0
    for p in s["phases"]:
        for sl in p["slices"]:
            total += 1
            if sl["status"] == "done":
                done += 1
    pct = int(round(100 * done / total)) if total else 0
    s["progress"] = {"done": done, "total": total, "pct": pct}
    open_blockers = [b for b in s["blockers"] if b["status"] == "open"]
    if open_blockers:
        s["status"] = "blocked"
    elif s["status"] not in ("complete", "failed"):
        if total and done == total:
            s["status"] = "complete"
        elif s["status"] == "blocked":
            s["status"] = "running"


class _Handler(BaseHTTPRequestHandler):
    server_version = "AuxlyDash/1.0"

    def log_message(self, *_args: Any) -> None:  # silence access logs
        pass

    # ---- helpers ----
    def _state(self) -> DashState:
        return self.server.dash_state  # type: ignore[attr-defined]

    def _token_ok(self, parsed) -> bool:
        token = self.server.token  # type: ignore[attr-defined]
        if not token:
            return True
        q = parse_qs(parsed.query)
        header = self.headers.get("X-Dash-Token")
        return header == token or (q.get("token", [""])[0] == token)

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, rel: str) -> None:
        ui_dir = self.server.ui_dir  # type: ignore[attr-defined]
        path = (ui_dir / rel).resolve()
        if not str(path).startswith(str(ui_dir.resolve())) or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".png": "image/png",
        }.get(path.suffix, "application/octet-stream")
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    # ---- routes ----
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/" or route == "/index.html":
            self._send_file("index.html")
            return
        if route == "/api/state":
            self._send_json(self._state().snapshot())
            return
        if route == "/api/actions":
            if not self._token_ok(parsed):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            self._send_json({"actions": self._state().drain_actions()})
            return
        if route == "/events":
            self._serve_events(parsed)
            return
        # static asset (app.js, logo.js, assets/*)
        rel = route.lstrip("/")
        if rel:
            self._send_file(rel)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/api/event", "/api/resolve"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not self._token_ok(parsed):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "bad json"}, status=400)
            return
        st = self._state()
        if parsed.path == "/api/event":
            new_state = st.apply_event(payload)
            self._send_json({"ok": True, "state": new_state})
            return
        # /api/resolve  (from the browser)
        kind = payload.get("kind")
        if kind == "blocker":
            bid = payload.get("id")
            resolution = payload.get("resolution", "")
            st.apply_event({"type": "resolve_blocker", "id": bid, "resolution": resolution})
            st.push_action({"type": "resolve_blocker", "id": bid, "resolution": resolution})
        elif kind == "warning":
            wid = payload.get("id")
            st.apply_event({"type": "dismiss_warning", "id": wid})
            st.push_action({"type": "dismiss_warning", "id": wid})
        self._send_json({"ok": True})

    def _serve_events(self, parsed) -> None:
        if not self._token_ok(parsed):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        st = self._state()
        q = st.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            # initial snapshot
            self._sse(st.snapshot())
            while True:
                try:
                    snap = q.get(timeout=15)
                    self._sse(snap)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            st.unsubscribe(q)

    def _sse(self, snap: Dict[str, Any]) -> None:
        self.wfile.write(b"data: " + json.dumps(snap).encode("utf-8") + b"\n\n")
        self.wfile.flush()


def serve(state_path: Path, token: str, host: str = "127.0.0.1", port: int = 0) -> None:
    ui_dir = Path(__file__).resolve().parent / "ui"
    state = DashState(state_path)
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.dash_state = state  # type: ignore[attr-defined]
    httpd.ui_dir = ui_dir  # type: ignore[attr-defined]
    httpd.token = token  # type: ignore[attr-defined]
    actual_port = httpd.server_address[1]
    url = f"http://{host}:{actual_port}/?token={token}"
    # Hand the resolved URL/port back to the launcher via the session file.
    session_path = state_path.parent / "session.json"
    session = {
        "url": url, "host": host, "port": actual_port, "token": token,
        "state_path": str(state_path), "pid": os.getpid(),
    }
    session_path.write_text(json.dumps(session, indent=2), encoding="utf-8")
    print(f"AUXLY_DASH_URL {url}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Auxly Execute Dashboard server")
    ap.add_argument("--state", required=True, help="path to state JSON snapshot file")
    ap.add_argument("--token", required=True, help="access token")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()
    serve(Path(args.state), args.token, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Auxly Console — shared multi-stage live dashboard server.

One localhost server + one browser tab backs the whole Auxly workflow. Every
skill (plan / execute / verify / review / board) renders into a *stage* of the
same console instead of opening its own tab. Skills mutate state through the
`console.py` CLI; the browser receives live updates over SSE; buttons in the UI
enqueue *intents* that the running Claude session polls and acts on.

Endpoints:
  GET  /                      -> console UI (ui/index.html)
  GET  /api/state             -> full state JSON
  GET  /events?token=...      -> SSE stream of state
  POST /api/event             -> mutate state  (X-Auxly-Token)         [skills]
  POST /api/intent            -> enqueue intent / resolve / dismiss     [browser]
  GET  /api/actions?token=... -> drain queued browser intents          [skills]

State is kept in memory and snapshotted to a JSON file so the CLI and a fresh
server can recover it. Localhost-only, token-guarded, single run.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import threading
from copy import deepcopy
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Optional approximate prices ($ per 1M tokens) for the meter. Tokens are always
# shown; cost only when a model is found here. Drop a prices.json beside the run
# state to override, e.g. {"opus": {"in": 15, "out": 75}}.
DEFAULT_PRICES: Dict[str, Dict[str, float]] = {}


def _empty_state() -> Dict[str, Any]:
    return {
        "run_id": "", "title": "", "run_status": "running", "active_stage": "",
        "stage_order": [], "stages": {}, "agents": [], "blockers": [], "warnings": [],
        "meter": {"agents": {}, "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "priced": False},
        "log": [], "created_at": now_iso(), "updated_at": now_iso(),
    }


def _check_stage_kind(ev: Dict[str, Any]) -> str:
    return "execute" if ev.get("stage") == "execute" else "verify"


class ConsoleState:
    def __init__(self, snapshot_path: Path, prices: Optional[Dict[str, Dict[str, float]]] = None) -> None:
        self._lock = threading.Lock()
        self._snapshot_path = snapshot_path
        self._subs: List[queue.Queue] = []
        self._actions: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._prices = prices or DEFAULT_PRICES
        if snapshot_path.exists():
            try:
                self._state = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except Exception:
                self._state = _empty_state()
        else:
            self._state = _empty_state()
        self._write()

    def _write(self) -> None:
        try:
            tmp = self._snapshot_path.with_suffix(self._snapshot_path.suffix + ".tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self._state, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._snapshot_path)
        except OSError:
            pass

    def subscribe(self) -> "queue.Queue":
        q: "queue.Queue" = queue.Queue()
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: "queue.Queue") -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def _broadcast(self) -> None:
        snap = deepcopy(self._state)
        for q in list(self._subs):
            try:
                q.put_nowait(snap)
            except queue.Full:
                pass

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

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

    def apply(self, ev: Dict[str, Any]) -> Dict[str, Any]:
        t = ev.get("type")
        with self._lock:
            s = self._state
            s["updated_at"] = now_iso()
            if t == "set_run":
                for k in ("title", "run_id", "run_status"):
                    if ev.get(k) is not None:
                        s[k] = ev[k]
            elif t == "stage":
                self._stage_upsert(s, ev)
            elif t == "activate":
                if ev.get("name") in s["stages"]:
                    s["active_stage"] = ev["name"]
            elif t == "stage_md":
                st = self._ensure_stage(s, ev.get("name"), ev.get("kind", "markdown"), title=ev.get("title"))
                st["data"]["markdown"] = ev.get("markdown", "")
            elif t == "phase":
                self._phase(s, ev)
            elif t == "slice":
                self._slice(s, ev)
            elif t == "check":
                self._check(s, ev)
            elif t == "finding":
                self._finding(s, ev)
            elif t == "board":
                st = self._ensure_stage(s, ev.get("name", "board"), "board", title=ev.get("title", "Runs"))
                st["data"]["runs"] = ev.get("runs", [])
            elif t == "blocker":
                self._blocker(s, ev)
            elif t == "resolve_blocker":
                self._resolve_blocker(s, ev.get("id"), ev.get("resolution", ""))
            elif t == "warning":
                self._warning(s, ev)
            elif t == "dismiss_warning":
                self._dismiss_warning(s, ev.get("id"))
            elif t == "agent":
                self._agent(s, ev)
            elif t == "meter":
                self._meter(s, ev)
            elif t == "log":
                s.setdefault("log", []).append({"ts": now_iso(), "msg": ev.get("msg", "")})
                s["log"] = s["log"][-200:]
            self._recompute(s)
            self._write()
            self._broadcast()
            return deepcopy(s)

    def _ensure_stage(self, s, name, kind, title=None, order=None) -> Dict[str, Any]:
        name = str(name or kind or "stage")
        if name not in s["stages"]:
            s["stages"][name] = {"name": name, "kind": kind, "title": title or name.replace("-", " ").title(),
                                 "status": "pending",
                                 "order": order if order is not None else len(s["stage_order"]), "data": {}}
            s["stage_order"].append(name)
            if not s.get("active_stage"):
                s["active_stage"] = name
        return s["stages"][name]

    def _stage_upsert(self, s, ev) -> None:
        st = self._ensure_stage(s, ev.get("name"), ev.get("kind", "markdown"),
                                title=ev.get("title"), order=ev.get("order"))
        if ev.get("kind"):
            st["kind"] = ev["kind"]
        if ev.get("title"):
            st["title"] = ev["title"]
        if ev.get("status"):
            st["status"] = ev["status"]
        if ev.get("order") is not None:
            st["order"] = ev["order"]
        if ev.get("activate"):
            s["active_stage"] = st["name"]
        s["stage_order"].sort(key=lambda n: s["stages"][n].get("order", 0))

    def _phase(self, s, ev) -> None:
        st = self._ensure_stage(s, ev.get("stage", "execute"), "execute")
        phases = st["data"].setdefault("phases", [])
        pid = str(ev.get("id"))
        for p in phases:
            if str(p["id"]) == pid:
                if ev.get("status"):
                    p["status"] = ev["status"]
                if ev.get("name"):
                    p["name"] = ev["name"]
                return
        phases.append({"id": pid, "name": ev.get("name") or pid, "status": ev.get("status") or "pending", "slices": []})

    def _slice(self, s, ev) -> None:
        st = self._ensure_stage(s, ev.get("stage", "execute"), "execute")
        phases = st["data"].setdefault("phases", [])
        sid = str(ev.get("id"))
        pid = sid.split(".")[0] if "." in sid else str(ev.get("phase") or "")
        phase = next((p for p in phases if str(p["id"]) == pid), None)
        if phase is None:
            phase = {"id": pid, "name": pid, "status": "active", "slices": []}
            phases.append(phase)
        for sl in phase["slices"]:
            if str(sl["id"]) == sid:
                if ev.get("status"):
                    sl["status"] = ev["status"]
                if ev.get("name"):
                    sl["name"] = ev["name"]
                if ev.get("note") is not None:
                    sl["note"] = ev["note"]
                return
        phase["slices"].append({"id": sid, "name": ev.get("name") or sid,
                                "status": ev.get("status") or "pending", "note": ev.get("note") or ""})

    def _check(self, s, ev) -> None:
        st = self._ensure_stage(s, ev.get("stage", "verify"), _check_stage_kind(ev))
        checks = st["data"].setdefault("checks", [])
        cid = str(ev.get("id") or ev.get("name"))
        for c in checks:
            if str(c["id"]) == cid:
                for f in ("name", "status", "output"):
                    if ev.get(f) is not None:
                        c[f] = ev[f]
                return
        checks.append({"id": cid, "name": ev.get("name") or cid,
                       "status": ev.get("status") or "pending", "output": ev.get("output") or ""})

    def _finding(self, s, ev) -> None:
        st = self._ensure_stage(s, ev.get("stage", "review"), "review")
        findings = st["data"].setdefault("findings", [])
        fid = str(ev.get("id") or f"f{len(findings) + 1}")
        for f in findings:
            if str(f["id"]) == fid:
                for k in ("severity", "file", "line", "title", "detail", "verdict", "status"):
                    if ev.get(k) is not None:
                        f[k] = ev[k]
                return
        findings.append({"id": fid, "severity": ev.get("severity", "info"), "file": ev.get("file", ""),
                         "line": ev.get("line", ""), "title": ev.get("title", ""), "detail": ev.get("detail", ""),
                         "verdict": ev.get("verdict", ""), "status": ev.get("status", "open")})

    def _blocker(self, s, ev) -> None:
        bid = ev.get("id") or f"b{len(s['blockers']) + 1}"
        for b in s["blockers"]:
            if b["id"] == bid:
                b.update({"subject": ev.get("subject", b["subject"]), "detail": ev.get("detail", b.get("detail", "")),
                          "status": "open"})
                return
        s["blockers"].append({"id": bid, "subject": ev.get("subject", ""), "detail": ev.get("detail", ""),
                              "slice": ev.get("slice", ""), "stage": ev.get("stage", ""), "status": "open",
                              "resolution": "", "ts": now_iso()})

    def _resolve_blocker(self, s, bid, resolution) -> None:
        for b in s["blockers"]:
            if b["id"] == bid:
                b["status"] = "resolved"
                b["resolution"] = resolution

    def _warning(self, s, ev) -> None:
        wid = ev.get("id") or f"w{len(s['warnings']) + 1}"
        if any(w["id"] == wid for w in s["warnings"]):
            return
        s["warnings"].append({"id": wid, "subject": ev.get("subject", ""), "detail": ev.get("detail", ""),
                              "slice": ev.get("slice", ""), "stage": ev.get("stage", ""), "status": "active",
                              "ts": now_iso()})

    def _dismiss_warning(self, s, wid) -> None:
        for w in s["warnings"]:
            if w["id"] == wid:
                w["status"] = "dismissed"

    def _agent(self, s, ev) -> None:
        aid = str(ev.get("id") or ev.get("name") or f"agent{len(s['agents']) + 1}")
        if ev.get("remove"):
            s["agents"] = [a for a in s["agents"] if a["id"] != aid]
            return
        for a in s["agents"]:
            if a["id"] == aid:
                for f in ("name", "kind", "model", "role", "status", "current"):
                    if ev.get(f) is not None:
                        a[f] = ev[f]
                if ev.get("status") in ("idle", "done") and ev.get("current") is None:
                    a["current"] = ""
                return
        s["agents"].append({"id": aid, "name": ev.get("name") or aid, "kind": ev.get("kind") or "claude",
                            "model": ev.get("model") or "", "role": ev.get("role") or "executor",
                            "status": ev.get("status") or "idle", "current": ev.get("current") or ""})

    def _meter(self, s, ev) -> None:
        m = s["meter"]
        aid = str(ev.get("agent") or ev.get("model") or "agent")
        model = ev.get("model") or aid
        ti = int(ev.get("tokens_in") or 0)
        to = int(ev.get("tokens_out") or 0)
        a = m["agents"].setdefault(aid, {"model": model, "role": ev.get("role", ""),
                                         "tokens_in": 0, "tokens_out": 0, "calls": 0, "cost": 0.0})
        a["model"] = model
        a["tokens_in"] += ti
        a["tokens_out"] += to
        a["calls"] += 1
        price = self._prices.get(model) or self._prices.get(model.split("/")[-1])
        if price:
            a["cost"] += (ti / 1_000_000.0) * price.get("in", 0) + (to / 1_000_000.0) * price.get("out", 0)
            m["priced"] = True
        m["tokens_in"] = sum(x["tokens_in"] for x in m["agents"].values())
        m["tokens_out"] = sum(x["tokens_out"] for x in m["agents"].values())
        m["cost"] = round(sum(x["cost"] for x in m["agents"].values()), 4)

    def _recompute(self, s) -> None:
        for st in s["stages"].values():
            if st["kind"] == "execute":
                phases = st["data"].get("phases", [])
                total = sum(len(p["slices"]) for p in phases)
                done = sum(1 for p in phases for sl in p["slices"] if sl["status"] == "done")
                st["data"]["progress"] = {"done": done, "total": total,
                                          "pct": int(round(100 * done / total)) if total else 0}
        if [b for b in s["blockers"] if b["status"] == "open"]:
            s["run_status"] = "blocked"
        elif s["run_status"] == "blocked":
            s["run_status"] = "running"


class _Handler(BaseHTTPRequestHandler):
    server_version = "AuxlyConsole/2.0"
    # SSE needs a persistent connection. Default HTTP/1.0 has none, so the
    # /events stream closes immediately and the browser EventSource reconnects
    # forever (stuck "reconnecting…"). HTTP/1.1 keeps the socket open; every
    # non-stream response sends Content-Length so keep-alive is framed right.
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a: Any) -> None:
        pass

    def _state(self) -> ConsoleState:
        return self.server.cstate  # type: ignore[attr-defined]

    def _token_ok(self, parsed) -> bool:
        token = self.server.token  # type: ignore[attr-defined]
        if not token:
            return True
        q = parse_qs(parsed.query)
        return self.headers.get("X-Auxly-Token") == token or q.get("token", [""])[0] == token

    def _json(self, payload, status=200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, rel) -> None:
        ui = self.server.ui_dir  # type: ignore[attr-defined]
        path = (ui / rel).resolve()
        if not str(path).startswith(str(ui.resolve())) or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ctype = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".png": "image/png"}.get(path.suffix, "application/octet-stream")
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        p = urlparse(self.path)
        r = p.path
        if r in ("/", "/index.html"):
            self._file("index.html"); return
        if r == "/api/state":
            self._json(self._state().snapshot()); return
        if r == "/api/actions":
            if not self._token_ok(p):
                self.send_error(HTTPStatus.FORBIDDEN); return
            self._json({"actions": self._state().drain_actions()}); return
        if r == "/events":
            self._events(p); return
        rel = r.lstrip("/")
        if rel:
            self._file(rel); return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        p = urlparse(self.path)
        if p.path not in ("/api/event", "/api/intent"):
            self.send_error(HTTPStatus.NOT_FOUND); return
        if not self._token_ok(p):
            self.send_error(HTTPStatus.FORBIDDEN); return
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._json({"ok": False, "error": "bad json"}, 400); return
        st = self._state()
        if p.path == "/api/event":
            self._json({"ok": True, "state": st.apply(payload)}); return
        kind = payload.get("kind")
        if kind == "blocker":
            st.apply({"type": "resolve_blocker", "id": payload.get("id"), "resolution": payload.get("resolution", "")})
            st.push_action({"type": "resolve_blocker", "id": payload.get("id"), "resolution": payload.get("resolution", "")})
        elif kind == "warning":
            st.apply({"type": "dismiss_warning", "id": payload.get("id")})
            st.push_action({"type": "dismiss_warning", "id": payload.get("id")})
        elif kind == "intent":
            st.push_action({"type": "intent", "name": payload.get("name"), "payload": payload.get("payload", {})})
            st.apply({"type": "log", "msg": f"UI intent → {payload.get('name')}"})
        self._json({"ok": True})

    def _events(self, parsed) -> None:
        if not self._token_ok(parsed):
            self.send_error(HTTPStatus.FORBIDDEN); return
        st = self._state()
        q = st.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self._sse(st.snapshot())
            while True:
                try:
                    self._sse(q.get(timeout=15))
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n"); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            st.unsubscribe(q)

    def _sse(self, snap) -> None:
        self.wfile.write(b"data: " + json.dumps(snap).encode("utf-8") + b"\n\n")
        self.wfile.flush()


def _load_prices(state_path: Path) -> Dict[str, Dict[str, float]]:
    pf = state_path.parent / "prices.json"
    if pf.exists():
        try:
            return json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return DEFAULT_PRICES


def serve(state_path: Path, token: str, host: str = "127.0.0.1", port: int = 0) -> None:
    ui_dir = Path(__file__).resolve().parent / "ui"
    state = ConsoleState(state_path, prices=_load_prices(state_path))
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.cstate = state    # type: ignore[attr-defined]
    httpd.ui_dir = ui_dir   # type: ignore[attr-defined]
    httpd.token = token     # type: ignore[attr-defined]
    actual = httpd.server_address[1]
    url = f"http://{host}:{actual}/?token={token}"
    (state_path.parent / "session.json").write_text(json.dumps(
        {"url": url, "host": host, "port": actual, "token": token, "state_path": str(state_path), "pid": os.getpid()},
        indent=2), encoding="utf-8")
    print(f"AUXLY_CONSOLE_URL {url}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Auxly Console server")
    ap.add_argument("--state", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()
    serve(Path(a.state), a.token, host=a.host, port=a.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

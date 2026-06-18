#!/usr/bin/env python3
"""Auxly Console — shared control CLI.

Every Auxly skill drives the same console through this CLI: `start` (or
`ensure`) brings up one server + browser tab; the other verbs push stage data,
agents, blockers, warnings, meter readings, and intents. Buttons in the UI
enqueue intents that `poll` drains so the running Claude session can launch the
next skill — all in the same tab.

Session discovery: a single ./auxly-console/current-session.json points at the
live server; pass --session to target a specific run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
RUN_ROOT = Path.cwd() / "auxly-console" / "runs"
CURRENT = Path.cwd() / "auxly-console" / "current-session.json"


# --------------------------------------------------------------------------- #
# Plan parsing (shared so execute / review / board can all read a final-plan.md)
# --------------------------------------------------------------------------- #
def parse_plan(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return _norm_json(json.loads(text))
    return _parse_md(text)


def _norm_json(data: Dict[str, Any]) -> Dict[str, Any]:
    phases = []
    for i, p in enumerate(data.get("phases", []), start=1):
        pid = str(p.get("id") or i)
        slices = []
        for j, sl in enumerate(p.get("slices", []) or p.get("tasks", []), start=1):
            if isinstance(sl, str):
                slices.append({"id": f"{pid}.{j}", "name": sl})
            else:
                slices.append({"id": str(sl.get("id") or f"{pid}.{j}"),
                               "name": sl.get("name") or sl.get("title") or f"{pid}.{j}"})
        if not slices:
            slices = [{"id": f"{pid}.1", "name": p.get("name") or pid}]
        phases.append({"id": pid, "name": p.get("name") or f"Phase {pid}", "slices": slices})
    return {"title": data.get("title") or "", "phases": phases, "markdown": data.get("markdown", "")}


def _parse_md(text: str) -> Dict[str, Any]:
    phases: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    title = ""
    phase_re = re.compile(r"^###\s+Phase\s+([0-9A-Za-z]+)\s*:?\s*(.*)$", re.I)
    gen_phase = re.compile(r"^###\s+(?!#)(.*)$")
    task_re = re.compile(r"^####\s+Task\s+([0-9A-Za-z.]+)\s*:?\s*(.*)$", re.I)
    gen_task = re.compile(r"^####\s+(?!#)(.*)$")
    auto = 0
    for line in text.splitlines():
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        m = phase_re.match(line)
        if m:
            cur = {"id": m.group(1), "name": m.group(2).strip() or f"Phase {m.group(1)}", "slices": []}
            phases.append(cur); continue
        gm = gen_phase.match(line)
        if gm and not task_re.match(line):
            auto += 1
            cur = {"id": str(auto), "name": gm.group(1).strip(), "slices": []}
            phases.append(cur); continue
        tm = task_re.match(line)
        if tm and cur is not None:
            cur["slices"].append({"id": tm.group(1), "name": tm.group(2).strip() or tm.group(1)}); continue
        gtm = gen_task.match(line)
        if gtm and cur is not None:
            cur["slices"].append({"id": f"{cur['id']}.{len(cur['slices']) + 1}", "name": gtm.group(1).strip()})
    for p in phases:
        if not p["slices"]:
            p["slices"] = [{"id": f"{p['id']}.1", "name": p["name"]}]
    return {"title": title, "phases": phases, "markdown": text}


# --------------------------------------------------------------------------- #
# Session + HTTP
# --------------------------------------------------------------------------- #
def _await_session(path: Path, timeout: float) -> Optional[Dict[str, Any]]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        time.sleep(0.1)
    return None


def _session(path: Optional[str]) -> Dict[str, Any]:
    p = Path(path) if path else CURRENT
    if not p.exists():
        sys.exit("No Auxly Console session. Run `console.py start` (or any skill that starts it) first.")
    return json.loads(p.read_text(encoding="utf-8"))


def _alive(session: Dict[str, Any]) -> bool:
    try:
        _get(session, "/api/state")
        return True
    except Exception:
        return False


def _post(session, route, payload) -> Dict[str, Any]:
    url = f"http://{session['host']}:{session['port']}{route}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "X-Auxly-Token": session["token"]})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(session, route) -> Dict[str, Any]:
    url = f"http://{session['host']}:{session['port']}{route}?token={session['token']}"
    req = urllib.request.Request(url, method="GET", headers={"X-Auxly-Token": session["token"]})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _event(session, ev) -> None:
    _post(session, "/api/event", ev)


def _start_server(title: str, port: int, open_browser: bool) -> Dict[str, Any]:
    ts = time.strftime("%Y%m%d-%H%M%S")
    run_dir = RUN_ROOT / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "state.json"
    token = secrets.token_urlsafe(16)
    server = HERE / "console_server.py"
    with open(run_dir / "server.log", "w") as logf:
        subprocess.Popen([sys.executable, str(server), "--state", str(state_path), "--token", token,
                          "--port", str(port)], stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
    session = _await_session(run_dir / "session.json", timeout=10.0)
    if session is None:
        sys.exit(f"Console server failed to start. See {run_dir / 'server.log'}")
    session["run_id"] = ts
    CURRENT.parent.mkdir(parents=True, exist_ok=True)
    CURRENT.write_text(json.dumps(session, indent=2), encoding="utf-8")
    _event(session, {"type": "set_run", "run_id": ts, "title": title, "run_status": "running"})
    if open_browser:
        try:
            webbrowser.open(session["url"])
        except Exception:
            pass
    return session


def _ensure(title: str, port: int, open_browser: bool) -> Dict[str, Any]:
    """Attach to a live console if present, else start one."""
    if CURRENT.exists():
        try:
            s = json.loads(CURRENT.read_text(encoding="utf-8"))
            if _alive(s):
                return s
        except Exception:
            pass
    return _start_server(title, port, open_browser)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def c_start(a):
    s = _start_server(a.title or "Auxly", a.port, not a.no_open)
    if a.plan:
        _seed_plan(s, Path(a.plan), a.title)
    print(s["url"]); print(f"session: {CURRENT}")


def c_ensure(a):
    s = _ensure(a.title or "Auxly", a.port, not a.no_open)
    print(s["url"]); print(f"session: {CURRENT}")


def _seed_plan(session, plan_path: Path, title: Optional[str]):
    plan = parse_plan(plan_path)
    run_title = title or plan.get("title") or "Execution"
    _event(session, {"type": "set_run", "title": run_title})
    _event(session, {"type": "stage", "name": "plan", "kind": "plan", "title": "Plan", "status": "done", "order": 0})
    _event(session, {"type": "stage_md", "name": "plan", "kind": "plan", "markdown": plan.get("markdown", "")})
    _event(session, {"type": "stage", "name": "execute", "kind": "execute", "title": "Execute",
                     "status": "pending", "order": 1, "activate": True})
    # Always seed the full lifecycle so the header shows every step
    # (Plan ▸ Execute ▸ Verify ▸ Review) from the start — they fill in as reached.
    _event(session, {"type": "stage", "name": "verify", "kind": "verify", "title": "Verify",
                     "status": "pending", "order": 2})
    _event(session, {"type": "stage", "name": "review", "kind": "review", "title": "Review",
                     "status": "pending", "order": 3})
    for p in plan["phases"]:
        _event(session, {"type": "phase", "stage": "execute", "id": p["id"], "name": p["name"], "status": "pending"})
        for sl in p["slices"]:
            _event(session, {"type": "slice", "stage": "execute", "id": sl["id"], "name": sl["name"], "status": "pending"})


def c_plan(a):
    s = _ensure(a.title or "Auxly", 0, not a.no_open)
    _seed_plan(s, Path(a.file), a.title)
    print(s["url"])


def c_stage(a):
    s = _session(a.session)
    ev = {"type": "stage", "name": a.name}
    for f in ("kind", "title", "status", "order"):
        v = getattr(a, f)
        if v is not None:
            ev[f] = v
    if a.activate:
        ev["activate"] = True
    _event(s, ev)


def c_activate(a):
    _event(_session(a.session), {"type": "activate", "name": a.name})


def c_phase(a):
    _event(_session(a.session), {"type": "phase", "stage": a.stage, "id": a.id, "status": a.status, "name": a.name})


def c_slice(a):
    ev = {"type": "slice", "stage": a.stage, "id": a.id, "status": a.status}
    if a.name:
        ev["name"] = a.name
    if a.note is not None:
        ev["note"] = a.note
    _event(_session(a.session), ev)


def c_check(a):
    ev = {"type": "check", "stage": a.stage, "id": a.id, "status": a.status}
    if a.name:
        ev["name"] = a.name
    if a.output is not None:
        ev["output"] = a.output
    _event(_session(a.session), ev)


def c_finding(a):
    ev = {"type": "finding", "stage": a.stage}
    for f in ("id", "severity", "file", "line", "title", "detail", "verdict", "status"):
        v = getattr(a, f)
        if v is not None:
            ev[f] = v
    _event(_session(a.session), ev)


def c_blocker(a):
    _event(_session(a.session), {"type": "blocker", "id": a.id, "subject": a.subject,
                                 "detail": a.detail or "", "slice": a.slice or "", "stage": a.stage or ""})
    print(f"blocker raised: {a.id or a.subject}")


def c_warning(a):
    _event(_session(a.session), {"type": "warning", "id": a.id, "subject": a.subject,
                                 "detail": a.detail or "", "slice": a.slice or "", "stage": a.stage or ""})


def c_agent(a):
    ev = {"type": "agent", "id": a.id}
    if getattr(a, "remove", False):
        ev["remove"] = True
        _event(_session(a.session), ev)
        return
    for f in ("name", "kind", "model", "role", "status", "current"):
        v = getattr(a, f)
        if v is not None:
            ev[f] = v
    _event(_session(a.session), ev)


def c_meter(a):
    _event(_session(a.session), {"type": "meter", "agent": a.agent, "model": a.model, "role": a.role or "",
                                 "tokens_in": a.tokens_in or 0, "tokens_out": a.tokens_out or 0})


def c_md(a):
    md = Path(a.file).read_text(encoding="utf-8") if a.file else (a.text or "")
    _event(_session(a.session), {"type": "stage_md", "name": a.name, "kind": a.kind or "markdown",
                                 "title": a.title, "markdown": md})
    if a.activate:
        _event(_session(a.session), {"type": "activate", "name": a.name})


def c_board(a):
    runs = scan_runs(Path(a.root) if a.root else Path.cwd())
    s = _ensure(a.title or "Auxly Board", 0, not a.no_open)
    _event(s, {"type": "stage", "name": "board", "kind": "board", "title": "Runs", "order": 99, "activate": True})
    _event(s, {"type": "board", "name": "board", "runs": runs})
    print(s["url"]); print(f"{len(runs)} runs")


def c_log(a):
    _event(_session(a.session), {"type": "log", "msg": a.message})


def c_set(a):
    _event(_session(a.session), {"type": "set_run", "run_status": a.status})


def c_done(a):
    _event(_session(a.session), {"type": "set_run", "run_status": "complete"})


def c_poll(a):
    print(json.dumps(_get(_session(a.session), "/api/actions"), indent=2))


def c_state(a):
    print(json.dumps(_get(_session(a.session), "/api/state"), indent=2))


def c_status(a):
    """Compact, cheap status poll — what the orchestrator checks from time to
    time to know progress, open blockers, and whether the run is complete."""
    s = _get(_session(a.session), "/api/state")
    ex = (s.get("stages", {}).get("execute", {}) or {}).get("data", {}) or {}
    pr = ex.get("progress", {}) or {}
    open_blk = [b for b in s.get("blockers", []) if b.get("status") != "resolved"]
    open_wrn = [w for w in s.get("warnings", []) if w.get("status") != "dismissed"]
    out = {
        "run_status": s.get("run_status", ""),
        "complete": s.get("run_status") == "complete",
        "progress": {"pct": pr.get("pct", 0), "done": pr.get("done", 0), "total": pr.get("total", 0)},
        "active_stage": s.get("active_stage", ""),
        "open_blockers": [{"id": b.get("id"), "subject": b.get("subject")} for b in open_blk],
        "open_warnings": len(open_wrn),
    }
    print(json.dumps(out, indent=2))


def c_wait_blocker(a):
    s = _session(a.session)
    deadline = time.time() + a.timeout
    while time.time() < deadline:
        st = _get(s, "/api/state")
        for b in st.get("blockers", []):
            if b["id"] == a.id and b["status"] == "resolved":
                print(json.dumps({"resolved": True, "id": a.id, "resolution": b.get("resolution", "")})); return
        time.sleep(a.interval)
    print(json.dumps({"resolved": False, "id": a.id}))


# --------------------------------------------------------------------------- #
# Board scan
# --------------------------------------------------------------------------- #
def scan_runs(root: Path) -> List[Dict[str, Any]]:
    runs = []
    patterns = ["auxly-console/runs/*/state.json", "auxly-dash/runs/*/state.json",
                "auxly-council/runs/*/final-plan.md", "llm-council/runs/*/final-plan.md"]
    for pat in patterns:
        for path in sorted(root.glob(pat)):
            try:
                if path.suffix == ".json":
                    s = json.loads(path.read_text(encoding="utf-8"))
                    runs.append({"id": s.get("run_id") or path.parent.name, "title": s.get("title") or "",
                                 "kind": "console", "status": s.get("run_status", ""),
                                 "stages": list((s.get("stages") or {}).keys()),
                                 "path": str(path.parent), "updated": s.get("updated_at", "")})
                else:
                    runs.append({"id": path.parent.name, "title": _first_title(path), "kind": "council",
                                 "status": "complete", "stages": ["plan"], "path": str(path.parent), "updated": ""})
            except Exception:
                continue
    runs.sort(key=lambda r: r.get("updated") or r.get("id"), reverse=True)
    return runs


def _first_title(md: Path) -> str:
    for line in md.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return md.parent.name


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="console.py", description="Auxly Console control CLI")
    ap.add_argument("--session", help="session.json path (default ./auxly-console/current-session.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help=""):
        p = sub.add_parser(name, help=help); p.set_defaults(func=fn); return p

    p = add("start", c_start, "start a fresh console + browser tab"); p.add_argument("--title"); p.add_argument("--plan"); p.add_argument("--port", type=int, default=0); p.add_argument("--no-open", action="store_true")
    p = add("ensure", c_ensure, "attach to a live console or start one"); p.add_argument("--title"); p.add_argument("--port", type=int, default=0); p.add_argument("--no-open", action="store_true")
    p = add("plan", c_plan, "load a final-plan.md/json into Plan + Execute stages"); p.add_argument("--file", required=True); p.add_argument("--title"); p.add_argument("--no-open", action="store_true")

    p = add("stage", c_stage, "create/update a stage tab"); p.add_argument("name"); p.add_argument("--kind"); p.add_argument("--title"); p.add_argument("--status"); p.add_argument("--order", type=int); p.add_argument("--activate", action="store_true")
    p = add("activate", c_activate, "switch the active stage tab"); p.add_argument("name")

    p = add("phase", c_phase, "update a phase"); p.add_argument("id"); p.add_argument("--stage", default="execute"); p.add_argument("--status", required=True, choices=["pending", "active", "done", "failed"]); p.add_argument("--name")
    p = add("slice", c_slice, "update a slice"); p.add_argument("id"); p.add_argument("--stage", default="execute"); p.add_argument("--status", required=True, choices=["pending", "running", "done", "failed", "blocked"]); p.add_argument("--name"); p.add_argument("--note")
    p = add("check", c_check, "update a verify check"); p.add_argument("id"); p.add_argument("--stage", default="verify"); p.add_argument("--status", required=True, choices=["pending", "running", "pass", "fail", "skip"]); p.add_argument("--name"); p.add_argument("--output")
    p = add("finding", c_finding, "add/update a review finding"); p.add_argument("--stage", default="review"); p.add_argument("--id"); p.add_argument("--severity", choices=["critical", "high", "medium", "low", "info"]); p.add_argument("--file"); p.add_argument("--line"); p.add_argument("--title"); p.add_argument("--detail"); p.add_argument("--verdict", choices=["confirmed", "rejected", "unsure"]); p.add_argument("--status")

    p = add("blocker", c_blocker, "raise a RED blocker"); p.add_argument("--subject", required=True); p.add_argument("--detail"); p.add_argument("--id"); p.add_argument("--slice"); p.add_argument("--stage")
    p = add("warning", c_warning, "raise an AMBER warning"); p.add_argument("--subject", required=True); p.add_argument("--detail"); p.add_argument("--id"); p.add_argument("--slice"); p.add_argument("--stage")
    p = add("agent", c_agent, "register/update an agent or subagent"); p.add_argument("id"); p.add_argument("--name"); p.add_argument("--kind"); p.add_argument("--model"); p.add_argument("--role"); p.add_argument("--status", choices=["active", "idle", "done"]); p.add_argument("--current"); p.add_argument("--remove", action="store_true", help="remove this agent from the panel")
    p = add("meter", c_meter, "add token usage for an agent/model"); p.add_argument("--agent", required=True); p.add_argument("--model", required=True); p.add_argument("--role"); p.add_argument("--tokens-in", dest="tokens_in", type=int); p.add_argument("--tokens-out", dest="tokens_out", type=int)

    p = add("md", c_md, "set a markdown stage's content"); p.add_argument("name"); p.add_argument("--kind"); p.add_argument("--title"); p.add_argument("--file"); p.add_argument("--text"); p.add_argument("--activate", action="store_true")
    p = add("board", c_board, "scan runs and show the board"); p.add_argument("--root"); p.add_argument("--title"); p.add_argument("--no-open", action="store_true")

    p = add("log", c_log, "append a log line"); p.add_argument("message")
    p = add("set", c_set, "set run status"); p.add_argument("--status", required=True, choices=["running", "paused", "blocked", "complete", "failed"])
    add("done", c_done, "mark run complete")
    add("poll", c_poll, "drain UI intents/resolutions")
    add("state", c_state, "print state JSON")
    add("status", c_status, "compact status poll (run_status, progress, open blockers)")
    p = add("wait-blocker", c_wait_blocker, "poll until a blocker resolves (bounded)"); p.add_argument("id"); p.add_argument("--timeout", type=float, default=60.0); p.add_argument("--interval", type=float, default=2.0)
    return ap


if __name__ == "__main__":
    args = build_parser().parse_args()
    rc = args.func(args)
    raise SystemExit(rc or 0)

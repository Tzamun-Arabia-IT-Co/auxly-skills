#!/usr/bin/env python3
"""Auxly Execute Dashboard — control CLI.

Claude calls this while executing an accepted plan. `start` parses the plan,
launches the dashboard server (detached) and opens the browser. The other
subcommands are thin HTTP clients that push execution events to the running
server, and `poll` drains browser actions (e.g. a human resolving a blocker).

Typical loop (driven by Claude):
    python3 dash.py start --plan final-plan.md --title "DB migration"
    python3 dash.py phase 1 --status active
    python3 dash.py slice 1.1 --status running
    python3 dash.py slice 1.1 --status done
    python3 dash.py warning --subject "PG14 vs PG16 mismatch"
    python3 dash.py blocker --subject "Need DB password" --id db-pass
    # ...poll until the human resolves it in the UI...
    python3 dash.py poll
    python3 dash.py done
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
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
RUN_ROOT = Path.cwd() / "auxly-dash" / "runs"
CURRENT_SESSION = Path.cwd() / "auxly-dash" / "current-session.json"


# --------------------------------------------------------------------------- #
# Plan parsing
# --------------------------------------------------------------------------- #
def parse_plan(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return _normalize_json_plan(json.loads(text))
    return _parse_markdown_plan(text)


def _normalize_json_plan(data: Dict[str, Any]) -> Dict[str, Any]:
    phases = []
    for i, p in enumerate(data.get("phases", []), start=1):
        pid = str(p.get("id") or i)
        slices = []
        for j, sl in enumerate(p.get("slices", []) or p.get("tasks", []), start=1):
            if isinstance(sl, str):
                slices.append({"id": f"{pid}.{j}", "name": sl, "status": "pending", "note": ""})
            else:
                slices.append({
                    "id": str(sl.get("id") or f"{pid}.{j}"),
                    "name": sl.get("name") or sl.get("title") or f"{pid}.{j}",
                    "status": "pending", "note": "",
                })
        if not slices:
            slices = [{"id": f"{pid}.1", "name": p.get("name") or pid, "status": "pending", "note": ""}]
        phases.append({"id": pid, "name": p.get("name") or f"Phase {pid}", "status": "pending", "slices": slices})
    return {"title": data.get("title") or "", "phases": phases}


def _parse_markdown_plan(text: str) -> Dict[str, Any]:
    """Parse a council final-plan.md: ### Phase N: name / #### Task N.M: name."""
    phases: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    title = ""
    phase_re = re.compile(r"^###\s+Phase\s+([0-9A-Za-z]+)\s*:?\s*(.*)$", re.IGNORECASE)
    # also accept a plain "### <name>" as a phase if no "Phase N" form is used
    generic_phase_re = re.compile(r"^###\s+(?!#)(.*)$")
    task_re = re.compile(r"^####\s+Task\s+([0-9A-Za-z.]+)\s*:?\s*(.*)$", re.IGNORECASE)
    generic_task_re = re.compile(r"^####\s+(?!#)(.*)$")
    auto_phase = 0
    for line in text.splitlines():
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        m = phase_re.match(line)
        if not m:
            gm = generic_phase_re.match(line)
            if gm and not task_re.match(line):
                auto_phase += 1
                current = {"id": str(auto_phase), "name": gm.group(1).strip(), "status": "pending", "slices": []}
                phases.append(current)
                continue
        if m:
            pid = m.group(1)
            name = m.group(2).strip() or f"Phase {pid}"
            current = {"id": pid, "name": name, "status": "pending", "slices": []}
            phases.append(current)
            continue
        tm = task_re.match(line)
        if tm and current is not None:
            current["slices"].append({"id": tm.group(1), "name": tm.group(2).strip() or tm.group(1),
                                      "status": "pending", "note": ""})
            continue
        gtm = generic_task_re.match(line)
        if gtm and current is not None and not tm:
            idx = len(current["slices"]) + 1
            current["slices"].append({"id": f"{current['id']}.{idx}", "name": gtm.group(1).strip(),
                                      "status": "pending", "note": ""})
    # phases with no slices -> single slice mirroring the phase
    for p in phases:
        if not p["slices"]:
            p["slices"] = [{"id": f"{p['id']}.1", "name": p["name"], "status": "pending", "note": ""}]
    return {"title": title, "phases": phases}


# --------------------------------------------------------------------------- #
# Session / HTTP helpers
# --------------------------------------------------------------------------- #
def _load_session(path: Optional[str]) -> Dict[str, Any]:
    p = Path(path) if path else CURRENT_SESSION
    if not p.exists():
        sys.exit(f"No dashboard session found at {p}. Run `dash.py start` first.")
    return json.loads(p.read_text(encoding="utf-8"))


def _post(session: Dict[str, Any], route: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"http://{session['host']}:{session['port']}{route}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Dash-Token": session["token"]})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(session: Dict[str, Any], route: str) -> Dict[str, Any]:
    url = f"http://{session['host']}:{session['port']}{route}?token={session['token']}"
    req = urllib.request.Request(url, method="GET", headers={"X-Dash-Token": session["token"]})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _event(session: Dict[str, Any], event: Dict[str, Any]) -> None:
    _post(session, "/api/event", event)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_start(args: argparse.Namespace) -> int:
    plan = parse_plan(Path(args.plan)) if args.plan else {"title": args.title or "", "phases": []}
    title = args.title or plan.get("title") or "Execution"
    ts = time.strftime("%Y%m%d-%H%M%S")
    run_dir = RUN_ROOT / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "state.json"
    token = secrets.token_urlsafe(16)
    log_path = run_dir / "server.log"

    # Launch the server detached; it will write session.json and print the URL.
    server = HERE / "dash_server.py"
    with open(log_path, "w") as logf:
        subprocess.Popen(
            [sys.executable, str(server), "--state", str(state_path), "--token", token,
             "--port", str(args.port)],
            stdout=logf, stderr=subprocess.STDOUT, start_new_session=True,
        )

    session_path = run_dir / "session.json"
    session = _await_session(session_path, timeout=10.0)
    if session is None:
        sys.exit(f"Dashboard server failed to start. See {log_path}")

    CURRENT_SESSION.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_SESSION.write_text(json.dumps(session, indent=2), encoding="utf-8")

    # Seed plan + run metadata.
    _event(session, {"type": "set_plan", "title": title, "phases": plan["phases"]})
    _event(session, {"type": "set_run", "run_id": ts, "title": title, "status": "running"})

    if not args.no_open:
        try:
            webbrowser.open(session["url"])
        except Exception:
            pass
    print(session["url"])
    print(f"session: {CURRENT_SESSION}")
    return 0


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


def cmd_phase(args: argparse.Namespace) -> int:
    s = _load_session(args.session)
    _event(s, {"type": "phase", "id": args.id, "status": args.status, "name": args.name})
    return 0


def cmd_slice(args: argparse.Namespace) -> int:
    s = _load_session(args.session)
    ev: Dict[str, Any] = {"type": "slice", "id": args.id, "status": args.status}
    if args.name:
        ev["name"] = args.name
    if args.note is not None:
        ev["note"] = args.note
    _event(s, ev)
    return 0


def cmd_blocker(args: argparse.Namespace) -> int:
    s = _load_session(args.session)
    _event(s, {"type": "blocker", "id": args.id, "subject": args.subject,
               "detail": args.detail or "", "slice": args.slice or ""})
    print(f"blocker raised: {args.id or args.subject}")
    return 0


def cmd_warning(args: argparse.Namespace) -> int:
    s = _load_session(args.session)
    _event(s, {"type": "warning", "id": args.id, "subject": args.subject,
               "detail": args.detail or "", "slice": args.slice or ""})
    return 0


def cmd_agent(args: argparse.Namespace) -> int:
    s = _load_session(args.session)
    ev: Dict[str, Any] = {"type": "agent", "id": args.id}
    for f in ("name", "kind", "model", "role", "status", "current"):
        v = getattr(args, f)
        if v is not None:
            ev[f] = v
    _event(s, ev)
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    s = _load_session(args.session)
    _event(s, {"type": "log", "msg": args.message})
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    s = _load_session(args.session)
    _event(s, {"type": "set_run", "status": args.status})
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    s = _load_session(args.session)
    _event(s, {"type": "set_run", "status": "complete"})
    return 0


def cmd_poll(args: argparse.Namespace) -> int:
    s = _load_session(args.session)
    out = _get(s, "/api/actions")
    print(json.dumps(out, indent=2))
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    s = _load_session(args.session)
    out = _get(s, "/api/state")
    print(json.dumps(out, indent=2))
    return 0


def cmd_wait_blocker(args: argparse.Namespace) -> int:
    """Poll until a specific blocker is resolved; print its resolution. Bounded
    so a single CLI call never hangs forever — Claude re-invokes if needed."""
    s = _load_session(args.session)
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        state = _get(s, "/api/state")
        for b in state.get("blockers", []):
            if b["id"] == args.id and b["status"] == "resolved":
                print(json.dumps({"resolved": True, "id": args.id, "resolution": b.get("resolution", "")}))
                return 0
        time.sleep(args.interval)
    print(json.dumps({"resolved": False, "id": args.id}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="dash.py", description="Auxly Execute Dashboard control CLI")
    ap.add_argument("--session", help="path to a session.json (default: ./auxly-dash/current-session.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("start", help="parse plan, launch dashboard, open browser")
    p.add_argument("--plan", help="path to final-plan.md or plan.json")
    p.add_argument("--title", help="run title")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--no-open", action="store_true", help="don't open a browser")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("phase", help="update a phase status")
    p.add_argument("id")
    p.add_argument("--status", required=True, choices=["pending", "active", "done", "failed"])
    p.add_argument("--name")
    p.set_defaults(func=cmd_phase)

    p = sub.add_parser("slice", help="update a slice status")
    p.add_argument("id")
    p.add_argument("--status", required=True, choices=["pending", "running", "done", "failed", "blocked"])
    p.add_argument("--name")
    p.add_argument("--note")
    p.set_defaults(func=cmd_slice)

    p = sub.add_parser("blocker", help="raise a RED blocker (requires human action)")
    p.add_argument("--subject", required=True)
    p.add_argument("--detail")
    p.add_argument("--id")
    p.add_argument("--slice")
    p.set_defaults(func=cmd_blocker)

    p = sub.add_parser("warning", help="raise an AMBER warning (awareness only)")
    p.add_argument("--subject", required=True)
    p.add_argument("--detail")
    p.add_argument("--id")
    p.add_argument("--slice")
    p.set_defaults(func=cmd_warning)

    p = sub.add_parser("agent", help="register/update an agent or subagent (active/idle/done)")
    p.add_argument("id", help="stable agent id (e.g. 'lead', 'sub-tests')")
    p.add_argument("--name")
    p.add_argument("--kind", help="codex|claude|gemini|agy|... (icon in UI)")
    p.add_argument("--model")
    p.add_argument("--role", help="executor|subagent|reviewer|…")
    p.add_argument("--status", choices=["active", "idle", "done"])
    p.add_argument("--current", help="what it is working on now, e.g. 'slice 1.2'")
    p.set_defaults(func=cmd_agent)

    p = sub.add_parser("log", help="append a log line")
    p.add_argument("message")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("set", help="set run status")
    p.add_argument("--status", required=True, choices=["running", "paused", "blocked", "complete", "failed"])
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("done", help="mark the run complete")
    p.set_defaults(func=cmd_done)

    p = sub.add_parser("poll", help="drain browser actions (resolutions/dismissals)")
    p.set_defaults(func=cmd_poll)

    p = sub.add_parser("state", help="print current state JSON")
    p.set_defaults(func=cmd_state)

    p = sub.add_parser("wait-blocker", help="poll until a blocker is resolved (bounded)")
    p.add_argument("id")
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--interval", type=float, default=2.0)
    p.set_defaults(func=cmd_wait_blocker)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

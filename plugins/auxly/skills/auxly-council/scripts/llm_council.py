#!/usr/bin/env python3
import argparse
import json
import os
import queue
from pathlib import Path
import random
import re
import shutil
import shlex
import subprocess
import sys
import time
import threading
from datetime import datetime, timedelta, timezone
import webbrowser
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import base64
import html as _html

RETRY_LIMIT = 2
# Planners write a full structured plan in one shot; opus/large models routinely
# need more than a couple of minutes, so default generously. Override with --timeout.
DEFAULT_TIMEOUT_SEC = 600
DEFAULT_UI_KEEPALIVE_SEC = 20 * 60
DEFAULT_UI_SESSION_TTL_SEC = 30 * 60

# Empty = let Codex use the account's default model. Pinning an API-only name
# (e.g. "gpt-5.2-codex") breaks ChatGPT-account Codex with a 400. Override per
# member in agents.json if you have API access to a specific model.
CODEX_MODEL = ""
# Headroom so an occasional Opus tool_use (despite tools being disabled) doesn't
# kill the run as error_max_turns before any plan text is produced.
CLAUDE_MAX_TURNS = 6
CODEX_REASONING = "xhigh"
CLAUDE_MODEL = "opus"
CLAUDE_FAST_MODEL = "sonnet"
GEMINI_MODEL = "gemini-3-pro-preview"
AGY_MODEL = ""  # empty -> let the agy CLI pick its configured default model

# CLIs whose presence flips the council from "Claude-only fallback" to a
# genuine multi-vendor council. If none of these are installed we still run a
# robust council, but staffed entirely by Claude personas (see build_auto_council).
EXTERNAL_PLANNER_CLIS = ("codex", "gemini", "agy")

@dataclass
class AgentConfig:
    name: str
    kind: str
    command: Optional[str] = None
    output_format: str = "text"
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    agent: Optional[str] = None
    attach: Optional[str] = None
    cli_format: Optional[str] = None
    prompt_mode: str = "arg"
    extra_args: List[str] = field(default_factory=list)

@dataclass
class AgentResult:
    name: str
    raw_output: str
    data: Optional[Dict[str, Any]]
    valid: bool
    error: Optional[str]


@dataclass
class RunningAgent:
    config: AgentConfig
    prompt: str
    start_time: float
    process: Any


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def extract_json_array(text: str) -> Optional[List[Any]]:
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _readable_failure(raw: str) -> Optional[str]:
    """When structured extraction fails, the raw output is usually a wall of
    JSON event lines that is unreadable in the UI. Pull any human-facing
    error/message strings out so the panel shows *why* it failed instead of a
    JSON dump. Returns None if nothing useful is found."""
    messages: List[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not (line.startswith("{") or line.startswith("[")):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        stack = [event]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                etype = str(node.get("type") or node.get("event") or "")
                for key in ("message", "error"):
                    val = node.get(key)
                    if isinstance(val, str) and val.strip():
                        prefix = "error" if ("error" in etype or key == "error") else "note"
                        messages.append(f"{val.strip()}")
                    elif isinstance(val, dict):
                        stack.append(val)
                for child in node.values():
                    if isinstance(child, (dict, list)):
                        stack.append(child)
            elif isinstance(node, list):
                stack.extend(node)
    # De-dupe while preserving order.
    seen: set = set()
    unique = [m for m in messages if not (m in seen or seen.add(m))]
    if not unique:
        return None
    body = "\n".join(f"- {m}" for m in unique[:8])
    return f"⚠️ Agent did not return a plan. Reported:\n\n{body}"


def extract_agent_response(config: AgentConfig, raw: str) -> str:
    kind = (config.kind or config.name).lower()
    if kind == "agy":
        # The Antigravity CLI prints plain text in --print mode, so the body is
        # already the plan markdown. Only fall back to failure-scraping if it
        # somehow emitted JSON event lines.
        text = (raw or "").strip()
        if text and not text.lstrip().startswith("{"):
            return text
        return _readable_failure(raw) or text
    if kind == "codex":
        for line in raw.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("event") or event.get("type")
            if kind == "turn.completed":
                content = event.get("content")
                if isinstance(content, str):
                    return content
                message = event.get("message")
                if isinstance(message, dict):
                    msg_content = message.get("content")
                    if isinstance(msg_content, str):
                        return msg_content
            if kind == "item.completed":
                item = event.get("item")
                if isinstance(item, dict):
                    if item.get("type") in ("agent_message", "assistant_message"):
                        text = item.get("text")
                        if isinstance(text, str):
                            return text
        return _readable_failure(raw) or raw

    if kind == "claude":
        # `claude --output-format json` returns a single result object, while
        # stream modes return an array of events. Handle the single-object case
        # first so a successful judge run isn't shown as a raw JSON dump.
        single = extract_json(raw)
        if single is None:
            try:
                single = json.loads(raw)
            except json.JSONDecodeError:
                single = None
        if isinstance(single, dict):
            if isinstance(single.get("result"), str):
                return single["result"]
            msg = single.get("message")
            if isinstance(msg, dict):
                content_list = msg.get("content")
                if isinstance(content_list, list):
                    for block in content_list:
                        if isinstance(block, dict) and isinstance(block.get("text"), str):
                            return block["text"]
        events = extract_json_array(raw)
        if events is None:
            return _readable_failure(raw) or raw
        if isinstance(events, list):
            for item in reversed(events):
                if isinstance(item, dict) and item.get("type") == "result":
                    result = item.get("result")
                    if isinstance(result, str):
                        return result
            for item in reversed(events):
                if isinstance(item, dict) and item.get("type") == "assistant":
                    msg = item.get("message")
                    if isinstance(msg, dict):
                        content_list = msg.get("content")
                        if isinstance(content_list, list):
                            for block in content_list:
                                if isinstance(block, dict) and isinstance(block.get("text"), str):
                                    return block["text"]
        return _readable_failure(raw) or raw

    if kind == "gemini":
        envelope = extract_json(raw)
        if envelope is None:
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                return raw
        if isinstance(envelope, dict):
            for key in ("response", "completion", "content", "output", "text"):
                value = envelope.get(key)
                if isinstance(value, str):
                    return value
            content = envelope.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        return item["text"]
        return _readable_failure(raw) or raw

    if kind == "opencode":
        # Prefer OpenCode JSON event stream output when --format json is used.
        text_parts: List[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            direct_text = event.get("text")
            if isinstance(direct_text, str):
                text_parts.append(direct_text)
                continue
            part = event.get("part")
            if isinstance(part, dict):
                part_text = part.get("text")
                if isinstance(part_text, str):
                    text_parts.append(part_text)
        if text_parts:
            return "".join(text_parts).strip()
        envelope = extract_json(raw)
        if envelope is None:
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                envelope = None
        if isinstance(envelope, dict):
            for key in ("response", "completion", "content", "output", "text", "message"):
                value = envelope.get(key)
                if isinstance(value, str):
                    return value
            content = envelope.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        return item["text"]
        if isinstance(envelope, list):
            for item in reversed(envelope):
                if isinstance(item, dict):
                    for key in ("content", "text", "message", "output"):
                        value = item.get(key)
                        if isinstance(value, str):
                            return value
        return _readable_failure(raw) or raw

    return _readable_failure(raw) or raw


def _build_command_and_input(config: AgentConfig, prompt: str) -> Tuple[List[str], Optional[str]]:
    kind = (config.kind or config.name).lower()
    if kind == "codex":
        model = config.model or CODEX_MODEL
        reasoning = config.reasoning_effort or CODEX_REASONING
        args = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
        ]
        # Only pin a model when one is explicitly set. ChatGPT-account Codex
        # rejects API-only model names (e.g. gpt-5.2-codex); omitting -m lets it
        # use the account's default model, which works on every Codex auth type.
        if model:
            args.extend(["-m", model])
        if reasoning:
            args.extend(["-c", f"model_reasoning_effort={reasoning}"])
        args.extend(config.extra_args)
        args.append(prompt)
        return (
            args,
            None,
        )
    if kind == "gemini":
        model = config.model or GEMINI_MODEL
        args = ["gemini", "--output-format", "json"]
        if model:
            args.extend(["--model", model])
        args.extend(config.extra_args)
        args.extend(["-p", prompt])
        return (
            args,
            None,
        )
    if kind == "agy":
        # Antigravity CLI: --print is a STRING flag whose VALUE is the prompt
        # (--prompt is its alias), not a boolean. So --print must come LAST and
        # take the prompt directly. Putting it earlier makes it swallow the next
        # flag as the "prompt" and the real prompt is dropped — agy then replies
        # with a generic greeting instead of a plan. Other flags go before it.
        args = ["agy", "--dangerously-skip-permissions"]
        if config.model:
            args.extend(["--model", config.model])
        args.extend(config.extra_args)
        args.extend(["--print", prompt])
        return (args, None)
    if kind == "claude":
        model = config.model or CLAUDE_MODEL
        args = [
            "claude",
            "--output-format",
            "json",
            "--model",
            model,
            # Tools are disabled below (--tools ""), so the model should answer
            # in one turn. But Opus sometimes still emits a tool_use block; with
            # --max-turns 1 that ends the run as error_max_turns with NO plan
            # text. A small headroom lets the denied tool attempt resolve and the
            # model produce its final answer on the next turn.
            "--max-turns",
            str(CLAUDE_MAX_TURNS),
            "--no-session-persistence",
            "--dangerously-skip-permissions",
            # --tools "" only disables BUILT-IN tools; MCP tools stay available.
            # A spawned planner inherits the user's MCP servers (auxly-memory,
            # etc.) and project CLAUDE.md, so Opus tries an MCP tool_use and the
            # run dies at max-turns with no plan. --strict-mcp-config with no
            # --mcp-config strips every MCP server, so the planner answers in one
            # turn from knowledge — which is exactly what we want for planning.
            "--tools",
            "",
            "--strict-mcp-config",
            "--disable-slash-commands",
        ]
        args.extend(config.extra_args)
        # Deliver the prompt over stdin, NOT as an argv argument. The judge prompt
        # embeds every full plan and easily exceeds Windows' ~32 KB command-line
        # limit (CreateProcess WinError 206: "filename or extension is too long"),
        # which otherwise stops the judge from launching at all. stdin is unbounded.
        args.append("-p")
        return (args, prompt + "\n")
    if kind == "opencode":
        args = ["opencode", "run"]
        args.extend(config.extra_args)
        if config.model:
            args.extend(["--model", config.model])
        if config.agent:
            args.extend(["--agent", config.agent])
        if config.cli_format:
            args.extend(["--format", config.cli_format])
        if config.attach:
            args.extend(["--attach", config.attach])
        args.append(prompt)
        return (args, None)
    if kind == "kimi":
        # Kimi Code: -p runs one prompt non-interactively and prints the response.
        # (-p cannot be combined with -y/--yolo; print mode needs no approval.)
        # -m sets the model. The planner prompt already forbids tools/file writes.
        args = ["kimi", "--output-format", "text"]
        if config.model:
            args.extend(["-m", config.model])
        args.extend(config.extra_args)
        args.extend(["-p", prompt])
        return (args, None)
    if kind == "qwen":
        # Qwen Code (Gemini-CLI lineage): one-shot prompt via -p, model via -m.
        args = ["qwen", "-y"]
        if config.model:
            args.extend(["-m", config.model])
        args.extend(config.extra_args)
        args.extend(["-p", prompt])
        return (args, None)
    if not config.command:
        raise ValueError(f"custom agent '{config.name}' requires a command")
    args = shlex.split(config.command)
    if config.extra_args:
        args.extend(config.extra_args)
    if (config.prompt_mode or "stdin").lower() == "stdin":
        return (args, prompt + "\n")
    return (args + [prompt], None)


def spawn_cli_agent(config: AgentConfig, prompt: str) -> RunningAgent:
    args, stdin_payload = _build_command_and_input(config, prompt)
    process = subprocess.Popen(
        args,
        stdin=subprocess.PIPE if stdin_payload is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    if stdin_payload is not None and process.stdin:
        process.stdin.write(stdin_payload)
        process.stdin.close()
    return RunningAgent(config=config, prompt=prompt, start_time=time.time(), process=process)


def collect_cli_output(running: RunningAgent, timeout_sec: int) -> str:
    try:
        stdout, stderr = running.process.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired as exc:
        running.process.kill()
        stdout, stderr = running.process.communicate()
        raise TimeoutError(f"{running.config.name} timed out") from exc
    combined = stdout or ""
    if stderr:
        combined = combined + "\n" + stderr
    return combined


def anonymize_text(text: str) -> str:
    patterns = [
        r"codex",
        r"claude",
        r"gemini",
        r"opencode",
        r"agy",
        r"antigravity",
        r"openai",
        r"anthropic",
        r"google",
        r"gpt[-_\\w]*",
        r"sk-[A-Za-z0-9]{10,}",
        r"system prompt",
        r"tool trace",
        r"trace id",
    ]
    pattern = re.compile("|".join(patterns), flags=re.IGNORECASE)
    return pattern.sub("[REDACTED]", text)


# Headers a planner's Markdown must contain to count as a *valid* plan.
# Intentionally the structural backbone only — a titled, phased plan — NOT the
# full references/templates/plan.md section list. Some good CLIs (e.g. agy) emit
# a solid plan but drop trailing sections like Pros/Cons/Open Questions; rejecting
# those outright drops a whole council member over formatting. The judge merges and
# fills gaps, so we accept any plan that has a title and phases and let quality be
# judged, not gate-kept by header completeness.
REQUIRED_PLAN_HEADERS = [
    "# Plan",
    "## Phases",
]


def validate_markdown_plan(text: str) -> Tuple[bool, Optional[str]]:
    missing = [header for header in REQUIRED_PLAN_HEADERS if header not in text]
    if missing:
        return False, "missing headers: " + ", ".join(missing)
    return True, None


def validate_markdown_judge(text: str) -> Tuple[bool, Optional[str]]:
    required = [
        "# Judge Report",
        "## Scores",
        "## Comparative Analysis",
        "## Missing Steps",
        "## Contradictions",
        "## Improvements",
        "## Pros",
        "## Cons",
        "## Final Plan",
    ]
    missing = [header for header in required if header not in text]
    if missing:
        return False, "missing headers: " + ", ".join(missing)
    return True, None


def _ui_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ui_deadline_from_now(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _ui_truncate(text: str, max_len: int = 600) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[:max_len].rstrip() + "…"


def _ui_update_timestamp(state: Dict[str, Any], timestamp: str) -> None:
    timestamps = state.get("timestamps")
    if not isinstance(timestamps, dict):
        timestamps = {}
    if "started_at" not in timestamps:
        timestamps["started_at"] = timestamp
    timestamps["updated_at"] = timestamp
    state["timestamps"] = timestamps


def _ui_emit(ui_instance: Optional["ui_server.UIServer"], event_type: str, payload: Dict[str, Any]) -> None:
    if not ui_instance:
        return
    ui_instance.broadcast({"type": event_type, "payload": payload})


def _ui_set_session_state(
    ui_state: Optional["ui_server.UIState"],
    ui_instance: Optional["ui_server.UIServer"],
    keep_open: bool,
    deadline: Optional[str],
    timestamp: str,
) -> None:
    if not ui_state:
        return
    def mutator(state: Dict[str, Any]) -> None:
        state["keep_open"] = keep_open
        state["ui_deadline"] = deadline or ""
        _ui_update_timestamp(state, timestamp)
    ui_state.mutate(mutator)
    _ui_emit(
        ui_instance,
        "session_update",
        {"keep_open": keep_open, "ui_deadline": deadline or "", "timestamp": timestamp},
    )


def _ui_set_phase(
    ui_state: Optional["ui_server.UIState"],
    ui_instance: Optional["ui_server.UIServer"],
    phase: str,
    timestamp: str,
) -> None:
    if not ui_state:
        return
    def mutator(state: Dict[str, Any]) -> None:
        state["phase"] = phase
        _ui_update_timestamp(state, timestamp)
    ui_state.mutate(mutator)
    _ui_emit(ui_instance, "phase_change", {"phase": phase, "timestamp": timestamp})


def _ui_upsert_planner(
    ui_state: Optional["ui_server.UIState"],
    ui_instance: Optional["ui_server.UIServer"],
    planner_id: str,
    status: str,
    summary: str,
    errors: Optional[List[str]],
    timestamp: str,
) -> None:
    if not ui_state:
        return
    entry = {"id": planner_id, "status": status, "summary": summary, "errors": errors or []}
    def mutator(state: Dict[str, Any]) -> None:
        planners = state.get("planners")
        if not isinstance(planners, list):
            planners = []
        index = next((i for i, item in enumerate(planners) if item.get("id") == planner_id), None)
        if index is None:
            planners.append(entry)
        else:
            planners[index] = entry
        state["planners"] = planners
        _ui_update_timestamp(state, timestamp)
    ui_state.mutate(mutator)
    _ui_emit(ui_instance, "planner_update", {"planner": entry, "timestamp": timestamp})


def _ui_update_judge(
    ui_state: Optional["ui_server.UIState"],
    ui_instance: Optional["ui_server.UIServer"],
    status: str,
    summary: str,
    errors: Optional[List[str]],
    timestamp: str,
) -> None:
    if not ui_state:
        return
    judge_entry = {"status": status, "summary": summary, "errors": errors or []}
    def mutator(state: Dict[str, Any]) -> None:
        state["judge"] = judge_entry
        _ui_update_timestamp(state, timestamp)
    ui_state.mutate(mutator)
    _ui_emit(ui_instance, "judge_update", {"judge": judge_entry, "timestamp": timestamp})


def _ui_set_final_plan(
    ui_state: Optional["ui_server.UIState"],
    ui_instance: Optional["ui_server.UIServer"],
    final_plan: str,
    timestamp: str,
) -> None:
    if not ui_state:
        return
    def mutator(state: Dict[str, Any]) -> None:
        state["final_plan"] = final_plan
        _ui_update_timestamp(state, timestamp)
    ui_state.mutate(mutator)
    _ui_emit(ui_instance, "final_plan", {"final_plan": final_plan, "timestamp": timestamp})


def render_planner_prompt(task_spec: Dict[str, Any], plan_template: str, prompt_template: str) -> str:
    brief = build_task_brief(task_spec)
    prompt = prompt_template.replace("{{TASK_BRIEF}}", brief)
    return prompt.replace("{{PLAN_TEMPLATE}}", plan_template)


def render_judge_prompt(task_spec: Dict[str, Any], plans: List[Dict[str, Any]], judge_template: str, prompt_template: str) -> str:
    brief = build_task_brief(task_spec)
    plans_block = "\n\n".join(f"### {p['label']}\n\n{p['plan']}" for p in plans)
    prompt = prompt_template.replace("{{TASK_BRIEF}}", brief)
    prompt = prompt.replace("{{PLANS_MD}}", plans_block)
    return prompt.replace("{{JUDGE_TEMPLATE}}", judge_template)


def load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def resolve_path(relative_path: str) -> str:
    base_dir = Path(__file__).resolve().parent
    return str((base_dir / relative_path).resolve())


def get_run_root() -> Path:
    return Path.cwd() / "auxly-council" / "runs"


def slugify(value: str, max_len: int = 40) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    if not cleaned:
        return "run"
    return cleaned[:max_len].strip("-")


def unique_run_dir(run_root: Path, base_name: str) -> Path:
    candidate = run_root / base_name
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        candidate = run_root / f"{base_name}-{counter}"
        if not candidate.exists():
            return candidate
        counter += 1


def maybe_trash_empty_dir(path: Path) -> None:
    if not path.exists() or not path.is_dir():
        return
    if any(path.iterdir()):
        return
    trash_bin = shutil.which("trash")
    if not trash_bin:
        return
    subprocess.run([trash_bin, str(path)], check=False)


def get_default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "auxly-council" / "agents.json"
    return Path.home() / ".config" / "auxly-council" / "agents.json"


def load_agent_config_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    data = load_json(str(path))
    if isinstance(data, dict) and "agents" in data and isinstance(data["agents"], dict):
        return data["agents"]
    if isinstance(data, dict) and ("planners" in data or "judge" in data):
        return data
    return None


def _display_model(config: "AgentConfig") -> str:
    """Human-facing model label for the council roster shown in the UI header."""
    if config.model:
        return config.model
    defaults = {
        "codex": CODEX_MODEL,
        "claude": CLAUDE_MODEL,
        "gemini": GEMINI_MODEL,
        "agy": "default",
        "opencode": "default",
    }
    return defaults.get(config.kind, "default")


# Planner-capable CLIs the council knows how to invoke (each has a command
# builder in _build_command_and_input). Detection only offers these so the user
# never selects a provider we can't actually run.
SUPPORTED_PLANNER_CLIS = ["codex", "claude", "gemini", "agy", "opencode", "kimi", "qwen"]

# Curated model suggestions per provider for the crew "model" dropdown — names
# are hard to remember. "" = the CLI's account/config default (recommended).
# The UI still lets the user type a model we don't list.
KNOWN_MODELS: Dict[str, List[str]] = {
    "codex": ["", "gpt-5-codex", "gpt-5.2-codex", "o4-mini"],
    "claude": ["opus", "sonnet", "haiku"],
    "gemini": ["gemini-3-pro-preview", "gemini-2.5-pro", "gemini-2.5-flash"],
    "agy": ["", "Gemini 3 Pro (High)", "Claude Opus 4.6 (Thinking)"],
    "opencode": [""],
    "kimi": ["", "kimi-k2", "kimi-k2-turbo"],
    "qwen": ["", "qwen3-coder-plus", "qwen3-coder"],
    "custom": [""],
}

# Friendly labels for the provider multi-select.
CLI_LABELS = {
    "codex": "Codex (OpenAI)",
    "claude": "Claude Code",
    "gemini": "Gemini CLI",
    "agy": "Antigravity (agy)",
    "opencode": "OpenCode",
    "kimi": "Kimi Code",
    "qwen": "Qwen Code",
}


def detect_available_clis() -> Dict[str, bool]:
    """Probe the PATH for each supported planner CLI."""
    return {name: shutil.which(name) is not None for name in SUPPORTED_PLANNER_CLIS}


def available_cli_catalog() -> List[Dict[str, Any]]:
    """List installed, planner-capable CLIs with their model suggestions —
    consumed by the `detect` command (provider multi-select) and the crew UI."""
    out: List[Dict[str, Any]] = []
    for kind, present in detect_available_clis().items():
        if not present:
            continue
        out.append({
            "kind": kind,
            "label": CLI_LABELS.get(kind, kind),
            "models": KNOWN_MODELS.get(kind, [""]),
            "default_model": _default_model_for(kind),
        })
    return out


def _default_model_for(kind: str) -> str:
    return {
        "codex": CODEX_MODEL,
        "claude": CLAUDE_MODEL,
        "gemini": GEMINI_MODEL,
        "agy": AGY_MODEL,
    }.get(kind, "")


def build_claude_persona_council() -> Dict[str, Any]:
    """Claude-only fallback: three differentiated Claude personas plus a Claude
    judge. Personas are injected via --append-system-prompt so each planner
    attacks the problem from a distinct angle, preserving the bias-resistant
    spirit of the council even when no other vendor CLI is installed."""
    persona = lambda text: ["--append-system-prompt", text]
    planners = [
        {
            "name": "council-architect",
            "kind": "claude",
            "model": CLAUDE_MODEL,
            "extra_args": persona(
                "You are a senior systems architect. Prioritize clean architecture, "
                "correct sequencing of work, clear interfaces, and long-term maintainability. "
                "Be explicit about trade-offs."
            ),
        },
        {
            "name": "council-pragmatist",
            # Use the same known-good default model as the other personas. The
            # bias-resistance comes from the distinct system prompts below, not
            # from mixing model tiers — and a faster tier (e.g. sonnet) that an
            # account can't serve would just hang and time out every run.
            "kind": "claude",
            "model": CLAUDE_MODEL,
            "extra_args": persona(
                "You are a shipping-focused pragmatist. Prefer the simplest plan that "
                "actually works, fast iteration, minimal moving parts, and quick wins. "
                "Call out scope you would cut."
            ),
        },
        {
            "name": "council-riskhawk",
            "kind": "claude",
            "model": CLAUDE_MODEL,
            "extra_args": persona(
                "You are a risk, security, and reliability hawk. Aggressively surface failure "
                "modes, edge cases, data-integrity hazards, and rollback safety. Make the "
                "Risks and Edge Cases sections strong."
            ),
        },
    ]
    judge = {"name": "council-judge", "kind": "claude", "model": CLAUDE_MODEL}
    return {"planners": planners, "judge": judge}


def build_auto_council() -> Optional[Dict[str, Any]]:
    """Resolve a council when the user has no saved agents config.

    - If any external CLI (codex / gemini / agy) is present, build a genuine
      multi-vendor council from whatever is installed, plus Claude if available.
    - If none of the external CLIs are present, fall back to a Claude-only
      persona council so the workflow still runs end to end.
    - If nothing usable is installed at all, return None (caller errors out).
    """
    available = detect_available_clis()
    has_external = any(available.get(name) for name in EXTERNAL_PLANNER_CLIS)

    if not has_external:
        if available.get("claude"):
            print(
                "[auxly-council] No codex / gemini / agy CLI found — convening a "
                "Claude-only multi-agent council (architect + pragmatist + risk hawk)."
            )
            return build_claude_persona_council()
        return None

    planners: List[Dict[str, Any]] = []
    if available.get("codex"):
        planners.append(
            {"name": "codex-1", "kind": "codex", "model": CODEX_MODEL, "reasoning_effort": CODEX_REASONING}
        )
    if available.get("gemini"):
        planners.append({"name": "gemini-2", "kind": "gemini", "model": GEMINI_MODEL})
    if available.get("agy"):
        agy_planner: Dict[str, Any] = {"name": "agy-3", "kind": "agy"}
        if AGY_MODEL:
            agy_planner["model"] = AGY_MODEL
        planners.append(agy_planner)
    if available.get("claude"):
        planners.append({"name": "claude-4", "kind": "claude", "model": CLAUDE_MODEL})
    if available.get("kimi"):
        planners.append({"name": "kimi-5", "kind": "kimi"})
    if available.get("opencode"):
        planners.append({"name": "opencode-6", "kind": "opencode"})
    if available.get("qwen"):
        planners.append({"name": "qwen-7", "kind": "qwen"})

    # Prefer Claude as judge for consistent rubric scoring; otherwise reuse the
    # first planner (load_agent_configs handles a missing judge too).
    judge = (
        {"name": "claude-judge", "kind": "claude", "model": CLAUDE_MODEL}
        if available.get("claude")
        else dict(planners[0])
    )
    installed = ", ".join(name for name, ok in available.items() if ok) or "none"
    print(f"[auxly-council] Auto-configured council from installed CLIs: {installed}.")
    return {"planners": planners, "judge": judge}


def configure_agents(config_path: Path) -> None:
    def prompt_text(label: str, default: Optional[str] = None) -> str:
        suffix = f" (default: {default})" if default else ""
        value = input(f"{label}{suffix}: ").strip()
        return value if value else (default or "")

    def prompt_choice(label: str, choices: List[str], default_idx: int = 1) -> int:
        while True:
            raw = input(f"{label} (default: {default_idx}): ").strip()
            if not raw:
                return default_idx
            try:
                value = int(raw)
            except ValueError:
                print("Please enter a number.")
                continue
            if 1 <= value <= len(choices):
                return value
            print(f"Choose a number between 1 and {len(choices)}.")

    def prompt_yes_no(label: str, default_yes: bool = True) -> bool:
        default = "Y/n" if default_yes else "y/N"
        raw = input(f"{label} [{default}] ").strip().lower()
        if not raw:
            return default_yes
        return raw in ("y", "yes")

    print("Council setup")
    if prompt_yes_no("Use default council (Codex CLI + Claude CLI + Gemini CLI)?", default_yes=True):
        planners = [
            {"name": "codex-1", "kind": "codex", "model": CODEX_MODEL, "reasoning_effort": CODEX_REASONING},
            {"name": "claude-2", "kind": "claude", "model": CLAUDE_MODEL},
            {"name": "gemini-3", "kind": "gemini", "model": GEMINI_MODEL},
        ]
        judge = planners[0]
    else:
        count_raw = prompt_text("How many planners?", "3")
        try:
            planner_count = max(1, int(count_raw))
        except ValueError:
            planner_count = 3

        planners = []
        for idx in range(1, planner_count + 1):
            print(f"\nPlanner {idx}")
            kinds = ["codex", "claude", "gemini", "agy", "opencode", "custom"]
            for i, kind in enumerate(kinds, start=1):
                print(f"{i}) {kind}")
            choice = prompt_choice("Choose CLI", kinds, default_idx=1)
            kind = kinds[choice - 1]

            default_name = f"{kind}-{idx}"
            name = prompt_text("Planner name", default_name) or default_name

            planner: Dict[str, Any] = {"name": name, "kind": kind}
            if kind == "codex":
                planner["model"] = prompt_text("Codex model", CODEX_MODEL)
                planner["reasoning_effort"] = prompt_text("Reasoning effort", CODEX_REASONING)
            elif kind == "claude":
                planner["model"] = prompt_text("Claude model", CLAUDE_MODEL)
            elif kind == "gemini":
                planner["model"] = prompt_text("Gemini model", GEMINI_MODEL)
            elif kind == "agy":
                model = prompt_text("agy model (blank = CLI default; run 'agy models' to list)", "")
                if model:
                    planner["model"] = model
            elif kind == "opencode":
                print(
                    "Opencode provider/model (note: run 'opencode models' in another terminal to see available models)"
                )
                model = prompt_text("Provider/model", "")
                while not model:
                    model = prompt_text("Provider/model", "")
                planner["model"] = model
            else:
                planner["command"] = prompt_text("Command", "")
                while not planner["command"]:
                    planner["command"] = prompt_text("Command", "")
                prompt_mode = prompt_text("Prompt mode (arg|stdin)", "arg").lower()
                planner["prompt_mode"] = "stdin" if prompt_mode == "stdin" else "arg"

            planners.append(planner)

        print("\nWhich model should be the judge?")
        for i, planner in enumerate(planners, start=1):
            model = planner.get("model")
            label = f"{planner['name']} ({planner['kind']}"
            if model:
                label += f": {model}"
            label += ")"
            print(f"{i}) {label}")
        judge_idx = prompt_choice("Select judge", planners, default_idx=1)
        judge = planners[judge_idx - 1]

    payload = {"planners": planners, "judge": judge}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    print(f"\nSaving config to {config_path}.")
    write_json(str(tmp_path), payload)
    os.replace(tmp_path, config_path)
    print("Saved.")


def build_task_brief(task_spec: Dict[str, Any]) -> str:
    lines = []
    task = (task_spec.get("task") or "").strip()
    lines.append(f"Task: {task}")
    constraints = task_spec.get("constraints") or []
    if constraints:
        lines.append("Constraints:")
        for item in constraints:
            lines.append(f"- {item}")
    repo = task_spec.get("repo_context") or {}
    if repo:
        root = repo.get("root")
        paths = repo.get("paths") or []
        notes = repo.get("notes")
        if root:
            lines.append(f"Repo root: {root}")
        if paths:
            lines.append("Relevant paths:")
            for path in paths:
                lines.append(f"- {path}")
        if notes:
            lines.append(f"Notes: {notes}")
    return "\n".join(lines).strip()


def _normalize_agent_spec(spec: Any, fallback_name: str) -> AgentConfig:
    if isinstance(spec, str):
        data = {"name": spec, "kind": spec}
    elif isinstance(spec, dict):
        data = spec
    else:
        raise ValueError("agent spec must be an object or string")
    name = str(data.get("name") or fallback_name).strip()
    kind = str(data.get("kind") or data.get("cli") or data.get("type") or name).strip().lower()
    output_format = str(data.get("output_format") or "text").strip()
    model = data.get("model")
    reasoning_effort = data.get("reasoning_effort") or data.get("reasoning")
    agent = data.get("agent")
    attach = data.get("attach")
    cli_format = data.get("format") or data.get("cli_format")
    command = data.get("command")
    prompt_mode = data.get("prompt_mode") or "arg"
    extra_args = data.get("extra_args") or []
    if not isinstance(extra_args, list):
        extra_args = [str(extra_args)]
    extra_args = [str(item) for item in extra_args]
    if kind == "opencode" and not cli_format:
        cli_format = "json"
    return AgentConfig(
        name=name,
        kind=kind,
        command=command,
        output_format=output_format,
        model=model,
        reasoning_effort=reasoning_effort,
        agent=agent,
        attach=attach,
        cli_format=cli_format,
        prompt_mode=prompt_mode,
        extra_args=extra_args,
    )


def load_agent_configs(task_spec: Dict[str, Any], config_path: Optional[Path] = None) -> Tuple[List[AgentConfig], AgentConfig]:
    agents_spec = task_spec.get("agents")
    if not agents_spec:
        config_path = config_path or get_default_config_path()
        config_spec = load_agent_config_file(config_path)
        if config_spec:
            agents_spec = config_spec

    if not agents_spec:
        # No task-spec agents and no saved config: auto-detect installed CLIs and
        # convene the best council we can (multi-vendor if available, else a
        # Claude-only persona council).
        agents_spec = build_auto_council()

    if not agents_spec:
        raise ValueError(
            "No usable planner CLI found. Install at least one of: claude, codex, gemini, agy, opencode — "
            "or run `./setup.sh` to configure your council manually."
        )

    if isinstance(agents_spec, list):
        planner_specs = agents_spec
        judge_spec = None
    elif isinstance(agents_spec, dict):
        planner_specs = agents_spec.get("planners") or agents_spec.get("agents") or []
        judge_spec = agents_spec.get("judge")
    else:
        raise ValueError("agents must be a list or object with planners")

    if not planner_specs:
        raise ValueError("agents.planners must include at least one agent")

    planners: List[AgentConfig] = []
    seen = set()
    for idx, spec in enumerate(planner_specs, start=1):
        agent = _normalize_agent_spec(spec, f"planner-{idx}")
        if agent.name in seen:
            agent.name = f"{agent.name}-{idx}"
        seen.add(agent.name)
        planners.append(agent)

    if judge_spec:
        judge = _normalize_agent_spec(judge_spec, "judge")
    else:
        primary = planners[0]
        judge = AgentConfig(
            name=f"{primary.name}-judge",
            kind=primary.kind,
            command=primary.command,
            output_format=primary.output_format,
            model=primary.model,
            reasoning_effort=primary.reasoning_effort,
            agent=primary.agent,
            attach=primary.attach,
            cli_format=primary.cli_format,
            prompt_mode=primary.prompt_mode,
            extra_args=list(primary.extra_args),
        )

    return planners, judge


def run_planners(
    task_spec: Dict[str, Any],
    planners: List[AgentConfig],
    planner_prompt_template: str,
    plan_template: str,
    timeout_sec: int,
    run_dir: str,
    ui_state: Optional["ui_server.UIState"] = None,
    ui_instance: Optional["ui_server.UIServer"] = None,
) -> List[AgentResult]:
    # One result per planner, keyed by name. Retries OVERWRITE the prior attempt
    # so a planner that fails-then-passes (or just fails N times) shows as a single
    # card with its final status — not one card per attempt.
    results_by_name: Dict[str, AgentResult] = {}
    remaining = planners[:]
    attempt = 0
    while remaining and attempt <= RETRY_LIMIT:
        running: List[RunningAgent] = []
        spawn_failures: List[Tuple[AgentConfig, str]] = []
        for planner in remaining:
            prompt = render_planner_prompt(task_spec, plan_template, planner_prompt_template)
            timestamp = _ui_timestamp()
            _ui_upsert_planner(
                ui_state,
                ui_instance,
                planner_id=planner.name,
                status="running",
                summary="starting…",
                errors=[],
                timestamp=timestamp,
            )
            # A planner whose CLI cannot even be launched (missing binary, a model
            # the account can't use, a Windows .cmd shim, etc.) must not crash the
            # whole council — degrade it to a failed tile and keep the others going.
            try:
                running.append(spawn_cli_agent(planner, prompt))
            except Exception as exc:  # noqa: BLE001 - surface any launch failure
                msg = f"could not launch '{planner.name}': {exc}"
                spawn_failures.append((planner, msg))
                _ui_upsert_planner(
                    ui_state,
                    ui_instance,
                    planner_id=planner.name,
                    status="failed",
                    summary=msg,
                    errors=[msg],
                    timestamp=_ui_timestamp(),
                )

        remaining = []
        for planner, msg in spawn_failures:
            results_by_name[planner.name] = AgentResult(name=planner.name, raw_output="", data=None, valid=False, error=msg)
        for entry in running:
            try:
                raw = collect_cli_output(entry, timeout_sec)
                timeout_error = None
            except TimeoutError as exc:
                raw = ""
                timeout_error = str(exc)
            normalized = extract_agent_response(entry.config, raw)
            plan_text = normalized.strip()
            if timeout_error is not None:
                valid, err = False, timeout_error
            else:
                valid, err = validate_markdown_plan(plan_text)
            plan_path = Path(run_dir) / f"plan-{entry.config.name}-attempt{attempt + 1}.md"
            write_attempt = attempt > 0 or not valid
            if write_attempt:
                plan_path.write_text(plan_text, encoding="utf-8")
            if valid:
                final_path = Path(run_dir) / f"plan-{entry.config.name}.md"
                final_path.write_text(plan_text, encoding="utf-8")
            timestamp = _ui_timestamp()
            status = "complete" if valid else ("failed" if timeout_error else "needs-fix")
            errors = [err] if err else []
            summary = plan_text
            _ui_upsert_planner(
                ui_state,
                ui_instance,
                planner_id=entry.config.name,
                status=status,
                summary=summary or ("error" if errors else ""),
                errors=errors,
                timestamp=timestamp,
            )
            result = AgentResult(
                name=entry.config.name,
                raw_output=raw,
                data={"path": str(plan_path if write_attempt else final_path), "text": plan_text},
                valid=valid,
                error=err,
            )
            results_by_name[entry.config.name] = result
            # Retry only structural/validation failures — those often pass on a
            # second try. A timeout almost never recovers and would otherwise burn
            # another full --timeout window per attempt, so fail it fast instead.
            if not valid and timeout_error is None and attempt < RETRY_LIMIT:
                retry_timestamp = _ui_timestamp()
                _ui_upsert_planner(
                    ui_state,
                    ui_instance,
                    planner_id=entry.config.name,
                    status="retrying",
                    summary="retry scheduled",
                    errors=[err] if err else [],
                    timestamp=retry_timestamp,
                )
                remaining.append(entry.config)

        attempt += 1
    # Preserve the original council order; one entry per planner.
    return [results_by_name[p.name] for p in planners if p.name in results_by_name]


def run_judge(
    task_spec: Dict[str, Any],
    plans: List[Dict[str, Any]],
    judge: AgentConfig,
    judge_prompt_template: str,
    judge_template: str,
    timeout_sec: int,
    run_dir: str,
    ui_state: Optional["ui_server.UIState"] = None,
    ui_instance: Optional["ui_server.UIServer"] = None,
) -> AgentResult:
    prompt = render_judge_prompt(task_spec, plans, judge_template, judge_prompt_template)
    start_timestamp = _ui_timestamp()
    _ui_update_judge(
        ui_state,
        ui_instance,
        status="running",
        summary="starting…",
        errors=[],
        timestamp=start_timestamp,
    )
    try:
        running = spawn_cli_agent(judge, prompt)
    except Exception as exc:  # noqa: BLE001 - judge CLI failed to launch
        msg = f"could not launch judge '{judge.name}': {exc}"
        _ui_update_judge(ui_state, ui_instance, status="failed", summary=msg,
                         errors=[msg], timestamp=_ui_timestamp())
        return AgentResult(name=judge.name, raw_output="", data=None, valid=False, error=msg)
    try:
        raw = collect_cli_output(running, timeout_sec)
        timeout_error = None
    except TimeoutError as exc:
        raw = ""
        timeout_error = str(exc)
    normalized = extract_agent_response(judge, raw)
    judge_text = normalized.strip()
    judge_path = Path(run_dir) / "judge.md"
    judge_path.write_text(judge_text, encoding="utf-8")
    if timeout_error is not None:
        valid, err = False, timeout_error
    else:
        valid, err = validate_markdown_judge(judge_text)
    finish_timestamp = _ui_timestamp()
    status = "complete" if valid else ("failed" if timeout_error else "needs-fix")
    errors = [err] if err else []
    summary = judge_text
    _ui_update_judge(
        ui_state,
        ui_instance,
        status=status,
        summary=summary or ("error" if errors else ""),
        errors=errors,
        timestamp=finish_timestamp,
    )
    return AgentResult(
        name=judge.name,
        raw_output=raw,
        data={"path": str(judge_path), "text": judge_text},
        valid=valid,
        error=err,
    )


def extract_final_plan(judge_text: str) -> str:
    marker = "## Final Plan"
    if marker not in judge_text:
        return judge_text
    after = judge_text.split(marker, 1)[1]
    plan_start = after.find("# Plan")
    if plan_start == -1:
        return after.strip()
    return after[plan_start:].strip()


# ============================================================================
# Static, self-contained HTML plan report (NO server). Opens via file://.
# This replaces the old live UI/SSE dashboard: the council writes one plan.html
# the user reviews, then they reply in Claude Code (execute / refine / edits).
# ============================================================================

def _inline_md(text: str) -> str:
    """Inline Markdown: code spans, links, bold, italic, strikethrough — HTML-safe."""
    # Stash code spans BEFORE escaping so their contents aren't double-formatted.
    codes: List[str] = []

    def _stash(m: "re.Match") -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    t = re.sub(r"`([^`]+)`", _stash, text)
    t = _html.escape(t)

    def _link(m: "re.Match") -> str:
        label, url = m.group(1), m.group(2)
        # Only allow safe URL schemes/relative links (escaped form of ':' is ':').
        if not re.match(r"^(https?://|mailto:|/|\.|#)", url):
            return m.group(0)
        return f'<a href="{url}" target="_blank" rel="noopener">{label}</a>'

    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", _link, t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"__(.+?)__", r"<strong>\1</strong>", t)
    t = re.sub(r"~~(.+?)~~", r"<del>\1</del>", t)
    t = re.sub(r"(?<![\*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", t)
    t = re.sub(r"(?<![_\w])_(?!\s)([^_\n]+?)(?<!\s)_(?!\w)", r"<em>\1</em>", t)

    def _restore(m: "re.Match") -> str:
        return "<code>" + _html.escape(codes[int(m.group(1))]) + "</code>"

    return re.sub(r"\x00(\d+)\x00", _restore, t)


def _md_to_html(md_text: str) -> str:
    """Dependency-free Markdown -> HTML: headings, ordered/unordered/nested lists,
    tables, blockquotes, fenced code, horizontal rules, plus rich inline formatting."""
    lines = (md_text or "").replace("\r\n", "\n").split("\n")
    out: List[str] = []
    list_stack: List[Tuple[int, str]] = []  # (indent, "ul"|"ol")
    para: List[str] = []
    i, n = 0, len(lines)

    def flush_para() -> None:
        if para:
            txt = " ".join(x.strip() for x in para).strip()
            if txt:
                out.append("<p>" + _inline_md(txt) + "</p>")
            para.clear()

    def close_all_lists() -> None:
        while list_stack:
            out.append(f"</{list_stack.pop()[1]}>")

    def _cells(row: str) -> List[str]:
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|"):
            row = row[:-1]
        return [c.strip() for c in row.split("|")]

    while i < n:
        ln = lines[i]

        mcode = re.match(r"^\s*```(\w*)\s*$", ln)
        if mcode:
            flush_para(); close_all_lists()
            i += 1
            buf: List[str] = []
            while i < n and not re.match(r"^\s*```\s*$", lines[i]):
                buf.append(lines[i]); i += 1
            i += 1
            out.append("<pre><code>" + _html.escape("\n".join(buf)) + "</code></pre>")
            continue

        if not ln.strip():
            flush_para(); close_all_lists()
            i += 1
            continue

        if re.match(r"^\s*([-*_])\1\1[-*_\s]*$", ln) and not re.match(r"^\s*[-*+]\s", ln):
            flush_para(); close_all_lists()
            out.append("<hr/>"); i += 1
            continue

        mh = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if mh:
            flush_para(); close_all_lists()
            lvl = min(len(mh.group(1)), 4)
            out.append(f"<h{lvl}>{_inline_md(mh.group(2).strip())}</h{lvl}>")
            i += 1
            continue

        if ("|" in ln and i + 1 < n and "-" in lines[i + 1]
                and re.match(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$", lines[i + 1])):
            flush_para(); close_all_lists()
            header = _cells(ln)
            i += 2
            out.append("<table><thead><tr>"
                       + "".join(f"<th>{_inline_md(c)}</th>" for c in header)
                       + "</tr></thead><tbody>")
            while i < n and "|" in lines[i] and lines[i].strip():
                cs = _cells(lines[i])
                out.append("<tr>" + "".join(f"<td>{_inline_md(c)}</td>" for c in cs) + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue

        if re.match(r"^\s*>\s?", ln):
            flush_para(); close_all_lists()
            buf = []
            while i < n and re.match(r"^\s*>\s?", lines[i]):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i])); i += 1
            out.append("<blockquote>" + _md_to_html("\n".join(buf)) + "</blockquote>")
            continue

        mli = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", ln)
        if mli:
            flush_para()
            indent = len(mli.group(1).expandtabs(2))
            tag = "ol" if re.match(r"\d+[.)]", mli.group(2)) else "ul"
            while list_stack and list_stack[-1][0] > indent:
                out.append(f"</{list_stack.pop()[1]}>")
            if not list_stack or list_stack[-1][0] < indent:
                out.append(f"<{tag}>"); list_stack.append((indent, tag))
            elif list_stack[-1][1] != tag:
                out.append(f"</{list_stack.pop()[1]}>")
                out.append(f"<{tag}>"); list_stack.append((indent, tag))
            out.append("<li>" + _inline_md(mli.group(3).strip()) + "</li>")
            i += 1
            continue

        para.append(ln); i += 1

    flush_para(); close_all_lists()
    return "\n".join(out)


def _logo_data_uri() -> str:
    try:
        p = Path(resolve_path("../references/auxly-logo.png"))
        b = base64.b64encode(p.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{b}"
    except Exception:
        return ""


_PLAN_CSS = """
:root{
  --bg:#0a0b0f;--ink:#07080b;
  --panel:rgba(255,255,255,.026);--panel-2:rgba(255,255,255,.045);
  --line:rgba(255,255,255,.09);--line-2:rgba(255,255,255,.14);
  --text:#e9ebf2;--muted:#8b93a7;--dim:#646b7d;
  --teal:#54d4c4;--violet:#9b8cf5;--amber:#f6b97a;--rose:#ff7a90;--green:#3dd6a8;--red:#ff6b81;
  --serif:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  --sans:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  --shadow:0 24px 60px -28px rgba(0,0,0,.85),0 2px 0 0 rgba(255,255,255,.03) inset;
}
*{box-sizing:border-box;}
html{scroll-behavior:smooth;}
body{
  margin:0;color:var(--text);font:16px/1.7 var(--sans);
  letter-spacing:.005em;-webkit-font-smoothing:antialiased;
  background:
    radial-gradient(1100px 560px at 12% -8%, rgba(84,212,196,.13), transparent 58%),
    radial-gradient(960px 520px at 102% 2%, rgba(155,140,245,.13), transparent 54%),
    radial-gradient(800px 800px at 50% 120%, rgba(246,185,122,.06), transparent 60%),
    var(--bg);
  background-attachment:fixed;
}
/* fine grain + faint grid for atmosphere/depth (self-contained, no network) */
body::before{
  content:"";position:fixed;inset:0;z-index:0;pointer-events:none;opacity:.5;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.035'/%3E%3C/svg%3E");
}
body::after{
  content:"";position:fixed;inset:0;z-index:0;pointer-events:none;opacity:.35;
  background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
  background-size:46px 46px;-webkit-mask-image:radial-gradient(900px 600px at 50% -5%,#000,transparent 70%);
          mask-image:radial-gradient(900px 600px at 50% -5%,#000,transparent 70%);
}
.wrap{position:relative;z-index:1;max-width:940px;margin:0 auto;padding:3rem 1.5rem 5rem;}

/* drifting ambient light behind everything (additive glow, paused if reduced-motion) */
.aurora{position:fixed;inset:-25%;z-index:0;pointer-events:none;filter:blur(70px);opacity:.55;}
.aurora span{position:absolute;border-radius:50%;mix-blend-mode:screen;will-change:transform;}
.aurora .a1{width:46vw;height:46vw;left:0;top:-8%;background:radial-gradient(circle,rgba(84,212,196,.55),transparent 64%);animation:drift1 24s ease-in-out infinite alternate;}
.aurora .a2{width:44vw;height:44vw;right:-4%;top:-6%;background:radial-gradient(circle,rgba(155,140,245,.55),transparent 64%);animation:drift2 29s ease-in-out infinite alternate;}
.aurora .a3{width:40vw;height:40vw;left:28%;bottom:-18%;background:radial-gradient(circle,rgba(246,185,122,.4),transparent 66%);animation:drift3 34s ease-in-out infinite alternate;}
@keyframes drift1{from{transform:translate(0,0) scale(1);}to{transform:translate(13vw,9vh) scale(1.18);}}
@keyframes drift2{from{transform:translate(0,0) scale(1);}to{transform:translate(-11vw,11vh) scale(1.12);}}
@keyframes drift3{from{transform:translate(0,0) scale(1);opacity:.5;}to{transform:translate(8vw,-7vh) scale(1.22);opacity:.85;}}

/* ---- masthead ---- */
.masthead{padding-bottom:1.6rem;margin-bottom:2rem;border-bottom:1px solid var(--line);}
.brandrow{display:flex;align-items:center;gap:.85rem;margin-bottom:1.7rem;}
.brandrow img{width:42px;height:42px;}
.brand{font:700 1.45rem/1 var(--sans);letter-spacing:-.01em;color:#fff;}
.brand em{font-style:normal;background:linear-gradient(90deg,var(--violet),var(--teal));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}
.kicker{margin-left:auto;font:600 .64rem/1 var(--sans);letter-spacing:.22em;text-transform:uppercase;color:var(--teal);border:1px solid rgba(84,212,196,.35);background:rgba(84,212,196,.07);padding:.4rem .65rem;border-radius:6px;}
.headline{font:700 1.5rem/1.25 var(--sans);letter-spacing:-.01em;color:#fff;margin:.3rem 0 .5rem;max-width:42ch;}
.meta{font:500 .72rem/1.5 var(--sans);letter-spacing:.12em;text-transform:uppercase;color:var(--dim);margin:0 0 1.4rem;}
.meta b{color:var(--muted);font-weight:600;}
.members{display:flex;flex-wrap:wrap;gap:.5rem;margin:0;}
.member{display:inline-flex;align-items:center;gap:.5rem;font:500 .76rem/1 var(--sans);letter-spacing:.02em;background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:.4rem .8rem;color:var(--muted);transition:border-color .2s,color .2s;}
.member:hover{border-color:var(--line-2);color:var(--text);}
.member .dot{width:.5rem;height:.5rem;border-radius:50%;flex:none;}
.member .dot.ok{background:var(--green);box-shadow:0 0 8px var(--green);}
.member .dot.failed{background:var(--red);box-shadow:0 0 8px var(--red);}

/* ---- section labels ---- */
.section-label{display:flex;align-items:center;gap:.65rem;font:600 .7rem/1 var(--sans);text-transform:uppercase;letter-spacing:.2em;color:var(--muted);margin:2.4rem 0 .9rem;}
.section-label::before{content:"";width:22px;height:2px;border-radius:2px;background:linear-gradient(90deg,var(--teal),transparent);}

/* ---- cards ---- */
.card{position:relative;background:var(--panel);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);}
.card::before{content:"";position:absolute;inset:0;border-radius:16px;padding:1px;background:linear-gradient(160deg,rgba(255,255,255,.14),transparent 40%);-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude;pointer-events:none;}
.brief{padding:1.3rem 1.5rem;margin-bottom:.4rem;}
.brief-label{font:600 .68rem/1 var(--sans);text-transform:uppercase;letter-spacing:.18em;color:var(--dim);margin-bottom:.9rem;}

/* ---- scroll boxes ---- */
.scrollbox{max-height:320px;overflow-y:auto;padding-right:.6rem;}
.final.scrollbox{max-height:74vh;}
.model-body.prose{max-height:460px;overflow-y:auto;padding-right:.5rem;}
.scrollbox::-webkit-scrollbar{width:9px;}
.scrollbox::-webkit-scrollbar-thumb{background:var(--line-2);border-radius:6px;}
.scrollbox::-webkit-scrollbar-thumb:hover{background:var(--muted);}
.scrollbox::-webkit-scrollbar-track{background:transparent;}

/* ---- workflow ---- */
.flow{margin:.2rem 0 1.6rem;padding:1.4rem;border-radius:16px;background:var(--panel);border:1px solid var(--line);box-shadow:var(--shadow);}
/* shared number badge */
.flow-n{display:inline-flex;align-items:center;justify-content:center;width:1.75rem;height:1.75rem;flex:none;border-radius:9px;background:var(--teal);color:var(--ink);font:700 .82rem/1 var(--sans);box-shadow:0 6px 18px -5px currentColor;}
.flow-name{font-weight:600;letter-spacing:.01em;color:var(--text);}
.flow-sub{font:500 .66rem/1 var(--sans);text-transform:uppercase;letter-spacing:.09em;color:var(--dim);white-space:nowrap;}

/* horizontal: single no-wrap track that scrolls (no ragged wrapping) */
.flow.horizontal{display:flex;flex-wrap:nowrap;align-items:center;gap:.55rem;overflow-x:auto;padding-bottom:.5rem;}
.flow.horizontal .flow-step{display:flex;align-items:center;gap:.7rem;flex:none;background:linear-gradient(160deg,var(--panel-2),transparent);border:1px solid var(--line);border-left:3px solid var(--teal);border-radius:12px;padding:.7rem 1rem;font-size:.9rem;transition:transform .18s,border-color .18s;}
.flow.horizontal .flow-step:hover{transform:translateY(-2px);border-color:var(--line-2);}
.flow-arrow{flex:none;color:var(--dim);font-size:1.2rem;line-height:1;}

/* vertical: timeline with a continuous connecting rail */
.flow.vertical{display:flex;flex-direction:column;}
.tl-item{position:relative;display:flex;align-items:flex-start;gap:.95rem;padding-bottom:1.4rem;}
.tl-item:last-child{padding-bottom:0;}
.tl-item::before{content:"";position:absolute;left:.875rem;top:1.9rem;bottom:-.1rem;width:2px;transform:translateX(-50%);background:linear-gradient(var(--line-2),rgba(255,255,255,.04));}
.tl-item:last-child::before{display:none;}
.tl-item .flow-n{position:relative;z-index:1;border-radius:50%;}
.tl-body{display:flex;flex-direction:column;gap:.25rem;padding-top:.2rem;}
.tl-name{font-weight:600;font-size:.96rem;color:var(--text);}

/* ---- final plan + prose ---- */
.final{padding:.4rem 1.6rem 1.4rem;border-radius:16px;background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--violet);box-shadow:var(--shadow);}
.prose{line-height:1.72;color:#dfe2ec;}
.prose h1,.prose h2,.prose h3{font-family:var(--serif);color:#fff;letter-spacing:-.01em;line-height:1.25;margin:1.5rem 0 .6rem;}
.prose h1{font-size:1.7rem;border-bottom:1px solid var(--line);padding-bottom:.5rem;}
.prose h2{font-size:1.32rem;}
.prose h3{font-size:1.1rem;color:var(--teal);font-family:var(--sans);font-weight:700;}
.prose h4{font:700 .82rem/1.3 var(--sans);text-transform:uppercase;letter-spacing:.1em;color:var(--violet);margin:1.1rem 0 .4rem;}
.prose p{margin:.7rem 0;}
.prose strong{color:#fff;font-weight:600;}
.prose em{color:#eef0f8;}
.prose a{color:var(--teal);text-decoration:none;border-bottom:1px solid rgba(84,212,196,.4);}
.prose a:hover{border-bottom-color:var(--teal);}
.prose code{background:rgba(155,140,245,.13);padding:.08rem .4rem;border-radius:5px;font:.84em/1 var(--mono);color:#cdc7ff;}
.prose pre{background:var(--ink);border:1px solid var(--line);border-radius:11px;padding:1rem;overflow:auto;margin:.9rem 0;}
.prose pre code{background:none;padding:0;color:#d6dbe8;}
.prose ul,.prose ol{padding-left:1.4rem;margin:.6rem 0;}
.prose li{margin:.3rem 0;}
.prose li::marker{color:var(--teal);}
.prose li>ul,.prose li>ol{margin:.3rem 0;}
.prose blockquote{margin:.9rem 0;padding:.5rem 1.1rem;border-left:3px solid var(--amber);background:rgba(246,185,122,.06);border-radius:0 10px 10px 0;color:var(--muted);font-style:italic;}
.prose hr{border:none;border-top:1px solid var(--line);margin:1.4rem 0;}
.prose table{border-collapse:collapse;width:100%;margin:1rem 0;font-size:.9em;display:block;overflow-x:auto;border-radius:9px;}
.prose th,.prose td{border:1px solid var(--line);padding:.55rem .75rem;text-align:left;vertical-align:top;}
.prose thead th{background:var(--panel-2);color:#fff;font:600 .8rem/1.3 var(--sans);text-transform:uppercase;letter-spacing:.04em;}
.prose tbody tr:nth-child(even){background:rgba(255,255,255,.018);}

/* ---- council member cards ---- */
details{background:var(--panel);border:1px solid var(--line);border-radius:13px;margin:.55rem 0;padding:.2rem 1.1rem;transition:border-color .2s;}
details:hover{border-color:var(--line-2);}
details summary{cursor:pointer;font:600 .92rem/1 var(--sans);letter-spacing:.01em;padding:.85rem 0;display:flex;align-items:center;gap:.7rem;list-style:none;}
details summary::-webkit-details-marker{display:none;}
details summary::after{content:"›";margin-left:auto;color:var(--dim);font-size:1.3rem;transition:transform .2s;}
details[open] summary::after{transform:rotate(90deg);}
details[open] summary{border-bottom:1px solid var(--line);margin-bottom:.7rem;}
.pill{font:700 .64rem/1 var(--sans);text-transform:uppercase;letter-spacing:.08em;padding:.28rem .55rem;border-radius:6px;border:1px solid var(--line);}
.pill.ok{color:var(--green);border-color:rgba(61,214,168,.4);background:rgba(61,214,168,.09);}
.pill.failed{color:var(--red);border-color:rgba(255,107,129,.4);background:rgba(255,107,129,.09);}
.model-body{font-size:.92rem;}
.failnote{white-space:pre-wrap;word-wrap:break-word;background:rgba(255,107,129,.07);border:1px solid rgba(255,107,129,.3);border-radius:9px;padding:.85rem;color:#ffb3bf;font:.85rem/1.5 var(--mono);}

/* ---- next steps ---- */
.next{margin-top:2.6rem;position:relative;border-radius:16px;padding:1.4rem 1.6rem;background:linear-gradient(135deg,rgba(84,212,196,.1),rgba(155,140,245,.09));border:1px solid rgba(84,212,196,.28);box-shadow:var(--shadow);}
.next h3{margin:.1rem 0 .7rem;font:700 .95rem/1.3 var(--sans);color:#fff;}
.next code{background:rgba(7,8,11,.55);border:1px solid var(--line);padding:.14rem .5rem;border-radius:6px;font-family:var(--mono);color:var(--teal);}
.next ul{margin:.5rem 0 0;padding-left:1.2rem;color:var(--muted);font-size:.92rem;}
.next li{margin:.45rem 0;}
.next li::marker{color:var(--teal);}
.muted-note{opacity:.7;font-style:italic;}

/* ---- entrance motion ---- */
@keyframes rise{from{opacity:0;transform:translateY(14px);}to{opacity:1;transform:none;}}
.masthead,.brief,.flow,.final,.section-label,details,.next{animation:rise .6s cubic-bezier(.2,.7,.2,1) both;}
.brief{animation-delay:.05s;}.section-label{animation-delay:.08s;}.flow{animation-delay:.12s;}.final{animation-delay:.16s;}
@media (prefers-reduced-motion:reduce){*{animation:none!important;}}
@media (max-width:560px){.headline{font-size:1.3rem;}.wrap{padding:2rem 1.1rem 4rem;}}
"""


def _plan_title(task_brief: str) -> str:
    """Short human title for the header, derived from the task brief's first line."""
    first = ""
    for line in (task_brief or "").splitlines():
        s = line.strip()
        if s:
            first = s
            break
    if first.lower().startswith("task:"):
        first = first[5:].strip()
    for stop in (". ", "; "):
        idx = first.find(stop)
        if 0 < idx <= 90:
            first = first[:idx]
            break
    if len(first) > 90:
        first = first[:88].rstrip() + "…"
    return _html.escape(first) or "Implementation plan"


_PHASE_RE = re.compile(r"^\s{0,3}#{3}\s+(Phase\b[^\n]*)$", re.MULTILINE)
_TASK_RE = re.compile(r"^\s{0,4}#{4}\s+", re.MULTILINE)
_H2_RE = re.compile(r"^\s{0,3}#{2}\s+", re.MULTILINE)
# Per-node colors so the workflow reads as a colorful pipeline, not one flat block.
_FLOW_PALETTE = [
    "#7c83fd", "#5ad1c4", "#2dd4a7", "#f6a96b",
    "#ff6b81", "#c084fc", "#fbbf24", "#38bdf8",
]


def _phase_flow_html(final_text: str) -> str:
    """Render the plan's phases (### Phase N: ...) as a self-contained CSS workflow.

    Dynamic: each node shows the phase name + its task count (#### Task ...), the
    layout flips between horizontal and vertical based on how many/large the
    phases are, and each node gets its own color. Returns "" when fewer than 2
    phases are found (so it only shows when applicable)."""
    text = final_text or ""
    matches = list(_PHASE_RE.finditer(text))
    if len(matches) < 2:
        return ""
    phases = []
    for idx, mt in enumerate(matches[:12]):
        raw = mt.group(1).strip()
        label = raw
        mm = re.match(r"Phase\s*\d+\s*[:\-–]\s*(.+)$", raw, re.IGNORECASE)
        if mm:
            label = mm.group(1).strip()
        seg_start = mt.end()
        seg_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        h2 = _H2_RE.search(text, seg_start, seg_end)  # stop counting at next top-level section
        if h2:
            seg_end = h2.start()
        ntasks = len(_TASK_RE.findall(text[seg_start:seg_end]))
        phases.append((label, ntasks))

    n = len(phases)
    longest = max(len(p[0]) for p in phases)
    # Long names or many phases read better as a vertical timeline; short/few as a row.
    vertical = n >= 5 or longest > 22

    def _sub(ntasks: int) -> str:
        if not ntasks:
            return ""
        return f'<span class="flow-sub">{ntasks} task{"s" if ntasks != 1 else ""}</span>'

    if vertical:
        items = []
        for i, (label, ntasks) in enumerate(phases, 1):
            color = _FLOW_PALETTE[(i - 1) % len(_FLOW_PALETTE)]
            safe = _html.escape(label) or f"Phase {i}"
            items.append(
                '<div class="tl-item">'
                f'<span class="flow-n" style="background:{color};box-shadow:0 0 0 4px {color}22,0 6px 18px -5px {color}">{i}</span>'
                f'<div class="tl-body"><span class="tl-name">{safe}</span>{_sub(ntasks)}</div></div>'
            )
        inner = '<div class="flow vertical">' + "".join(items) + "</div>"
    else:
        steps = []
        for i, (label, ntasks) in enumerate(phases, 1):
            color = _FLOW_PALETTE[(i - 1) % len(_FLOW_PALETTE)]
            safe = _html.escape(label) or f"Phase {i}"
            if i > 1:
                steps.append('<span class="flow-arrow">→</span>')
            steps.append(
                f'<div class="flow-step" style="border-left-color:{color}">'
                f'<span class="flow-n" style="background:{color}">{i}</span>'
                f'<span class="flow-name">{safe}</span>{_sub(ntasks)}</div>'
            )
        inner = '<div class="flow horizontal">' + "".join(steps) + "</div>"

    return '<div class="section-label">Workflow</div>' + inner


def render_plan_html(
    run_dir: Path,
    task_brief: str,
    final_text: str,
    planners: List[AgentConfig],
    planner_results: List[AgentResult],
    judge_result: AgentResult,
) -> Path:
    logo = _logo_data_uri()
    by_name = {p.name: p for p in planners}
    n_ok = sum(1 for r in planner_results if r.valid)

    cards = []
    for r in planner_results:
        cfg = by_name.get(r.name)
        kind = (cfg.kind if cfg else "") or ""
        model = _display_model(cfg) if cfg else ""
        ok = r.valid
        body_raw = ((r.data or {}).get("text") if ok else (r.error or r.raw_output or "")) or ""
        label = _html.escape(f"{r.name}" + (f" · {kind}" if kind else "") + (f" · {model}" if model else ""))
        pill = '<span class="pill ok">plan</span>' if ok else '<span class="pill failed">failed</span>'
        if ok:
            inner = f'<div class="model-body prose">{_md_to_html(body_raw)}</div>'
        else:
            inner = f'<div class="model-body"><div class="failnote">{_html.escape(body_raw)}</div></div>'
        cards.append(f"<details><summary>{pill} {label}</summary>{inner}</details>")
    cards_html = "\n".join(cards) or '<p class="sub">No council members ran.</p>'

    # Council line-up shown up top: who sat on the council and whether they produced a plan.
    chips = []
    for r in planner_results:
        cfg = by_name.get(r.name)
        kind = (cfg.kind if cfg else "") or ""
        model = _display_model(cfg) if cfg else ""
        cls = "ok" if r.valid else "failed"
        lbl = _html.escape(r.name + (f" · {kind}" if kind else "") + (f" · {model}" if model else ""))
        chips.append(f'<span class="member"><span class="dot {cls}"></span>{lbl}</span>')
    members_html = '<div class="members">' + "".join(chips) + "</div>" if chips else ""

    title = _plan_title(task_brief)
    brief_html = _md_to_html(task_brief or "_(no brief)_")
    flow_html = _phase_flow_html(final_text)
    final_html = _md_to_html(final_text or "_No final plan was produced._")
    logo_img = f'<img src="{logo}" alt="Auxly"/>' if logo else ""
    run_label = _html.escape(run_dir.name)

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Auxly Council — {title}</title>
<style>{_PLAN_CSS}</style></head>
<body><div class="aurora" aria-hidden="true"><span class="a1"></span><span class="a2"></span><span class="a3"></span></div><div class="wrap">
  <header class="masthead">
    <div class="brandrow">{logo_img}<span class="brand">Auxly <em>Council</em></span><span class="kicker">Vetted Plan</span></div>
    <h1 class="headline">{title}</h1>
    <div class="meta">Run <b>{run_label}</b> · <b>{n_ok}/{len(planner_results)}</b> council members produced a plan</div>
    {members_html}
  </header>

  <div class="card brief"><div class="brief-label">Task brief</div><div class="prose scrollbox">{brief_html}</div></div>

  {flow_html}
  <div class="section-label">Final plan (merged &amp; vetted)</div>
  <div class="final prose scrollbox">{final_html}</div>

  <div class="section-label">Each council member's plan</div>
  {cards_html}

  <div class="next">
    <h3>✓ Reviewed? Head back to Claude Code:</h3>
    <ul>
      <li>Run <code>/auxly-execute</code> to work this plan (live progress via Claude's todo list).</li>
      <li>Or just <b>tell Claude what to change</b> in plain words — it edits <code>final-plan.md</code> (or re-runs the council) and re-opens this report. <span class="muted-note">(“refine” is plain chat, not a command.)</span></li>
      <li>Or edit <code>final-plan.md</code> yourself, then run <code>/auxly-execute</code>.</li>
    </ul>
  </div>
</div></body></html>
"""
    out = run_dir / "plan.html"
    out.write_text(doc, encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(prog="llm-council")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run")
    run.add_argument("--spec", required=True, help="Path to task spec JSON")
    run.add_argument("--out", required=False, help="Also copy the final plan Markdown here")
    run.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC)
    run.add_argument("--seed", type=int, default=None)
    run.add_argument("--config", required=False, help="Path to agents config JSON")
    run.add_argument("--no-open", action="store_true", help="Do not auto-open the plan report in a browser")

    configure = sub.add_parser("configure")
    configure.add_argument("--config", required=False, help="Path to write agents config JSON")

    detect = sub.add_parser("detect", help="List installed planner CLIs + model suggestions as JSON")
    detect.add_argument("--json", action="store_true", help="(default) emit JSON")

    args = parser.parse_args()

    if args.cmd == "detect":
        catalog = available_cli_catalog()
        print(json.dumps({"available": catalog, "count": len(catalog)}, indent=2))
        return 0

    if args.cmd == "configure":
        config_path = Path(args.config) if args.config else get_default_config_path()
        configure_agents(config_path)
        return 0

    # ---- run -----------------------------------------------------------------
    try:
        task_spec = load_json(args.spec)
    except FileNotFoundError:
        print(f"Spec file not found: {args.spec}", file=sys.stderr)
        return 2

    config_path = Path(args.config) if args.config else get_default_config_path()
    prompt_text = load_text(resolve_path("../references/prompts.md"))
    planner_prompt = prompt_text.split("## Judge Prompt")[0].split("```text", 1)[1].rsplit("```", 1)[0]
    judge_prompt = prompt_text.split("## Judge Prompt", 1)[1].split("```text", 1)[1].rsplit("```", 1)[0]
    plan_template = load_text(resolve_path("../references/templates/plan.md"))
    judge_template = load_text(resolve_path("../references/templates/judge.md"))

    run_root = get_run_root()
    run_root.mkdir(parents=True, exist_ok=True)
    base_label = task_spec.get("run_id") or task_spec.get("run_label")
    if not base_label:
        base_label = f"{time.strftime('%Y%m%d')}-{slugify(task_spec.get('task') or 'run')}"
    run_dir = unique_run_dir(run_root, base_label)
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        planners, judge = load_agent_configs(task_spec, config_path=config_path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.seed is not None:
        random.seed(args.seed)

    planner_results = run_planners(
        task_spec, planners, planner_prompt, plan_template, args.timeout, str(run_dir),
        ui_state=None, ui_instance=None,
    )
    latest_valid: Dict[str, Dict[str, Any]] = {}
    for result in planner_results:
        if result.valid and result.data:
            latest_valid[result.name] = result.data
    valid_plans = list(latest_valid.values())

    randomized_plans = []
    for idx, plan in enumerate(valid_plans):
        randomized_plans.append({"label": f"Plan {idx + 1}", "plan": anonymize_text(plan["text"])})
    random.shuffle(randomized_plans)

    judge_result = run_judge(
        task_spec, randomized_plans, judge, judge_prompt, judge_template, args.timeout, str(run_dir),
        ui_state=None, ui_instance=None,
    )
    final_text = extract_final_plan(judge_result.data.get("text", "") if judge_result.data else "")
    final_path = run_dir / "final-plan.md"
    final_path.write_text(final_text, encoding="utf-8")

    html_path = render_plan_html(
        run_dir, build_task_brief(task_spec), final_text, planners, planner_results, judge_result
    )
    if not args.no_open:
        try:
            webbrowser.open(html_path.as_uri())
        except Exception:
            pass

    if args.out:
        Path(args.out).write_text(final_text, encoding="utf-8")

    print(json.dumps({
        "run_dir": str(run_dir),
        "final_plan": str(final_path),
        "plan_html": str(html_path),
        "planners_ok": sum(1 for r in planner_results if r.valid),
        "planners_total": len(planner_results),
        "judge_valid": judge_result.valid,
    }, indent=2))

    maybe_trash_empty_dir(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

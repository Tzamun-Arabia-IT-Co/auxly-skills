#!/usr/bin/env python3
"""Render a self-contained, Auxly-branded HTML report for an /auxly-execute run.

Pure Python stdlib. No servers, no network, no third-party deps. The orchestrator
(Claude) assembles a small JSON describing the finished implementation and pipes it
in; this writes `execute-report.html` and opens it in the browser.

Usage:
  python3 render_report.py --spec report.json [--out DIR] [--no-open]
  cat report.json | python3 render_report.py            # spec on stdin

Spec schema (all fields optional except title):
  {
    "title": "Re-open job cards + insurance-paid claims",
    "goal":  "one-line restatement of what was built",
    "generated_at": "2026-06-20 14:30",          # optional; defaults to now
    "summary_md": "Markdown prose summary ...",
    "phases":  [{"name":"Phase 1: Foundations","status":"done","note":"..."}],
    "changes": [{"path":"src/x.js","status":"modified","note":"reopen module"}],
    "tests":   {"commands":["npm test","npm run lint"], "result":"42 passed, 0 failed",
                "status":"pass"},
    "git":     {"branch":"fix/ai","remote":"origin","pushed":false,
                "ahead":3,"behind":0,"uncommitted":2,"last_commit":"abc1234 add reopen"},
    "crew":    [{"role":"implementer","agent":"claude general-purpose","model":"claude",
                 "tokens":84852,"duration_s":23.3}],
    "next_steps": ["Push the branch","Open a PR"]
  }
"""
import argparse
import base64
import html as _html
import json
import re
import sys
import webbrowser
from datetime import datetime
from pathlib import Path


# --------------------------------------------------------------------------- md
def _inline_md(text: str) -> str:
    """Inline Markdown -> HTML on already-escaped text: code, links, bold, em."""
    # stash code spans first so their contents aren't further formatted
    spans = []

    def _stash(m):
        spans.append(m.group(1))
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _stash, text)
    text = _html.escape(text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
                  r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\w)\*([^*]+)\*(?!\w)", r"<em>\1</em>", text)
    text = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"<em>\1</em>", text)

    def _unstash(m):
        return "<code>" + _html.escape(spans[int(m.group(1))]) + "</code>"

    return re.sub(r"\x00(\d+)\x00", _unstash, text)


def _md_to_html(md: str) -> str:
    """Compact Markdown -> HTML: headings, ul/ol, fenced code, paragraphs."""
    lines = (md or "").replace("\r\n", "\n").split("\n")
    out, i, n = [], 0, len(lines)
    list_stack = []  # ("ul"|"ol")

    def close_lists(to=0):
        while len(list_stack) > to:
            out.append(f"</{list_stack.pop()}>")

    while i < n:
        line = lines[i]
        if line.strip().startswith("```"):
            close_lists()
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(_html.escape(lines[i]))
                i += 1
            i += 1
            out.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
            continue
        h = re.match(r"^(#{1,4})\s+(.*)$", line)
        if h:
            close_lists()
            lvl = len(h.group(1))
            out.append(f"<h{lvl}>{_inline_md(h.group(2).strip())}</h{lvl}>")
            i += 1
            continue
        ol = re.match(r"^(\s*)\d+\.\s+(.*)$", line)
        ul = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        if ol or ul:
            kind = "ol" if ol else "ul"
            content = (ol or ul).group(2)
            if not list_stack or list_stack[-1] != kind:
                if list_stack:
                    close_lists()
                out.append(f"<{kind}>")
                list_stack.append(kind)
            out.append(f"<li>{_inline_md(content)}</li>")
            i += 1
            continue
        if not line.strip():
            close_lists()
            i += 1
            continue
        close_lists()
        out.append(f"<p>{_inline_md(line.strip())}</p>")
        i += 1
    close_lists()
    return "\n".join(out)


# ------------------------------------------------------------------------- bits
def _logo_data_uri() -> str:
    p = Path(__file__).resolve().parent.parent / "references" / "auxly-logo.png"
    try:
        return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
    except Exception:
        return ""


def _fmt_int(v) -> str:
    try:
        return f"{int(v):,}"
    except Exception:
        return _html.escape(str(v))


_STATUS_CLASS = {
    "added": "ok", "new": "ok", "done": "ok", "pass": "ok", "passed": "ok",
    "modified": "warn", "changed": "warn", "renamed": "warn", "partial": "warn",
    "deleted": "bad", "removed": "bad", "fail": "bad", "failed": "bad",
}


def _status_pill(status: str) -> str:
    s = (status or "").strip().lower()
    cls = _STATUS_CLASS.get(s, "dim")
    return f'<span class="pill {cls}">{_html.escape(status or "—")}</span>'


_CSS = """
:root{
  --bg:#0a0b0f;--ink:#07080b;--panel:rgba(255,255,255,.026);--panel-2:rgba(255,255,255,.045);
  --line:rgba(255,255,255,.09);--line-2:rgba(255,255,255,.14);
  --text:#e9ebf2;--muted:#8b93a7;--dim:#646b7d;
  --teal:#54d4c4;--violet:#9b8cf5;--amber:#f6b97a;--green:#3dd6a8;--red:#ff6b81;
  --sans:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  --shadow:0 24px 60px -28px rgba(0,0,0,.85),0 2px 0 0 rgba(255,255,255,.03) inset;
}
*{box-sizing:border-box;}
body{margin:0;color:var(--text);font:16px/1.7 var(--sans);letter-spacing:.005em;-webkit-font-smoothing:antialiased;
  background:radial-gradient(1100px 560px at 12% -8%,rgba(84,212,196,.13),transparent 58%),
    radial-gradient(960px 520px at 102% 2%,rgba(155,140,245,.13),transparent 54%),var(--bg);background-attachment:fixed;}
body::after{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;opacity:.32;
  background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
  background-size:46px 46px;-webkit-mask-image:radial-gradient(900px 600px at 50% -5%,#000,transparent 70%);
          mask-image:radial-gradient(900px 600px at 50% -5%,#000,transparent 70%);}
.wrap{position:relative;z-index:1;max-width:940px;margin:0 auto;padding:3rem 1.5rem 5rem;}
.masthead{padding-bottom:1.5rem;margin-bottom:1.9rem;border-bottom:1px solid var(--line);}
.brandrow{display:flex;align-items:center;gap:.85rem;margin-bottom:1.5rem;}
.brandrow img{width:42px;height:42px;border-radius:12px;box-shadow:0 0 0 1px var(--line),0 8px 24px -8px rgba(84,212,196,.5);}
.brand{font:700 1.45rem/1 var(--sans);letter-spacing:-.01em;color:#fff;}
.brand em{font-style:normal;background:linear-gradient(90deg,var(--violet),var(--teal));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}
.kicker{margin-left:auto;font:600 .64rem/1 var(--sans);letter-spacing:.22em;text-transform:uppercase;color:var(--teal);border:1px solid rgba(84,212,196,.35);background:rgba(84,212,196,.07);padding:.4rem .65rem;border-radius:6px;}
.headline{font:700 1.5rem/1.25 var(--sans);letter-spacing:-.01em;color:#fff;margin:.3rem 0 .4rem;max-width:42ch;}
.goal{color:var(--muted);font-size:.96rem;margin:0 0 1rem;}
.meta{font:500 .72rem/1.5 var(--sans);letter-spacing:.1em;text-transform:uppercase;color:var(--dim);margin:.2rem 0 0;}
.section-label{display:flex;align-items:center;gap:.65rem;font:600 .7rem/1 var(--sans);text-transform:uppercase;letter-spacing:.2em;color:var(--muted);margin:2.3rem 0 .9rem;}
.section-label::before{content:"";width:22px;height:2px;border-radius:2px;background:linear-gradient(90deg,var(--teal),transparent);}
.card{position:relative;background:var(--panel);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);padding:1.3rem 1.5rem;}
.prose{line-height:1.72;color:#dfe2ec;}
.prose h1,.prose h2,.prose h3,.prose h4{color:#fff;line-height:1.3;margin:1.1rem 0 .5rem;}
.prose h1{font-size:1.4rem;}.prose h2{font-size:1.18rem;}.prose h3{font-size:1.04rem;color:var(--teal);}
.prose p{margin:.55rem 0;}.prose strong{color:#fff;}.prose code{background:rgba(155,140,245,.13);padding:.06rem .35rem;border-radius:5px;font:.84em/1 var(--mono);color:#cdc7ff;}
.prose pre{background:var(--ink);border:1px solid var(--line);border-radius:11px;padding:1rem;overflow:auto;margin:.8rem 0;}
.prose pre code{background:none;padding:0;color:#d6dbe8;}
.prose ul,.prose ol{padding-left:1.4rem;margin:.5rem 0;}.prose li{margin:.28rem 0;}.prose li::marker{color:var(--teal);}
.prose a{color:var(--teal);text-decoration:none;border-bottom:1px solid rgba(84,212,196,.4);}
table{border-collapse:collapse;width:100%;margin:.2rem 0;font-size:.9rem;}
th,td{border:1px solid var(--line);padding:.55rem .7rem;text-align:left;vertical-align:top;}
thead th{background:var(--panel-2);color:#fff;font:600 .72rem/1.3 var(--sans);text-transform:uppercase;letter-spacing:.06em;}
tbody tr:nth-child(even){background:rgba(255,255,255,.018);}
td.path,td.num{font-family:var(--mono);font-size:.86rem;}
td.num{text-align:right;color:#fff;}
.pill{display:inline-block;font:700 .62rem/1 var(--sans);text-transform:uppercase;letter-spacing:.07em;padding:.28rem .5rem;border-radius:6px;border:1px solid var(--line);white-space:nowrap;}
.pill.ok{color:var(--green);border-color:rgba(61,214,168,.4);background:rgba(61,214,168,.09);}
.pill.warn{color:var(--amber);border-color:rgba(246,185,122,.4);background:rgba(246,185,122,.09);}
.pill.bad{color:var(--red);border-color:rgba(255,107,129,.4);background:rgba(255,107,129,.09);}
.pill.dim{color:var(--muted);}
.gitgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.8rem;}
.gitcell{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:.85rem 1rem;}
.gitcell .k{font:600 .62rem/1 var(--sans);text-transform:uppercase;letter-spacing:.12em;color:var(--dim);margin-bottom:.5rem;}
.gitcell .v{font:600 1rem/1.2 var(--sans);color:#fff;}
.gitcell .v.mono{font-family:var(--mono);font-size:.92rem;}
.cmd{display:block;font-family:var(--mono);font-size:.86rem;background:var(--ink);border:1px solid var(--line);border-left:3px solid var(--teal);border-radius:8px;padding:.55rem .8rem;margin:.4rem 0;color:#d6dbe8;}
.tok-total{margin-top:.6rem;font:600 .8rem/1 var(--sans);color:var(--muted);}
.tok-total b{color:#fff;}
.next{margin-top:2.4rem;border-radius:16px;padding:1.3rem 1.6rem;background:linear-gradient(135deg,rgba(84,212,196,.1),rgba(155,140,245,.09));border:1px solid rgba(84,212,196,.28);box-shadow:var(--shadow);}
.next h3{margin:.1rem 0 .6rem;font:700 .95rem/1.3 var(--sans);color:#fff;}
.next code{background:rgba(7,8,11,.55);border:1px solid var(--line);padding:.12rem .45rem;border-radius:6px;font-family:var(--mono);color:var(--teal);}
.next ul{margin:.4rem 0 0;padding-left:1.2rem;color:var(--muted);font-size:.92rem;}.next li{margin:.4rem 0;}.next li::marker{color:var(--teal);}
.empty{color:var(--dim);font-style:italic;}
@media (max-width:560px){.headline{font-size:1.3rem;}.wrap{padding:2rem 1.1rem 4rem;}}
"""


def render(spec: dict) -> str:
    esc = _html.escape
    title = esc(str(spec.get("title") or "Implementation report"))
    goal = spec.get("goal")
    when = spec.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M")
    logo = _logo_data_uri()
    logo_img = f'<img src="{logo}" alt="Auxly"/>' if logo else ""

    parts = []

    # masthead
    goal_html = f'<p class="goal">{esc(str(goal))}</p>' if goal else ""
    parts.append(
        '<header class="masthead">'
        f'<div class="brandrow">{logo_img}<span class="brand">Auxly <em>Execute</em></span>'
        '<span class="kicker">Implementation Report</span></div>'
        f'<h1 class="headline">{title}</h1>{goal_html}'
        f'<div class="meta">Generated {esc(str(when))}</div></header>'
    )

    # summary
    if spec.get("summary_md"):
        parts.append('<div class="section-label">Summary</div>')
        parts.append(f'<div class="card prose">{_md_to_html(spec["summary_md"])}</div>')

    # phases / tasks
    phases = spec.get("phases") or []
    if phases:
        rows = "".join(
            f'<tr><td>{esc(str(p.get("name","")))}</td>'
            f'<td>{_status_pill(p.get("status",""))}</td>'
            f'<td>{esc(str(p.get("note","") or ""))}</td></tr>'
            for p in phases
        )
        parts.append('<div class="section-label">What was done</div>')
        parts.append(
            '<div class="card"><table><thead><tr><th>Phase / task</th>'
            f'<th>Status</th><th>Notes</th></tr></thead><tbody>{rows}</tbody></table></div>'
        )

    # changed files
    changes = spec.get("changes") or []
    if changes:
        rows = "".join(
            f'<tr><td class="path">{esc(str(c.get("path","")))}</td>'
            f'<td>{_status_pill(c.get("status",""))}</td>'
            f'<td>{esc(str(c.get("note","") or ""))}</td></tr>'
            for c in changes
        )
        parts.append(f'<div class="section-label">Files changed ({len(changes)})</div>')
        parts.append(
            '<div class="card"><table><thead><tr><th>Path</th><th>Change</th>'
            f'<th>Notes</th></tr></thead><tbody>{rows}</tbody></table></div>'
        )

    # how to test
    tests = spec.get("tests") or {}
    if tests:
        cmds = "".join(f'<code class="cmd">{esc(str(c))}</code>'
                       for c in (tests.get("commands") or []))
        res = ""
        if tests.get("result"):
            res = (f'<p style="margin:.7rem 0 0">Last result: '
                   f'{_status_pill(tests.get("status","")) if tests.get("status") else ""} '
                   f'{esc(str(tests.get("result")))}</p>')
        body = cmds or '<p class="empty">No test commands provided.</p>'
        parts.append('<div class="section-label">How to test</div>')
        parts.append(f'<div class="card">{body}{res}</div>')

    # git status
    git = spec.get("git") or {}
    if git:
        pushed = git.get("pushed")
        push_pill = (_status_pill("pushed") if pushed is True
                     else _status_pill("not pushed") if pushed is False
                     else '<span class="pill dim">unknown</span>')
        cells = []
        if git.get("branch") is not None:
            cells.append(('Branch', esc(str(git["branch"])), True))
        cells.append(('Pushed', push_pill, False))
        if git.get("ahead") is not None or git.get("behind") is not None:
            cells.append(('Ahead / behind',
                          f'{_fmt_int(git.get("ahead",0))} / {_fmt_int(git.get("behind",0))}', True))
        if git.get("uncommitted") is not None:
            cells.append(('Uncommitted', _fmt_int(git.get("uncommitted")), True))
        if git.get("remote") is not None:
            cells.append(('Remote', esc(str(git["remote"])), True))
        if git.get("last_commit") is not None:
            cells.append(('Last commit', esc(str(git["last_commit"])), True))
        grid = "".join(
            f'<div class="gitcell"><div class="k">{k}</div>'
            f'<div class="v{" mono" if mono else ""}">{v}</div></div>'
            for (k, v, mono) in cells
        )
        parts.append('<div class="section-label">Git status</div>')
        parts.append(f'<div class="gitgrid">{grid}</div>')

    # token usage per agent/model
    crew = spec.get("crew") or []
    if crew:
        total_tok = 0
        rows = []
        for c in crew:
            tok = c.get("tokens")
            try:
                total_tok += int(tok)
            except Exception:
                pass
            dur = c.get("duration_s")
            dur_txt = f'{float(dur):.1f}s' if isinstance(dur, (int, float)) else esc(str(dur or "—"))
            rows.append(
                f'<tr><td>{esc(str(c.get("role","") or "—"))}</td>'
                f'<td>{esc(str(c.get("agent","") or c.get("model","") or "—"))}</td>'
                f'<td class="path">{esc(str(c.get("model","") or "—"))}</td>'
                f'<td class="num">{_fmt_int(tok) if tok is not None else "—"}</td>'
                f'<td class="num">{dur_txt}</td></tr>'
            )
        parts.append('<div class="section-label">Token usage by agent</div>')
        parts.append(
            '<div class="card"><table><thead><tr><th>Role</th><th>Agent</th><th>Model</th>'
            f'<th>Tokens</th><th>Duration</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
            f'<div class="tok-total">Total tokens: <b>{_fmt_int(total_tok)}</b></div></div>'
        )

    # next steps
    nxt = spec.get("next_steps") or []
    if nxt:
        items = "".join(f"<li>{_inline_md(str(s))}</li>" for s in nxt)
        parts.append(f'<div class="next"><h3>Next steps</h3><ul>{items}</ul></div>')

    body = "\n".join(parts)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"/>'
        '<meta name="viewport" content="width=device-width,initial-scale=1"/>'
        f'<title>Auxly Execute — {title}</title><style>{_CSS}</style></head>'
        f'<body><div class="wrap">{body}</div></body></html>'
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render an Auxly Execute HTML report.")
    ap.add_argument("--spec", help="path to JSON spec (default: stdin)")
    ap.add_argument("--out", default=".", help="output directory (default: cwd)")
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = ap.parse_args(argv)

    raw = Path(args.spec).read_text(encoding="utf-8") if args.spec else sys.stdin.read()
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON spec: {exc}", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "execute-report.html"
    out.write_text(render(spec), encoding="utf-8")

    if not args.no_open:
        try:
            webbrowser.open(out.resolve().as_uri())
        except Exception:
            pass
    print(json.dumps({"report_html": str(out.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# CLI Notes (Context7)

## Codex CLI
- Non-interactive execution: `codex exec` (or `codex e`).
- JSON streaming: `codex exec --json "..."` outputs JSON Lines events to stdout.
- Structured output: `codex exec --output-schema ./schema.json -o ./output.json "..."`.
- Final message is written to stdout; streaming activity goes to stderr.
- Auth: `codex` uses whatever the CLI is signed into — a ChatGPT-account subscription by default
  (no API key, no API billing). The council omits `-m` so it runs on the account's default model.
- Model override: `codex exec -m <model> -c model_reasoning_effort=xhigh "..."`. Only pin a model
  with API-key auth; ChatGPT-account Codex returns `HTTP 400 "model is not supported when using
  Codex with a ChatGPT account"` for API-only names like `gpt-5.2-codex`.

## Claude Code
- Launch interactive agent: `claude` in the repo directory.
- Non-interactive print mode: `claude -p "query"` (prints response and exits).
- JSON output: `claude -p "query" --output-format json`.
- Schema-validated JSON: `claude -p --json-schema '<schema>' "query"` (print mode only).
- Model selection: `claude -p --model opus "query"` (alias for latest Opus).
- Claude CLI accepts a full model name via `--model` (example in docs uses `claude-sonnet-4-5-20250929`).
- Debug mode: `claude --debug`.

## Gemini CLI
- Non-interactive prompt: `gemini -p "..."`.
- Structured JSON output: `gemini -p "..." --output-format json`.
- Streaming JSON events: `gemini -p "..." --output-format stream-json`.
- Model selection: `gemini -p "..." --model gemini-3-pro-preview` (requires preview features enabled).

## agy (Antigravity) CLI
- Non-interactive print mode: `agy --print "..."` (alias `-p`). Output is plain text (the plan
  Markdown), not JSON.
- Run unattended without permission prompts: `agy --print --dangerously-skip-permissions "..."`.
- Model selection: `agy --model "<name>" --print "..."` — list names with `agy models`
  (e.g. "Gemini 3.1 Pro (High)", "Claude Opus 4.6 (Thinking)"). Omit `--model` to use the default.
- Print timeout: `--print-timeout` (default 5m).

## OpenCode CLI
- Non-interactive prompt: `opencode run "..."`.
- Run flags include `--model` (provider/model), `--agent`, `--format` (default or json), and `--attach` to a running server.
- `--format json` returns raw JSON events; default format prints text.
- List available models with `opencode models` (optionally `--refresh`).

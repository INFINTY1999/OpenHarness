# Tracing a run

`--trace` logs every step of a session — startup, prompt routing, each model
turn, each tool call — so you can watch exactly what the harness does between
"I typed a prompt" and "I got an answer".

```bash
uv run oh --trace
```

The path is printed to stderr on startup, e.g.
`Tracing this run to: ~/.openharness/logs/trace-20260816.log`. Follow it in a
second terminal:

```bash
tail -f ~/.openharness/logs/trace-$(date +%Y%m%d).log
```

## Why a file instead of stdout

The default UI is a React/Ink frontend that owns the terminal, and the Python
backend talks to it over JSON-lines on **stdout**. A `print()` in the backend
would corrupt that protocol and scramble the TUI, so trace lines go to a file.

`--trace` exports `OPENHARNESS_TRACE` and `OPENHARNESS_TRACE_FILE`, so the
backend subprocess the frontend spawns appends to the *same* file. Each line is
tagged with its process role — `[cli:123]` for the launcher, `[backend:456]`
for the backend host — so both halves read as one timeline.

## Flags and environment

| Flag | Env | Effect |
| --- | --- | --- |
| `--trace` | `OPENHARNESS_TRACE=1` | Turn tracing on |
| `--trace-file PATH` | `OPENHARNESS_TRACE_FILE=PATH` | Write somewhere else |
| `--trace-stderr` | `OPENHARNESS_TRACE=stderr` | Also mirror to stderr |

`--trace-stderr` is for headless runs (`-p`, `--backend-only`, `--task-worker`)
where nothing is drawing a full-screen UI. Avoid it with the interactive TUI.

Tracing is off by default and each `trace()` call is a cheap early-return when
disabled, so the instrumentation costs nothing in normal use.

## Reading the output

A `-p` run that used one tool looks like this:

```
cli.start           argv='-p Use the bash tool…' cwd=…
cli.mode            mode=print
runtime.settings    model=… api_format=openai permission_mode=default max_turns=200
runtime.plugins     total=0
runtime.api_client  client=OpenAICompatibleClient
runtime.mcp         connected=0 failed=0
runtime.tools       count=40 names='agent, bash, edit_file, …'
runtime.system_prompt  chars=6699
runtime.hook        event=session_start
input.received      line='Use the bash tool…'
input.prompt        not a slash command; sending to the model
engine.submit       history=1 model=…
engine.hook         event=user_prompt_submit
query.loop.start    messages=1 max_turns=200
query.turn.start    turn=1
query.api.request   client=OpenAICompatibleClient messages=1 tools=40 system_chars=6699
api.openai.stream   POST /chat/completions (streaming)
query.api.response  elapsed_ms=8455.6 stop_reason=tool_calls in=7727 out=62 tool_uses=1
query.tools.requested  count=1 mode=sequential tools=bash
tool.start          tool=bash input="{'command': 'echo hi'}"
tool.permission     allowed=false needs_confirmation=true read_only=false
tool.permission.answer  approved=true
tool.done           elapsed_ms=27.6 is_error=false output=hi
query.tools.fed_back   results=1 errors=0
query.turn.start    turn=2                    ← results go back to the model
query.api.response  stop_reason=stop tool_uses=0
query.loop.finish   no tool calls requested; the answer is final turns_used=2
input.complete      messages=4 in=15511 out=65
runtime.close
```

### Step prefixes

| Prefix | Source | What it covers |
| --- | --- | --- |
| `cli.*` | `openharness/cli.py` | Argument parsing and which mode was chosen |
| `ui.frontend.*` | `ui/react_launcher.py` | Ink frontend launch, backend command |
| `backend.*` | `ui/backend_host.py` | JSON-lines protocol traffic from the TUI |
| `runtime.*` | `ui/runtime.py` | Settings, plugins, MCP, tools, system prompt |
| `input.*` | `ui/runtime.py` | Slash command vs. prompt routing, snapshot save |
| `engine.*` | `engine/query_engine.py` | History append, `user_prompt_submit` hook |
| `query.*` | `engine/query.py` | The agent loop: turns, compaction, API, tools |
| `tool.*` | `engine/query.py` | Hooks, permission decision, execution, output |
| `api.*` | `api/client.py`, `api/openai_client.py` | The outbound HTTP request |

## Adding your own trace points

```python
from openharness.services.trace import trace, trace_span

trace("my.step", "what just happened", key=value)

with trace_span("my.slow.thing", target=name):
    ...  # emits my.slow.thing.start / .done with elapsed_ms
```

Values are collapsed to a single truncated line, so a long tool output or a
multi-line prompt stays one readable field. If a field is expensive to compute,
guard it:

```python
from openharness.services.trace import is_enabled as trace_enabled

if trace_enabled():
    trace("my.step", payload=expensive_summary())
```

Related: `--debug` turns on standard Python `logging` at DEBUG level to stderr,
which is noisier and orthogonal to tracing.

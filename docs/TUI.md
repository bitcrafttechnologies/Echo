# Echo TUI Core

The Echo TUI is the first-party local, offline, SSH-friendly operator surface.
It is intentionally a presentation adapter over Echo's management plane, not a
second runtime architecture.

```text
Echo TUI / tmux workspace
          |
          v
LocalEchoClient or HttpEchoClient
          |
          v
RuntimeServiceProtocol / HTTP adapter
          |
          v
Echo Core
```

## Launch

After installing the package, `echoc` and `echoc console` create or attach to the
`echo-core` tmux session. The default local management endpoint is
`http://127.0.0.1:8000`.

```console
echoc console
echoc console --api-url http://127.0.0.1:8000
echoc console --no-tmux
echoc console --snapshot --surface signals
python -m echo console --no-tmux
```

If tmux is unavailable, use `--no-tmux`. `--snapshot` prints a complete
semantic text view for scripts, recovery terminals, screen readers, and tests.
No internet, browser, desktop environment, Svelte process, cloud model, or
truecolor support is required. A running Echo management plane is still
required; embedded hosts can construct `EchoTui(LocalEchoClient(service))`
without HTTP.

The tmux workspace contains `dashboard`, `chat`, `signals`, `tasks`, `entity`,
`providers`, `logs`, `config`, and `shell` windows. Alt+1 through Alt+7 select the seven Echo
views. Native tmux controls continue to work.

Within a full-screen view:

- `1`–`8` selects Overview, Chat, Signals, Tasks, Entity, Providers,
  Configuration, or Logs.
- `:` opens the shared structured Echo command prompt.
- `c` sends a message from Chat as a normal `UserMessage` Signal.
- `f` filters Signals by `type`/`source` or Logs by `severity`/`event_type`.
- `e` selects an Entity in the Entity and Chat views.
- `r` refreshes immediately.
- `q` exits the current TUI process.

Examples for the command prompt and one-shot CLI use the same grammar:

```console
echoc status
echoc entity list
echoc entity inspect bit
echoc signal list
echoc signal inspect SIGNAL_ID
echoc signal inject test.signal '{"value": true}'
echoc task list
echoc task inspect TASK_ID
echoc task cancel TASK_ID
echoc action list
echoc state get bit
echoc state set bit '{"mode": "ready"}'
echoc logs
echoc provider status
echoc provider mode auto
```

## Web parity for this iteration

| Capability | TUI surface/control |
| --- | --- |
| Runtime state, ID, uptime, recent activity | Overview |
| Signal list, payload, metadata, routing, related Task/Action IDs | Signals |
| Active/history Tasks, hierarchy, details, cancellation | Tasks + command prompt |
| Entity list, state, handlers, active Tasks | Entity |
| Identity, traits, control state, drives, self-model, attention | Entity |
| Per-person relationship state | Entity |
| Typed working, episodic, semantic, preference, and relationship memory | Entity |
| Provider mode, health, active provider/model, latency, failures, history | Providers + command prompt |
| Severity/event-type structured records | Logs + `f` filter |
| `UserMessage` Signal injection and associated Action responses | Chat |

The TUI polls detached snapshots, so WebSocket-only visual arrival pausing is
not reproduced as mutable runtime behavior. Signal observation and runtime
processing remain independent.

## Configuration

`echoc config entity bit`, `echoc config core`, `echoc config models`, and
`echoc config signals` delegate to nvim, falling back to vi. The tmux `config`
window opens `entities/` the same way.

Phase 6A supplies shared TOML parsing and startup validation. `echoc` reads
`echo.toml` (or `ECHO_CONFIG`) before connecting and uses its Console URL,
request timeout, and tmux session defaults. `echoc config validate [PATH]`
checks an edited file without starting a client. Phase 6B adds `echoc config
reload`, which calls the same explicit service operation as the web Console.
Invalid or restart-required changes are reported without partially applying
the candidate; the TUI has no terminal-only reload path.

Phase 6C adds the Configuration surface. It shows every effective setting with
`live_editable`, `restart_required`, or `hidden` status and never renders API
keys. `config inspect` reads that shared view and `config set <dotted-path>
<json-value>` uses the same validated control operation as the web Console.

## Standing integration rule

Every phase that adds an operator-visible capability must update the TUI and
web UI together in small, relevant, independently tested chunks. A subsystem
registers serializable surface metadata and exposes its status, inspection,
configuration, actions, and live streams through the shared management plane.
Both presentations consume that plane; neither manipulates subsystem internals.

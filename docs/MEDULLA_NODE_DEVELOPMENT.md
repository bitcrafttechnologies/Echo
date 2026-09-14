# Medulla Node development

This guide sets up Echo with two standalone local processes: a MacBook node
that provides bounded computer context and a web research node that provides
allowlisted network access. Neither process contains an Entity, cognition,
memory, or an LLM. Echo must approve and separately authorize each connection
before either node can execute an Action or deliver telemetry.

## Capabilities

`macbook-medulla-node` provides:

- `filesystem.list`, `filesystem.stat`, and `filesystem.read` beneath explicitly
  configured roots. Reads are UTF-8 and size bounded; no file write, delete,
  command execution, or unrestricted traversal capability exists.
- `battery.status`, `system.health`, and `system.datetime`.
- `location.current` and `weather.current` when a coarse location is configured.
- `battery.telemetry`, `system.health.telemetry`, and
  `system.datetime.telemetry` Signals at the configured interval.

`web-medulla-node` provides:

- `web.search` for keyword searches whose results are filtered to approved
  domains.
- `web.fetch` for direct HTTPS URLs on approved domains.

The default source set is GitHub, MIT OpenCourseWare, arXiv, OpenStax, DOAJ,
Project Gutenberg, and Wikinews. Direct HTTP, URL credentials, nonstandard
ports, unapproved domains, private/link-local IP addresses, and redirects away
from approved domains fail closed.

## Install and configure Echo

From the repository root:

```console
python -m pip install -e '.[api,websocket]'
```

Enable Echo's explicit node listener in `echo.toml`:

```toml
[discovery]
approval_mode = "manual"

[medulla]
enabled = true
endpoint = "ws://127.0.0.1:8765/medulla"
```

This listener does not perform network discovery. It only accepts nodes that
were explicitly configured with its address.

Create the node files, or copy the checked-in examples:

```console
macbook-medulla-node init macbook-node.yaml
web-medulla-node init web-node.yaml
```

Edit `macbook.file_roots` before running. Use the smallest roots Echo needs;
do not configure `/` or your entire home directory. Location is declarative so
macOS location permission is not silently requested. Remove the `location`
section to disable both location and weather capabilities.

Edit `web.allowed_domains` to change the research boundary. Hostnames allow
their subdomains but not lookalike suffixes.

Validate and inspect both advertised manifests completely offline:

```console
macbook-medulla-node validate macbook-node.yaml
macbook-medulla-node manifest macbook-node.yaml
web-medulla-node validate web-node.yaml
web-medulla-node manifest web-node.yaml
```

## Run the three processes

```console
# Terminal 1
python -m echo.host --config echo.toml

# Terminal 2
macbook-medulla-node run macbook-node.yaml

# Terminal 3
web-medulla-node run web-node.yaml
```

Both nodes should remain `AWAITING_APPROVAL`. Their capabilities are not in
Echo's trusted registry yet, and pre-approval telemetry is discarded.

## Approve and authorize

Open `echoc console` and select **Medulla Nodes**, or open the web Console and
select **Medulla Nodes**. Review the exact capabilities, resources, Signals,
and requirements for each candidate.

Approval and authorization are deliberately separate:

1. Select **Approve** and record a reason.
2. Review requested identity, metadata, credentials, and permissions.
3. Enter `{}` for nodes with no connection requirements, or a narrowly scoped
   authorization object for a node that requests them.
4. Select **Authorize requirements**. Only then does the node become `ACTIVE`.

The same flow is available over the management API:

```console
curl http://127.0.0.1:8000/medulla/nodes
curl -X POST http://127.0.0.1:8000/medulla/nodes/nathans-macbook/decision \
  -H 'content-type: application/json' \
  -d '{"decision":"approve","reason":"local computer context requested"}'
curl -X POST http://127.0.0.1:8000/medulla/nodes/nathans-macbook/authorization \
  -H 'content-type: application/json' -d '{"authorization":{}}'
```

Repeat approval and authorization for `open-web`. **Decline** leaves a node
available for later reconsideration. **Block** suppresses future offers until
an operator explicitly unblocks it.

## Use the nodes from Echo

Once active, ordinary prompts conservatively gather matching read-only context
before inference. Examples:

```text
How is my battery and system health?
What time is it and what is the weather here?
Search for MIT algorithms courses.
Read "https://arxiv.org/abs/1706.03762" and summarize it.
Read "/Users/me/Documents/notes/today.txt" and help me organize it.
```

Node results appear in the inference request as `medulla_observations`; the
model can use them when forming its response. A file path still has to pass the
MacBook node's configured-root check, and a URL still has to pass the web
node's scheme, domain, redirect, and network-address checks.

Telemetry arrives as normal Medulla Signals with node, provider, adapter,
resource, timestamp, correlation, and sequence provenance. Inspect it in the
Signals view or filter the API by Signal type.

## Develop and test an adapter

Adapters subclass `MedullaAdapter`, declare only serializable capabilities,
resources, and Signals, and implement asynchronous `start`, `execute`, and
`stop` methods. Use an injected backend for operating-system, hardware, or HTTP
effects so unit tests stay deterministic. Never put callbacks, imports, shell
commands, secrets, or executable objects in a manifest.

Run the focused contract tests:

```console
PYTHONPATH=src python -m unittest \
  tests.test_medulla_desktop_web_nodes \
  tests.test_medulla_management_ui
cd console && npm test -- --run && npm run check && npm run build
```

The checked-in configurations are
[`examples/macbook-medulla-node.yaml`](../examples/macbook-medulla-node.yaml)
and [`examples/web-medulla-node.yaml`](../examples/web-medulla-node.yaml).

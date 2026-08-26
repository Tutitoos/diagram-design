# Using this from Atenea

Atenea reaches a server two ways. A **capability** is a promise with a schema
and competitors behind it; a **raw passthrough** is somebody else's tool under
somebody else's name, held open so six clients do not each spawn their own
copy. This registers as a passthrough, because with no second provider of
diagrams to compete with, a capability would be a funnel with one lane.

## The server

Add to `~/.config/atenea/atenea.toml`, beside the other `[[mcp_server]]`
blocks. `id` carries no dot: it is the middle segment of `raw.<id>.<tool>`.

```toml
[[mcp_server]]
id = "diagram"
command = ["/path/to/diagram-design/mcp/bin/diagram-design-mcp"]
timeout = "45s"
expose = "raw"
tools = ["doctor", "types", "spec", "templates", "template", "profiles",
         "import_drawio", "import_mermaid", "validate",
         "export_svg", "export_png"]
effects = ["read"]

  [[mcp_server.tool]]
  name = "doctor"
  effects = ["read", "process"]

  [[mcp_server.tool]]
  name = "import_drawio"
  effects = ["read", "process"]

  [[mcp_server.tool]]
  name = "import_mermaid"
  effects = ["read", "process"]

  [[mcp_server.tool]]
  name = "validate"
  effects = ["read", "process"]

  [[mcp_server.tool]]
  name = "export_png"
  effects = ["read", "write", "process"]

  [mcp_server.env]
  PATH = "/opt/homebrew/bin:/usr/bin:/bin"
```

The tools arrive as `raw.diagram.*` on the wire, which clients render as
`raw_diagram_doctor` and so on.

`instance` is left at its default of `shared`. This server holds no per-chat
state, and one upstream session for every client is the whole point of a
passthrough.

`expose = "raw"` also keeps the server out of `atenea wrap`. That is correct:
pointing a client straight at it would route around both the effects gate and
the receipt.

### Effects

The base is `read`, which is the whole truth for the five knowledge tools and
for `export_svg` — it transforms text in memory and spawns nothing. The four
that shell out to a Python script declare `process`. Only `export_png` writes.

A chat may cause at most what `client_effects` grants it. If that list does
not hold `write`, `export_png` is refused at the door:

```
permission_denied: session 20260826T192745-1a373c may not authorize write
```

That is the gate working, not a misconfiguration. Enabling it means adding
`"write"` to `client_effects`, which widens what **every** chat may cause —
a decision about the machine, not about this tool. Everything else works
without touching it.

## The agent

`diagrammer` authors a diagram end to end, for callers that have no skill of
their own to read. It is declared as an ordinary `[[agent]]`; the far side is
`mcp/bin/diagram-agent`, which speaks Atenea's agent wire — one JSON assignment on
stdin, one report on stdout, exit 0 on every path.

```toml
[[agent]]
name = "diagrammer"
kind = "specialized"
summary = "Authors an editorial HTML+SVG diagram and validates it"
command = "/path/to/diagram-design/mcp/bin/diagram-agent"
context = ["repository"]
effects = ["read", "write", "process"]
max_duration = "10m"
max_tokens = 120000

  [[agent.result]]
  name = "path"
  type = "string"
  required = true
  summary = "The HTML that was written"

  [[agent.result]]
  name = "type"
  type = "string"
  required = true
  summary = "The diagram type it chose from the catalogue"

  [[agent.result]]
  name = "checks"
  type = "string"
  required = true
  summary = "ok, or the self-check findings that still stand"

  [[agent.result]]
  name = "bytes"
  type = "int"
  required = true
  summary = "Size of the HTML on disk"
```

Run it with:

```sh
atenea agent diagrammer --repository <id> --objective "..." --confirm [source-file...]
```

A named `.mmd` or `.drawio` is parsed by the extractors first and folded into
the brief, which is the case where this beats drawing from scratch.

Two things about that command line are not optional, and both come from the
`write` effect:

- **`--confirm`, in a real terminal.** Atenea refuses a `write` agent without
  it (`agent diagrammer may cause write or external effects`), and refuses
  `--confirm` itself when stdin is not a tty. `atenea agent` has no `--allow`
  flag, so there is no non-interactive path: run it from a terminal, or drive
  `mcp/bin/diagram-agent` directly if you are scripting.
- **`--repository`.** The diagram is written into the repository root Atenea
  names in the assignment. Without the flag, Atenea resolves one from the
  working directory, and a directory that is not a registered repository
  resolves to something else entirely — which is how a first test run left a
  diagram loose in an unrelated checkout. The agent refuses outright when it
  is handed no root at all, but it cannot second-guess a root it was given.

It needs a model CLI: `DIAGRAM_MODEL_BIN` (default `claude`), optionally
`DIAGRAM_MODEL`. Calling a model counts as `read` by the convention Atenea's
own `explore` and `plan` agents already follow; `write` is here because this
is the one agent that puts a file on disk, and `process` because it spawns
that CLI and the skill's `self_check.py`.

A diagram that fails its checks gets one repair round with the findings fed
back. If it still fails, the verdict is `incomplete` rather than `ok`: the
file is there and worth looking at, but calling it finished would be the one
lie this agent is in a position to tell.

## Verifying

The daemon reads settings at startup, so reload it after editing:

```sh
launchctl kickstart -k "gui/$(id -u)/com.tutitoos.atenea"
```

Then:

```sh
atenea config show >/dev/null    # the file still parses
atenea status                    # `diagram` appears under `servers`
atenea agent __x__ /dev/null     # the error lists diagrammer among the types
```

A freshly registered server shows `unknown ... (nobody has asked yet)`. That
is the lazy dial working: nothing is spawned until the first call needs it.

To exercise the tools without a client, drive the bridge — but keep stdin open
while you wait, because `atenea mcp` exits as soon as either direction ends,
and a plain pipe that closes after three lines takes it down before the
service has answered.

## Response size

Two line ceilings sit between this server and a client, and the tighter one
governs. The passthrough reads with an 8 MiB scanner; the bridge to the client
caps at 1 MiB. Neither truncates — an oversized line ends the scan and kills
the session — so the server refuses above 768 KiB with a message naming what
to narrow. The largest response it can be asked for today is `spec` with every
optional section on, at roughly 185 KB. A test holds that line across
upstream syncs.

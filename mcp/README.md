# diagram-design-mcp

An MCP server over the `diagram-design` skill this repository ships, so that
clients other than Claude Code can use it.

## Why this exists

`diagram-design` is a Claude Code plugin: a skill, some slash commands and
three Python scripts. It has no MCP server and no `mcpServers` key. That means
it only works inside the Claude Code family — omp, Codex and OpenCode cannot
reach it at all.

This directory is the server that was missing. It exposes two things:

- **The deterministic half** the upstream scripts already do: import a
  `.drawio` or Mermaid source, validate a generated diagram, export it.
- **The design knowledge**, which the plugin serves by putting files on disk
  where a Claude Code skill can read them. Serving the catalogue, the type
  specs and the templates as tools is what lets any client author a diagram
  in the same house style.

Nothing here parses draw.io, Mermaid or HTML. `skills/diagram-design/scripts/`
is the authority on those formats; this wraps them.

## Where this sits

This is a fork of [cathrynlavery/diagram-design](https://github.com/cathrynlavery/diagram-design)
with the server added under `mcp/`. Nothing outside `mcp/` is ours, and nothing
in it has been modified — an upstream sync is an ordinary merge.

```
skills/diagram-design/     upstream: SKILL.md, references/, assets/, scripts/
scripts/                   upstream: the repository's own gates
mcp/
  src/diagram_mcp/
    skill.py               finds the plugin tree and reads it
    server.py              MCP over stdio
    agent.py               the `diagrammer` agent for Atenea
    tools/                 one module per group of tools
  bin/diagram-design-mcp   the MCP server
  bin/diagram-agent        the agent
  bin/run-tests            the suite
  docs/atenea.md           how to register it with Atenea
```

The server locates the skill by walking up from its own file, so it works from
a clone with no configuration. `DIAGRAM_SKILL_ROOT` overrides that when you
want it to read a different checkout.

## Install

```sh
git clone git@github.com:Tutitoos/diagram-design.git
cd diagram-design && ./mcp/bin/run-tests
```

There is nothing to `pip install` for the server itself. It is stdlib-only and
runs under any Python 3.9 or newer — note that upstream's own dev scripts want
3.10+, but this server does not.

**PNG export is the one exception.** It needs Playwright, and Homebrew's Python
is externally managed (PEP 668), so it goes in a virtualenv rather than into
the system interpreter:

```sh
python3 -m venv mcp/.venv
mcp/.venv/bin/pip install playwright
mcp/.venv/bin/playwright install chromium
```

Then point the server at that interpreter with `DIAGRAM_MCP_PYTHON`. Ask the
`doctor` tool what any given installation can actually do.

## Tools

| tool | what it does | effects |
|---|---|---|
| `doctor` | what works here, and the command that fixes what does not | read, process |
| `types` | the diagram catalogue, derived from the checkout | read |
| `spec` | one type's layout spec + style guide + output contract | read |
| `templates` | the starting templates this checkout ships | read |
| `template` | one starting template, whole | read |
| `profiles` | the operator's saved brand profiles (read-only) | read |
| `import_drawio` | a draw.io file's normalized structure | read, process |
| `import_mermaid` | Mermaid source's normalized structure, from a file or inline | read, process |
| `validate` | check a diagram against the skill's own contract | read, process |
| `export_svg` | standalone SVG, **returned as text** | read |
| `export_png` | rasterized PNG, **written to disk** | read, write, process |

`export_svg` returns and `export_png` writes, which is a deliberate departure
from `references/export.md` (it writes both). A returned PNG would have to be
base64 and one MCP response is capped; keeping SVG read-only also means the
common case needs no `write` permission.

## Use from Atenea

Register it as a raw passthrough; the tools arrive as `raw_diagram_*`. See
[docs/atenea.md](docs/atenea.md).

## Testing

```sh
./mcp/bin/run-tests
```

57 tests, `unittest`, no dependency to install first — deliberately, since the
server ships stdlib-only and a suite that needed a package would be the one
thing here that did. `DIAGRAM_MCP_PYTHON=/usr/bin/python3 ./mcp/bin/run-tests`
runs them against the 3.9 floor.

Upstream's own gates (`scripts/`) are theirs and are not run from here.

## Environment

| variable | what it does |
|---|---|
| `DIAGRAM_MCP_PYTHON` | interpreter to run under; otherwise the newest `python3.N` on PATH |
| `DIAGRAM_SKILL_ROOT` | read a different plugin checkout instead of this one |
| `DIAGRAM_PROFILES_DIR` | where brand profiles live (default `~/.diagram-design/profiles`) |
| `DIAGRAM_MODEL_BIN` | the model CLI the agent calls (default `claude`) |
| `DIAGRAM_MODEL` | model name passed to that CLI |
| `DIAGRAM_MODEL_TIMEOUT` | seconds for one model turn (default 300) |

## Licence

MIT, the same as the plugin it is part of. See [LICENSE](../LICENSE).

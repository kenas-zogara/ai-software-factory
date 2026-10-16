# AI software factory

The work item owns the spec. The PR owns the build. A local board shows where each one stands, and its buttons move work along.

The design, and every decision behind it, is in [`design/factory-design.html`](design/factory-design.html).

| Path | What it is |
| --- | --- |
| `skills/factory/` | The `/factory` skill: `factory.py` (grill, check, approve, start), `sync.py` (board), `actions.py` (board buttons) |
| `hooks/factory-gate.py` | PreToolUse gates: claim guard, ready gate, build-worker deny list |
| `hooks/factory-live.py` | Live AI-review markers and instant board syncs |
| `macos/` | The `factory-orca://` URL handler source and the launchd sync job |

## Status

Runs today on one machine, against Azure DevOps, GitHub and [Orca](https://orca.dev). Before it can be published, these personal values have to move into a config file: the ADO org and project, the GitHub owner, the assignee email, the vault path, and the repo paths.

## Local install

These paths are symlinks into this repo:

- `~/.claude/skills/factory`
- `~/.claude/hooks/factory-gate.py`
- `~/.claude/hooks/factory-live.py`

The hooks are registered in `~/.claude/settings.json`. `macos/factory-orca-link.applescript` is compiled as `~/Applications/Factory Orca Link.app`, which handles the `factory-orca` URL scheme.

---
name: factory
description: Kenas's AI software factory foreman. `/factory grill <id>` opens a spec workspace for an ADO PBI or Bug, `/factory grill-session <id>` runs the grill inside it, `/factory approve <id>` passes the spec gate, `/factory sync` refreshes the board.
disable-model-invocation: true
---

# factory

Design and every decision behind it: https://claude.ai/artifact/FsrvLPEKuiQBXjLQMiNqFE. The ADO item owns the spec. The PR owns the build. The board only shows what `sync.py` works out from them.

Scripts live next to this file. Run them with `python3 ~/.claude/skills/factory/<script>`.

## `grill <id>`: run in your main session

Run `factory.py grill <id>` and relay its output. It refuses an item past `New`, and an item that already has a `spec-<id>` workspace. Otherwise it opens a worktree of the flexii_documents vault named `spec-<id>`, and starts Claude in it with `/factory grill-session <id>`.

## `grill-session <id>`: runs inside `spec-<id>`

You shape the PBI or Bug with Kenas until it passes the spec gate. Write no code, and put no code in the item.

1. **Read the sources.** Read the item with its relations through the ADO MCP. Then read its parent Feature (Figma is often linked only there), its sibling PBIs, and any BE PBI or PR it depends on. Read repo code only to check a claim. Code never goes into the PBI.
2. **Start the note** at `specs/<id>-<slug>.md`:

   ```markdown
   ---
   type: spec
   ado: <id>
   repos: [<repo>, ...]
   created: <YYYY-MM-DD>
   topics: []
   ---
   ## Decisions
   ## Open questions
   ## Grill log
   ```

   Commit it to this branch. Commit again after every round.
3. **Grill.** Run `cat ~/.claude/skills/grilling/SKILL.md` and follow it with Kenas. After each round:
   - a settled decision goes under **Decisions** as one dated line with its reason
   - an unsettled one goes under **Open questions**
   - the condensed round goes under **Grill log**

   Decisions from calls belong here too. Ask Kenas whether any were made outside the ticket.
4. **Write the spec back to the item** using the `az-create-ticket` conventions: `<h3>` Problem, Why and What, and checkable AC with no code. For a Bug: repro steps and the expected behaviour. Also add:
   - a `BE dependency: <PR, PBI, or none>` line
   - the Figma frame links, or a `no Figma` line

   Show Kenas the text first. Write it through the ADO MCP once he says yes.
5. **Check.** Run `factory.py check <id>` and fix what it reports. When it prints `All 5 checks pass.`, tell Kenas to run `/factory approve <id>` from his main session. Stop there. Moving the item is his call, and its state is not yours to change.

## `approve <id>`: run in your main session

Run `factory.py approve <id>` and relay its output word for word. The script:
1. reruns the five checks and refuses on any failure
2. commits the note, fast-forwards the spec branch into the vault's `main`, moves the item to Approved, and posts an ADO comment
3. removes the `spec-<id>` workspace

If the fast-forward fails, the vault's `main` has moved. Rebase `spec-<id>` onto it, then rerun approve.

## `start <id>`: run in your main session

Move the item into the sprint first if asked (ADO MCP, `System.IterationPath`). Then run `factory.py start <id>` and relay its output. It refuses an item that isn't Approved or Committed, or one with no `repos:` line in its spec note. An open PR or an Orca workspace whose comment carries `AB#<id>` counts as a claim. For each repo in the note it creates a Task under the item, opens a `<id>-<slug>` workspace off `origin/dev`, and starts a worker with the build brief. Then it moves the item to Committed. The worker renames its branch, fills the Task, builds, and ships a draft PR.

## Board actions

The buttons on the board are `factory-orca://<action>?…` links. `Factory Orca Link.app` passes them to `open-orca.sh`, which runs `actions.py`. Results and refusals arrive as macOS notifications, each action is logged to `~/.claude/factory/actions.log`, and every action ends with a sync.

| Button | Card | Does |
| --- | --- | --- |
| Approve / Start | Awaiting approval / Approved | `factory.py approve` / `start`, after a confirm |
| AI review / e2e review | Any open PR | A fresh agent: in my PR's build workspace if it has one, otherwise in a `review-<n>` workspace on the PR head (Orca comment `review: <url>`). It posts on my PRs. On other people's PRs it writes findings to `~/.claude/factory/findings/<owner>-<repo>-<n>.md` and waits for Kenas |
| Remove | Any card with a workspace | `orca worktree rm`, after a confirm. Refused while there are uncommitted or unpushed commits |
| Hide / Unhide | Build and Reviews cards with no workspace | `hidden.json`. The card returns on a new head commit or a new review request |

The Reviews lane (To review, AI reviewing, Findings ready, Reviewed) is worked out from GitHub, the live markers and the findings files. A findings file newer than the PR's last commit sends one notification.

## Gates: `~/.claude/hooks/factory-gate.py`

A PreToolUse hook. Every block is logged to `~/.claude/factory/gate-log.jsonl`.

- **`gh pr create`, the claim guard.** Every `AB#` in the body must be past the spec gate (for a Task, its parent's state), and no other open PR in the repo may claim it.
- **`gh pr ready`, the ready gate.** Four checks:
  - a test commit at or before the first code commit, or a `No test: <reason>` line
  - an eval comment (first line `<!-- factory:eval -->`) newer than the last commit
  - `<img>` evidence when UI files changed, or a `No UI change: <reason>` line
  - docs changed, or "not doc-affecting"
- **Build workspaces (`<id>-<slug>`), the deny list.** No force-push to a shared branch or without a named branch. No Slack posts. No new ADO items, and no edits to a spec's title or AC.

If the hook can't reach ADO or GitHub, it warns instead of blocking.

## `sync`

Run `sync.py`, then `open ~/.claude/factory/board.html`. Besides the board, sync makes two kinds of write:
- **BE watch.** An in-flight card whose spec's `BE dependency:` line names an unmerged PR, or an item not yet on dev, gets the `needs-be` flag and PR label. When the dependency lands, the label is removed and the worker's terminal is sent "continue" once. State is kept in `be-wait.json`.
- **Merge metrics.** Each merged PR adds one line to the `## Build log` of its spec note in the vault: gate blocks by kind, eval posts, and review comments from others. Sync commits that file only. launchd (`com.kenas.factory-sync`) already runs it every 2 minutes. Its log is `~/.claude/factory/sync.log`.

## Not built yet

The monthly `reflect` over the build logs. It proposes edits and waits for approval, so run it by hand: type `reflect`, pointing at `specs/*.md` Build log sections.

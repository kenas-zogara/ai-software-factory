---
name: factory
description: Kenas's AI software factory foreman. `/factory grill <id>` opens a spec workspace for an ADO PBI or Bug, `/factory grill-session <id>` runs the grill inside it, `/factory approve <id>` passes the spec gate, `/factory sync` refreshes the board.
disable-model-invocation: true
---

# factory

Design and every decision behind it: https://claude.ai/artifact/FsrvLPEKuiQBXjLQMiNqFE. The ADO item owns the spec. The PR owns the build. The board only shows what `sync.py` works out from them.

Scripts live next to this file. Run them with `python3 ~/.claude/skills/factory/<script>`.

## `grill <id>`: run in your main session

Run `factory.py grill <id>` and relay its output. It refuses an item whose spec is already approved, and an item that already has a `spec-<id>` workspace. ADO state doesn't matter: an item created straight into Committed still gets grilled. Otherwise it opens a worktree of the flexii_documents vault named `spec-<id>`, and starts Claude in it with `/factory grill-session <id>`.

## `grill-session <id>`: runs inside `spec-<id>`

You shape the PBI or Bug with Kenas until it passes the spec gate. Write no code, and put no code in the item.

**A Task** gets a shorter grill. Its parent PBI or Bug owns Product and Architecture: read them from the parent and its spec note, and never re-ask them. Run only Program design and Vertical slices. In step 4, write the Task's six sections per the `az-create-ticket` Task table instead of Problem, Why, What and AC. Code is welcome there. The note's Design and Slices must both be filled to pass the check.

1. **Read the sources, and check the code in the same turn.** Read the item with its relations through the ADO MCP. Then read its parent Feature (Figma is often linked only there), its sibling PBIs, and any BE PBI or PR it depends on. In the same turn, dispatch an `Explore` subagent to find what the code decides today: the current behaviour, the shared rule that owns it, and which repo and app hold it. This worktree is the vault, not a repo, so read code with `git -C ~/Documents/flexii/<repo> fetch -q origin dev && git -C ~/Documents/flexii/<repo> show origin/dev:<path>`. A question whose answer might be "match what exists" waits for that check. Code never goes into the PBI.

   For a Bug, reproduce the symptom to what the user sees, per step 1 of the `bug-fix` playbook, before you write Why or AC. Harm the repro did not show stays out of Why.
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
   ## Design
   ## Slices
   ## Open questions
   ## Grill log
   ```

   Commit it to this branch. Commit again after every round.
3. **Grill, top-down through four stages.** Run `cat ~/.claude/skills/grilling/SKILL.md` and follow it with Kenas. Each stage's decisions are prerequisites of the next, so they order the frontier. Run a stage only when it leaves Kenas a decision. Otherwise write its answer from the code check in one line and move on. The FE component tree and a multi-box architecture diagram are drawn either way.

   | Stage | Settles | Lands in |
   | --- | --- | --- |
   | Product | Who the user is, the problem, the user stories. If the parent Feature does not name the user, ask that first | The item: Problem, Why, What, AC |
   | Architecture | Where each story lands: which app or package, BE service and API, DB, infra, mobile. When it touches more than one box, draw it with the `diagram-design` skill, type architecture delta, publish it as a private artifact, and show it before program design starts | `repos:` and `BE dependency:` lines. The diagram link goes in the note's `## Design`, never on the item |
   | Program design | How it is built inside each layer. Read the repo's `docs/architecture.md` and the domain rule doc first. For FE work, always draw the component tree below | The note's `## Design` |
   | Vertical slices | The PRs, each user-visible and shippable alone, with scope and done-when. Dependencies go first. After them, the slice Kenas can judge against the AC goes first | The note's `## Slices`, in build order |

   The FE component tree is pseudo-JSX. Mark `+` new, `~` changed, and leave existing nodes unmarked. A trailing comment names the node's data (query or mutation) and who owns its state. `fns:` lists new helpers with their home folder. Draw up to 3 options, each with a one-line trade-off, and mark your pick:

   ```
   Option A (pick): one tab, server-paged
   <WorkerDetailsPage>                    // route, owns ?tab (nuqs)
     <WorkerHeader worker />
     <Tabs>
       + <ShiftHistoryTab workerId>       // useShiftHistoryQuery(workerId)
           <AdminTable columns rows />
           + <EmptyState />
       ~ <ProfileTab />                   // drop useAttributes()
   fns: + toShiftRows(dto) → Row[]        // features/workers/lib/
   ```

   Design and Slices hold code, so they stay in the note, which the build reads. The item stays code-free. When the code check leaves Kenas no decision in any stage, skip the interview: show him the drafted spec and ask for a yes.

   After each round:
   - a settled decision goes under **Decisions** as one dated line with its reason
   - an unsettled one goes under **Open questions**
   - the condensed round goes under **Grill log**

   Decisions from calls belong here too. Ask Kenas whether any were made outside the ticket.
4. **Write the spec back to the item** using the conventions in `~/Documents/flexii/flex-store/.agents/skills/az-create-ticket/SKILL.md` (the vault has no repo skills): `<h3>` Problem, Why and What, and checkable AC with no code. For a Bug: repro steps and the expected behaviour. Also add:
   - a `BE dependency: <PR, PBI, or none>` line
   - the Figma frame links, or a `no Figma` line

   Show Kenas the text first. Write it through the ADO MCP once he says yes.
5. **Check.** Run `factory.py check <id>` and fix what it reports. When it prints `All checks pass.`, tell Kenas to run `/factory approve <id>` from his main session. Stop there. Moving the item is his call, and its state is not yours to change.

## `approve <id>`: run in your main session

Run `factory.py approve <id>` and relay its output word for word. The script:
1. reruns the checks and refuses on any failure
2. commits the note, fast-forwards the spec branch into the vault's `main`, and posts the `Spec approved by Kenas` ADO comment. That comment is the approval every gate reads. A `New` PBI or Bug also moves to Approved; any other item keeps its state
3. removes the `spec-<id>` workspace

If the fast-forward fails, the vault's `main` has moved. Rebase `spec-<id>` onto it, then rerun approve.

## `start <id>`: run in your main session

Move the item into the sprint first if asked (ADO MCP, `System.IterationPath`). Then run `factory.py start <id>` and relay its output. It refuses an item with no approval comment, or one with no `repos:` line in its spec note. An open PR or an Orca workspace whose comment carries `AB#<id>` counts as a claim. For each repo in the note it creates a Task under the item, opens a `<id>-<slug>` workspace off `origin/dev`, and starts a worker with the build brief. Then it moves the item to Committed. The worker renames its branch, fills the Task, builds, and ships a draft PR.

`start <task> [repo]` builds an existing To Do Task instead: one workspace, no new Task, and the Task moves to In Progress. Its repo comes from the argument, or from the single `repos:` entry in the Task's or its parent's spec note. The parent's state still has to pass the claim guard at `gh pr create`.

## Board actions

The buttons on the board are `factory-orca://<action>?…` links. `Factory Orca Link.app` passes them to `open-orca.sh`, which runs `actions.py`. Each action, with its result or refusal, is logged to `~/.claude/factory/actions.log`. Every action ends with a sync.

| Button | Card | Does |
| --- | --- | --- |
| Approve / Start | Awaiting approval / Approved | `factory.py approve` / `start`, after a confirm |
| AI review / e2e review | Any open PR | A fresh agent: in my PR's build workspace if it has one, otherwise in a `review-<n>` workspace on the PR head (Orca comment `review: <url>`). It posts on my PRs. On other people's PRs it writes findings to `~/.claude/factory/findings/<owner>-<repo>-<n>.md` and waits for Kenas |
| Remove | Any card with a workspace | `orca worktree rm`, after a confirm. Refused while there are uncommitted or unpushed commits |
| Hide / Unhide | Build and Reviews cards with no workspace | `hidden.json`. The card returns on a new head commit or a new review request |

The Reviews lane (To review, AI reviewing, Findings ready, Reviewed) is worked out from GitHub, the live markers and the findings files.

## Gates: `~/.claude/hooks/factory-gate.py`

A PreToolUse hook. Every block is logged to `~/.claude/factory/gate-log.jsonl`.

- **`gh pr create`, the claim guard.** Every `AB#` in the body needs the approval comment, on the item or, for a Task, on its parent. ADO state doesn't count. No other open PR in the repo may claim it.
  - A Bug that skips the grill passes with a `No spec: <reason>` line in the PR body. The pass is logged as `no-spec` and lands in the spec note's build log.
- **The approval comment.** Only `factory.py approve` may post it. An ADO MCP comment containing `Spec approved by Kenas` is refused.
- **`gh pr ready`, the ready gate.** Three checks. Eval is not one of them: Kenas runs it when he chooses, or with the board's AI review button.
  - a test commit at or before the first code commit, or a `No test: <reason>` line
  - `<img>` evidence when UI files changed, or a `No UI change: <reason>` line
  - docs changed, or "not doc-affecting"
- **Build workspaces (`<id>-<slug>`), the deny list.** No force-push to a shared branch or without a named branch. No Slack posts. No new ADO items, and no edits to a spec's title or AC.

If the hook can't reach ADO or GitHub, it warns instead of blocking.

## `sync`

Run `sync.py`, then `open ~/.claude/factory/board.html`. Besides the board, sync makes two kinds of write:
- **BE watch.** An in-flight card whose spec's `BE dependency:` line names an unmerged PR, or an item not yet on dev, gets the `needs-be` flag and PR label. When the dependency lands, the label is removed and the worker's terminal is sent "continue" once. State is kept in `be-wait.json`.
- **Merge metrics.** Each merged PR adds one line to the `## Build log` of its spec note in the vault: gate blocks by kind, eval posts, and review comments from others. Sync commits that file only. launchd (`com.kenas.factory-sync`) runs it every 2 minutes by opening `factory-orca://sync`, because only Factory Orca Link.app may read `~/Documents`. Failures land in `~/.claude/factory/actions.log`.

## Not built yet

The monthly `reflect` over the build logs. It proposes edits and waits for approval, so run it by hand: type `reflect`, pointing at `specs/*.md` Build log sections.

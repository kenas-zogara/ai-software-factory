#!/usr/bin/env python3
"""Factory foreman, deterministic half. The judgement (grilling) lives in SKILL.md.

  factory.py grill <id>     open the spec workspace for an ADO item and start its grill session
  factory.py check <id>     run the spec-gate check, change nothing
  factory.py approve <id>   check, then move the item to Approved (a Task stays To Do), comment, land the note, close the workspace
  factory.py start <id>     one Task and one build workspace per repo in the spec note; item -> Committed
  factory.py start <task> [repo]  one build workspace for a To Do Task; Task -> In Progress
"""
import glob, html, json, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(__file__))
from sync import ADO, ado, gh_json, sh  # noqa: E402

VAULT = os.path.expanduser("~/Documents/flexii/flexii_documents")
APPROVED = "Approved"
ME = "kenas@flexii.net"
FIELDS = "System.Title,System.WorkItemType,System.State,System.Description,System.Parent,Microsoft.VSTS.Common.AcceptanceCriteria,Microsoft.VSTS.TCM.ReproSteps"
TASK_SECTIONS = ("What to build", "Contributes to", "Affected repositories and systems", "Target branch", "Blocking dependencies", "Done when")
CODE = re.compile(r"<code>|```|\b[\w/.-]+\.(tsx?|jsx?|java|py|kt|sql)\b|\b\w+\(\)|\b[a-z]+[A-Z]\w*\b")


def text(h):
    return html.unescape(re.sub(r"<[^>]+>", " ", h or "")).strip()


def item(i):
    return ado(f"wit/workitems/{i}?fields={FIELDS}&api-version=7.1")["fields"]


def worktrees():
    return json.loads(sh("orca", "worktree", "list", "--json"))["result"]["worktrees"]


def spec_wt(i):
    return next((w for w in worktrees() if w.get("displayName") == f"spec-{i}"), None)


def note_path(i):
    wt = spec_wt(i)
    root = wt["path"] if wt else VAULT
    hits = glob.glob(os.path.join(root, "specs", f"{i}-*.md"))
    return hits[0] if hits else None


def section(note_text, name):
    m = re.search(rf"^##\s+{name}\s*$(.*?)(?=^##\s|\Z)", note_text, re.M | re.S)
    return m.group(1) if m else ""


def check_task(i, f):
    desc = f.get("System.Description") or ""
    fails = []
    missing = [h for h in TASK_SECTIONS if not re.search(rf"<h3>\s*{h}\s*</h3>", desc, re.I)]
    if missing:
        fails.append(f"1. Description is missing the {', '.join(missing)} section(s)")
    note = note_path(i)
    if not note:
        fails.append(f"2. No spec note at specs/{i}-<slug>.md")
    else:
        s = open(note).read()
        if empty := [h for h in ("Design", "Slices") if not section(s, h).strip()]:
            fails.append(f"2. The spec note's {' and '.join(empty)} section(s) are empty")
        if re.search(r"^\s*[-*\d]", section(s, "Open questions"), re.M):
            fails.append("3. The spec note still has open questions")
    return f, fails, note


def check(i):
    f = item(i)
    if f["System.WorkItemType"] == "Task":
        return check_task(i, f)
    is_bug = f["System.WorkItemType"] == "Bug"
    desc = f.get("System.Description") or ""
    ac = f.get("Microsoft.VSTS.Common.AcceptanceCriteria") or ""
    body = desc + ac + (f.get("Microsoft.VSTS.TCM.ReproSteps") or "")
    parent = item(f["System.Parent"]).get("System.Description", "") if f.get("System.Parent") else ""
    fails = []
    if is_bug:
        if not text(f.get("Microsoft.VSTS.TCM.ReproSteps")):
            fails.append("1. Repro steps are empty")
    else:
        missing = [h for h in ("Problem", "Why", "What") if not re.search(rf"<h3>\s*{h}\s*</h3>", desc, re.I)]
        if missing:
            fails.append(f"1. Description is missing the {', '.join(missing)} section(s)")
    if not text(ac):
        fails.append("2. Acceptance criteria are empty")
    elif not is_bug and (m := CODE.search(text(ac))):
        fails.append(f"2. Acceptance criteria contain code: '{m.group(0)}'. Rewrite it in product terms")
    if not re.search(r"BE dependency\s*:", text(body), re.I):
        fails.append("3. No 'BE dependency:' line (name the BE PR or PBI, or write 'BE dependency: none')")
    if "figma.com" not in body + parent and not re.search(r"no figma", text(body), re.I):
        fails.append("4. No Figma link on the item or its Feature, and no 'no Figma' line")
    note = note_path(i)
    if not note:
        fails.append(f"5. No spec note at specs/{i}-<slug>.md")
    else:
        if re.search(r"^\s*[-*\d]", section(open(note).read(), "Open questions"), re.M):
            fails.append("5. The spec note still has open questions")
    return f, fails, note


def spec_state(f):
    """The state an item is specced in: a Task is To Do, a PBI or Bug is New."""
    return "To Do" if f["System.WorkItemType"] == "Task" else "New"


def cmd_grill(i):
    f = item(i)
    if f["System.State"] != spec_state(f):
        sys.exit(f"{i} is {f['System.State']}, past the spec phase. Nothing to grill.")
    if wt := spec_wt(i):
        sys.exit(f"spec-{i} already exists at {wt['path']}. Open it in Orca.")
    if not any(r.get("path") == VAULT for r in json.loads(sh("orca", "repo", "list", "--json"))["result"].get("repos", [])):
        sh("orca", "repo", "add", "--path", VAULT)
    out = sh("orca", "worktree", "create", "--repo", f"path:{VAULT}", "--name", f"spec-{i}", "--base-branch", "main",
             "--comment", f"spec: {f['System.Title']} {ADO}/_workitems/edit/{i}",
             "--agent", "claude", "--prompt", f"/factory grill-session {i}", "--json")
    print(f"Opened spec-{i} for '{f['System.Title']}'. The grill session is starting there.\n{out.strip()[:300]}")


def cmd_check(i):
    f, fails, note = check(i)
    print(f"{i} {f['System.Title']} ({f['System.State']})")
    print("\n".join(fails) if fails else "All checks pass.")
    return fails


def cmd_approve(i):
    f, fails, note = check(i)
    is_task = f["System.WorkItemType"] == "Task"
    if f["System.State"] != spec_state(f):
        sys.exit(f"{i} is already {f['System.State']}.")
    if fails:
        sys.exit("Not approved:\n" + "\n".join(fails))
    wt = spec_wt(i)
    rel = os.path.relpath(note, wt["path"] if wt else VAULT)
    if wt:
        if sh("git", "-C", wt["path"], "status", "--porcelain"):
            sh("git", "-C", wt["path"], "add", "-A")
            sh("git", "-C", wt["path"], "commit", "-m", f"spec({i}): {f['System.Title']}")
        sh("git", "-C", VAULT, "merge", "--ff-only", wt["branch"].removeprefix("refs/heads/"))
    # A Task has no Approved state: it stays To Do, which is what start builds from.
    if not is_task:
        ado(f"wit/workitems/{i}?api-version=7.1", [{"op": "add", "path": "/fields/System.State", "value": APPROVED}], patch=True)
    ado(f"wit/workItems/{i}/comments?api-version=7.1-preview.4",
        {"text": f"Spec approved by Kenas. All spec-gate checks passed. Decision log: flexii_documents/{rel}"})
    if wt:
        sh("orca", "worktree", "rm", "--worktree", f"name:spec-{i}")
    print(f"{i} is {'approved, still To Do' if is_task else 'Approved'}. Note landed at {rel}." + (" spec workspace removed." if wt else ""))


# Stacked build: FACTORY_BASE=origin/<parent PR branch> factory.py start <id>
BASE = os.environ.get("FACTORY_BASE", "origin/dev")


def note_repos(note):
    m = re.search(r"^repos:\s*\[(.*?)\]", open(note).read(), re.M)
    return [r.strip() for r in m.group(1).split(",") if r.strip()] if m else []


def brief(i, f, task, repo, note):
    stack = f"based on {BASE.removeprefix('origin/')} (stacked; GitHub retargets it to dev when that merges) " if BASE != "origin/dev" else ""
    source ="the decision log's Design and Slices" if note else f"Task {task}'s description"
    return (f"/kenas-mode Build Task {task} for PBI {i} '{f['System.Title']}' in {repo}. "
            f"Spec: {ADO}/_workitems/edit/{i} (owns the AC). Decision log: {note or 'none'}. Read both before any code. "
            f"1. Rename the branch to this repo's prefix convention (worktree-workspace playbook). "
            f"2. plan from {source}, then fill Task {task} with its sections per az-create-ticket. "
            f"If Slices lists more than one, build only the first; the claim guard allows one open PR per item, so list the rest under Blocked on me. "
            f"3. test first, implement, then the repo's review skill on your own diff. "
            f"4. ship as a draft PR {stack}whose body carries AB#{task} and AB#{i}. "
            f"5. Stop and report. Leave the PR as a draft.")


def find_note(*ids):
    return next((h[0] for i in ids if (h := glob.glob(os.path.join(VAULT, "specs", f"{i}-*.md")))), None)


def cmd_start(i, repo=None):
    # ponytail: no gh pr create guard hook and no draft PR at start; the worker opens it via ship. Add the hook when a second worker races a claim.
    f = item(i)
    if f["System.WorkItemType"] == "Task":
        return start_task(i, f, repo)
    if f["System.State"] not in (APPROVED, "Committed"):
        sys.exit(f"{i} is {f['System.State']}. Only an Approved spec or a To Do Task starts a build.")
    note = find_note(i)
    repos = note_repos(note) if note else []
    if not repos:
        sys.exit(f"No repos: line in the spec note for {i}. Add one, e.g. repos: [flex-store].")
    taken = [w["displayName"] for w in worktrees() if f"AB#{i}" in (w.get("comment") or "")]
    if taken:
        sys.exit(f"{i} is already claimed by workspace(s): {', '.join(taken)}.")
    slug = slug_of(f)
    iteration = ado(f"wit/workitems/{i}?fields=System.IterationPath&api-version=7.1")["fields"]["System.IterationPath"]
    for repo in repos:
        if prs := gh_json("pr", "list", "-R", f"Flexii321/{repo}", "--state", "open", "--search", f"AB#{i}", "--json", "number"):
            print(f"{repo}: skipped, PR #{prs[0]['number']} already claims AB#{i}.")
            continue
        task = ado("wit/workitems/$Task?api-version=7.1", [
            {"op": "add", "path": "/fields/System.Title", "value": f"[{repo}] {f['System.Title'].split('] ', 1)[-1]}"},
            {"op": "add", "path": "/fields/System.AreaPath", "value": "Flexii\\Web app"},
            {"op": "add", "path": "/fields/System.IterationPath", "value": iteration},
            {"op": "add", "path": "/fields/System.AssignedTo", "value": ME},
            {"op": "add", "path": "/relations/-", "value": {"rel": "System.LinkTypes.Hierarchy-Reverse",
                                                            "url": f"{ADO}/_apis/wit/workItems/{i}"}},
        ], patch=True)["id"]
        open_build(i, f, task, repo, note, f"{i}-{slug}")
        print(f"{repo}: Task {task} created, workspace {i}-{slug} opened, worker started.")
    ado(f"wit/workitems/{i}?api-version=7.1", [{"op": "add", "path": "/fields/System.State", "value": "Committed"}], patch=True)


def slug_of(f):
    return re.sub(r"[^a-z0-9]+", "-", re.sub(r"^\[[^\]]*\]\s*", "", f["System.Title"]).lower()).strip("-")[:40].rstrip("-")


def open_build(pbi, pf, task, repo, note, name):
    sh("orca", "worktree", "create", "--repo", f"path:{os.path.expanduser('~/Documents/flexii/' + repo)}",
       "--name", name, "--no-parent", "--base-branch", BASE, "--setup", "run",
       "--comment", f"build: AB#{pbi} AB#{task} {pf['System.Title']}",
       "--agent", "claude", "--prompt", brief(pbi, pf, task, repo, note), "--json")


def start_task(t, f, repo):
    """A Task is already one repo's build: no spec gate of its own, no child Task. Its parent owns the spec."""
    if f["System.State"] != "To Do":
        sys.exit(f"Task {t} is {f['System.State']}. Only a To Do Task starts a build.")
    if not f.get("System.Parent"):
        sys.exit(f"Task {t} has no parent PBI or Bug to own its spec.")
    pbi = f["System.Parent"]
    note = find_note(t, pbi)
    repos = [repo] if repo else (note_repos(note) if note else [])
    if len(repos) != 1:
        sys.exit(f"Name Task {t}'s repo: factory.py start {t} <repo>, or one repos: entry in specs/{t}-<slug>.md.")
    repo = repos[0]
    if taken := [w["displayName"] for w in worktrees() if f"AB#{t}" in (w.get("comment") or "")]:
        sys.exit(f"Task {t} is already claimed by workspace(s): {', '.join(taken)}.")
    if prs := gh_json("pr", "list", "-R", f"Flexii321/{repo}", "--state", "open", "--search", f"AB#{t}", "--json", "number"):
        sys.exit(f"Task {t} is already claimed by PR #{prs[0]['number']} in {repo}.")
    open_build(pbi, item(pbi), t, repo, note, f"{t}-{slug_of(f)}")
    ado(f"wit/workitems/{t}?api-version=7.1", [{"op": "add", "path": "/fields/System.State", "value": "In Progress"}], patch=True)
    print(f"{repo}: workspace {t}-{slug_of(f)} opened for Task {t}, worker started. Task {t} is In Progress.")


if __name__ == "__main__":
    cmds = {"grill": cmd_grill, "check": cmd_check, "approve": cmd_approve, "start": cmd_start}
    a = sys.argv[1:]
    if not (2 <= len(a) <= 3) or a[0] not in cmds or not a[1].isdigit() or (len(a) == 3 and a[0] != "start"):
        sys.exit(__doc__)
    sys.exit(1 if cmds[a[0]](int(a[1]), *a[2:]) else 0)

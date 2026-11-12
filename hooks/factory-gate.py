#!/usr/bin/env python3
# Factory gates, PreToolUse. Design: https://claude.ai/artifact/FsrvLPEKuiQBXjLQMiNqFE
#   gh pr create  claim guard: every AB# in the body has an approved spec (or, for a Bug, a "No spec:" line),
#                 and no other open PR in this repo claims it
#   gh pr ready   ready gate: test-first or "No test:", UI evidence, a docs verdict
#   ADO comment   only factory.py approve posts the approval comment
#   build worker  deny list for sessions in an Orca build workspace (<task>-<slug>): no force-push to a shared
#                 branch, no Slack posts, no new ADO items, no title/description/AC edits but on its own Task
# Every block, and every "No spec:" pass, is appended to ~/.claude/factory/gate-log.jsonl; merge metrics read it.
import json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.expanduser("~/.claude/skills/factory"))
from sync import APPROVAL, ado, spec_approved  # noqa: E402

data = json.load(sys.stdin)
tool, inp = data.get("tool_name", ""), data.get("tool_input") or {}
cwd = data.get("cwd") or os.getcwd()
cmd = inp.get("command", "") if tool == "Bash" else ""
# A heredoc body is data, not a command (same rule as gh-pr-guard.sh).
cmd = re.sub(r"<<-?\s*(['\"]?)(\w+)\1.*?\n\s*\2\b", "", cmd, flags=re.S)
START = r"(?:^|[;&|(]\s*|\n\s*)"
LOG = os.path.expanduser("~/.claude/factory/gate-log.jsonl")
SHARED = {"dev", "main", "master", "staging", "production"}
SPEC_FIELDS = ("/fields/System.Title", "/fields/System.Description", "/fields/Microsoft.VSTS.Common.AcceptanceCriteria")
TEST_FILE = re.compile(r"(\.(test|spec)\.[jt]sx?$|(^|/)test_[^/]+\.py$|Test\.(java|kt)$|_test\.dart$|/__tests__/)")
UI_FILE = re.compile(r"\.(tsx|jsx|css|scss|dart)$")


def run(*a):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, timeout=60)


def gh(*a):
    r = run("gh", *a)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:200])
    return json.loads(r.stdout or "null")


def log(gate, reason, **extra):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps({"at": time.time(), "gate": gate, "cwd": cwd, "reason": reason, **extra}) + "\n")
    except OSError:
        pass


def deny(gate, reason, **extra):
    log(gate, reason, **extra)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    sys.exit(0)


def warn(text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}))
    sys.exit(0)


def body_of_create():
    m = re.search(r"--body-file[\s=]+(['\"]?)(\S+?)\1(\s|$)", cmd)
    if m:
        try:
            return open(os.path.join(cwd, os.path.expanduser(m.group(2)))).read()
        except OSError:
            return ""
    m = re.search(r"(--body|-b)[\s=]+(['\"])(.*?)\2", cmd, re.S)
    return m.group(3) if m else ""


def kind_of(i):
    return ado(f"wit/workitems/{i}?fields=System.WorkItemType,System.Parent&api-version=7.1")["fields"]


def claim_guard():
    body = body_of_create()
    ids = {int(x) for x in re.findall(r"AB#(\d+)", body)}
    if not ids:
        return
    no_spec = re.search(r"^\s*No spec:\s*(\S.*)$", body, re.M | re.I)
    repo = gh("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]
    branch = run("git", "branch", "--show-current").stdout.strip()
    for i in sorted(ids):
        f = kind_of(i)
        chain, kind = [i], f["System.WorkItemType"]
        if kind == "Task" and f.get("System.Parent"):  # a Task is specced on its own or through its parent
            chain.append(f["System.Parent"])
            kind = kind_of(f["System.Parent"])["System.WorkItemType"]
        if not any(spec_approved(x) for x in chain):
            if no_spec and kind == "Bug":
                log("no-spec", no_spec.group(1).strip(), ab=i)
            else:
                deny("claim", f"AB#{i} has no approved spec (ADO state doesn't count). Run /factory grill {chain[-1]} "
                              f"and /factory approve {chain[-1]} first, or drop AB#{i} from the body. For a Bug "
                              "(a hotfix too) that skips the grill, add a 'No spec: <reason>' line to the body.", ab=i)
        others = [p for p in gh("pr", "list", "-R", repo, "--state", "open", "--search", f"AB#{i}",
                                "--json", "number,headRefName,body")
                  if p["headRefName"] != branch and re.search(rf"AB#{i}\b", p["body"] or "")]
        if others:
            deny("claim", f"AB#{i} is already claimed by open PR #{others[0]['number']} in {repo}. "
                          "One PR per Task per repo: stack on it or take a different Task.", ab=i)


def ready_gate():
    m = re.search(r"gh\s+pr\s+ready\s+(\S+)", cmd)
    arg = [m.group(1)] if m and not m.group(1).startswith("-") else []
    if re.search(r"\s--undo\b", cmd):
        return
    pr = gh("pr", "view", *arg, "--json", "number,url,body,baseRefName,headRefName,commits,files,comments,reviews")
    body, files = pr["body"] or "", [f["path"] for f in pr["files"]]
    fails = []

    if not re.search(r"^\s*No test:\s*\S", body, re.M | re.I):
        base = pr["baseRefName"]
        run("git", "fetch", "-q", "origin", base)
        shas = run("git", "rev-list", "--reverse", f"origin/{base}..HEAD").stdout.split()
        first_test = first_code = None
        for n, sha in enumerate(shas):
            changed = run("git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha).stdout.split()
            if first_test is None and any(TEST_FILE.search(p) for p in changed):
                first_test = n
            if first_code is None and any(UI_FILE.search(p) or re.search(r"\.(tsx?|jsx?|py|java|kt|dart)$", p)
                                          for p in changed if not TEST_FILE.search(p)):
                first_code = n
        if first_code is not None and (first_test is None or first_test > first_code):
            fails.append("Test first: no commit touches a test before (or with) the first code commit. "
                         "Add a 'No test: <reason>' line to the PR body if this change deserves none.")

    if any(UI_FILE.search(p) for p in files) and "<img" not in body and not re.search(r"^\s*No UI change:\s*\S", body, re.M | re.I):
        fails.append("UI evidence: the PR touches UI files but its body has no screenshot. Add them per the "
                     "pr-evidence playbook, or a 'No UI change: <reason>' line.")

    if not (any(p.startswith("docs/") or p.endswith(".md") for p in files)
            or re.search(r"not doc-affecting|docs?:\s*none", body, re.I)):
        fails.append("Docs verdict: no docs changed and the body doesn't say 'not doc-affecting'. Run update-docs.")

    if fails:
        deny("ready", f"Ready gate refused PR #{pr['number']}:\n- " + "\n- ".join(fails), pr=pr["url"])


def own_task():
    """factory start names a build workspace <task>-<slug>, so the path holds the worker's Task id."""
    m = re.search(r"/orca/workspaces/(?!flexii_documents/)[^/]+/(\d+)-[^/]+", cwd)
    return int(m.group(1)) if m else None


def worker_deny_list():
    if tool == "Bash":
        m = re.search(START + r"git\s+(-C\s+\S+\s+)?push\b([^;&|\n]*)", cmd)
        words = m.group(2).split() if m else []
        if any(w.startswith(("--force", "-f")) or w.startswith("+") for w in words):
            refs = [w for w in words if not w.startswith("-")]
            if len(refs) < 2 or any(r.lstrip("+").split(":")[-1] in SHARED for r in refs[1:]):
                deny("worker", "Build workers never force-push a shared branch, or force-push without naming the branch. "
                               "Push your own branch by name: git push --force-with-lease origin <branch>.")
    elif re.match(r"mcp__.*[Ss]lack__slack_(send|schedule|create|update)", tool):
        deny("worker", "Build workers don't post to Slack. Put it in the PR or the report and let Kenas send it.")
    elif tool == "mcp__azure-devops__wit_work_item_write":
        own = own_task()
        if inp.get("action") in ("create", "add_child"):
            deny("worker", "Build workers don't create ADO items. To split your slice: add each new slice as a numbered "
                           "'**title**' line under ## Slices (under '### <repo>' when the note has several repos) in the "
                           "spec note, ~/Documents/flexii/flexii_documents/specs/<pbi>-*.md, commit it there, and list them "
                           "under Blocked on me. Kenas reruns factory start <pbi>, which creates a Task per new slice.", ab=own)
        edits = [(inp.get("id"), u.get("path", "")) for u in inp.get("updates") or []] \
            + [(u.get("id"), u.get("path", "")) for u in inp.get("batchUpdates") or []]
        targets = {int(i or 0) for i, p in edits if p.startswith(SPEC_FIELDS)}
        if targets and (targets != {own} or kind_of(own)["System.WorkItemType"] != "Task"):
            deny("worker", f"Title, description and AC are yours to edit on your own Task {own} only. The parent "
                           "PBI's or Bug's belong to Kenas: raise the change in your report.", ab=own)


try:
    if tool == "mcp__azure-devops__wit_work_item_comment_write" and APPROVAL.lower() in (inp.get("text") or "").lower():
        deny("approval", "Only factory.py approve posts the spec approval. Ask Kenas to run /factory approve <id>.")
    if own_task():
        worker_deny_list()
    if re.search(START + r"gh\s+pr\s+create\b", cmd):
        claim_guard()
    elif re.search(START + r"gh\s+pr\s+ready\b", cmd):
        ready_gate()
except SystemExit:
    raise
except Exception as e:  # a gate that can't reach ADO or GitHub warns rather than wedging the session
    warn(f"factory-gate could not run ({e}). Check the claim and ready-gate conditions by hand before going on.")

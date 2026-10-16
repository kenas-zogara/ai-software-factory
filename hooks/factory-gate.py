#!/usr/bin/env python3
# Factory gates, PreToolUse. Design: https://claude.ai/artifact/FsrvLPEKuiQBXjLQMiNqFE
#   gh pr create  claim guard: every AB# in the body is past the spec gate, and no other open PR in this repo claims it
#   gh pr ready   ready gate: test-first or "No test:", an eval newer than the last commit, UI evidence, a docs verdict
#   build worker  deny list for sessions in an Orca build workspace (<id>-<slug>): no force-push to a shared
#                 branch, no Slack posts, no new ADO items or spec-field edits
# Every block is appended to ~/.claude/factory/gate-log.jsonl; merge metrics read it.
import json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.expanduser("~/.claude/skills/factory"))
data = json.load(sys.stdin)
tool, inp = data.get("tool_name", ""), data.get("tool_input") or {}
cwd = data.get("cwd") or os.getcwd()
cmd = inp.get("command", "") if tool == "Bash" else ""
# A heredoc body is data, not a command (same rule as gh-pr-guard.sh).
cmd = re.sub(r"<<-?\s*(['\"]?)(\w+)\1.*?\n\s*\2\b", "", cmd, flags=re.S)
START = r"(?:^|[;&|(]\s*|\n\s*)"
LOG = os.path.expanduser("~/.claude/factory/gate-log.jsonl")
EVAL_MARK = "<!-- factory:eval -->"
PRE_SPEC = {"New", "Removed"}
SHARED = {"dev", "main", "master", "staging", "production"}
SPEC_FIELDS = ("/fields/System.Title", "/fields/Microsoft.VSTS.Common.AcceptanceCriteria")
TEST_FILE = re.compile(r"(\.(test|spec)\.[jt]sx?$|(^|/)test_[^/]+\.py$|Test\.(java|kt)$|_test\.dart$|/__tests__/)")
UI_FILE = re.compile(r"\.(tsx|jsx|css|scss|dart)$")


def run(*a):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, timeout=60)


def gh(*a):
    r = run("gh", *a)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:200])
    return json.loads(r.stdout or "null")


def deny(gate, reason, **extra):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps({"at": time.time(), "gate": gate, "cwd": cwd, "reason": reason, **extra}) + "\n")
    except OSError:
        pass
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


def claim_guard():
    from sync import ado
    ids = {int(x) for x in re.findall(r"AB#(\d+)", body_of_create())}
    if not ids:
        return
    repo = gh("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]
    branch = run("git", "branch", "--show-current").stdout.strip()
    for i in sorted(ids):
        f = ado(f"wit/workitems/{i}?fields=System.State,System.WorkItemType,System.Parent&api-version=7.1")["fields"]
        spec = f
        if f["System.WorkItemType"] == "Task" and f.get("System.Parent"):
            spec = ado(f"wit/workitems/{f['System.Parent']}?fields=System.State&api-version=7.1")["fields"]
        if spec["System.State"] in PRE_SPEC:
            deny("claim", f"AB#{i}'s spec is {spec['System.State']}, so it hasn't passed the spec gate. "
                          f"Run /factory grill and /factory approve on it first, or drop AB#{i} from the body.", ab=i)
        others = [p for p in gh("pr", "list", "-R", repo, "--state", "open", "--search", f"AB#{i}",
                                "--json", "number,headRefName") if p["headRefName"] != branch]
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

    last_commit = max((c.get("committedDate") or "" for c in pr["commits"]), default="")
    evals = [c.get("createdAt") or c.get("submittedAt") or "" for c in pr["comments"] + pr["reviews"]
             if EVAL_MARK in (c.get("body") or "")]
    if not any(t > last_commit for t in evals):
        fails.append(f"Eval: no eval comment (marked {EVAL_MARK}) newer than the last commit. Run the eval "
                     "playbook from a fresh agent and post it on the PR.")

    if any(UI_FILE.search(p) for p in files) and "<img" not in body and not re.search(r"^\s*No UI change:\s*\S", body, re.M | re.I):
        fails.append("UI evidence: the PR touches UI files but its body has no screenshot. Add them per the "
                     "pr-evidence playbook, or a 'No UI change: <reason>' line.")

    if not (any(p.startswith("docs/") or p.endswith(".md") for p in files)
            or re.search(r"not doc-affecting|docs?:\s*none", body, re.I)):
        fails.append("Docs verdict: no docs changed and the body doesn't say 'not doc-affecting'. Run update-docs.")

    if fails:
        deny("ready", f"Ready gate refused PR #{pr['number']}:\n- " + "\n- ".join(fails), pr=pr["url"])


def in_build_workspace():
    return bool(re.search(r"/orca/workspaces/(?!flexii_documents/)[^/]+/\d+-[^/]+", cwd))


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
        if inp.get("action") in ("create", "add_child"):
            deny("worker", "Build workers don't create ADO items. The foreman (factory start) creates the Task.")
        paths = [u.get("path", "") for u in (inp.get("updates") or []) + (inp.get("batchUpdates") or [])]
        if any(p.startswith(SPEC_FIELDS) for p in paths):
            deny("worker", "The spec's title and acceptance criteria belong to Kenas. Raise the change in your report.")


try:
    if in_build_workspace():
        worker_deny_list()
    if re.search(START + r"gh\s+pr\s+create\b", cmd):
        claim_guard()
    elif re.search(START + r"gh\s+pr\s+ready\b", cmd):
        ready_gate()
except SystemExit:
    raise
except Exception as e:  # a gate that can't reach ADO or GitHub warns rather than wedging the session
    warn(f"factory-gate could not run ({e}). Check the claim and ready-gate conditions by hand before going on.")

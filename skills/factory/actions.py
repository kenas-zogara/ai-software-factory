#!/usr/bin/env python3
"""Board buttons. Factory Orca Link.app hands every factory-orca://<action>?... URL to open-orca.sh, which runs this.

  open?path=P              focus the workspace's agent terminal
  approve?id=N, start?id=N factory.py approve|start (the board confirms first)
  review?url=U&kind=ai|e2e a fresh agent reviews the PR: in its build workspace when it's mine and has one,
                           otherwise in a review-<n> workspace on the PR head
  remove?path=P            orca worktree rm, refused while the workspace holds local-only work
  hide?key=K&head=H&req=R  set a card aside until its head commit or review request changes
  unhide?key=K

Runs detached with no terminal, so results and refusals go to ~/.claude/factory/actions.log.
Every action ends with a sync, so the card moves at once."""
import json, os, re, subprocess, sys, time, urllib.parse

os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/Applications/Orca.app/Contents/Resources/bin:" + os.environ.get("PATH", "")
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from sync import FINDINGS, HIDDEN, LIVE, OUT, gh_json, sh  # noqa: E402

HERE = os.path.dirname(os.path.realpath(__file__))
REPOS = os.path.expanduser("~/Documents/flexii")
LOG = os.path.join(OUT, "actions.log")


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%F %T')} {msg}\n")


def worktrees():
    return json.loads(sh("orca", "worktree", "list", "--json"))["result"]["worktrees"]


def focus(path):
    ts = json.loads(sh("orca", "terminal", "list", "--worktree", f"path:{path}", "--json"))["result"]["terminals"]
    ts.sort(key=lambda t: not t.get("agentIdentity"))
    subprocess.run(["open", "-a", "Orca"])
    if ts:
        sh("orca", "terminal", "switch", "--terminal", ts[0]["handle"])


def factory(cmd, i):
    r = subprocess.run(["/usr/bin/python3", os.path.join(HERE, "factory.py"), cmd, i], capture_output=True, text=True, timeout=600)
    out = (r.stdout.strip() or r.stderr.strip()).splitlines()
    log(out[-1] if out else f"{cmd} {i} finished")
    return r.returncode == 0


def findings_path(owner, repo, n):
    return os.path.join(FINDINGS, f"{owner}-{repo}-{n}.md")


def review_prompt(url, pr, mine, kind, findings):
    e2e = (" Then run the e2e review: e2e-audit for the specs this diff affects, run those Playwright specs against this "
           "workspace's own dev server (its port band, the dev API), qa-test on the changed behaviour, and ui-ux-test if "
           "UI files changed. Report missing coverage; write no new specs.") if kind == "e2e" else ""
    if mine:
        return (f"/kenas-mode You are a fresh reviewer of my PR {url}; you did not write it. Run the eval playbook on it.{e2e} "
                "Post the findings on the PR per pr-evidence, including the verdict comment whose first line is "
                "<!-- factory:eval -->. Change no code.")
    return (f"/kenas-mode Review {pr['author']['login']}'s PR {url}: run pr-review, then the eval playbook.{e2e} "
            f"Post NOTHING to GitHub yet. Write the findings to {findings}, then stop and wait. Kenas reads them here and "
            "says post (or edits them) first. Only then post them as one review.")


def review(url, kind):
    owner, repo, n = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url).groups()
    pr = gh_json("pr", "view", url, "--json", "number,title,author,headRefName")
    mine = pr["author"]["login"] == sh("gh", "api", "user", "--jq", ".login").strip()
    wts = worktrees()
    name = f"review-{n}"
    wt = (mine and next((w for w in wts if w["branch"].removeprefix("refs/heads/") == pr["headRefName"]), None)) \
        or next((w for w in wts if w.get("displayName") == name and (w.get("comment") or "").startswith("review:")), None)
    if wt:
        for m in os.listdir(LIVE) if os.path.isdir(LIVE) else []:
            if json.load(open(os.path.join(LIVE, m))).get("cwd", "").startswith(wt["path"]):
                return focus(wt["path"])  # a review is already running here
        sh("orca", "agent", "launch", "--agent", "claude", "--worktree", f"path:{wt['path']}", "--focus",
           "--prompt", review_prompt(url, pr, mine, kind, findings_path(owner, repo, n)))
        return log(f"{kind} review started on #{n} in {wt.get('displayName') or wt['path']}")
    local = os.path.join(REPOS, repo)
    sh("git", "-C", local, "fetch", "-q", "origin", f"+pull/{n}/head:refs/remotes/origin/pr/{n}")
    sh("orca", "worktree", "create", "--repo", f"path:{local}", "--name", name, "--no-parent",
       "--base-branch", f"origin/pr/{n}", "--setup", "run", "--activate", "--comment", f"review: {url}",
       "--agent", "claude", "--prompt", review_prompt(url, pr, mine, kind, findings_path(owner, repo, n)), "--json")
    log(f"{kind} review started on #{n} in a new workspace")


def remove(path):
    git = lambda *a: subprocess.run(["git", "-C", path, *a], capture_output=True, text=True).stdout.strip()
    dirty = git("status", "--porcelain")
    unpushed = git("log", "--oneline", "HEAD", "--not", "--remotes")
    if dirty or unpushed:
        why = "uncommitted changes" if dirty else f"{len(unpushed.splitlines())} unpushed commit(s)"
        return log(f"Not removed: {os.path.basename(path)} has {why}.")
    sh("orca", "worktree", "rm", "--worktree", f"path:{path}")
    log(f"Removed {os.path.basename(path)}")


def set_hidden(key, entry):
    try:
        hidden = json.load(open(HIDDEN))
    except (OSError, ValueError):
        hidden = {}
    if entry is None:
        hidden.pop(key, None)
    else:
        hidden[key] = entry
    json.dump(hidden, open(HIDDEN, "w"), indent=1)


def main(url):
    u = urllib.parse.urlparse(url)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
    action = u.netloc or u.path.strip("/")
    log(url)
    try:
        if action == "open":
            focus(q["path"])
        elif action in ("approve", "start"):
            factory(action, q["id"])
        elif action == "review":
            review(q["url"], q.get("kind", "ai"))
        elif action == "remove":
            remove(q["path"])
        elif action == "hide":
            set_hidden(q["key"], {"head": q.get("head", ""), "req": q.get("req", ""), "at": time.time()})
        elif action == "unhide":
            set_hidden(q["key"], None)
        else:
            log(f"Unknown board action: {action}")
    except Exception as e:
        log(f"  failed: {e}")
        log(f"{action} failed: {str(e)[:120]}")
    if action != "open":
        subprocess.run(["/usr/bin/python3", os.path.join(HERE, "sync.py")], capture_output=True, timeout=300)


if __name__ == "__main__":
    main(sys.argv[1])

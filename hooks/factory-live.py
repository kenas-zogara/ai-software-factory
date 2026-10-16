#!/usr/bin/env python3
# Factory board live updates. AI review is only knowable from inside the session
# running it, so the session leaves a marker the board reads:
#   PreToolUse Skill(review|pr-review|code-review) or a `cat` of the eval playbook -> live/<session>.json
#   Stop -> marker removed (the review turn is over)
# Those events, plus gh pr create/ready/merge and orca worktree create/rm, kick a
# background sync so the board moves within seconds instead of waiting for launchd.
import json, os, re, subprocess, sys, time

LIVE = os.path.expanduser("~/.claude/factory/live")
SYNC = os.path.expanduser("~/.claude/skills/factory/sync.py")
LOCK = os.path.expanduser("~/.claude/factory/sync.lock")
REVIEW_SKILLS = {"review", "pr-review", "code-review"}
MOVES = re.compile(r"\bgh pr (create|ready|merge)\b|\borca worktree (create|rm)\b")

data = json.load(sys.stdin)
event, tool = data.get("hook_event_name"), data.get("tool_name")
inp = data.get("tool_input") or {}
marker = os.path.join(LIVE, f"{data.get('session_id', 'unknown')}.json")
cwd = data.get("cwd") or os.getcwd()


def kick_sync():
    try:
        if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 60:
            return
        open(LOCK, "w").close()
        subprocess.Popen(["/bin/sh", "-c", f"python3 '{SYNC}' >/dev/null 2>&1; rm -f '{LOCK}'"],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


try:
    if event == "PreToolUse" and (
        (tool == "Skill" and inp.get("skill") in REVIEW_SKILLS)
        or (tool == "Bash" and "playbooks/eval.md" in inp.get("command", ""))
    ):
        branch = subprocess.run(["git", "-C", cwd, "branch", "--show-current"],
                                capture_output=True, text=True).stdout.strip()
        if branch:
            os.makedirs(LIVE, exist_ok=True)
            json.dump({"branch": branch, "cwd": cwd, "stage": "ai-review", "at": time.time()}, open(marker, "w"))
            kick_sync()
    elif event == "Stop" and os.path.exists(marker):
        os.remove(marker)
        kick_sync()
    elif event == "PostToolUse" and tool == "Bash" and MOVES.search(inp.get("command", "")):
        kick_sync()
except Exception:
    pass  # the board is a view; it must never break a session
sys.exit(0)

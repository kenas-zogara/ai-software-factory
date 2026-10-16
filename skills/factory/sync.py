#!/usr/bin/env python3
"""Factory sync: derive every card's column from ADO, GitHub, Orca and the live
markers sessions leave, then write ~/.claude/factory/board.html and board.json.
Nothing stores a column; each run recomputes it. The only writes: the needs-be PR label and a
worker wake-up when a BE dependency reaches dev (be_watch), and one build-log line per merged PR
in its spec note (record_merges)."""
import base64, functools, glob, html, json, os, re, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

OUT = os.path.expanduser("~/.claude/factory")
LIVE = os.path.join(OUT, "live")
VAULT = os.path.expanduser("~/Documents/flexii/flexii_documents")
SPEC_WTS = os.path.expanduser("~/orca/workspaces/flexii_documents")
ADO = "https://dev.azure.com/Flexii/Flexii"
TEAM_ITER = "[Flexii]\\Flexii Team"
STALL_HOURS = 6
NOW = datetime.now(timezone.utc)

COLUMNS = [
    ("to-grill", "To grill", "spec"),
    ("grilling", "Grilling", "spec"),
    ("awaiting-approval", "Awaiting approval", "spec"),
    ("ready", "Approved", "spec"),
    ("building", "Building", "build"),
    ("ai-review", "AI review", "build"),
    ("human-review", "Human review", "build"),
    ("merged", "Merged to dev", "build"),
    ("to-review", "To review", "review"),
    ("ai-reviewing", "AI reviewing", "review"),
    ("findings-ready", "Findings ready", "review"),
    ("reviewed", "Reviewed", "review"),
]
# Cards here open their Orca workspace via ~/Applications/Factory Orca Link.app (open-orca.sh -> actions.py).
ORCA_COLUMNS = {"building", "ai-review", "ai-reviewing", "findings-ready"}
NEEDS_YOU = {"awaiting-approval", "human-review", "findings-ready"}
FINDINGS = os.path.join(OUT, "findings")
HIDDEN = os.path.join(OUT, "hidden.json")
NOTIFIED = os.path.join(OUT, "notified.json")
EVAL_MARK = "<!-- factory:eval -->"
# ADO state -> column, for an item no PR or workspace speaks for.
ADO_COLUMN = {"Approved": "ready", "Committed": "ready", "In Progress": "building",
              "PR Review": "human-review", "Testing": "merged", "Pending Deployment": "merged"}


def sh(*cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError(f"{' '.join(cmd[:3])}: {(r.stderr.strip() or r.stdout.strip())[:200]}")
    return r.stdout


def gh_json(*args):
    return json.loads(sh("gh", *args) or "null")


def ado(path, body=None, patch=False):
    pat = sh("security", "find-generic-password", "-a", os.environ["USER"], "-s", "ado-pat", "-w").strip()
    req = urllib.request.Request(f"{ADO}/_apis/{path}", data=json.dumps(body).encode() if body else None,
                                 method="PATCH" if patch else None)
    req.add_header("Authorization", "Basic " + base64.b64encode(f":{pat}".encode()).decode())
    req.add_header("Content-Type", "application/json-patch+json" if patch else "application/json")
    for attempt in range(3):  # resets and timeouts are transient; one blip should not drop every ADO card
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def ab_ids(text):
    return {int(x) for x in re.findall(r"AB#(\d+)", text or "")}


def age(t):
    if not t:
        return ""
    if isinstance(t, str):
        t = datetime.fromisoformat(t.replace("Z", "+00:00"))
    h = (NOW - t).total_seconds() / 3600
    return f"{int(h)}h" if h < 48 else f"{int(h / 24)}d"


def spec_note_paths():
    """Notes on the vault's main, then on each spec-<id> branch: a grill's note reaches main only at approve."""
    return glob.glob(os.path.join(VAULT, "specs", "*.md")) + glob.glob(os.path.join(SPEC_WTS, "spec-*", "specs", "*.md"))


def fetch_ado():
    # Specs are usually groomed in the backlog, so a spec note or workspace pulls its item in from any iteration.
    spec_ids = {m.group(1) for p in spec_note_paths() if (m := re.match(r"(\d+)-", os.path.basename(p)))}
    spec_ids |= {m.group(1) for p in glob.glob(os.path.join(SPEC_WTS, "spec-*")) if (m := re.search(r"spec-(\d+)$", p))}
    in_spec = f" OR [System.Id] IN ({','.join(sorted(spec_ids))})" if spec_ids else ""
    wiql = {"query": (
        "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = 'Flexii' "
        "AND [System.AssignedTo] = @Me "
        f"AND ([System.IterationPath] = @CurrentIteration('{TEAM_ITER}'){in_spec}) "
        "AND [System.WorkItemType] IN ('Product Backlog Item', 'Bug', 'Task') "
        "AND [System.State] NOT IN ('Done', 'Removed')")}
    ids = [w["id"] for w in ado("wit/wiql?api-version=7.1", wiql)["workItems"]]
    fields = "System.Id,System.Title,System.WorkItemType,System.State,System.Parent"
    items = {}
    for i in range(0, len(ids), 200):
        chunk = ",".join(map(str, ids[i:i + 200]))
        for w in ado(f"wit/workitems?ids={chunk}&fields={fields}&api-version=7.1")["value"]:
            f = w["fields"]
            items[w["id"]] = {"id": w["id"], "title": f["System.Title"], "type": f["System.WorkItemType"],
                              "state": f["System.State"], "parent": f.get("System.Parent")}
    return items


def fetch_prs():
    since = (NOW - timedelta(days=7)).date().isoformat()
    found = gh_json("search", "prs", "--author=@me", "--state=open", "--json", "repository", "--limit", "100")
    found += gh_json("search", "prs", "--author=@me", "--merged", f"--merged-at=>={since}",
                     "--json", "repository", "--limit", "100")
    fields = "number,title,url,isDraft,body,labels,headRefName,headRefOid,updatedAt,mergedAt"
    prs, merged = [], []
    for repo in sorted({p["repository"]["nameWithOwner"] for p in found}):
        for state, bucket, extra in (("open", prs, []), ("merged", merged, ["--search", f"merged:>={since}"])):
            for p in gh_json("pr", "list", "--repo", repo, "--author", "@me", "--state", state,
                             "--json", fields, "--limit", "50", *extra):
                p["repo"] = repo.split("/")[1]
                bucket.append(p)
    for p in prs:  # per PR: comments and commits on a 50-PR list query exceed GitHub's GraphQL node limit
        p.update(gh_json("pr", "view", p["url"], "--json", "comments,commits"))
    return prs, merged


def fetch_reviews(orca):
    """Others' open PRs that request my review, plus any PR a review-<n> workspace was opened for."""
    me = sh("gh", "api", "user", "--jq", ".login").strip()
    urls = {p["url"] for p in gh_json("search", "prs", "--review-requested=@me", "--state=open", "--json", "url", "--limit", "50")}
    urls |= {m.group(1) for w in orca.values() if (m := re.match(r"review: (\S+)", w.get("comment") or ""))}
    out = []
    for u in sorted(urls):
        p = gh_json("pr", "view", u, "--json", "number,title,url,author,state,isDraft,headRefOid,updatedAt,commits,reviews,reviewRequests")
        if p["author"]["login"] == me:
            continue
        p["repo"] = u.split("/")[4]
        p["requested"] = any((r.get("login") or "") == me for r in p["reviewRequests"])
        p["mine_at"] = max((r.get("submittedAt") or "" for r in p["reviews"] if (r.get("author") or {}).get("login") == me), default="")
        out.append(p)
    return out


def last_commit_at(p):
    return max((c.get("committedDate") or "" for c in p.get("commits") or []), default="")


def fetch_orca():
    try:
        wts = json.loads(sh("orca", "worktree", "list", "--json"))["result"]["worktrees"]
    except Exception:
        return {}
    return {w["branch"].removeprefix("refs/heads/"): w for w in wts if not w.get("isMainWorktree")}


def live_markers():
    """A session running a review leaves live/<id>.json naming its branch; its Stop hook removes it."""
    out = {}
    for p in glob.glob(os.path.join(LIVE, "*.json")):
        try:
            m = json.load(open(p))
            out[m["branch"]] = m
        except (OSError, ValueError, KeyError):
            pass
    return out


def spec_note(item_id):
    wt = glob.glob(os.path.join(SPEC_WTS, f"spec-{item_id}", "specs", f"{item_id}-*.md"))
    notes = wt or glob.glob(os.path.join(VAULT, "specs", f"{item_id}-*.md"))
    return open(notes[0]).read() if notes else None


def has_decisions(note):
    m = re.search(r"^##\s+Decisions\s*$(.*?)(?=^##\s|\Z)", note, re.M | re.S)
    return bool(m and re.search(r"^\s*[-*\d]", m.group(1), re.M))


def open_questions(note):
    m = re.search(r"^##\s+Open questions\s*$(.*?)(?=^##\s|\Z)", note, re.M | re.S)
    return bool(m and re.search(r"^\s*[-*\d]", m.group(1), re.M))


def idle_flag(wt):
    ts = wt.get("lastActivityAt")
    if not ts:
        return []
    t = datetime.fromtimestamp(ts / 1000, timezone.utc)
    return [f"idle {age(t)}"] if NOW - t >= timedelta(hours=STALL_HOURS) else []


def build_cards(items, prs, merged, orca, live, reviews=()):
    cards, claimed = [], set()
    live_paths = {m["cwd"] for m in live.values()}

    def claim(ids):
        claimed.update(ids)
        claimed.update(items[i]["parent"] for i in ids if i in items and items[i]["parent"])

    seen_branches = {p["headRefName"] for p in prs + merged}
    for p in prs:
        ids = ab_ids(p.get("body"))
        claim(ids)
        wt = orca.get(p["headRefName"])
        evaled = any(EVAL_MARK in (c.get("body") or "") and (c.get("createdAt") or "") > last_commit_at(p)
                     for c in p.get("comments") or [])
        cards.append({"col": "ai-review" if p["headRefName"] in live else "human-review",
                      "title": p["title"], "url": p["url"], "repo": p["repo"], "pr": p["number"], "ab": sorted(ids),
                      "flags": (["draft"] if p["isDraft"] else []) + (["eval ✓"] if evaled else [])
                      + [l["name"] for l in p.get("labels") or []], "head": p.get("headRefOid", ""),
                      "workspace": wt["displayName"] if wt else None, "wt": wt["path"] if wt else None, "age": age(p["updatedAt"])})
    for p in merged:
        ids = ab_ids(p.get("body"))
        claim(ids)
        cards.append({"col": "merged", "title": p["title"], "url": p["url"], "repo": p["repo"], "pr": p["number"],
                      "ab": sorted(ids), "flags": [], "workspace": None, "age": age(p["mergedAt"])})
    for branch, wt in orca.items():
        name = wt.get("displayName") or branch
        if branch in seen_branches or name.startswith("spec-") or (wt.get("comment") or "").startswith("review:"):
            continue
        ids = ab_ids(wt.get("comment"))
        claim(ids)
        cards.append({"col": "ai-review" if branch in live else "building", "title": name,
                      "url": f"file://{wt['path']}", "repo": wt.get("projectId", "").split("/")[-1] or None,
                      "pr": None, "ab": sorted(ids),"flags": idle_flag(wt), "workspace": name, "wt": wt["path"], "age": ""})
    for it in items.values():
        if it["type"] == "Task" or it["id"] in claimed:
            continue
        if it["state"] == "New":
            note = spec_note(it["id"])
            spec_ws = any(w.get("displayName") == f"spec-{it['id']}" for w in orca.values())
            if note and has_decisions(note) and not open_questions(note):
                col = "awaiting-approval"
            else:
                col = "grilling" if spec_ws or note else "to-grill"
        else:
            col = ADO_COLUMN.get(it["state"], "ready")
        cards.append({"col": col, "title": it["title"], "url": f"{ADO}/_workitems/edit/{it['id']}", "repo": None,
                      "pr": None, "ab": [it["id"]], "flags": [it["type"], it["state"]], "workspace": None, "age": "",
                      "state": it["state"]})
    by_url = {(w.get("comment") or "").removeprefix("review: ").strip(): w for w in orca.values()
              if (w.get("comment") or "").startswith("review:")}
    for p in reviews:
        wt = by_url.get(p["url"])
        last = last_commit_at(p)
        findings = os.path.join(FINDINGS, f"{p['url'].split('/')[3]}-{p['repo']}-{p['number']}.md")
        fresh = os.path.exists(findings) and datetime.fromtimestamp(os.path.getmtime(findings), timezone.utc).isoformat() > last
        if wt and wt["path"] in live_paths:
            col = "ai-reviewing"
        elif p["mine_at"] > last:
            col = "reviewed"
        elif fresh:
            col = "findings-ready"
        else:
            col = "to-review" if p["state"] == "OPEN" else "reviewed"
        flags = (["draft"] if p["isDraft"] else []) + (["requested"] if p["requested"] else []) \
            + ([p["state"].lower()] if p["state"] != "OPEN" else []) + (idle_flag(wt) if wt else [])
        cards.append({"col": col, "title": p["title"], "url": p["url"], "repo": p["repo"], "pr": p["number"], "ab": [],
                      "flags": flags, "author": p["author"]["login"], "head": p["headRefOid"], "req": str(int(p["requested"])),
                      "findings": findings if fresh else None,
                      "workspace": wt["displayName"] if wt else None, "wt": wt["path"] if wt else None, "age": age(p["updatedAt"])})
    return cards


def apply_hidden(cards):
    """Mark set-aside cards hidden; drop the entry once the card's head commit or review request has moved on."""
    try:
        hidden = json.load(open(HIDDEN))
    except (OSError, ValueError):
        hidden = {}
    urls = {c["url"] for c in cards}
    keep = {k: v for k, v in hidden.items() if k not in urls}  # a card missing this sync (a source blip) stays hidden
    for c in cards:
        h = hidden.get(c["url"])
        if not h:
            continue
        new_commit = h["head"] != c.get("head", "")
        new_request = h["req"] != "1" and c.get("req") == "1"
        if not (new_commit or new_request):
            c["hidden"] = age(datetime.fromtimestamp(h["at"], timezone.utc))
            keep[c["url"]] = h
    if keep != hidden:
        json.dump(keep, open(HIDDEN, "w"), indent=1)


def notify_findings(cards):
    """One macOS notification per findings file that becomes ready."""
    try:
        seen = json.load(open(NOTIFIED))
    except (OSError, ValueError):
        seen = {}
    for c in cards:
        if c["col"] == "findings-ready" and c.get("findings"):
            stamp = str(os.path.getmtime(c["findings"]))
            if seen.get(c["url"]) != stamp:
                msg = f"Findings ready on #{c['pr']} {c['title'][:60]}"
                subprocess.run(["osascript", "-e", f"display notification {json.dumps(msg)} with title \"Factory\""])
                seen[c["url"]] = stamp
    json.dump(seen, open(NOTIFIED, "w"))


# file:// blocks fetch, so the page re-injects board.js (written by each sync) and
# swaps only the [data-k] nodes whose markup changed; page and column scroll stay put.
SOFT_REFRESH = """<script>
const KEY = "factory-collapsed";
const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; } };
const collapsed = load();
const SHOW = "factory-show-hidden";
const showing = (() => { try { return JSON.parse(localStorage.getItem(SHOW)) || {}; } catch { return {}; } })();
// Capture phase: a toggle inside <summary> must not fold the lane, and an action asks before it runs.
document.addEventListener("click", e => {
  const t = e.target.closest(".toggle");
  if (t) {
    e.preventDefault(); e.stopPropagation();
    const d = t.closest("details");
    d.dataset.show = d.dataset.show === "1" ? "0" : "1";
    showing[d.dataset.lane] = d.dataset.show === "1";
    try { localStorage.setItem(SHOW, JSON.stringify(showing)); } catch {}
    return;
  }
  const a = e.target.closest("a[data-confirm]");
  if (a && !confirm(a.dataset.confirm)) e.preventDefault();
}, true);
document.querySelectorAll("details[data-lane]").forEach(d => {
  if (showing[d.dataset.lane]) d.dataset.show = "1";
  if (collapsed[d.dataset.lane]) d.open = false;
  d.addEventListener("toggle", () => {
    collapsed[d.dataset.lane] = !d.open;
    try { localStorage.setItem(KEY, JSON.stringify(collapsed)); } catch {}
  });
});
window.applyBoard = inner => {
  const next = document.createElement("template");
  next.innerHTML = inner;
  next.content.querySelectorAll("[data-k]").forEach(n => {
    const cur = document.querySelector(`[data-k="${n.dataset.k}"]`);
    if (!cur) return;
    for (const a of n.attributes) if (cur.getAttribute(a.name) !== a.value) cur.setAttribute(a.name, a.value);
    if (n.children.length && n.querySelector("[data-k]")) return;
    if (cur.innerHTML !== n.innerHTML) cur.innerHTML = n.innerHTML;
  });
};
setInterval(() => {
  document.getElementById("board-js")?.remove();
  const s = document.createElement("script");
  s.id = "board-js";
  s.src = "board.js?t=" + Date.now();
  document.body.append(s);
}, 20000);
</script>"""

LANES = [("spec", "Spec", "Grilled and approved on the ADO item"),
         ("build", "Build", "One PR per repo, stage read from the PR"),
         ("review", "Reviews", "Other people's PRs: requested, or opened for review")]
LANE_OF = {key: lane for key, _, lane in COLUMNS}


def act(label, action, confirm=None, cls="", **q):
    href = html.escape(f"factory-orca://{action}?{urllib.parse.urlencode(q)}")
    ask = f' data-confirm="{html.escape(confirm)}"' if confirm else ""
    return f'<a class="btn act {cls}" href="{href}"{ask}>{label}</a>'


def render(cards, errors):
    def card(c):
        meta = []
        if c["repo"]:
            meta.append(f'<span class="repo">{html.escape(c["repo"])}</span>')
        orca_url = f'factory-orca://open?path={urllib.parse.quote(c["wt"], safe="")}' if c.get("wt") else None
        if c["pr"]:
            meta.append(f'<span>#{c["pr"]}</span>')
        meta += [f'<span>AB#{i}</span>' for i in c["ab"]]
        meta += [f'<span>{html.escape(f.removeprefix("area:"))}</span>' for f in c["flags"] if f.startswith("area:")]
        meta += [f'<span class="flag{" warn" if f.startswith(("needs", "idle", "blocked")) else ""}">'
                 f'{html.escape(f)}</span>' for f in c["flags"] if not f.startswith("area:")]
        lane = LANE_OF[c["col"]]
        ab = c["ab"][0] if c["ab"] else None
        acts = []
        if c.get("hidden"):
            meta.append(f'<span class="flag">hidden {c["hidden"]}</span>')
            acts.append(act("Unhide", "unhide", key=c["url"]))
        else:
            if c["col"] == "awaiting-approval" and ab:
                acts.append(act("Approve", "approve", f"Approve AB#{ab}? It moves to Approved and its spec note lands in the vault.", "go", id=ab))
            if c["col"] == "ready" and c.get("state") == "Approved" and ab:
                acts.append(act("Start", "start", f"Start AB#{ab}? This creates a Task and a build workspace, and moves it to Committed.", "go", id=ab))
            if c["pr"] and c["col"] != "merged":
                acts += [act("AI review", "review", url=c["url"], kind="ai"), act("e2e review", "review", url=c["url"], kind="e2e")]
            if c.get("wt"):
                acts.append(act("Remove", "remove", f"Remove workspace {c.get('workspace') or c['wt']}? It's refused if it holds uncommitted or unpushed work.",
                                "quiet", path=c["wt"]))
            elif lane != "spec" and c["col"] != "merged":  # merged cards age out after 7 days
                acts.append(act("Hide", "hide", None, "quiet", key=c["url"], head=c.get("head", ""), req=c.get("req", "")))
        if orca_url:
            if c["pr"]:
                meta.append(f'<a class="btn" href="{html.escape(c["url"])}" target="_blank" rel="noopener">PR ↗</a>')
            meta.append(f'<a class="btn orca" href="{orca_url}">Orca ↗</a>')
        if c.get("author"):
            meta.append(f'<span>@{html.escape(c["author"])}</span>')
        if c["age"]:
            meta.append(f'<span class="age">{c["age"]}</span>')
        if orca_url and c["col"] in ORCA_COLUMNS:
            hit = f'<a class="hit" href="{orca_url}" aria-label="Open {html.escape(c["title"])} in Orca"></a>'
        else:
            hit = f'<a class="hit" href="{html.escape(c["url"])}" target="_blank" rel="noopener" aria-label="{html.escape(c["title"])}"></a>'
        actions = f'<div class="acts">{"".join(acts)}</div>' if acts else ""
        return (f'<div class="card{" hidden" if c.get("hidden") else ""}">{hit}<div class="t">{html.escape(c["title"])}</div>'
                f'<div class="meta">{"".join(meta)}</div>{actions}</div>')

    lanes, waiting = [], 0
    for lane, name, sub in LANES:
        cols, total, hidden_n = [], 0, 0
        for key, label, phase in COLUMNS:
            if phase != lane:
                continue
            cs = [c for c in cards if c["col"] == key]
            shown = [c for c in cs if not c.get("hidden")]
            total += len(shown)
            hidden_n += len(cs) - len(shown)
            you = key in NEEDS_YOU
            if you:
                waiting += len(shown)
            tag = " · you" if you and shown else ""
            cols.append(f'<section class="col" data-k="col-{key}" data-you="{int(you and bool(shown))}"><h3 data-k="h-{key}"><span>{label}{tag}</span>'
                        f'<b>{len(shown)}</b></h3><div class="cards" data-k="c-{key}">{"".join(map(card, cs)) or "<p class=empty>Empty</p>"}</div></section>')
        toggle = (f'<button type="button" class="toggle" data-k="hid-{lane}" data-n="{hidden_n}">'
                  f'<span class="off">Show hidden ({hidden_n})</span><span class="on">Showing hidden ({hidden_n})</span></button>') if lane != "spec" else ""
        lanes.append(f'<details class="lane {lane}" data-lane="{lane}" open><summary class="lanehead"><svg class="chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg><h2>{name}</h2><p>{sub}</p><i></i>'
                     f'{toggle}<span data-k="n-{lane}">{total}</span></summary><div class="cols">{"".join(cols)}</div></details>')
    err = "".join(f'<p class="err">{html.escape(e)}</p>' for e in errors)
    stamp = datetime.now().strftime("%a %d %b, %H:%M")
    summary = f'<strong>{waiting}</strong> waiting on you' if waiting else "Nothing waiting on you"
    inner = (f'<header><h1>Factory Board</h1><p data-k="sum">{summary}</p><time data-k="stamp">updated {stamp}</time></header>'
             f'<div data-k="err">{err}</div>{"".join(lanes)}')
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Factory Board</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap">
<style>
/* two stacked swimlanes on one shared 4-column grid, so a spec column sits over its build column */
:root{{--bg:#f7f7f5;--surface:#fff;--fg:#18191b;--muted:#74777d;--line:#e6e6e2;--hover:#cfd0cc;
--you:#c2521d;--warn:#b42318;--spec:#3b63c4;--build:#1f8a62;
--sans:"Geist",ui-sans-serif,system-ui,-apple-system,sans-serif;--mono:"Geist Mono",ui-monospace,SFMono-Regular,monospace}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111213;--surface:#1a1b1d;--fg:#ececec;--muted:#93969c;--line:#26282b;--hover:#3a3d41;
--you:#f08a57;--warn:#f47067;--spec:#7d9cf0;--build:#4fc495;color-scheme:dark}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:13px/1.45 var(--sans);-webkit-font-smoothing:antialiased;
padding-block:28px 48px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}}
.wrap{{max-width:1600px;margin:0 auto;display:grid;gap:28px}}
header{{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 20px}}
h1{{margin:0;font-size:18px;font-weight:600;letter-spacing:-.01em}}
header p{{margin:0;color:var(--muted)}}header strong{{color:var(--you);font-weight:600}}
header time{{margin-left:auto;font:12px var(--mono);color:var(--muted)}}
.lane{{display:block}}[data-k="err"]:empty{{display:none}}.lane[open]>.cols{{margin-top:12px}}
.lanehead{{cursor:pointer;list-style:none;user-select:none;display:flex;align-items:center;gap:10px;margin:0 -8px;padding:6px 8px;border-radius:6px;transition:background .12s}}
.lanehead:hover{{background:var(--surface)}}.lanehead:focus-visible{{outline:2px solid var(--spec);outline-offset:2px}}
.lanehead h2{{margin:0;font-size:13px;font-weight:600;display:flex;align-items:center;gap:8px}}
.lanehead::-webkit-details-marker{{display:none}}
.chev{{flex:none;width:16px;height:16px;color:var(--muted);transition:transform .15s}}.lane[open] .chev{{transform:rotate(90deg)}}.lanehead:hover .chev{{color:var(--fg)}}
.lanehead h2::before{{content:"";width:8px;height:8px;border-radius:50%;background:var(--build)}}
.spec .lanehead h2::before{{background:var(--spec)}}
.lanehead p{{margin:0;color:var(--muted)}}.lanehead i{{flex:1;border-bottom:1px dashed var(--line)}}.lanehead span{{font:12px var(--mono);color:var(--muted)}}
.cols{{display:grid;grid-template-columns:repeat(4,minmax(220px,1fr));gap:16px;overflow-x:auto;align-items:start}}
.col{{display:grid;gap:6px;min-width:0;align-content:start}}
.cards{{display:grid;gap:6px;align-content:start;max-height:min(62vh,640px);overflow-y:auto;overscroll-behavior:contain;padding-right:2px;scrollbar-width:thin}}
h3{{margin:0 0 2px;display:flex;align-items:center;gap:8px;padding-bottom:4px;border-bottom:1px solid var(--line);font-size:12px;font-weight:500;color:var(--muted)}}
h3 b{{margin-left:auto;font:500 11px var(--mono)}}
[data-you="1"] h3{{color:var(--you);border-bottom-color:var(--you)}}
.card{{position:relative;display:grid;gap:4px;background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:8px 10px;color:inherit;text-decoration:none;transition:border-color .12s}}
.hit{{position:absolute;inset:0;border-radius:6px}}.card:hover{{border-color:var(--hover)}}.card:has(.hit:focus-visible){{outline:2px solid var(--spec);outline-offset:2px}}.hit:focus{{outline:none}}
[data-you="1"] .card{{box-shadow:inset 2px 0 0 var(--you)}}
.t{{font-weight:500;min-width:0;overflow-wrap:anywhere;text-wrap:pretty}}
.meta{{display:flex;flex-wrap:wrap;align-items:center;gap:4px 6px;font:11px var(--mono);color:var(--muted);font-variant-numeric:tabular-nums}}.repo{{color:var(--fg)}}
.age{{margin-left:auto}}
.flag{{background:var(--bg);border-radius:4px;padding:0 5px}}.flag.warn{{color:var(--warn)}}
.btn{{position:relative;font-weight:500;padding:2px 8px;border-radius:4px;background:var(--bg);color:var(--muted);text-decoration:none;transition:color .12s,background .12s}}
.btn:hover{{color:var(--fg);background:var(--line)}}.btn.orca{{color:var(--build)}}.btn:focus-visible{{outline:2px solid var(--spec);outline-offset:1px}}
.review .lanehead h2::before{{background:var(--you)}}
.acts{{position:relative;display:flex;flex-wrap:wrap;gap:4px;margin-top:2px}}
.act{{font:500 11px var(--mono);border:1px solid var(--line);background:var(--surface);color:var(--fg)}}
.act.go{{border-color:var(--spec);color:var(--spec)}}.act.quiet{{color:var(--muted);margin-left:auto}}
.card.hidden{{background:var(--bg);border-style:dashed;border-color:var(--hover);opacity:.72;box-shadow:none}}
.card.hidden .t{{color:var(--muted)}}
.lane:not([data-show="1"]) .card.hidden{{display:none}}
.toggle{{font:11px var(--mono);color:var(--muted);background:none;border:1px solid var(--line);border-radius:4px;padding:1px 7px;cursor:pointer}}
.toggle:hover{{color:var(--fg);border-color:var(--hover)}}.toggle[data-n="0"]{{display:none}}
.toggle .on,.lane[data-show="1"] .toggle .off{{display:none}}.lane[data-show="1"] .toggle .on{{display:inline}}
.lane[data-show="1"] .toggle{{color:var(--fg);border-color:var(--fg)}}
.empty{{margin:0;padding:12px;border:1px dashed var(--line);border-radius:6px;color:var(--muted);text-align:center;font-size:12px}}
.err{{margin:0;color:var(--warn);font:12px var(--mono)}}
@media (prefers-reduced-motion:reduce){{.card,.chev,.lanehead,.btn{{transition:none}}}}
</style></head><body><div class="wrap">{inner}</div>{SOFT_REFRESH}</body></html>"""
    return page, inner


BE_STATE = os.path.join(OUT, "be-wait.json")
GATE_LOG = os.path.join(OUT, "gate-log.jsonl")
ON_DEV = {"Testing", "Pending Deployment", "Done"}


def spec_id(i, items):
    """The PBI or Bug an id belongs to: a Task's parent, anything else itself."""
    it = items.get(i)
    if it is None:
        f = ado(f"wit/workitems/{i}?fields=System.WorkItemType,System.Parent&api-version=7.1")["fields"]
        it = {"type": f["System.WorkItemType"], "parent": f.get("System.Parent")}
    return it["parent"] if it["type"] == "Task" and it.get("parent") else i


@functools.cache
def be_refs(pbi):
    desc = ado(f"wit/workitems/{pbi}?fields=System.Description&api-version=7.1")["fields"].get("System.Description") or ""
    lines = re.sub(r"<[^>]+>", "", re.sub(r"</?(div|p|li|br|ul|ol|h\d)\b[^>]*>", "\n", desc))
    m = re.search(r"BE dependency\s*:\s*([^\n]*)", html.unescape(lines), re.I)
    line = m.group(1) if m else ""
    prs = re.findall(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+", line)
    rest = re.sub(r"https?://\S+", "", line)
    return prs + [int(x) for x in re.findall(r"(?:AB#|PBI\s*#?|#)(\d{3,})", rest)]


@functools.cache
def ref_on_dev(ref):
    if isinstance(ref, str):
        return gh_json("pr", "view", ref, "--json", "state")["state"] == "MERGED"
    return ado(f"wit/workitems/{ref}?fields=System.State&api-version=7.1")["fields"]["System.State"] in ON_DEV


def agent_terminal(wt_path):
    ts = json.loads(sh("orca", "terminal", "list", "--worktree", f"path:{wt_path}", "--json"))["result"]["terminals"]
    ts.sort(key=lambda t: not t.get("agentIdentity"))
    return ts[0]["handle"] if ts else None


def be_watch(cards, items):
    """Flag cards whose spec waits on a BE change; on the change reaching dev, drop the label and wake the worker once."""
    try:
        state = json.load(open(BE_STATE))
    except (OSError, ValueError):
        state = {}
    for c in cards:
        if c["col"] not in ("building", "ai-review", "human-review") or not c["ab"]:
            continue
        pbi = str(spec_id(c["ab"][0], items))
        refs = be_refs(int(pbi))
        if not refs:
            continue
        waiting = [r for r in refs if not ref_on_dev(r)]
        repo = c.get("repo") and f"Flexii321/{c['repo']}"
        if waiting:
            c["flags"].append("needs-be")
            if c.get("pr") and not state.get(pbi, {}).get("labelled"):
                sh("gh", "label", "create", "needs-be", "-R", repo, "--color", "d93f0b", "--force")
                sh("gh", "pr", "edit", str(c["pr"]), "-R", repo, "--add-label", "needs-be")
            state[pbi] = {"waiting": True, "labelled": bool(c.get("pr"))}
        elif state.get(pbi, {}).get("waiting"):
            if c.get("pr"):
                sh("gh", "pr", "edit", str(c["pr"]), "-R", repo, "--remove-label", "needs-be")
            if c.get("wt") and (term := agent_terminal(c["wt"])):
                sh("orca", "terminal", "send", "--terminal", term, "--text",
                   f"BE dependency for {pbi} ({', '.join(map(str, refs))}) is on dev now. Continue the build.", "--enter")
            state[pbi] = {"waiting": False}
    json.dump(state, open(BE_STATE, "w"))


def record_merges(merged, items):
    """On merge, append the PR's gate blocks, eval posts and others' review comments to its spec note, once."""
    try:
        gates = [json.loads(l) for l in open(GATE_LOG)]
    except (OSError, ValueError):
        gates = []
    me = sh("gh", "api", "user", "--jq", ".login").strip()
    for p in merged:
        for pbi in {spec_id(i, items) for i in ab_ids(p.get("body"))}:
            notes = glob.glob(os.path.join(VAULT, "specs", f"{pbi}-*.md"))
            if not notes or p["url"] in open(notes[0]).read():
                continue
            repo = f"Flexii321/{p['repo']}"
            blocks = [g for g in gates if g.get("pr") == p["url"] or g.get("ab") in ab_ids(p.get("body"))
                      or f"/{pbi}-" in g.get("cwd", "")]
            convo = gh_json("pr", "view", str(p["number"]), "-R", repo, "--json", "comments,reviews")
            evals = sum("<!-- factory:eval -->" in (c.get("body") or "") for c in convo["comments"])
            inline = gh_json("api", f"repos/{repo}/pulls/{p['number']}/comments")
            others = sum(c["user"]["login"] != me for c in inline) + sum(
                (r.get("author") or {}).get("login") != me for r in convo["reviews"] if r.get("body"))
            kinds = ", ".join(f"{k} {sum(g['gate'] == k for g in blocks)}" for k in ("claim", "ready", "worker")
                              if any(g["gate"] == k for g in blocks)) or "none"
            line = (f"- {p['mergedAt'][:10]}: {p['url']} merged. Gate blocks: {kinds}. Eval posts: {evals}. "
                    f"Review comments from others: {others}.\n")
            text = open(notes[0]).read()
            text = text.rstrip("\n") + ("\n" if "## Build log" in text else "\n## Build log\n") + line
            open(notes[0], "w").write(text)
            sh("git", "-C", VAULT, "commit", "-q", "-m", f"build({pbi}): record #{p['number']}", "--", notes[0])


def main():
    errors, items, prs, merged, orca = [], {}, [], [], {}
    for name, fn in (("ADO", fetch_ado), ("GitHub", fetch_prs), ("Orca", fetch_orca)):
        try:
            res = fn()
            if name == "ADO": items = res
            elif name == "GitHub": prs, merged = res
            else: orca = res
        except Exception as e:  # one dead source must not blank the whole board
            errors.append(f"{name} unavailable: {e}")
    reviews = []
    try:
        reviews = fetch_reviews(orca)
    except Exception as e:
        errors.append(f"Reviews unavailable: {e}")
    cards = build_cards(items, prs, merged, orca, live_markers(), reviews)
    os.makedirs(FINDINGS, exist_ok=True)
    for name, fn, args in (("BE watch", be_watch, (cards, items)), ("Merge metrics", record_merges, (merged, items)),
                           ("Hidden", apply_hidden, (cards,)), ("Notify", notify_findings, (cards,))):
        try:
            fn(*args)
        except Exception as e:
            errors.append(f"{name} failed: {e}")
    json.dump({"generatedAt": NOW.isoformat(), "errors": errors, "cards": cards}, open(f"{OUT}/board.json", "w"), indent=1)
    tmp = f"{OUT}/board.html.tmp"
    page, inner = render(cards, errors)
    open(tmp, "w").write(page)
    os.replace(tmp, f"{OUT}/board.html")
    open(tmp, "w").write(f"applyBoard({json.dumps(inner)});")
    os.replace(tmp, f"{OUT}/board.js")
    print(f"{len(cards)} cards, {len(errors)} errors -> {OUT}/board.html")
    for e in errors:
        print("  " + e, file=sys.stderr)


if __name__ == "__main__":
    main()

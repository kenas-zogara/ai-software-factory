#!/bin/bash
# Handles factory-orca://open?path=<worktree path>: brings Orca to front on that
# worktree's agent terminal (or its first terminal).
ORCA=/Applications/Orca.app/Contents/Resources/bin/orca
path=$(/usr/bin/python3 -c 'import sys,urllib.parse as u;print(u.parse_qs(u.urlparse(sys.argv[1]).query)["path"][0])' "$1") || exit 1
handle=$("$ORCA" terminal list --worktree "path:$path" --json 2>/dev/null | /usr/bin/python3 -c '
import json, sys
ts = json.load(sys.stdin)["result"]["terminals"]
ts.sort(key=lambda t: not t.get("agentIdentity"))
print(ts[0]["handle"] if ts else "")')
open -a Orca
[ -n "$handle" ] && "$ORCA" terminal switch --terminal "$handle" >/dev/null

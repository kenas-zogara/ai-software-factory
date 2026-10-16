#!/bin/bash
# Factory Orca Link.app runs this with every factory-orca:// URL. actions.py does the work.
exec /usr/bin/python3 "$(dirname "$(readlink -f "$0")")/actions.py" "$1"

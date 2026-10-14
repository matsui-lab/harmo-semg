#!/bin/sh
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
/bin/sh bootstrap.sh check
status=$?
printf '\nPress Return to close. '
read answer
exit "$status"

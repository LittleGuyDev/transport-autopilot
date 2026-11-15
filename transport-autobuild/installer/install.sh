#!/bin/sh
cd -- "$(dirname -- "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
    python3 ./install.py "$@"
    status=$?
else
    printf '%s\n' 'Python 3.8 or later is required. Install Python 3 from https://www.python.org/downloads/ and run this installer again.'
    status=1
fi
if [ -t 0 ] && [ "${TA_NO_PAUSE:-}" != 1 ]; then
    printf '\nPress Return to close... '
    read -r answer
fi
exit "$status"

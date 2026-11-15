#!/bin/sh
# Finder entry point; keep the terminal open so failures remain readable.
cd -- "$(dirname -- "$0")" || exit 1
exec /bin/sh ./install.sh "$@"

# Owned payload migration data

`legacy_payloads.json` contains earlier Transport Autopilot renderer and worker
patch sections so the installer can recognize and replace **our own** installed
code. The entries were generated from Transport Autopilot payloads. They do not
contain another mod's payload, a complete game script, or an installer copied
from another author. Small literal game strings are retained as exact patch
anchors.

Some earlier development packages reused version labels. Migration therefore
matches the complete renderer and worker pair, not a displayed version number.
Each entry records a SHA-256 fingerprint of the canonical JSON encoding of
its `app` and `sim` sections. Two entries with the same version remain distinct
when their owned sections differ.

Only the recorded Geoplanetical 1.1.3 payloads are supported by this installer.
Unknown, edited, partial, duplicated, or mixed blocks stop installation before
the game is modified. An exact match lets the installer remove only our owned
patches while preserving unrelated game content and other mods.

Historical validation used disposable copies of local game files. Those files,
campaigns, and diagnostic receipts are not part of this repository.

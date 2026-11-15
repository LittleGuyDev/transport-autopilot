# Original installer implementation

The Python implementation was written for Transport Autopilot. No other game
mod installer was used as a reference. It uses only the Python standard library.

Primary references for the Electron archive format:

- https://github.com/electron/asar/blob/main/README.md
- https://github.com/electron/asar/blob/main/src/pickle.ts
- https://github.com/electron/asar/blob/main/src/disk.ts
- https://github.com/electron/asar/blob/main/src/integrity.ts

The first eight bytes describe the size of the following Pickle header. That
header contains UTF-8 JSON; packed file offsets are relative to the data region.
Changed files use SHA-256 whole-file and block integrity. Unchanged entries and
data bytes are preserved. Replacements append new data and update their entry
offset, allowing unchanged deduplicated entries and unknown metadata to survive.

Platform integration references:

- https://www.electronjs.org/docs/latest/tutorial/asar-integrity
- https://www.electronjs.org/docs/latest/tutorial/fuses
- https://developer.apple.com/documentation/xcode/using-the-latest-code-signature-format

Only our own historical Transport Autopilot payload data is used for upgrades;
no previous installer source or algorithms were used.

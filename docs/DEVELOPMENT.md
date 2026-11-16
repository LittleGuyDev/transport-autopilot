# Development

## Layout and requirements

`transport-autobuild/` contains the simulation planner (`sim.js`), native game adapter (`native-transport.js`), worker activation policy, renderer UI, native mod manifest, independent installer, and tests. `updater/` contains the shared pre-launch update coordinator, release verifier, Steam setup, launcher, and tests. `scripts/` contains standalone build and check entry points; `docs/` contains contributor guidance.

The Python runtime uses only the standard library and supports Python 3.8 or newer. Source tests also need Node.js; use Node.js 22 or newer for the game-independent checks. Optional browser acceptance uses Playwright and Chromium. Signature tests need OpenSSL for ephemeral test keys; players do not need OpenSSL.

## Build

From the repository root:

```sh
python3 scripts/build_unsigned.py
```

This creates the manual-install folder `build/Transport-Autopilot/` and ZIP `build/Transport-Autopilot-1.1.1-unsigned.zip`. It does not modify an installed game. Keep the generated folder together, close the game, then use its normal installer. Contributor builds support **manual updates only**; they contain no updater enrollment or publisher authentication files. Generated files are ignored by Git.

The lower-level command `python3 transport-autobuild/build.py` creates the intermediate `transport-autobuild/dist/`. That intermediate includes updater helpers without a signed manifest and is not an install-ready package; use `scripts/build_unsigned.py` for a contributor build. `--no-automatic` alone does not make incomplete signed-package contents acceptable.

Official automatic-update packages include a signed manifest covering their complete contents. Little Guy Dev keeps the private release signing key outside the repository and packages. The public verification key in `updater/trust.py` is intentionally public. Contributors can build and modify the source; they cannot sign an official Little Guy Dev release. A fork that distributes its own signed updates must establish its own identity and key, rather than impersonating the official publisher or changing an existing user's pinned trust.

## Source checks

Run these from the repository root:

```sh
python3 -m unittest discover -s transport-autobuild/installer -p 'test_*.py'
python3 -m unittest discover -s updater -p 'test_*.py'
node transport-autobuild/test-sim.cjs
node transport-autobuild/test-native-transport.cjs
node transport-autobuild/test-activation.cjs
node transport-autobuild/test-manager-ui.cjs
node transport-autobuild/ui-check.cjs
```

The installer tests use disposable synthetic archives, executable headers, and profiles. Signature tests generate an ephemeral test key; they do not use the publisher key. These checks establish source behavior, not acceptance against every installed game build.

Optional browser acceptance is `node transport-autobuild/test-ui-browser.cjs` after installing Playwright and Chromium. It renders synthetic public state and writes results to a temporary folder; `PLAYWRIGHT_MODULE`, `CHROMIUM_PATH`, and `UI_ACCEPTANCE_OUTPUT` provide explicit overrides.

The manager suite can also check exact game anchors when `GEOPLANETICAL_APP_BUNDLE` and `GEOPLANETICAL_SIM_BUNDLE` point to locally owned bundles. Set `GEOPLANETICAL_MODS_SOURCE` for its optional native backend check. The native engine and construction-over-time harnesses require a compatible installed game. Inspect their arguments and use disposable game copies. Do not publish game files or full bundles. See the [public validation summary](../transport-autobuild/VALIDATION.md).

## Design boundaries

The renderer sends state, configuration, and run requests to the worker. The planner chooses useful work; the native adapter obtains game quotes and starts normal game construction. Activation is captured from the native launch snapshot. Settings persist per campaign and country.

The installer recognizes exact supported patch anchors and owned historical payloads, preserves unrelated content, and uses verified backups and recoverable transactions. The updater authenticates enrolled packages before applying changes and launching the game. Unknown game changes, incomplete packages, or failed verification stop with an error.

Keep the current tested target explicit: Transport Autopilot **1.1.1**, Geoplanetical **1.1.4 / Steam build 26709373**. The payload separately declares an `exact-hooks` compatibility policy with minimum stable game version `1.1.4`. Later stable versions may try the unchanged hooks; all ownership, integrity and unique-match checks still apply. Do not add a future version to the officially tested list without evidence from that build. Signed releases repeat this policy in their authenticated manifest, and enrollment migrates to an immutable runtime that advertises `exact-hooks-v1` while retaining the earlier runtime and trust key. Follow [CONTRIBUTING.md](../CONTRIBUTING.md) for pull requests.

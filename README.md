# Transport Autopilot

Free, open source transport construction automation for **Geoplanetical** by Little Guy Dev.

[![Support Transport Autopilot on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/U7U0R66N6)

Transport Autopilot expands roads, freeways, rail, and useful pipeline and port upgrades you control, within a monthly construction budget and cash reserve. It uses the game's normal costs, materials, construction times, and ownership rules.

- **Build when needed** covers current demand and your chosen spare capacity.
- **Keep expanding** optionally continues selected road and rail construction.
- **Overview, Settings, and Projects** show needs, choices, and work in progress.
- Automation starts off. Settings are saved per campaign and country.

## Install

**[Get Transport Autopilot on Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=3839206386)**

Subscribe, wait for the download, then run the included installer once with the game closed. Subscribing alone does not install the automation code. Python **3.8 or newer** is required. Read the [complete installation, update, and removal guide](transport-autobuild/README.txt).

Current release: **1.1.0**, for **Geoplanetical 1.1.3 / Steam build 26651281**, on Windows and native Mac. See [compatibility and validation](transport-autobuild/VALIDATION.md) for tested behavior and limits. New game versions need a compatibility review.

The `main` branch includes an unreleased Windows updater fix. The existing signed 1.1.0 package predates that fix; publishing this source repository has not changed the Workshop download.

Find the panel in **Transport Room → Build → Transport Autopilot**, **Settings → Game**, or the game's search. Saving settings and starting construction are separate actions. Disabling automation does not cancel existing projects.

## Source and contributions

[Development guide](docs/DEVELOPMENT.md) · [Contributing](CONTRIBUTING.md) · [Changelog](transport-autobuild/CHANGELOG.md) · [Report a bug](https://github.com/LittleGuyDev/transport-autopilot/issues)

The repository contains the automation, user interface, independent installer, and shared updater source. Game files, campaigns, gameplay screenshots, and private signing keys are excluded. You need your own copy of Geoplanetical to run the mod or native game integration checks.

Official release packages carry Little Guy Dev's signature for automatic updates. Community source builds use the manual installer and manual updates; see the development guide. Building the source does not give a community build that publisher identity. The updater checks files already downloaded by Steam or placed in the selected folder; it does not download GitHub releases.

## License and credits

Little Guy Dev's original code and documentation are available under the [MIT License](LICENSE). This does not license Geoplanetical, its game code or assets, third-party brands, or other authors' mods. Small literal game patch anchors identify supported locations; no complete game scripts or game binaries are distributed here.

The installer and updater were independently implemented; their [installer sources](transport-autobuild/installer/SOURCES.md), [legacy payload notes](transport-autobuild/installer/LEGACY-PAYLOADS.md), and [updater provenance](updater/PROVENANCE.md) describe the approach. Transport Autopilot is an unofficial community mod and is not affiliated with Polytale Games.

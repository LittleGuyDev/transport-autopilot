# Current prepared release — 1.1.1 / Geoplanetical 1.1.4

Native Mac acceptance used Geoplanetical 1.1.4, Steam build 26709373. The complete installed mod stack passed a 31-day copied-campaign simulation and save reopen with no critical/reference/type diagnostics. Transport's native engine checks passed; the normal installed Steam game reached its main menu with all 13 installed mod entries active. In a separate app and copied profile, the Transport Room launcher and Overview, Settings and Projects tabs displayed current report, budget and permission data. No real campaign was loaded or advanced by the window checks. All 31 original saves retained exact bytes and modification times.

The installer tests cover later stable versions with unchanged exact hooks, rejection of changed hooks, malformed policies and versions, preservation of strict older packages, ownership and transaction recovery. Updater tests cover signed-policy agreement and immutable runtime-capability migration, including a crash between the Steam launch-line switch and config commit. The public source retains the previously validated Windows native-ACL and explicit UTF-8 child-process fixes.

**Limits:** native Windows 1.1.4 gameplay has not been accepted. Synthetic tests and older Windows installation tests are not a Windows 1.1.4 playthrough. Matching hook text permits a future version to be tried but does not establish semantic compatibility. The official tested target remains 1.1.4 until a later build is actually checked. The 1.1.1 Workshop upload is pending; source publication does not change installed Workshop files.

Current public source checks passed 107 installer tests and 139 updater tests (three native Windows-only checks skipped on Mac), plus 51 simulation tests, 47 adapter checks, activation, manager and UI structure checks. The unsigned contributor package built successfully and its gameplay payload matched the canonical 1.1.1 package exactly.

The records below describe earlier snapshots and retain their original scope.

# Compatibility and validation

Transport Autopilot **1.1.0** targets **Geoplanetical 1.1.3 / Steam build 26651281**, on Windows and native Mac. The installer checks supported game identity, exact patch anchors, owned payloads, and archive integrity. A later game update is not automatically supported.

## Recorded acceptance before the public source import

The following summarizes local validation recorded during development in November 2026. Private campaigns, full game bundles, receipts, and task records are not published in this repository.

| Area | Verified scope and limit |
| --- | --- |
| Planner and native adapter | Source checks cover demand, budgets, cash reserves, ownership, pending work, project limits, and the optional Keep expanding mode. |
| User interface | Synthetic DOM and Chromium checks cover tabs, saved and draft choices, keyboard operation, narrow layouts, and larger text. These are fixture tests, not screenshots of a live campaign. |
| Native Mac engine | Disposable game/campaign copies exercised real project creation, daily processing, save/reopen behavior, activation, and opt-in land expansion. Starting a project is distinct from completing it; separate construction-over-time checks exercised normal completion paths. |
| Mac installation | Copied-game and installed-state checks covered installation, repeated installation, removal, interrupted-operation recovery, archive integrity and signing, plus preservation of unrelated mod code and checked profile files. |
| Windows installation | Windows virtual machine checks covered copied-game installation, removal, recovery, integrity, repeat operations, and preservation. Windows in-game UI and a full desktop playthrough remain unverified. |
| Automatic updates | Source fixtures and disposable native Mac/Windows installations covered authenticated packages, tampered/incomplete/older release refusal, recovery, shared coordination, independent removal, and prevention of unwanted reinstall. |
| Steam setup | Disposable dummy installations checked graceful Steam closure, launch-option persistence, a shared hook, conditional restoration, and preservation of user options. These checks did not establish real-game overlay or Steamworks behavior. |

The current repository's game-independent checks can be reproduced using the [development guide](../docs/DEVELOPMENT.md). Passing those checks does not reproduce all historical native acceptance or establish compatibility with a different game build.

## Practical limits

Normal game costs, materials, time, locations, and ownership remain authoritative. The mod cannot guarantee full delivery through foreign infrastructure, blockades, damage, or unavailable routes. Lowering a budget or disabling automation does not cancel existing construction.

Keep installer backups. After an unsupported game update or an installation failure, retain the exact error and use a compatible release; do not restore an old game backup over a newer game version or remove verification checks.

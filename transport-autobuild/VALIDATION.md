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

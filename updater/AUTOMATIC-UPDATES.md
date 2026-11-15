# Automatic updates for Transport Autopilot and Tourism Dashboard

Run each mod's installer once; it sets up Steam automatically. After that,
ordinary updates downloaded to the selected mod folders are checked and applied
before Steam starts Geoplanetical. This works on Windows and native Mac.

Steam delivers Workshop downloads. The updater checks the files already on your
computer; it does not search the internet or download newer ZIPs.

## First setup

1. Save and close Geoplanetical and any other Steam games. Let Steam finish
   downloading the mod, and sign in to the Steam account you use for this game.
2. Open the mod's Workshop folder. On Windows, double-click `install.bat`; on
   Mac, double-click `install.command`. Check the game location and confirm.
   Before you press Enter/Return, the installer warns that Steam may briefly
   close and reopen to save Geoplanetical's Launch Options.
3. Wait for both installation and automatic-update setup to finish, then start
   Geoplanetical through Steam as usual. No copying or pasting is normally needed.

Setup never force-quits Steam. It waits for a normal shutdown and stops if Steam
cannot close. It validates the selected Steam account and game, keeps a private
backup and recovery record, and changes only Geoplanetical's Launch Options.
Existing plain game flags are kept. Unknown custom launch wrappers or conflicting
accounts are left unchanged; the error explains what needs attention. The mod
may already be installed when setup stops. Close Steam, sign in to the intended
account or resolve your custom options as directed, then run the installer again.
A recognized exact updater line from an earlier manual setup can be adopted.

You need Python 3.8 or newer. Keep the Python installation you used available.
You do not need OpenSSL or a background service, and setup does not ask for
administrator access.

## If you use both mods

Install Transport Autopilot and Tourism Dashboard once each. For the same game,
they share one updater and one Steam Launch Options entry. Setup does not stack
wrappers around each other. Each mod keeps its own registration
and can be updated or removed independently.

The installer also creates a **Play Geoplanetical** shortcut and prints its
location. It checks updates and opens Steam. Keep the Steam Launch Options line
in place when using that shortcut too.

## What happens at the next launch

Wait for Steam downloads to finish, then press Play. The updater verifies the
complete update packages, checks the game, applies any supported changes, and
only then starts the game. It checks files again even if the displayed mod
version has not changed. A launch may take longer when an update needs backups
and installation.

The files must carry Little Guy Dev's release signature and match the release's
recorded contents. Update work uses a private copy for your account, and keeps
the installer's compatibility checks, backups and recovery. Manual installation,
removal and automatic updates take turns so they do not change the game together.

If a download is incomplete, the files are changed, an update fails, or the game
is unsupported, the launch stops with an error. Keep that message when asking
for help. Let an unfinished Steam download complete before trying again. Do not
remove verification or replace game files with old backups to bypass an error.
The error window gives the location of the private `last-error.txt` file for
support. Successful launches do not create a failure log.

## Keep the source folder in place

The updater remembers the mod folder from the first installation. Keep that
folder available and keep its release files together.

- **Workshop:** run the first installer from Steam's downloaded Workshop folder.
  Later Steam downloads to that folder become available for the next game launch.
- **Standalone ZIP:** the updater follows the extracted folder you selected. It
  does not discover or fetch new releases. You must put a complete newer release
  in that folder, or run setup from its new location.

Moving or deleting the source folder can stop automatic launching. Moving the
game or removing or moving Python can also require setup again. A future game
version may need a compatible mod release before it can launch through this
check.

## Manual updates and removal

To skip automatic-update setup during a manual install, use either command from
the mod folder:

```text
Windows: install.bat install --no-automatic
Mac:     sh install.sh install --no-automatic
```

`--no-automatic` skips new setup. It does not disable a registration that already
exists. To use Steam with manual updates instead, remove the generated updater
line from Launch Options, keeping any other custom options you need. Use Steam's
normal Play button and run each mod's installer when you want to apply updates.
The separate Play Geoplanetical shortcut still checks updates if you use it.

To remove a mod, close the game and use its `uninstall.bat` or
`uninstall.command`. Removal also stops automatic updates for that mod, so the
next Steam launch will not silently reinstall it. The other mod's updates keep
working. Removing the last enrolled mod restores the previous Launch Options
only if the current entry is still the one setup wrote; later user edits are
preserved. The updater runtime stays if another registration needs it. Initial
setup, reinstalling or final removal may briefly close and reopen Steam; ordinary
automatic game-file updates do not restart Steam for configuration. Save and close
other Steam games before those setup/removal operations. Unsubscribing alone does
not uninstall a mod.

## Future updater changes

This first release keeps the trusted updater program installed during setup.
Ordinary signed mod updates can use it. A future update that needs a different
updater format or trust key will need a deliberate new setup, following that
release's instructions. It is not automatically replaced just because a new
file appears in a downloaded folder.

For release preparation, this is updater protocol 1. The release-verification key stays pinned. A signed runtime capability upgrade
can be installed explicitly during setup; an incompatible future protocol still
requires a reviewed migration and new setup instructions.

Vaccination Autopilot adds a third independent product. Installing it with the
current installer can move an older two-product setup to a complete, verified
runtime signed by the same pinned key. The old runtime is retained for recovery;
Steam keeps one launch entry. A failed Steam settings change stops setup and can
be retried by running the current installer. Use the current complete installers
when removing or repairing any of the three products after this upgrade.

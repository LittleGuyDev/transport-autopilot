#!/usr/bin/env python3
"""Transport Autopilot's original, single-product installer.

Written for this project from Electron's format documentation. It never reads
another mod's installer or patch specifications. Existing game contents are the
input; only exact Transport Autopilot blocks are changed.
"""
from __future__ import annotations

import sys
# Signed packages must not gain unsigned __pycache__ files before verification.
sys.dont_write_bytecode = True
if sys.version_info < (3, 8):
    print('ERROR: Python 3.8 or later is required. Install Python, then run this file again.')
    raise SystemExit(1)

import argparse
import contextlib
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import uuid


def _source_sibling(name):
    """Ignore stale bytecode left by earlier manual installer releases."""
    path = Path(__file__).resolve().parent / (name + '.py')
    loaded = sys.modules.get(name)
    if loaded is not None and getattr(loaded, '__file__', None) == str(path):
        return loaded
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    return module


try:
    for _sibling_name in ('core', 'platform_support', 'registration'):
        _source_sibling(_sibling_name)
    from core import (allows_game_version, validate_compatibility_policy, ArchiveEdit, AsarArchive, FileContent, InstallError, Transaction,
                      apply_section, atomic_write, file_sha, reverse_own_payload,
                      sha256, transition_section)
    import platform_support as platform
    import registration
except (ImportError, OSError):
    print('ERROR: Some Transport Autopilot installer files are missing.')
    print('Download or extract the complete mod folder and keep its files together.')
    raise SystemExit(1)

NAME = 'Transport Autopilot'
APP_SCRIPT = 'renderer/app.bundle.js'
SIM_SCRIPT = 'renderer/simulation-worker.bundle.js'
MARKERS = (b'__geoTransport', b'__transportAutobuildUI')
PACKAGE = Path(__file__).resolve().parent

AUTO_PRODUCT = 'transport-autopilot'
AUTO_FILES = ('auto_update.py', 'update_signature.py', 'update_trust.py',
              'update_setup.py', 'update_launcher.py', 'steam_setup.py',
              'release-manifest.json', 'release-manifest.sig')


def _automatic_module(filename, expected=None):
    """Load this manual package's bootstrap only; later workers use a trusted cache."""
    path = safe(PACKAGE / filename)
    name = '_lgd_manual_' + AUTO_PRODUCT.replace('-', '_') + '_' + path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    if expected is None:
        source = path.read_bytes()
    else:
        # Read once, authenticate against the already verified manifest, and
        # execute these exact bytes even if the download changes afterwards.
        with path.open('rb') as stream:
            source = stream.read(expected['size'] + 1)
        if len(source) != expected['size'] or hashlib.sha256(source).hexdigest() != expected['sha256']:
            raise InstallError('The automatic-update package changed after verification. Download the complete release again.')
    exec(compile(source, str(path), 'exec'), module.__dict__)
    return module


def automatic_package():
    """Legacy unsigned packages remain manual; partial new packages stop safely."""
    present = [safe(PACKAGE / name).exists() for name in AUTO_FILES]
    if not any(present):
        return None
    if not all(present) or any(not safe(PACKAGE / name).is_file() for name in AUTO_FILES):
        raise InstallError('The automatic-update package is incomplete. Download or extract the complete release again.')
    engine = _automatic_module('auto_update.py')
    trust = _automatic_module('update_trust.py')
    automatic = {'engine': engine, 'key': trust.PUBLIC_KEY}
    authenticated = verify_automatic(automatic)
    automatic['setup'] = _automatic_module('update_setup.py',
                                            expected=authenticated.manifest['files']['update_setup.py'])
    automatic['steam'] = _automatic_module('steam_setup.py',
                                            expected=authenticated.manifest['files']['steam_setup.py'])
    return automatic


def verify_automatic(automatic):
    if automatic is not None:
        return automatic['engine'].verify_package(PACKAGE, automatic['key'], AUTO_PRODUCT)


@contextlib.contextmanager
def automatic_transaction(automatic, archive, coordinator_locked):
    if automatic is None or coordinator_locked:
        yield
    else:
        with automatic['setup'].transaction(archive, trusted_engine=automatic['engine']):
            yield


@contextlib.contextmanager
def automatic_removal(automatic, action, archive):
    removing = action == 'uninstall' and automatic is not None
    if removing:
        automatic['setup'].unregister(AUTO_PRODUCT, archive, trusted_engine=automatic['engine'], already_locked=True,
                                        steam_helper=automatic['steam'], steam_consent=True)
    try:
        yield
    except BaseException:
        if removing:
            print('Automatic updates for %s are disabled. Removal did not finish; run the installer again to finish removal.' % NAME,
                  file=sys.stderr)
        raise



def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode('utf-8')


def read_json(path):
    if path.stat().st_size > 4 * 1024 * 1024:
        raise InstallError('An installer metadata file is unexpectedly large.')
    return json.loads(path.read_text(encoding='utf-8'))


def safe(path):
    return registration._safe(path)


def digest(path):
    path = safe(path)
    return file_sha(path) if path.exists() else None


def detected_version(payload):
    if payload is None:
        return 'not installed'
    return str(payload.get('version') or '/'.join(payload.get('labels', ['earlier version'])))


def package_files(directory):
    manifest = registration._manifest(directory / 'mod.json')
    registration._png(directory / 'thumbnail.png')
    payload = read_json(directory / 'transport-payload.json')
    if (payload.get('schema') != 1 or payload.get('id') != registration.MOD_ID or
            payload.get('version') != manifest['version'] or
            payload.get('gameVersions') != ['1.1.4']):
        raise InstallError('The installer files do not belong to the same release. Download the whole package again.')
    validate_compatibility_policy(payload)
    for key in ('app', 'sim'):
        section = payload.get(key)
        if not isinstance(section, dict) or not isinstance(section.get('patches'), list):
            raise InstallError('The Transport Autopilot payload is incomplete.')
        if any(not isinstance(v, str) for pair in section['patches'] for v in pair):
            raise InstallError('Invalid Transport Autopilot patch text.')
    legacy = read_json(directory / 'legacy_payloads.json')
    if legacy.get('schema') != 1 or not isinstance(legacy.get('payloads'), list):
        raise InstallError('The Transport Autopilot upgrade data is incomplete.')
    return manifest, payload, legacy['payloads']


def normalize_with_recovery(argument):
    try:
        return safe(platform.normalize_archive(argument))
    except ValueError:
        supplied = safe(Path(argument).expanduser())
        roots = [supplied]
        if supplied.name.endswith('.app'):
            roots.append(supplied.parent)
        for root in roots:
            target = safe(root / 'Geoplanetical.app')
            operations = safe(root / '.transport-autopilot-installer/operations')
            if not operations.exists():
                continue
            for marker in operations.glob('*/mac-pending.json'):
                value = read_json(safe(marker))
                backup = safe(marker.parent / 'before.app')
                if (value.get('schema') == 1 and value.get('app') == str(target) and
                        (backup / 'Contents/Resources/app.asar').is_file()):
                    return target / 'Contents/Resources/app.asar'
        raise InstallError('Geoplanetical was not found in the selected folder.')


def find_game(argument, interactive):
    if argument:
        return normalize_with_recovery(argument)
    candidates = platform.discover_installations()
    if not candidates:
        for library in platform.steam_libraries():
            try:
                candidates.append(normalize_with_recovery(library / 'steamapps/common/Geoplanetical'))
            except InstallError:
                pass
    if len(candidates) == 1:
        return safe(candidates[0])
    if not interactive:
        raise InstallError('Specify the Geoplanetical folder with --game when it cannot be found automatically.')
    if candidates:
        print('More than one game installation was found:')
        for i, candidate in enumerate(candidates, 1):
            print('  %d. %s' % (i, candidate))
        choice = input('Choose the installation number: ').strip()
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            return safe(candidates[int(choice) - 1])
        raise InstallError('No game was selected. Nothing was changed.')
    value = input('Paste the Geoplanetical game folder here: ').strip().strip('"')
    return normalize_with_recovery(value)


def game_root(archive):
    app = platform.macos_app(archive)
    return app.parent if app else archive.parent.parent


def state_root(archive):
    return safe(game_root(archive) / '.transport-autopilot-installer')


@contextlib.contextmanager
def exclusive(state):
    safe(state)
    state.mkdir(parents=True, exist_ok=True)
    path = safe(state / 'lock')
    with path.open('a+b') as handle:
        handle.seek(0)
        if handle.read(1) == b'':
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise InstallError('Another Transport Autopilot installer is running. Close it and try again.') from exc
        try:
            yield
        finally:
            if sys.platform == 'win32':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def file_targets(archive, parsed):
    paths = [archive]
    if parsed.entry(SIM_SCRIPT).get('unpacked'):
        paths.append(safe(Path(str(archive) + '.unpacked') / SIM_SCRIPT))
    app = platform.macos_app(archive)
    paths.extend([app / 'Contents/Info.plist'] if app else [game_root(archive) / 'Geoplanetical.exe'])
    return paths


def unchanged(expected):
    if any(digest(Path(path)) != value for path, value in expected.items()):
        raise InstallError('A file changed while the installer was preparing. Nothing will be overwritten.')


def tree_snapshot(root):
    """Fingerprint the complete app, including unrelated mods and signatures."""
    import hashlib
    snapshot = {}
    for folder, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(folder) / name
            info = path.lstat()
            key = path.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode):
                snapshot[key] = ['link', os.readlink(path)]
            elif stat.S_ISDIR(info.st_mode):
                snapshot[key] = ['directory', stat.S_IMODE(info.st_mode)]
            elif stat.S_ISREG(info.st_mode):
                digest_value = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest_value.update(chunk)
                snapshot[key] = ['file', stat.S_IMODE(info.st_mode), digest_value.hexdigest()]
            else:
                raise InstallError('Unsupported file type in the game application: ' + key)
    return snapshot


def plan(archive, action, directory=PACKAGE, profile=None):
    manifest, payload, migrations = package_files(directory)
    parsed = AsarArchive.from_file(archive)
    game = json.loads(parsed.read('package.json').decode('utf-8'))
    if (game.get('name') != 'geoplanetical' or not allows_game_version(game, payload) or
            str(game.get('steam', {}).get('appId')) != '940230'):
        raise InstallError('This release requires Geoplanetical 1.1.4 or a newer stable version with matching hooks. No files were changed.')
    header_hash = sha256(parsed.header_bytes)
    app = parsed.read(APP_SCRIPT, Path(str(archive) + '.unpacked'))
    sim = parsed.read(SIM_SCRIPT, Path(str(archive) + '.unpacked'))
    # Approval is based on the exact bytes used to construct this plan, not on
    # whatever a concurrent editor may have written by the end of planning.
    effective_profile = profile if profile is not None else registration.profile_directory()
    metadata_before = {str(path): digest(path) for path in allowed_metadata(archive, effective_profile)}
    source_before = {str(path): digest(path) for path in file_targets(archive, parsed)
                     if path != archive and not str(path).endswith(SIM_SCRIPT)}
    if parsed.entry(SIM_SCRIPT).get('unpacked'):
        source_before[str(safe(Path(str(archive) + '.unpacked') / SIM_SCRIPT))] = sha256(sim)
    # Also validate an already-installed copy. A no-op install must not report
    # success if the executable or application metadata rejects this archive.
    platform.macos_integrity_changes(archive, header_hash, header_hash)
    platform.windows_integrity_changes(archive, header_hash, header_hash)
    current = None
    base_app, base_sim = app, sim
    if any(marker in app or marker in sim for marker in MARKERS):
        base_app, base_sim, current = reverse_own_payload(app, sim, [payload] + migrations)
    if action == 'uninstall':
        new_app, new_sim = base_app, base_sim
        metadata, native = registration.plan_removal(directory, archive, profile_dir=profile)
    else:
        if current:
            new_app = transition_section(app, current['app'], payload['app'])
            new_sim = transition_section(sim, current['sim'], payload['sim'])
        else:
            new_app = apply_section(app, payload['app'])
            new_sim = apply_section(sim, payload['sim'])
        metadata, native = registration.plan_registration(directory, archive, profile_dir=profile)
    changes = {}
    if (new_app, new_sim) != (app, sim):
        packed, unpacked = {}, {}
        for name, data, original in ((APP_SCRIPT, new_app, app), (SIM_SCRIPT, new_sim, sim)):
            # Keep unchanged entries exactly as indexed, including their offsets
            # and integrity metadata, and avoid rewriting an unchanged worker.
            if data == original:
                continue
            if parsed.entry(name).get('unpacked'):
                unpacked[name] = data
                changes[safe(Path(str(archive) + '.unpacked') / name)] = data
            else:
                packed[name] = data
        rebuilt = parsed.plan_replace(packed, unpacked)
        changes[archive] = rebuilt
        old_hash = header_hash
        new_hash = sha256(rebuilt.header_bytes)
        changes.update(platform.macos_integrity_changes(archive, old_hash, new_hash))
        changes.update(platform.windows_integrity_changes(archive, old_hash, new_hash))
    record_path = safe(state_root(archive) / 'installed.json')
    product = None if action == 'uninstall' else encoded({
        'schema': 1, 'id': registration.MOD_ID, 'name': NAME, 'version': manifest['version'],
        'gameVersion': game['version'], 'archive': str(archive),
        'payloadSha256': sha256(encoded({key: payload[key] for key in ('app', 'sim')})),
    })
    metadata[record_path] = product
    metadata = {path: data for path, data in metadata.items()
                if (path.read_bytes() if path.exists() else None) != data}
    parsed._check_source()
    expected = dict(metadata_before)
    expected.update(source_before)
    expected[str(archive)] = digest(archive)
    parsed._check_source()
    unchanged(expected)
    return dict(archive=archive, action=action, version=manifest['version'], game=game,
                detected=current, changes=changes, metadata=metadata, expected=expected,
                app=new_app, sim=new_sim, native=native)


def verify_game(archive, wanted_app, wanted_sim):
    parsed = AsarArchive.from_file(archive)
    if (parsed.read(APP_SCRIPT, Path(str(archive) + '.unpacked')) != wanted_app or
            parsed.read(SIM_SCRIPT, Path(str(archive) + '.unpacked')) != wanted_sim):
        raise InstallError('The installed game did not pass verification.')
    digest_value = sha256(parsed.header_bytes)
    # Passing the same old/new hash validates the packaged integrity without changing it.
    platform.macos_integrity_changes(archive, digest_value, digest_value)
    platform.windows_integrity_changes(archive, digest_value, digest_value)


def allowed_metadata(archive, profile):
    folder = safe(profile / 'mods' / registration.FOLDER)
    return {safe(folder / name) for name in registration.FILES} | {
        safe(profile / '.transport-autopilot-installer/native-registration.json'),
        safe(state_root(archive) / 'installed.json')}


def recover_operations(archive, profile):
    state = state_root(archive)
    operations = state / 'operations'
    if not operations.exists():
        return
    app = platform.macos_app(archive)
    allowed = allowed_metadata(archive, profile)
    if app is None:
        allowed |= {archive, safe(Path(str(archive) + '.unpacked') / SIM_SCRIPT), game_root(archive) / 'Geoplanetical.exe'}
    for op in sorted(operations.iterdir()):
        safe(op)
        marker = op / 'mac-pending.json'
        txn = Transaction(op / 'files', allowed)
        if marker.exists():
            if platform.game_running(archive):
                raise InstallError('Close Geoplanetical before recovering an interrupted installation.')
            value = read_json(marker)
            if (app is None or value.get('schema') != 1 or value.get('app') != str(app) or
                    not isinstance(value.get('beforeTree'), dict) or
                    not isinstance(value.get('afterTree'), dict)):
                raise InstallError('An interrupted installation has an invalid recovery record.')
            backup = safe(op / 'before.app')
            if backup.exists():
                if tree_snapshot(backup) != value['beforeTree']:
                    raise InstallError('The recovery backup changed; it was preserved for review.')
                if app.exists():
                    if tree_snapshot(app) != value['afterTree']:
                        raise InstallError('The game changed after an interrupted installation. Its backup was preserved.')
                    displaced = op / 'interrupted-new.app'
                    if displaced.exists():
                        raise InstallError('Recovery needs review: more than one interrupted game copy exists.')
                    os.replace(app, displaced)
                os.replace(backup, app)
            if txn.journal.exists():
                txn.recover()
            elif txn.receipt.exists():
                txn.uninstall()
            marker.unlink()
            print('Recovered the previous game after an interrupted installation.')
        elif txn.journal.exists():
            if platform.game_running(archive):
                raise InstallError('Close Geoplanetical before recovering an interrupted installation.')
            txn.recover()
            print('Recovered files from an interrupted installation.')



def require_closed(archive):
    if platform.game_running(archive):
        raise InstallError('Geoplanetical started while files were being prepared. Close it and run the installer again.')


def commit(result, profile):
    archive = result['archive']
    if platform.game_running(archive):
        raise InstallError('Save and close Geoplanetical before installing or uninstalling.')
    unchanged(result['expected'])
    if not result['changes'] and not result['metadata']:
        return False
    op = safe(state_root(archive) / 'operations' / uuid.uuid4().hex)
    op.mkdir(parents=True)
    print('Preparing %s files...' % NAME, flush=True)
    app = platform.macos_app(archive)
    metadata = result['metadata']
    unchanged(result['expected'])
    if app and result['changes']:
        print('Preparing a safe copy of the game...', flush=True)
        staged = op / 'staged.app'
        before_tree = tree_snapshot(app)
        subprocess.run(['/usr/bin/ditto', str(app), str(staged)], check=True, capture_output=True)
        if tree_snapshot(staged) != before_tree:
            raise InstallError('The staged game copy differs from the original; the original was not changed.')
        for path, data in result['changes'].items():
            destination = staged / path.relative_to(app)
            mode = stat.S_IMODE(destination.stat().st_mode)
            if isinstance(data, ArchiveEdit):
                # The verified private copy is already our staging destination.
                # Preserve its original mode before signing the finished app.
                data.write_to(destination)
                destination.chmod(mode)
            else:
                atomic_write(destination, data, mode)
        platform.resign_macos(staged)
        staged_archive = staged / archive.relative_to(app)
        verify_game(staged_archive, result['app'], result['sim'])
        unchanged(result['expected'])
        if tree_snapshot(app) != before_tree:
            raise InstallError('The game application changed while the installer was preparing. No replacement was made.')
        if platform.game_running(archive):
            raise InstallError('The game started while installation was preparing. Close it and try again.')
        after_tree = tree_snapshot(staged)
        after_archive = after_tree[archive.relative_to(app).as_posix()][2]
        marker = op / 'mac-pending.json'
        atomic_write(marker, encoded({'schema': 1, 'app': str(app),
                                     'beforeArchive': result['expected'][str(archive)],
                                     'afterArchive': after_archive,
                                     'beforeTree': before_tree, 'afterTree': after_tree}))
        try:
            require_closed(archive)
            os.replace(app, op / 'before.app')
            os.replace(staged, app)
            txn = Transaction(op / 'files', allowed_metadata(archive, profile))
            if metadata:
                txn.install(metadata, {'product': NAME, 'version': result['version']},
                            expected_before={p: result['expected'][str(p)] for p in metadata},
                            pre_apply=lambda: require_closed(archive))
            verify_game(archive, result['app'], result['sim'])
            marker.unlink()
        except BaseException:
            recover_operations(archive, profile)
            raise
    else:
        prepared = {}
        for path, data in result['changes'].items():
            prepared[path] = data.write_to(op / 'prepared.asar') if isinstance(data, ArchiveEdit) else data
        unchanged(result['expected'])
        changes = dict(prepared); changes.update(metadata)
        allowed = allowed_metadata(archive, profile) | set(result['changes'])
        txn = Transaction(op / 'files', allowed)
        print('Saving a backup and applying the change...', flush=True)
        txn.install(changes, {'product': NAME, 'version': result['version']},
                    post_apply=lambda: verify_game(archive, result['app'], result['sim']),
                    expected_before={p: result['expected'][str(p)] for p in changes},
                    pre_apply=lambda: require_closed(archive))
    # The verified operation backups remain; this temporary materialized output
    # is no longer needed once the commit has completed.
    prepared_archive = op / 'prepared.asar'
    if prepared_archive.exists():
        prepared_archive.unlink()
    return True


def main(argv=None, *, _coordinator_locked=False):
    parser = argparse.ArgumentParser(description='Install or remove only Transport Autopilot.')
    parser.add_argument('action', nargs='?', default='install', choices=['install', 'uninstall', 'check', 'status'])
    parser.add_argument('folder', nargs='?', help='Geoplanetical game folder (normally found automatically)')
    parser.add_argument('--game', help='Geoplanetical game folder')
    parser.add_argument('--yes', action='store_true', help='Continue without the confirmation question')
    parser.add_argument('--profile', help=argparse.SUPPRESS)
    parser.add_argument('--no-automatic', action='store_true', help='Install without setting up automatic updates')
    options = parser.parse_args(argv)
    automatic = automatic_package()
    manifest, _, _ = package_files(PACKAGE)
    print('%s %s — Little Guy Dev' % (NAME, manifest['version']), flush=True)
    archive = find_game(options.game or options.folder, sys.stdin.isatty())
    profile = safe(Path(options.profile).expanduser() if options.profile else registration.profile_directory())
    state = state_root(archive)
    if options.action not in ('check', 'status') and platform.game_running(archive):
        raise InstallError('Save and close Geoplanetical first. No files were changed.')
    if options.action in ('check', 'status'):
        if any((state / 'operations').glob('*/mac-pending.json')) or any((state / 'operations').glob('*/files/pending.json')):
            raise InstallError('An interrupted installation needs recovery. Close the game and run this installer again.')
        result = plan(archive, 'install', profile=profile)
        print('Game: Geoplanetical ' + result['game']['version'])
        print('Folder: ' + str(game_root(archive)))
        print('Existing Transport Autopilot: ' + detected_version(result['detected']))
        print('Installer package: Transport Autopilot ' + result['version'])
        print('Compatibility check passed. Other mods will be kept as they are.')
        return 0
    pending = (any((state / 'operations').glob('*/mac-pending.json')) or
               any((state / 'operations').glob('*/files/pending.json')))
    if not pending and not options.yes:
        result = plan(archive, options.action, profile=profile)
        print('Game: Geoplanetical ' + result['game']['version'])
        print('Folder: ' + str(game_root(archive)))
        print('Existing Transport Autopilot: ' + detected_version(result['detected']))
    elif pending:
        print('A previous installation was interrupted. It will be recovered before continuing.')
    print('Only Transport Autopilot will be %s. Other mods will be kept as they are.' %
          ('removed' if options.action == 'uninstall' else 'installed'))
    if automatic is not None and os.environ.get('LGD_AUTO_CHILD') != '1' and (
            options.action == 'uninstall' or not options.no_automatic):
        print('Steam may briefly close and reopen to set up or restore launch options.')
        print('Save and close your other Steam games before continuing. Steam will not be forced to quit.')
    if not options.yes:
        if not sys.stdin.isatty():
            raise InstallError('Confirmation is required. Run interactively or pass --yes.')
        answer = input('Press Enter to continue, or type cancel: ').strip().lower()
        if answer not in ('', 'y', 'yes'):
            print('Cancelled. Nothing was changed.'); return 0
    # A manual package may have changed while confirmation was pending.
    verify_automatic(automatic)
    with automatic_transaction(automatic, archive, _coordinator_locked):
        # Disable enrollment before removal so it cannot reinstall a removed mod.
        with automatic_removal(automatic, options.action, archive), exclusive(state):
            recover_operations(archive, profile)
            result = plan(archive, options.action, profile=profile)
            if options.yes:
                print('Game: Geoplanetical ' + result['game']['version'])
                print('Folder: ' + str(game_root(archive)))
                print('Existing %s: ' % NAME + detected_version(result['detected']))
            # Recheck the complete manual package after planning and before writes.
            verify_automatic(automatic)
            changed = commit(result, profile)
            if options.action == 'uninstall':
                print('%s removed. Other mods and saved games were kept.' % NAME)
                print('Unsubscribe in Steam if you also want to remove its downloaded listing.')
            else:
                print('%s %s %s.' % (NAME, result['version'], 'installed' if changed else 'is already installed'))
                print('Start the game through Steam. Find Transport Autopilot in Manage mods.')
                print('Construction settings: Transport Room > Build > Transport Autopilot > Settings.')
        if (options.action != 'uninstall' and automatic is not None and
                not options.no_automatic and os.environ.get('LGD_AUTO_CHILD') != '1'):
            try:
                enrollment = automatic['setup'].register(PACKAGE, archive, profile,
                                                          public_key=automatic['key'],
                                                          trusted_engine=automatic['engine'], already_locked=True,
                                                          steam_helper=automatic['steam'], steam_consent=True)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                raise InstallError('The mod is installed, but automatic-update setup failed: ' + str(exc)) from exc
            print('Automatic updates and Steam launch options are ready. No copy and paste is needed.')
            print('Instructions: ' + str(enrollment.instructions))
            print('Play shortcut: ' + str(enrollment.shortcut))
    return 0


if __name__ == '__main__':
    try:
        if sys.version_info < (3, 8):
            raise InstallError('Python 3.8 or later is required. Install Python, then run this file again.')
        sys.exit(main())
    except (InstallError, OSError, ValueError, subprocess.SubprocessError) as error:
        print('\nERROR: %s' % error, file=sys.stderr)
        print('Installation did not finish. Keep this message when asking for help.', file=sys.stderr)
        sys.exit(1)

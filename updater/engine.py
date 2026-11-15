"""Authenticated, per-user pre-launch coordinator for independent mods.

Only this installed runtime and its pinned verifier execute before package
authentication. Downloaded Python is copied into a private verified snapshot
and loaded in a fresh isolated interpreter. No network, service or shell is used.
"""
from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
import functools
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile

MANIFEST = 'release-manifest.json'
SIGNATURE = 'release-manifest.sig'
PRODUCTS = {'transport-autopilot': 'transport-payload.json',
            'tourism-dashboard': 'tourism-payload.json',
            'vaccination-autopilot': 'vaccination-payload.json'}
RUNTIME_FILES = {'auto_update.py', 'update_signature.py', 'update_trust.py',
                 'update_setup.py', 'update_launcher.py'}
CHECKSUMS = 'SHA256SUMS.txt'
SOURCE_METADATA = frozenset({'.DS_Store', 'desktop.ini', 'Thumbs.db'})
MAX_GAME_VERSIONS = 16
MAX_FILE = 64 * 1024 * 1024
MAX_PACKAGE = 128 * 1024 * 1024
RUNTIME = Path(__file__).absolute()


class UpdateError(ValueError):
    """A failed precondition must stop the game launch."""


@dataclass(frozen=True)
class VerifiedPackage:
    root: Path
    manifest: dict
    digest: str
    bytes_hashed: int


@dataclass(frozen=True)
class UpdateResult:
    executable: Path
    products: tuple
    bytes_hashed: int
    changed: bool


def safe_path(value):
    path = Path(os.path.abspath(os.fspath(value)))
    for part in (path,) + tuple(path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise UpdateError('Symlink or reparse path is not allowed: ' + str(part))
    return path


def _absolute(value):
    if not Path(value).is_absolute():
        raise UpdateError('An explicit absolute path is required: ' + str(value))
    return safe_path(value)


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise UpdateError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def _read(path, limit):
    path = safe_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise UpdateError('Invalid or oversized file: ' + str(path))
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise UpdateError('File grew while being read: ' + str(path))
    return data


def _explicit_semver(version):
    if not isinstance(version, str) or len(version) > 64:
        return False
    match = re.fullmatch(
        r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
        r'(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
        r'(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?', version)
    if match is None:
        return False
    prerelease = match[4]
    return not prerelease or not any(part.isdigit() and len(part) > 1 and part.startswith('0')
                                    for part in prerelease.split('.'))


def _game_versions(value):
    return (isinstance(value, list) and 1 <= len(value) <= MAX_GAME_VERSIONS and
            all(_explicit_semver(version) for version in value) and len(set(value)) == len(value))


@functools.lru_cache(maxsize=1)
def _trusted_verifier():
    # These siblings belong to the stable installed runtime, never a download.
    sibling = RUNTIME.with_name('update_signature.py')
    if not sibling.exists():
        sibling = RUNTIME.with_name('signature.py')
    spec = importlib.util.spec_from_file_location('_pinned_update_signature', safe_path(sibling))
    module = importlib.util.module_from_spec(spec)
    # Source downloads may retain legacy bytecode caches. A matching forged
    # timestamp/size cache must never become the pre-authentication verifier.
    exec(compile(_read(sibling, MAX_FILE), str(sibling), 'exec'), module.__dict__)
    return module.verify_manifest


def verify_package(root, public_key, product, *, allow_legacy_cache=True):
    """Authenticate manifest, then every file; execute no package code."""
    root = _absolute(root)
    raw = _read(root / MANIFEST, 1024 * 1024)
    manifest = _trusted_verifier()(raw, _read(root / SIGNATURE, 1024), public_key)
    if (not isinstance(manifest, dict) or type(manifest.get('schema')) is not int or manifest.get('schema') != 1 or
            manifest.get('product') != product or product not in PRODUCTS or
            type(manifest.get('sequence')) is not int or manifest['sequence'] < 1 or
            type(manifest.get('minimum_updater')) is not int or manifest.get('minimum_updater') != 1 or
            not _game_versions(manifest.get('game_versions')) or
            not isinstance(manifest.get('version'), str) or
            not re.fullmatch(r'\d+\.\d+\.\d+', manifest['version'])):
        raise UpdateError('Unsupported or wrong-product signed release manifest')
    files = manifest.get('files')
    required = {'install.py', 'core.py', 'platform_support.py', 'registration.py',
                'legacy_payloads.json', 'mod.json', PRODUCTS[product]} | RUNTIME_FILES
    if product == 'transport-autopilot':
        required.add('thumbnail.png')
    if not isinstance(files, dict) or not required <= set(files) or len(files) > 256:
        raise UpdateError('The signed release is missing required installer files')
    total = 0
    for name, value in files.items():
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name)
                or name in (MANIFEST, SIGNATURE, CHECKSUMS) or name.endswith('.')
                or name.split('.')[0].casefold() in {'con', 'prn', 'aux', 'nul'} |
                    {'com' + str(i) for i in range(1, 10)} | {'lpt' + str(i) for i in range(1, 10)}
                or not isinstance(value, dict) or set(value) != {'sha256', 'size'}
                or type(value.get('size')) is not int or not 0 <= value['size'] <= MAX_FILE
                or not isinstance(value.get('sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', value['sha256'])):
            raise UpdateError('Unsafe file entry in signed release manifest')
        total += value['size']
    if total > MAX_PACKAGE or len({name.casefold() for name in files}) != len(files):
        raise UpdateError('Oversized package or ambiguous Windows filenames')
    actual_names = {entry.name for entry in root.iterdir()}
    for name in SOURCE_METADATA & actual_names:
        path = safe_path(root / name)
        info = path.stat()
        if (not allow_legacy_cache or not stat.S_ISREG(info.st_mode) or
                info.st_size > 1024 * 1024):
            raise UpdateError('Unexpected or unsafe source-folder metadata: ' + name)
        # Finder and Explorer may add these harmless files to an existing
        # download. Never read, authenticate, execute or snapshot their bytes.
        actual_names.remove(name)
    if '__pycache__' in actual_names:
        cache = safe_path(root / '__pycache__')
        if not allow_legacy_cache or not cache.is_dir():
            raise UpdateError('Unexpected Python cache in verified snapshot')
        # Old manual releases created this residue. It is never read as code,
        # copied into snapshots, or accepted as a link to another directory.
        for entry in cache.iterdir():
            entry = safe_path(entry)
            if not entry.is_file():
                raise UpdateError('Unexpected non-file in legacy Python cache')
        actual_names.remove('__pycache__')
    expected_names = set(files) | {MANIFEST, SIGNATURE}
    if actual_names not in (expected_names, expected_names | {CHECKSUMS}):
        raise UpdateError('Release contains unexpected or incomplete files')
    verified_hashes = {MANIFEST: hashlib.sha256(raw).hexdigest(),
                       SIGNATURE: hashlib.sha256(_read(root / SIGNATURE, 1024)).hexdigest()}
    payload_bytes = None
    for name, entry in files.items():
        data = _read(root / name, MAX_FILE)
        if len(data) != entry['size'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise UpdateError('Release file failed verification: ' + name)
        verified_hashes[name] = entry['sha256']
        if name == PRODUCTS[product]:
            payload_bytes = data
    payload = _json(payload_bytes)
    if (not isinstance(payload, dict) or
            payload.get('gameVersions') != manifest['game_versions']):
        raise UpdateError('Signed game versions and authenticated installer payload differ')
    if CHECKSUMS in actual_names:
        checksums = {}
        for line in _read(root / CHECKSUMS, 64 * 1024).decode('ascii').splitlines():
            match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9_.-]*)', line)
            if not match or match[2] in checksums:
                raise UpdateError('Invalid or duplicate release checksum entry')
            checksums[match[2]] = match[1]
        if checksums != verified_hashes:
            raise UpdateError('Release checksums do not match the authenticated package')
    return VerifiedPackage(root, manifest, hashlib.sha256(raw).hexdigest(), total)


def _private_directory(path):
    path = safe_path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if hasattr(os, 'getuid') and info.st_uid != os.getuid():
        raise UpdateError('Updater directory belongs to a different user')
    if os.name != 'nt' and info.st_mode & 0o077:
        raise UpdateError('Updater directory must be private to the current user')
    return path


@contextlib.contextmanager
def exclusive(state_dir):
    """One launch/update coordinator across both products and game roots."""
    state = _private_directory(state_dir)
    path = safe_path(state / 'coordinator.lock')
    with path.open('a+b') as handle:
        try:
            # Windows byte locks can deny even this initialization read.
            # Map contention consistently before as well as during locking.
            handle.seek(0)
            if not handle.read(1):
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise UpdateError('Another mod updater or game launch is in progress') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


@contextlib.contextmanager
def verified_snapshot(source, state_dir, public_key, product):
    """A fresh private snapshot closes source-download verification/use races."""
    original = verify_package(source, public_key, product)
    snapshots = _private_directory(Path(state_dir) / 'snapshots')
    root = Path(tempfile.mkdtemp(prefix=product + '-', dir=snapshots))
    try:
        for name in list(original.manifest['files']) + [MANIFEST, SIGNATURE]:
            data = _read(original.root / name, MAX_FILE if name not in (MANIFEST, SIGNATURE) else 1024 * 1024)
            target = root / name
            with target.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            target.chmod(0o400)
        result = verify_package(root, public_key, product, allow_legacy_cache=False)
        if result.digest != original.digest:
            raise UpdateError('Release changed during snapshot creation')
        root.chmod(0o500)
        yield result
    finally:
        # Only this random task-owned directory is removed; installed state is kept.
        if root.exists():
            root.chmod(0o700)
            for child in root.iterdir():
                child.chmod(0o600)
            shutil.rmtree(root)


def _load_installer(snapshot):
    """Called only inside the worker, after snapshot authentication."""
    sys.dont_write_bytecode = True
    # Isolated Python omits script/cwd and PYTHONPATH. Permit only verified
    # snapshot siblings, plus its original standard-library search paths.
    sys.path.insert(0, str(snapshot))
    for name in ('core', 'platform_support', 'registration', 'install'):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location('install', snapshot / 'install.py')
    installer = importlib.util.module_from_spec(spec)
    sys.modules['install'] = installer
    spec.loader.exec_module(installer)
    return installer


def _pending(installer, archive):
    operations = safe_path(installer.state_root(archive) / 'operations')
    return (any(operations.glob('*/mac-pending.json')) or
            any(operations.glob('*/files/pending.json')))


def _identities(paths):
    result = {}
    for path in paths:
        path = safe_path(path)
        try:
            info = path.stat()
        except FileNotFoundError:
            result[str(path)] = None
            continue
        result[str(path)] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    return result


def fast_check(installer, archive, profile):
    """Validate all owned bytes and native integrity, without hashing ASAR bulk.

    A change returns False, sending the existing installer through its complete
    expected-hash, lock, recovery, backup and transaction path. Invalid/modified
    owned content raises rather than being treated as a harmless cache miss.
    """
    if _pending(installer, archive):
        return False
    manifest, payload, migrations = installer.package_files(installer.PACKAGE)
    parsed = installer.AsarArchive.from_file(archive)
    observed = set(installer.allowed_metadata(archive, profile)) | {archive}
    observed.update(installer.file_targets(archive, parsed))
    observed.add(_executable(installer, archive))
    for name in (installer.APP_SCRIPT, installer.SIM_SCRIPT):
        if parsed.entry(name).get('unpacked'):
            observed.add(Path(str(archive) + '.unpacked') / name)
    identities = _identities(observed)
    game = _json(parsed.read('package.json'))
    if (game.get('name') != 'geoplanetical' or game.get('version') not in payload['gameVersions']
            or str(game.get('steam', {}).get('appId')) != '940230'):
        raise UpdateError('This release supports Geoplanetical versions: ' + ', '.join(payload['gameVersions']))
    # Validate paths before the archive reader can resolve away link evidence.
    unpacked = safe_path(str(archive) + '.unpacked')
    for name in (installer.APP_SCRIPT, installer.SIM_SCRIPT):
        if parsed.entry(name).get('unpacked'):
            safe_path(unpacked / name)
    app = parsed.read(installer.APP_SCRIPT, unpacked)
    sim = parsed.read(installer.SIM_SCRIPT, unpacked)
    header_hash = installer.sha256(parsed.header_bytes)
    mac_app = installer.platform.macos_app(archive)
    if mac_app:
        safe_path(mac_app / 'Contents/Info.plist')
    else:
        safe_path(installer.game_root(archive) / 'Geoplanetical.exe')
    installer.platform.macos_integrity_changes(archive, header_hash, header_hash)
    installer.platform.windows_integrity_changes(archive, header_hash, header_hash)
    current = None
    if any(marker in app or marker in sim for marker in installer.MARKERS):
        _, _, current = installer.reverse_own_payload(app, sim, [payload] + migrations)
    record_path = safe_path(installer.state_root(archive) / 'installed.json')
    if record_path.exists() and current:
        record = _json(_read(record_path, 4 * 1024 * 1024))
        known_versions = [current['version']] if current.get('version') else current.get('labels', [])
        expected_record = {'schema': 1, 'id': installer.registration.MOD_ID, 'name': installer.NAME,
                           'version': record.get('version') if isinstance(record, dict) else None,
                           'gameVersion': game['version'], 'archive': str(archive),
                           'payloadSha256': installer.sha256(installer.encoded({key: current[key] for key in ('app', 'sim')}))}
        if (record != expected_record or type(record.get('schema')) is not int or
                record.get('version') not in known_versions):
            raise UpdateError('Owned installed-version record changed; refusing automatic overwrite')
    if current:
        wanted_app = installer.transition_section(app, current['app'], payload['app'])
        wanted_sim = installer.transition_section(sim, current['sim'], payload['sim'])
    else:
        wanted_app = installer.apply_section(app, payload['app'])
        wanted_sim = installer.apply_section(sim, payload['sim'])
    metadata, _ = installer.registration.plan_registration(installer.PACKAGE, archive, profile_dir=profile)
    metadata[safe_path(installer.state_root(archive) / 'installed.json')] = installer.encoded({
        'schema': 1, 'id': installer.registration.MOD_ID, 'name': installer.NAME,
        'version': manifest['version'], 'gameVersion': game['version'], 'archive': str(archive),
        'payloadSha256': installer.sha256(installer.encoded({key: payload[key] for key in ('app', 'sim')})),
    })
    unchanged = (wanted_app, wanted_sim) == (app, sim)
    for path, expected in metadata.items():
        path = safe_path(path)
        actual = _read(path, MAX_FILE) if path.exists() else None
        unchanged = unchanged and actual == expected
    parsed._check_source()
    if identities != _identities(observed):
        raise UpdateError('Game or native mod metadata changed during validation')
    if _pending(installer, archive):
        raise UpdateError('An installation began during validation; retry after it finishes')
    return unchanged


def _executable(installer, archive):
    app = installer.platform.macos_app(archive)
    if app:
        info = plistlib.loads(_read(safe_path(app / 'Contents/Info.plist'), 4 * 1024 * 1024))
        name = info.get('CFBundleExecutable')
        if (not isinstance(name, str) or name in ('', '.', '..') or
                Path(name).name != name or '\\' in name):
            raise UpdateError('Invalid native macOS game executable')
        executable = safe_path(app / 'Contents/MacOS' / name)
    else:
        executable = safe_path(installer.game_root(archive) / 'Geoplanetical.exe')
    if not executable.is_file():
        raise UpdateError('The validated game executable is missing')
    return executable


def worker(snapshot, product, game, profile, public_key, apply=True):
    package = verify_package(snapshot, public_key, product, allow_legacy_cache=False)
    installer = _load_installer(package.root)
    if installer.package_files(package.root)[0]['version'] != package.manifest['version']:
        raise UpdateError('Signed release version and installer payload differ')
    supplied_game = _absolute(game)
    # Keep recovery discoverable when the configured archive is temporarily
    # absent during a native bundle swap. The original installer accepts its
    # enclosing game directory for this recovery case.
    game_argument = supplied_game
    if supplied_game.name == 'app.asar' and not supplied_game.exists():
        native = installer.platform.macos_app(supplied_game)
        game_argument = native.parent if native else supplied_game.parent.parent
    archive = safe_path(installer.find_game(str(game_argument), False))
    if supplied_game.name == 'app.asar' and archive != supplied_game:
        raise UpdateError('The game archive differs from the explicitly registered path')
    profile = _absolute(profile)
    if installer.platform.game_running(archive):
        raise UpdateError('Save and close Geoplanetical before updating or launching')
    def check():
        # Keep the original per-product lock as well as the shared coordinator
        # lock, so an existing manual installer cannot race a no-change check.
        with installer.exclusive(installer.state_root(archive)):
            ready = fast_check(installer, archive, profile)
            target = str(_executable(installer, archive)) if ready else None
            return ready, target
    ready, target = check()
    changed = False
    if not ready and apply:
        # Existing original installer retains all full-archive preconditions.
        child_marker = os.environ.get('LGD_AUTO_CHILD')
        os.environ['LGD_AUTO_CHILD'] = '1'
        try:
            with contextlib.redirect_stdout(sys.stderr):
                code = installer.main(['--game', str(game_argument), '--profile', str(profile), '--yes'],
                                      _coordinator_locked=True)
        finally:
            if child_marker is None:
                os.environ.pop('LGD_AUTO_CHILD', None)
            else:
                os.environ['LGD_AUTO_CHILD'] = child_marker
        if code != 0:
            raise UpdateError('The mod installer failed')
        ready, target = check()
        if not ready:
            raise UpdateError('The installed update failed its final validation')
        changed = True
    if installer.platform.game_running(archive):
        raise UpdateError('Geoplanetical started during update validation')
    return {'ready': ready, 'changed': changed,
            'executable': target,
            'digest': package.digest, 'sequence': package.manifest['sequence']}


def _atomic_json(path, value):
    path = safe_path(path)
    temporary = path.with_name(path.name + '.tmp-' + os.urandom(8).hex())
    try:
        with temporary.open('xb') as stream:
            stream.write((json.dumps(value, sort_keys=True) + '\n').encode())
            stream.flush(); os.fsync(stream.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def run_updates(state_dir, registrations, game, profile, *, public_key,
                python=None, runner=None, on_ready=None, before_updates=None):
    """Update all explicitly enrolled products, or raise before game launch.

    The caller owns launch and must compare its original Steam executable to
    the returned executable before forwarding only its original argument list.
    """
    state = _absolute(state_dir)
    game, profile = _absolute(game), _absolute(profile)
    python = _absolute(python or sys.executable)
    runner = runner or subprocess.run
    registrations = list(registrations)
    if (not registrations or len(registrations) > len(PRODUCTS) or
            any(not isinstance(r, dict) or set(r) != {'product', 'package_root'} or r.get('product') not in PRODUCTS or
                not r.get('package_root') for r in registrations) or
            len({r['product'] for r in registrations}) != len(registrations)):
        raise UpdateError('Invalid automatic-update product registrations')
    changed, total, executable, releases = False, 0, None, {}
    with exclusive(state):
        if before_updates is not None:
            before_updates()
        record_path = state / 'releases.json'
        previous = _json(_read(record_path, 1024 * 1024)) if record_path.exists() else {}
        if not isinstance(previous, dict):
            raise UpdateError('Invalid updater release state')
        # Authenticate/snapshot EVERY package before any product can mutate.
        with contextlib.ExitStack() as stack:
            packages = []
            for registration in registrations:
                product = registration['product']
                package = stack.enter_context(verified_snapshot(registration['package_root'], state, public_key, product))
                prior = previous.get(product)
                if prior is not None:
                    if (not isinstance(prior, dict) or type(prior.get('sequence')) is not int or
                            not isinstance(prior.get('digest'), str)):
                        raise UpdateError('Invalid updater product release state')
                    if (package.manifest['sequence'] < prior['sequence'] or
                            package.manifest['sequence'] == prior['sequence'] and package.digest != prior['digest']):
                        raise UpdateError('Signed release rollback or conflicting sequence rejected')
                packages.append(package)
                # Source, private snapshot, check worker, and apply worker.
                total += package.bytes_hashed * 4
            def invoke(package, apply):
                product = package.manifest['product']
                command = [str(python), '-I', '-S', str(RUNTIME), '--worker-apply' if apply else '--worker-check',
                           '--snapshot', str(package.root), '--product', product,
                           '--game', str(game), '--profile', str(profile),
                           '--key-json', json.dumps(public_key, separators=(',', ':'))]
                result = runner(command, shell=False, capture_output=True, text=True, check=False)
                if result.returncode != 0:
                    raise UpdateError('Update failed before game launch: ' + (result.stderr or result.stdout)[-4000:])
                status = _json(result.stdout)
                if (not isinstance(status, dict) or type(status.get('ready')) is not bool or
                        status.get('digest') != package.digest or
                        status.get('sequence') != package.manifest['sequence']):
                    raise UpdateError('Invalid verified-installer response')
                return status

            # A bad second product must be discovered before the first changes.
            for package in packages:
                invoke(package, False)
            for package in packages:
                product = package.manifest['product']
                status = invoke(package, True)
                if status['ready'] is not True:
                    raise UpdateError('The installer did not finish its update')
                target = _absolute(status['executable'])
                if executable is not None and executable != target:
                    raise UpdateError('Products selected different game executables')
                executable = target
                changed |= bool(status['changed'])
                releases[product] = {'sequence': package.manifest['sequence'], 'digest': package.digest}
                # A later independent product can fail after this one's durable
                # commit. Preserve each accepted floor before the next apply.
                _atomic_json(record_path, {**previous, **releases})
            result = UpdateResult(executable, tuple(releases), total, changed)
            if on_ready is not None:
                on_ready(result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Authenticated mod-update worker')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--worker-check', action='store_true')
    mode.add_argument('--worker-apply', action='store_true')
    for option in ('snapshot', 'product', 'game', 'profile', 'key-json'):
        parser.add_argument('--' + option, required=True)
    args = parser.parse_args(argv)
    result = worker(args.snapshot, args.product, args.game, args.profile,
                    _json(args.key_json), apply=args.worker_apply)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)

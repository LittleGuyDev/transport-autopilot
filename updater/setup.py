"""Explicit, per-user enrollment for authenticated pre-launch mod updates.

The first trusted manual installation enrolls its signed package and can call
the authenticated installer-only helper to configure Steam safely. Subsequent
products share the same pinned runtime, launch settings and coordinator lock.
"""
from __future__ import annotations

from dataclasses import dataclass
import ast
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

RUNTIME_FILES = ('auto_update.py', 'update_signature.py', 'update_trust.py',
                 'update_launcher.py', 'update_setup.py')
HERE = Path(__file__).absolute().parent


def _sibling(installed, source, name):
    path = HERE / installed
    if not path.is_file():
        path = HERE / source
    # Reject link/reparse evidence without importing anything from a package.
    _safe(path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError('Trusted updater module is unavailable: ' + str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    previous_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous_bytecode
    return module


def _engine():
    return _sibling('auto_update.py', 'engine.py', '_lgd_enrollment_engine')


def _safe(value):
    path = Path(os.path.abspath(os.fspath(value)))
    for ancestor in (path,) + tuple(path.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Updater paths cannot contain symlinks or reparse points: ' + str(ancestor))
    return path


def _absolute(value):
    if not Path(value).is_absolute():
        raise ValueError('Updater paths must be absolute: ' + str(value))
    return _safe(value)


def _windows_private(path, created=False):
    """Give only the current Windows token user an inheritable full-access ACE.

    chmod does not enforce Windows confidentiality. These native APIs install a
    protected DACL instead; no shell, environment-selected utility or administrator
    elevation is involved. New snapshots inherit this user-only access.
    """
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    token = wintypes.HANDLE()
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    sid_text = wintypes.LPWSTR()
    descriptor = ctypes.c_void_p()
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                              wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        advapi.GetTokenInformation.restype = wintypes.BOOL
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise ctypes.WinError(ctypes.get_last_error())
        sddl = 'O:' + sid_text.value + 'D:P(A;OICI;FA;;;' + sid_text.value + ')'
        if not created:
            # Never turn an existing shared/foreign directory into a trusted
            # runtime by merely tightening its permissions after the fact.
            needed = wintypes.DWORD()
            advapi.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
                                               wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            advapi.GetFileSecurityW.restype = wintypes.BOOL
            advapi.GetFileSecurityW(str(path), 0x00000005, None, 0, ctypes.byref(needed))
            if not needed.value:
                raise ctypes.WinError(ctypes.get_last_error())
            existing = ctypes.create_string_buffer(needed.value)
            if not advapi.GetFileSecurityW(str(path), 0x00000005, existing, needed, ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            # Compare the native descriptor, not its SDDL spelling: Windows may
            # render equivalent rights/SIDs/auto-inheritance flags differently.
            owner, existing_dacl = ctypes.c_void_p(), ctypes.c_void_p()
            defaulted, present = wintypes.BOOL(), wintypes.BOOL()
            control, revision = wintypes.WORD(), wintypes.DWORD()
            advapi.GetSecurityDescriptorOwner.argtypes = [ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
            advapi.GetSecurityDescriptorOwner.restype = wintypes.BOOL
            advapi.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p,
                ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
            advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
            advapi.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p,
                ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(wintypes.BOOL)]
            advapi.GetSecurityDescriptorDacl.restype = wintypes.BOOL
            advapi.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            advapi.EqualSid.restype = wintypes.BOOL
            if (not advapi.GetSecurityDescriptorOwner(existing, ctypes.byref(owner), ctypes.byref(defaulted)) or
                    not advapi.GetSecurityDescriptorControl(existing, ctypes.byref(control), ctypes.byref(revision)) or
                    not advapi.GetSecurityDescriptorDacl(existing, ctypes.byref(present), ctypes.byref(existing_dacl), ctypes.byref(defaulted))):
                raise ctypes.WinError(ctypes.get_last_error())
            class AclSize(ctypes.Structure):
                _fields_ = [('count', wintypes.DWORD), ('used', wintypes.DWORD), ('free', wintypes.DWORD)]
            class AllowedAce(ctypes.Structure):
                _fields_ = [('kind', wintypes.BYTE), ('flags', wintypes.BYTE),
                            ('size', wintypes.WORD), ('mask', wintypes.DWORD),
                            ('sid_start', wintypes.DWORD)]
            acl_size, ace = AclSize(), ctypes.c_void_p()
            advapi.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int]
            advapi.GetAclInformation.restype = wintypes.BOOL
            advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
            advapi.GetAce.restype = wintypes.BOOL
            advapi.GetLengthSid.argtypes = [ctypes.c_void_p]
            advapi.GetLengthSid.restype = wintypes.DWORD
            private = bool(owner and advapi.EqualSid(owner, sid) and control.value & 0x1000 and
                           present.value and existing_dacl)
            if private:
                if not advapi.GetAclInformation(existing_dacl, ctypes.byref(acl_size), ctypes.sizeof(acl_size), 2):
                    raise ctypes.WinError(ctypes.get_last_error())
                private = acl_size.count == 1
            if private:
                if not advapi.GetAce(existing_dacl, 0, ctypes.byref(ace)):
                    raise ctypes.WinError(ctypes.get_last_error())
                allowed = ctypes.cast(ace, ctypes.POINTER(AllowedAce)).contents
                # ACCESS_ALLOWED_ACE_TYPE, object/container inheritance and
                # FILE_ALL_ACCESS; no other trustees or inherited grants.
                private = bool(allowed.kind == 0 and allowed.flags == 3 and
                               allowed.mask == 0x001f01ff and
                               allowed.size == AllowedAce.sid_start.offset + advapi.GetLengthSid(sid) and
                               advapi.EqualSid(ace.value + AllowedAce.sid_start.offset, sid))
            if not private:
                raise ValueError('Existing Windows updater cache is not owned exclusively by this user')
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        advapi.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                                                   ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
        advapi.GetSecurityDescriptorDacl.restype = wintypes.BOOL
        if not advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not present.value or not dacl:
            raise ValueError('Private Windows updater DACL could not be constructed')
        advapi.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
                                                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        advapi.SetNamedSecurityInfoW.restype = wintypes.DWORD
        error = advapi.SetNamedSecurityInfoW(str(path), 1, 0x80000005, sid, None, dacl, None)
        if error:
            raise ctypes.WinError(error)
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(token)


def _private(path):
    path = _safe(path)
    created = not path.exists()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('Updater cache is not a directory')
    if hasattr(os, 'getuid') and info.st_uid != os.getuid():
        raise ValueError('Updater cache belongs to a different user')
    if os.name == 'nt':
        _windows_private(path, created=created)
    elif info.st_mode & 0o077:
        # An existing shared cache is rejected, never silently made trusted.
        if not created:
            raise ValueError('Updater cache must be private to the current user')
        path.chmod(0o700)
    return path


def state_directory(archive):
    archive = _absolute(archive)
    if sys.platform == 'darwin':
        base = Path.home() / 'Library/Application Support'
    elif os.name == 'nt':
        value = os.environ.get('LOCALAPPDATA')
        if not value or not Path(value).is_absolute():
            raise ValueError('Windows LOCALAPPDATA must be an absolute per-user directory')
        base = Path(value)
    else:
        raise ValueError('Automatic launching supports Windows and native macOS only')
    identity = hashlib.sha256(os.path.normcase(str(archive)).encode('utf-8')).hexdigest()[:24]
    return _safe(base / 'Little Guy Dev/Geoplanetical Auto Updates' / identity)


@contextlib.contextmanager
def transaction(archive, trusted_engine, state_dir=None):
    """Serialize a signed manual transaction with automatic update and launch.

    The manual installer supplies its retained, authenticated engine and keeps
    this context around recovery, commit and enrollment/removal. Its internal
    worker call already holds the parent coordinator lock and does not nest it.
    """
    archive = _absolute(archive)
    state = _private(_absolute(state_dir) if state_dir is not None else state_directory(archive))
    with trusted_engine.exclusive(state):
        yield state


def _load_key(runtime=None):
    if runtime is None:
        return _sibling('update_trust.py', 'trust.py', '_lgd_setup_pinned_trust').PUBLIC_KEY
    path = _safe(Path(runtime) / 'update_trust.py')
    spec = importlib.util.spec_from_file_location('_lgd_existing_pinned_trust', path)
    module = importlib.util.module_from_spec(spec)
    previous_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous_bytecode
    return module.PUBLIC_KEY


def _runtime_path(state, runtime):
    """Keep immutable runtime generations inside this private registration."""
    runtime = _absolute(runtime)
    if runtime.parent != state or not re.fullmatch(r'runtime(?:-[a-f0-9]{24})?', runtime.name):
        raise ValueError('Registered runtime is outside this private updater state')
    return runtime


def _runtime_products(runtime):
    """Read the pinned capability table without executing downloaded code."""
    source = _safe(runtime / 'auto_update.py')
    if source.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Installed updater runtime is unexpectedly large')
    for node in ast.parse(source.read_bytes()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PRODUCTS' for t in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
                return set(value)
    raise ValueError('Installed updater capability table is invalid')


def _runtime_capabilities(runtime):
    """Read opt-in compatibility features from an already trusted runtime."""
    source = _safe(runtime / 'auto_update.py')
    if source.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Installed updater runtime is unexpectedly large')
    for node in ast.parse(source.read_bytes()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'RUNTIME_CAPABILITIES' for t in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, (tuple, list)) and all(isinstance(v, str) for v in value):
                return set(value)
            raise ValueError('Installed updater feature table is invalid')
    return set()


def _install_runtime(engine, snapshot, state, runtime, key):
    """Publish a complete verified generation; never overwrite the pinned one."""
    if runtime.exists():
        _private(runtime)
        for name in RUNTIME_FILES:
            if engine._read(runtime / name, engine.MAX_FILE) != engine._read(snapshot.root / name, engine.MAX_FILE):
                raise ValueError('Existing updater generation differs from the signed release')
        return
    temporary = Path(tempfile.mkdtemp(prefix='runtime-pending-', dir=state))
    try:
        if os.name == 'nt':
            _windows_private(temporary, created=True)
        else:
            _private(temporary)
        for name in RUNTIME_FILES:
            target = temporary / name
            with target.open('xb') as stream:
                stream.write(engine._read(snapshot.root / name, engine.MAX_FILE))
                stream.flush()
                os.fsync(stream.fileno())
            target.chmod(0o400)
        if _load_key(temporary) != key:
            raise ValueError('Signed runtime trust key does not match the enrollment key')
        os.replace(temporary, runtime)
    finally:
        if temporary.exists():
            for child in temporary.iterdir():
                child.chmod(0o600)
            shutil.rmtree(temporary)


def _config(engine, path):
    value = engine._json(engine._read(path, 1024 * 1024))
    return _validate_config(engine, value)


def _validate_config(engine, value):
    if (not isinstance(value, dict) or set(value) != {'schema', 'game', 'profile', 'python', 'products', 'runtime'}
            or type(value.get('schema')) is not int or value['schema'] != 1
            or not isinstance(value.get('products'), list) or len(value['products']) > len(engine.PRODUCTS)):
        raise ValueError('Invalid automatic-update registration file')
    for name in ('game', 'profile', 'python', 'runtime'):
        if not isinstance(value[name], str):
            raise ValueError('Invalid automatic-update path')
        _absolute(value[name])
    products = value['products']
    if any(not isinstance(item, dict) or set(item) != {'product', 'package_root'}
           or item['product'] not in engine.PRODUCTS or not isinstance(item['package_root'], str)
           for item in products):
        raise ValueError('Invalid enrolled product')
    if len({item['product'] for item in products}) != len(products):
        raise ValueError('Duplicate enrolled product')
    for item in products:
        _absolute(item['package_root'])
    return value


ENROLLMENT_PENDING = 'enrollment-pending.json'


def _launcher_config(engine, path, runtime):
    """Allow only the exact signed generation prepared before a Steam switch.

    A crash between Steam's atomic field update and our config update leaves
    the old config intact. The new launcher can use its bounded private journal;
    the old immutable launcher continues using the old config normally.
    """
    current = _config(engine, path)
    if current['runtime'] == str(runtime):
        return current
    pending_path = _safe(path.parent / ENROLLMENT_PENDING)
    if not pending_path.exists():
        return current
    pending = engine._json(engine._read(pending_path, 2 * 1024 * 1024))
    if (not isinstance(pending, dict) or set(pending) != {'schema', 'previous', 'next'} or
            type(pending.get('schema')) is not int or pending['schema'] != 1 or
            pending['previous'] != current):
        raise ValueError('Invalid pending updater enrollment')
    wanted = _validate_config(engine, pending['next'])
    if (wanted['runtime'] != str(runtime) or
            _runtime_path(path.parent, wanted['runtime']) != runtime or
            any(wanted[key] != current[key] for key in ('game', 'profile')) or
            _load_key(runtime) != _load_key(_runtime_path(path.parent, current['runtime']))):
        raise ValueError('Pending enrollment does not belong to this trusted runtime')
    return wanted


@dataclass(frozen=True)
class RegistrationResult:
    launcher: Path
    config: Path
    shortcut: Path
    launch_options: str
    instructions: Path
    steam_configured: bool = False


def _launch_options(python, launcher, config):
    command = [str(python), '-I', '-S', str(launcher), '--config', str(config), '--steam', '--']
    if os.name == 'nt':
        return subprocess.list2cmdline(command) + ' %command%'
    return ' '.join(shlex.quote(part) for part in command) + ' %command%'


def _instructions(state, python, launcher, config, *, steam_configured=False, options_override=None):
    command = [str(python), '-I', '-S', str(launcher), '--config', str(config)]
    if os.name == 'nt':
        options = subprocess.list2cmdline(command + ['--steam', '--']) + ' %command%'
        shortcut = _safe(state / 'Play Geoplanetical.cmd')
        # Windows paths cannot contain quotes; percent expansion is disabled by
        # doubling literal percent characters in this generated batch file.
        body = '@echo off\r\nsetlocal DisableDelayedExpansion\r\n' + subprocess.list2cmdline(command).replace('%', '%%') + '\r\n'
    else:
        options = ' '.join(shlex.quote(part) for part in command + ['--steam', '--']) + ' %command%'
        shortcut = _safe(state / 'Play Geoplanetical.command')
        body = '#!/bin/sh\nexec ' + ' '.join(shlex.quote(part) for part in command) + '\n'
    if options_override is not None:
        options = options_override
    with shortcut.open('w', encoding='utf-8', newline='') as stream:
        stream.write(body)
    shortcut.chmod(0o700)
    instructions = _safe(state / 'AUTOMATIC-UPDATES.txt')
    if steam_configured:
        heading = ('Automatic updates are ready. Steam launch options were set up automatically.\n'
                   'No copy and paste is needed. Start Geoplanetical through Steam as usual.\n\n'
                   'The installer kept existing game options and saved a private settings backup.\n'
                   'If Steam was open, it was closed gracefully and reopened.\n\n'
                   'Current Steam launch line (for support):\n\n')
    else:
        heading = ('Automatic updates are enrolled for this game installation.\n\n'
                   'One-time Steam setup: Library > Geoplanetical > Properties > General > Launch Options.\n'
                   'Paste this complete line, preserving %command%:\n\n')
    instructions.write_text(
        heading + options + '\n\n' +
        ('' if steam_configured else 'Steam settings were not changed automatically. ') +
        'Keep the selected Python installation available.\n'
        'You can also use the adjacent Play Geoplanetical shortcut; it checks updates and opens Steam.\n'
        'Leave Steam launch options in place so every Steam launch checks updates before starting the game.\n'
        'A failed verification or installation stops the game launch. Save and close the game before updating.\n',
        encoding='utf-8')
    instructions.chmod(0o600)
    return RegistrationResult(launcher, config, shortcut, options, instructions, steam_configured)


def register(package, archive, profile, public_key=None, state_dir=None, trusted_engine=None,
             already_locked=False, steam_helper=None, steam_consent=False):
    """Enroll a signed mod; explicit manual callers can configure Steam safely."""
    # A manual installer retains these already authenticated objects across its
    # commit. Reloading a mutable Workshop source here would reopen a verify/use
    # race before enrollment's own package verification could reject changes.
    engine = trusted_engine if trusted_engine is not None else _engine()
    if already_locked and trusted_engine is None:
        raise ValueError('Enrollment under an existing lock requires the retained trusted engine')
    package, archive, profile = _absolute(package), _absolute(archive), _absolute(profile)
    if not archive.is_file() or not package.is_dir() or profile.exists() and not profile.is_dir():
        raise ValueError('Select existing game/package paths and a valid profile directory path')
    state = _private(_absolute(state_dir) if state_dir is not None else state_directory(archive))
    runtime = _safe(state / 'runtime')
    config_path = _safe(state / 'config.json')
    supplied_key = public_key if public_key is not None else _load_key()
    with contextlib.nullcontext() if already_locked else engine.exclusive(state):
        previous = _config(engine, config_path) if config_path.exists() else None
        if previous and (previous['game'] != str(archive) or previous['profile'] != str(profile)):
            raise ValueError('This updater cache is already enrolled for a different game or profile')
        if previous:
            runtime = _runtime_path(state, previous['runtime'])
        if runtime.exists():
            _private(runtime)
            if any(not _safe(runtime / name).is_file() for name in RUNTIME_FILES):
                raise ValueError('Installed trusted updater runtime is incomplete')
            key = _load_key(runtime)
            if key != supplied_key:
                raise ValueError('The package signing key differs from the pinned installed runtime')
        else:
            if previous:
                raise ValueError('Installed trusted updater runtime is missing')
            key = supplied_key
        # Product is only a selector here: it is authenticated by snapshot before
        # any scripts are copied or executed and must match a known product.
        raw = engine._json(engine._read(package / engine.MANIFEST, 1024 * 1024))
        product = raw.get('product') if isinstance(raw, dict) else None
        if product not in engine.PRODUCTS:
            raise ValueError('Unsupported automatic-update product')
        with engine.verified_snapshot(package, state, key, product) as snapshot:
            if not set(RUNTIME_FILES) <= set(snapshot.manifest['files']):
                raise ValueError('Signed package does not include the complete trusted updater runtime')
            release_path = _safe(state / 'releases.json')
            releases = engine._json(engine._read(release_path, 1024 * 1024)) if release_path.exists() else {}
            if not isinstance(releases, dict):
                raise ValueError('Invalid automatic-update release state')
            prior = releases.get(product)
            sequence, digest = snapshot.manifest['sequence'], snapshot.digest
            if prior is not None and (not isinstance(prior, dict) or type(prior.get('sequence')) is not int
                                      or sequence < prior['sequence']
                                      or sequence == prior['sequence'] and digest != prior.get('digest')):
                raise ValueError('Signed release rollback or conflicting sequence rejected')
            needed_capabilities = {'exact-hooks-v1'} if snapshot.manifest.get('compatibility_policy') else set()
            if runtime.exists() and (product not in _runtime_products(runtime)
                                     or not needed_capabilities <= _runtime_capabilities(runtime)):
                # A newly enrolled product needs a launcher that knows its identity.
                # Only a complete package signed by the already pinned key can
                # supply it. Retain the old generation for repair; config and the
                # Steam field are switched by their existing atomic transactions.
                fingerprint = hashlib.sha256(b''.join(
                    engine._read(snapshot.root / name, engine.MAX_FILE) for name in RUNTIME_FILES)).hexdigest()[:24]
                runtime = _runtime_path(state, state / ('runtime-' + fingerprint))
                _install_runtime(engine, snapshot, state, runtime, key)
                required = {row['product'] for row in previous['products']} if previous else set()
                if not required.union({product}) <= _runtime_products(runtime):
                    raise ValueError('Signed runtime does not support every enrolled product')
            elif not runtime.exists():
                _install_runtime(engine, snapshot, state, runtime, key)
            required = {row['product'] for row in previous['products']} if previous else set()
            if not required.union({product}) <= _runtime_products(runtime):
                raise ValueError('Signed runtime does not support every enrolled product')
            if not needed_capabilities <= _runtime_capabilities(runtime):
                raise ValueError('Signed runtime does not support the release compatibility policy')
            products = list(previous['products']) if previous else []
            products = [item for item in products if item['product'] != product]
            products.append({'product': product, 'package_root': str(package)})
            python = _absolute(Path(sys.executable).resolve())
            value = {'schema': 1, 'game': str(archive), 'profile': str(profile), 'python': str(python),
                     'products': sorted(products, key=lambda item: item['product']), 'runtime': str(runtime)}
            releases[product] = {'sequence': sequence, 'digest': digest}
            pending_path = _safe(state / ENROLLMENT_PENDING)
            migrating = previous is not None and previous['runtime'] != str(runtime) and steam_helper is not None
            if migrating:
                # Publish recovery before touching Steam. Never strand its old
                # launch line with a config pointing at an incompatible runtime.
                engine._atomic_json(pending_path, {'schema': 1, 'previous': previous, 'next': value})
            else:
                engine._atomic_json(release_path, releases)
                engine._atomic_json(config_path, value)
        instructions = _instructions(state, python, runtime / 'update_launcher.py', config_path)
        if steam_helper is not None:
            owned_lines = () if previous is None else (_launch_options(
                previous['python'], Path(previous['runtime']) / 'update_launcher.py', config_path),)
            steam_result = steam_helper.configure(archive, state, instructions.launch_options,
                                                  owned_lines=owned_lines, consent=steam_consent,
                                                  private=_private)
            if migrating:
                engine._atomic_json(release_path, releases)
                engine._atomic_json(config_path, value)
                pending_path.unlink(missing_ok=True)
            instructions = _instructions(state, python, runtime / 'update_launcher.py', config_path,
                                         steam_configured=True, options_override=steam_result.launch_options)
        return instructions


def unregister(product, archive, state_dir=None, trusted_engine=None, already_locked=False,
               steam_helper=None, steam_consent=False):
    """Remove enrollment before uninstall so a removed mod cannot reinstall."""
    engine = trusted_engine if trusted_engine is not None else _engine()
    if already_locked and trusted_engine is None:
        raise ValueError('Removal under an existing lock requires the retained trusted engine')
    if product not in engine.PRODUCTS:
        raise ValueError('Unsupported automatic-update product')
    archive = _absolute(archive)
    state = _absolute(state_dir) if state_dir is not None else state_directory(archive)
    config_path = _safe(state / 'config.json')
    if not config_path.exists():
        return False
    with contextlib.nullcontext() if already_locked else engine.exclusive(state):
        value = _config(engine, config_path)
        if value['game'] != str(archive):
            raise ValueError('Updater registration belongs to a different game')
        products = [item for item in value['products'] if item['product'] != product]
        if products == value['products']:
            # A prior removal may have disabled the last product before Steam
            # closed or its field restoration completed. Retry that cleanup.
            if not products and steam_helper is not None:
                steam_helper.remove(archive, state, consent=steam_consent, private=_private)
            return False
        value['products'] = products
        engine._atomic_json(config_path, value)
        if not products and steam_helper is not None:
            steam_helper.remove(archive, state, consent=steam_consent, private=_private)
    return True

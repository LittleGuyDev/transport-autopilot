"""Explicit manual-install Steam setup; never imported by the pinned runtime.

Only the selected user's Geoplanetical LaunchOptions value is owned here. Full
configuration backups are recovery evidence, never whole-file restore sources.
Callers must hold their enrollment lock and supply their private-directory helper.
"""
from dataclasses import dataclass, replace
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

APP_ID = '940230'
STEAM_ID_BASE = 76561197960265728
MAX_FILE = 16 * 1024 * 1024
METADATA = 'steam-launch.json'


class SteamError(ValueError):
    """Safe, actionable error text that contains no private account/config data."""


@dataclass(frozen=True)
class Token:
    value: str
    start: int
    end: int


@dataclass
class Entry:
    key: Token
    value: object


@dataclass
class Object:
    children: dict
    opening: int
    closing: int


def parse(data):
    """Bounded KeyValues1 reader with exact byte spans and strict ambiguity checks."""
    if not isinstance(data, bytes) or len(data) > MAX_FILE or b'\0' in data:
        raise SteamError('Steam configuration is invalid or too large; no settings changed.')
    try:
        data.decode('utf-8-sig')
    except UnicodeError:
        raise SteamError('Steam configuration is not valid UTF-8; no settings changed.')
    pos = 3 if data.startswith(b'\xef\xbb\xbf') else 0
    tokens = []
    length = len(data)
    escapes = {ord('n'): b'\n', ord('r'): b'\r', ord('t'): b'\t', ord('"'): b'"', ord('\\'): b'\\'}
    while pos < length:
        if data[pos] in b' \t\r\n':
            pos += 1
            continue
        if data[pos:pos+2] == b'//':
            end = data.find(b'\n', pos + 2)
            pos = length if end < 0 else end + 1
            continue
        if data[pos:pos+2] == b'/*':
            end = data.find(b'*/', pos + 2)
            if end < 0:
                raise SteamError('Steam configuration has an unfinished comment.')
            pos = end + 2
            continue
        start = pos
        byte = data[pos]
        if byte in b'{}':
            tokens.append(Token(chr(byte), pos, pos + 1))
            pos += 1
        elif byte == ord('"'):
            pos += 1
            value = bytearray()
            while pos < length and data[pos] != ord('"'):
                if data[pos] == ord('\\'):
                    pos += 1
                    if pos >= length or data[pos] not in escapes:
                        raise SteamError('Steam configuration has an unsupported string escape.')
                    value.extend(escapes[data[pos]])
                else:
                    if data[pos] < 32 and data[pos] not in b'\t\r\n':
                        raise SteamError('Steam configuration contains an invalid string.')
                    value.append(data[pos])
                pos += 1
            if pos >= length:
                raise SteamError('Steam configuration has an unfinished string.')
            pos += 1
            tokens.append(Token(value.decode('utf-8'), start, pos))
        else:
            while pos < length and data[pos] not in b' \t\r\n{}"':
                if data[pos:pos+2] in (b'//', b'/*'):
                    break
                pos += 1
            if pos == start or any(c in data[start:pos] for c in b'[]#'):
                raise SteamError('Steam configuration contains unsupported syntax.')
            tokens.append(Token(data[start:pos].decode('utf-8'), start, pos))
        if len(tokens) > 200000 or pos - start > 1024 * 1024:
            raise SteamError('Steam configuration exceeds safe parsing limits.')

    index = 0

    def obj(depth, opening=-1):
        nonlocal index
        if depth > 64:
            raise SteamError('Steam configuration nesting exceeds safe limits.')
        children = {}
        while index < len(tokens):
            key = tokens[index]
            if key.value == '}' and data[key.start:key.end] == b'}':
                if opening < 0:
                    raise SteamError('Steam configuration contains an unexpected brace.')
                index += 1
                return Object(children, opening, key.start)
            if data[key.start:key.end] in (b'{', b'}'):
                raise SteamError('Steam configuration contains an invalid key.')
            index += 1
            if index >= len(tokens):
                raise SteamError('Steam configuration is missing a value.')
            value = tokens[index]
            index += 1
            raw = data[value.start:value.end]
            if raw == b'{':
                value = obj(depth + 1, value.start)
            elif raw == b'}':
                raise SteamError('Steam configuration is missing a value.')
            folded = key.value.casefold()
            if folded in children:
                raise SteamError('Steam configuration contains duplicate keys; resolve them in Steam first.')
            children[folded] = Entry(key, value)
        if opening >= 0:
            raise SteamError('Steam configuration has an unfinished object.')
        return Object(children, -1, length)

    return obj(0)


def _object(parent, key, required=True):
    entry = parent.children.get(key.casefold())
    if entry is None and not required:
        return None
    if entry is None or not isinstance(entry.value, Object):
        raise SteamError('Steam configuration is missing an expected object.')
    return entry.value


def _value(parent, key, required=True):
    entry = parent.children.get(key.casefold())
    if entry is None and not required:
        return None
    if entry is None or not isinstance(entry.value, Token):
        raise SteamError('Steam configuration has an invalid expected value.')
    return entry.value.value


def _encode(value):
    return ('"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
            .replace('\r', '\\r').replace('\t', '\\t') + '"').encode('utf-8')


def _launch(data):
    current = _object(parse(data), 'UserLocalConfigStore')
    for name in ('Software', 'Valve', 'Steam', 'apps', APP_ID):
        current = _object(current, name, False)
        if current is None:
            return None
    entry = current.children.get('launchoptions')
    if entry is not None and not isinstance(entry.value, Token):
        raise SteamError('Steam Launch Options has an invalid object value.')
    return entry


def replace_launch_options(data, value, raw_value=None):
    """Replace one token, insert its missing ancestors, or remove only that field."""
    field = _launch(data)
    if field is not None:
        start = field.key.start if value is None else field.value.start
        end = field.value.end
        replacement = b'' if value is None else (_encode(value) if raw_value is None else raw_value)
        result = data[:start] + replacement + data[end:]
    elif value is None:
        return data
    else:
        current = _object(parse(data), 'UserLocalConfigStore')
        names = ('Software', 'Valve', 'Steam', 'apps', APP_ID)
        missing = []
        for index, name in enumerate(names):
            following = _object(current, name, False)
            if following is None:
                missing = names[index:]
                break
            current = following
        newline = b'\r\n' if b'\r\n' in data else b'\n'
        snippet = b'"LaunchOptions"\t\t' + (_encode(value) if raw_value is None else raw_value) + newline
        for name in reversed(missing):
            snippet = _encode(name) + newline + b'{' + newline + snippet + b'}' + newline
        result = data[:current.closing] + newline + snippet + data[current.closing:]
    actual = _launch(result)
    if (None if actual is None else actual.value.value) != value:
        raise SteamError('Steam Launch Options readback failed.')
    return result


def _safe(path):
    path = Path(path)
    if not path.is_absolute():
        raise SteamError('Steam setup requires absolute paths.')
    path = Path(os.path.abspath(str(path)))
    for item in (path,) + tuple(path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise SteamError('Steam setup refuses symbolic links and reparse points.')
    return path


def _read(path):
    path = _safe(path)
    with path.open('rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE:
            raise SteamError('Steam configuration is not a bounded regular file.')
        data = source.read(MAX_FILE + 1)
        after = os.fstat(source.fileno())
    if _identity(before) != _identity(after) or len(data) != before.st_size:
        raise SteamError('Steam configuration changed during inspection; retry setup.')
    if _identity(path.stat()) != _identity(after):
        raise SteamError('Steam configuration was replaced during inspection; retry setup.')
    return data, _identity(after)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, stat.S_IMODE(info.st_mode))


def _registry():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
            root = winreg.QueryValueEx(key, 'SteamPath')[0]
            try:
                auto_login = winreg.QueryValueEx(key, 'AutoLoginUser')[0]
            except OSError:
                auto_login = None
    except OSError:
        raise SteamError('Steam has no configured Windows installation. Open Steam and sign in first.')
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam\ActiveProcess') as key:
            active = winreg.QueryValueEx(key, 'ActiveUser')[0]
    except OSError:
        active = 0
    if (not isinstance(root, str) or type(active) is not int or not 0 <= active <= 0xffffffff
            or auto_login is not None and not isinstance(auto_login, str)):
        raise SteamError('Steam has no unambiguous selected Windows account. Open Steam and sign in first.')
    return {'SteamPath': root, 'ActiveUser': active, 'AutoLoginUser': auto_login}


@dataclass(frozen=True)
class Target:
    root: Path
    executable: Path
    localconfig: Path
    evidence: tuple
    selector: object


def discover(archive, platform=None, steam_root=None, windows_registry=None):
    """Resolve the selected Steam account and prove the enrolled library owns it."""
    platform = sys.platform if platform is None else platform
    archive = _safe(archive)
    if not archive.is_file():
        raise SteamError('The enrolled Steam game archive is unavailable.')
    registry = None
    if platform == 'win32':
        registry = _registry() if windows_registry is None else windows_registry()
        root = _safe(registry['SteamPath'])
        if steam_root is not None and _safe(steam_root) != root:
            raise SteamError('The selected Steam root does not match the current Windows account.')
        account_id = registry.get('ActiveUser')
        windows_auto = registry.get('AutoLoginUser')
        if type(account_id) is not int or not 0 <= account_id <= 0xffffffff:
            raise SteamError('Steam has no unambiguous selected Windows account.')
        if windows_auto is not None and not isinstance(windows_auto, str):
            raise SteamError('Steam has an invalid selected Windows account record.')
        executable = root / 'steam.exe'
        selector = (str(root), account_id)
    elif platform == 'darwin':
        root = _safe(steam_root if steam_root is not None else Path.home() / 'Library/Application Support/Steam')
        executable = root / 'Steam.AppBundle/Steam/Contents/MacOS/steam_osx'
        selector = None
    else:
        raise SteamError('Automatic Steam setup supports native Windows and macOS only.')
    executable_info = _safe(executable).stat()
    if not stat.S_ISREG(executable_info.st_mode):
        raise SteamError('The selected Steam client executable is unavailable.')
    evidence = [(executable, None, _identity(executable_info))]

    def inspect(path):
        content, identity = _read(path)
        evidence.append((path, content, identity))
        return parse(content)

    users = _object(inspect(root / 'config/loginusers.vdf'), 'users')
    selected = []
    account_names = set()
    auto = None
    if platform == 'darwin':
        config = inspect(root / 'registry.vdf')
        for name in ('Registry', 'HKCU', 'Software', 'Valve', 'Steam'):
            # Steam's macOS registry has HKCU as its top-level object.
            if name == 'Registry' and 'registry' not in config.children:
                continue
            config = _object(config, name)
        auto = _value(config, 'AutoLoginUser')
        if not auto:
            raise SteamError('Steam has no selected Mac account. Open Steam and sign in first.')
    for key, entry in users.children.items():
        if not re.fullmatch(r'[0-9]{17}', key) or not isinstance(entry.value, Object):
            raise SteamError('Steam login account records are invalid.')
        numeric = int(key) - STEAM_ID_BASE
        if not 0 < numeric <= 0xffffffff:
            raise SteamError('Steam login account records have an unsupported identity.')
        name = _value(entry.value, 'AccountName')
        if not name or name.casefold() in account_names:
            raise SteamError('Steam login account records have duplicate or missing account names.')
        account_names.add(name.casefold())
        automatic = _value(entry.value, 'AutoLogin', False)
        windows_selected = False
        if platform == 'win32':
            if account_id:
                windows_selected = numeric == account_id
            elif windows_auto:
                windows_selected = name.casefold() == windows_auto.casefold()
            else:
                windows_selected = automatic == '1'
        if (windows_selected or
                platform == 'darwin' and name.casefold() == auto.casefold()):
            selected.append(numeric)
    if len(selected) != 1:
        raise SteamError('Steam account selection is ambiguous. Open Steam, sign in, then retry setup.')
    localconfig = _safe(root / 'userdata' / str(selected[0]) / 'config/localconfig.vdf')
    inspect(localconfig)
    libraries = [root]
    folder_path = root / 'steamapps/libraryfolders.vdf'
    folders = _object(inspect(folder_path), 'libraryfolders')
    for key, entry in folders.children.items():
        if key.isdecimal():
            value = _value(entry.value, 'path') if isinstance(entry.value, Object) else entry.value.value
            library = _safe(value)
            if library not in libraries:
                libraries.append(library)
    matching = []
    for library in libraries:
        manifest = library / ('steamapps/appmanifest_' + APP_ID + '.acf')
        if not manifest.exists():
            continue
        app = _object(inspect(manifest), 'AppState')
        if _value(app, 'appid') != APP_ID:
            raise SteamError('Steam game manifest has an unexpected application identity.')
        directory = _value(app, 'installdir')
        if (not directory or directory in ('.', '..') or '/' in directory or '\\' in directory
                or ':' in directory):
            raise SteamError('Steam game manifest has an unsafe installation directory.')
        game = library / 'steamapps/common' / directory
        candidates = (game / 'resources/app.asar', game / 'Contents/Resources/app.asar',
                      game / 'Geoplanetical.app/Contents/Resources/app.asar')
        if archive in candidates:
            matching.append(manifest)
    if len(matching) != 1:
        raise SteamError('The selected game installation does not belong to this Steam library; select its installed Steam copy.')
    selector = (str(root), selected[0])
    return Target(root, _safe(executable), localconfig, tuple(evidence), selector)


def _metadata(state):
    path = _safe(state / METADATA)
    if not path.exists():
        return None
    try:
        data = json.loads(_read(path)[0].decode('utf-8'))
    except (UnicodeError, ValueError):
        raise SteamError('Steam setup recovery record is invalid; keep it for manual recovery.')
    required = {'schema', 'archive', 'localconfig', 'original_value', 'original_token', 'current',
                'previous', 'backup', 'phase', 'operation'}
    if (not isinstance(data, dict) or set(data) != required or data['schema'] != 1
            or data['phase'] not in ('prepared', 'complete', 'removed')
            or data['operation'] not in ('configure', 'remove')
            or type(data['schema']) is not int
            or any(not isinstance(data[k], str) for k in ('archive', 'localconfig', 'backup'))
            or data['current'] is not None and not isinstance(data['current'], str)
            or data['original_value'] is not None and not isinstance(data['original_value'], str)
            or data['previous'] is not None and not isinstance(data['previous'], str)):
        raise SteamError('Steam setup recovery record is invalid; keep it for manual recovery.')
    try:
        raw = base64.b64decode(data['original_token'], validate=True) if data['original_token'] is not None else None
        if raw is not None:
            token = _value(parse(b'"v" ' + raw), 'v')
            if token != data['original_value']:
                raise ValueError()
        elif data['original_value'] is not None:
            raise ValueError()
    except (ValueError, TypeError):
        raise SteamError('Steam setup original-value record is invalid.')
    return data


def _plain_flags(value):
    if value is None or not value.strip():
        return
    # A plain game argument list begins with a switch. Unknown commands, shell
    # syntax, Steam placeholders, and environment assignments are never wrapped.
    if (not value.lstrip().startswith('-') or '%' in value or
            any(char in value for char in '\r\n\0;&|<>`$')):
        raise SteamError('Existing Steam Launch Options use an unknown wrapper. Keep them unchanged and review Launch Options manually.')
    quoted = False
    escaped = False
    for char in value:
        if escaped:
            escaped = False
        elif char == '\\':
            escaped = True
        elif char == '"':
            quoted = not quoted
    if quoted:
        raise SteamError('Existing Steam Launch Options have an unfinished quote; review them in Steam first.')


@dataclass(frozen=True)
class Plan:
    archive: Path
    state: Path
    target: Target
    source: bytes
    identity: tuple
    updated: bytes
    record: dict
    platform: str
    steam_root: object
    windows_registry: object


@dataclass(frozen=True)
class Result:
    changed: bool
    launch_options: str
    restored: bool = False

    @property
    def message(self):
        return 'Steam Launch Options restored.' if self.restored else 'Steam Launch Options configured.'


def plan(archive, state, generated_line, *, platform=None, steam_root=None, windows_registry=None,
         owned_lines=()):
    platform = sys.platform if platform is None else platform
    archive, state = _safe(archive), _safe(state)
    if (not isinstance(generated_line, str) or generated_line.count('%command%') != 1
            or not generated_line.endswith(' %command%') or '\n' in generated_line or '\r' in generated_line):
        raise SteamError('Generated Steam launch command is invalid.')
    target = discover(archive, platform, steam_root, windows_registry)
    source, identity = _read(target.localconfig)
    entry = _launch(source)
    current = None if entry is None else entry.value.value
    record = _metadata(state)
    if record is not None:
        if record['archive'] != str(archive) or record['localconfig'] != str(target.localconfig):
            raise SteamError('Steam recovery record belongs to a different installation or account.')
        if record['phase'] == 'removed':
            record = None
        elif current not in (record['current'], record['previous']) or (
                record['phase'] == 'complete' and current != record['current']):
            raise SteamError('Steam Launch Options changed outside this installer. Keep them unchanged and review them manually.')
    if record is None:
        original = current
        original_token = None if entry is None else source[entry.value.start:entry.value.end]
        for known in (generated_line,) + tuple(owned_lines):
            if not isinstance(known, str) or not known.endswith(' %command%') or known.count('%command%') != 1:
                raise SteamError('A recorded owned Steam launch command is invalid.')
            if current == known or current is not None and current.startswith(known + ' '):
                original = None if current == known else current[len(known) + 1:]
                _plain_flags(original)
                original_token = None if original is None else _encode(original)
                break
        _plain_flags(original)
        record = {'schema': 1, 'archive': str(archive), 'localconfig': str(target.localconfig),
                  'original_value': original, 'original_token': None if original_token is None else
                  base64.b64encode(original_token).decode('ascii'),
                  'current': '', 'previous': current, 'backup': '', 'phase': 'prepared',
                  'operation': 'configure'}
    suffix = record['original_value']
    wanted = generated_line + (' ' + suffix if suffix else '')
    record = dict(record, current=wanted, previous=current, phase='prepared', operation='configure')
    return Plan(archive, state, target, source, identity, replace_launch_options(source, wanted),
                record, platform, steam_root, windows_registry)


class Process:
    """Native inspection and graceful shutdown only; never terminates a process."""
    def __init__(self, platform):
        self.platform = platform

    def is_running(self, executable):
        if self.platform == 'win32':
            import ctypes
            buffer = ctypes.create_unicode_buffer(32768)
            size = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
            if not 0 < size < len(buffer) or not Path(buffer.value).is_absolute():
                raise SteamError('Cannot inspect Steam processes safely.')
            result = subprocess.run([str(Path(buffer.value) / 'tasklist.exe'), '/FO', 'CSV', '/NH'],
                                    check=True, capture_output=True, text=True, timeout=10)
            return any(row and row[0].casefold() in ('steam.exe', 'steamwebhelper.exe')
                       for row in csv.reader(io.StringIO(result.stdout)))
        result = subprocess.run(['/bin/ps', '-axo', 'comm='], check=True,
                                capture_output=True, text=True, timeout=10)
        return any(Path(line.strip()).name.casefold() in ('steam_osx', 'steam', 'steamwebhelper')
                   for line in result.stdout.splitlines())

    def shutdown(self, executable):
        subprocess.run([str(_safe(executable)), '-shutdown'], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)

    def restart(self, executable):
        subprocess.Popen([str(_safe(executable)), '-silent'], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)

    def wait(self):
        time.sleep(.25)


def _sync_directory(path):
    if os.name != 'nt':
        descriptor = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _replace_windows(path, temporary):
    # ReplaceFile preserves the existing file's DACL, attributes and creation
    # time. Flags 0 deliberately refuse ACL/metadata merge failures.
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = kernel.ReplaceFileW
    function.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                         wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    function.restype = wintypes.BOOL
    if not function(str(path), str(temporary), None, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


def _copy_windows_dacl(source, destination):
    """Protect the temporary file before any private localconfig bytes are written."""
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    get = advapi.GetFileSecurityW
    get.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    get.restype = wintypes.BOOL
    needed = wintypes.DWORD()
    get(str(source), 4, None, 0, ctypes.byref(needed))
    if not needed.value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(needed.value)
    if not get(str(source), 4, descriptor, needed, ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    set_security = advapi.SetFileSecurityW
    set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    set_security.restype = wintypes.BOOL
    if not set_security(str(destination), 0x80000004, descriptor):
        raise ctypes.WinError(ctypes.get_last_error())


def _atomic(path, data, mode=0o600, before_replace=None, preserve_metadata=False):
    path = _safe(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.steam-', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            if os.name == 'nt' and preserve_metadata:
                _copy_windows_dacl(path, temporary)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.chmod(temporary, mode)
        if before_replace is not None:
            before_replace()
        _safe(path)
        if os.name == 'nt' and preserve_metadata:
            _replace_windows(path, temporary)
        else:
            os.replace(temporary, path)
        _sync_directory(path.parent)
        if _read(path)[0] != data:
            raise SteamError('Steam setup write did not pass readback; preserve recovery files.')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _save_record(state, record):
    _atomic(state / METADATA, (json.dumps(record, sort_keys=True) + '\n').encode('utf-8'))


def _check_plan(prepared):
    target = discover(prepared.archive, prepared.platform, prepared.steam_root, prepared.windows_registry)
    if (target.root != prepared.target.root or target.localconfig != prepared.target.localconfig
            or target.selector != prepared.target.selector or target.evidence != prepared.target.evidence):
        raise SteamError('Steam account or configuration changed during setup; retry setup.')
    data, identity = _read(prepared.target.localconfig)
    if data != prepared.source or identity != prepared.identity:
        raise SteamError('Steam configuration changed during setup; retry setup.')


def _after_shutdown(prepared):
    """Steam may flush unrelated bytes on clean exit; preserve those fresh bytes."""
    target = discover(prepared.archive, prepared.platform, prepared.steam_root, prepared.windows_registry)
    if (target.root != prepared.target.root or target.localconfig != prepared.target.localconfig
            or target.selector != prepared.target.selector):
        raise SteamError('Steam account selection changed while closing; retry setup.')
    source, identity = _read(target.localconfig)
    before = _launch(prepared.source)
    after = _launch(source)
    if (None if before is None else before.value.value) != (None if after is None else after.value.value):
        raise SteamError('Steam Launch Options changed while closing; review them before retrying setup.')
    wanted = _launch(prepared.updated)
    value = None if wanted is None else wanted.value.value
    raw = None if wanted is None else prepared.updated[wanted.value.start:wanted.value.end]
    return replace(prepared, target=target, source=source, identity=identity,
                   updated=replace_launch_options(source, value, raw))


def apply(prepared, *, consent=False, process=None, private=None, timeout=30):
    """Apply a preflight with consent to gracefully close/reopen Steam if needed."""
    if private is None:
        raise SteamError('Steam setup requires the installer private-state helper.')
    private(prepared.state)
    _check_plan(prepared)
    process = Process(prepared.platform) if process is None else process
    was_running = process.is_running(prepared.target.executable)
    if was_running and not consent:
        raise SteamError('Save and close Steam games, then allow the installer to close and reopen Steam.')
    restart = False
    try:
        if was_running:
            restart = True
            process.shutdown(prepared.target.executable)
            until = time.monotonic() + min(30, max(0, timeout))
            while process.is_running(prepared.target.executable) and time.monotonic() < until:
                process.wait()
            if process.is_running(prepared.target.executable):
                restart = False  # Still running: do not start a second client.
                raise SteamError('Steam did not close in time. No Steam settings changed; close it and retry setup.')
        if was_running:
            prepared = _after_shutdown(prepared)
        _check_plan(prepared)
        digest = hashlib.sha256(prepared.source).hexdigest()
        backup = prepared.state / ('steam-localconfig-' + digest + '.vdf.backup')
        if backup.exists():
            if _read(backup)[0] != prepared.source:
                raise SteamError('Steam configuration backup conflicts with its recovery record.')
        else:
            _atomic(backup, prepared.source)
        record = dict(prepared.record, backup=backup.name)
        if prepared.updated == prepared.source:
            _save_record(prepared.state, dict(record, phase='complete'))
            return Result(False, record['current'] or '')
        _save_record(prepared.state, record)

        def check():
            _check_plan(prepared)
            if process.is_running(prepared.target.executable):
                raise SteamError('Steam restarted during setup. No Steam settings changed; close it and retry.')
        _atomic(prepared.target.localconfig, prepared.updated, prepared.identity[-1], check,
                preserve_metadata=True)
        _save_record(prepared.state, dict(record, phase='complete'))
        return Result(True, record['current'] or '')
    finally:
        if restart and not process.is_running(prepared.target.executable):
            process.restart(prepared.target.executable)


def configure(archive, state, generated_line, *, platform=None, steam_root=None,
              windows_registry=None, consent=False, process=None, private=None, owned_lines=()):
    return apply(plan(archive, state, generated_line, platform=platform, steam_root=steam_root,
                      windows_registry=windows_registry, owned_lines=owned_lines),
                 consent=consent, process=process, private=private)


def remove(archive, state, *, platform=None, steam_root=None, windows_registry=None,
           consent=False, process=None, private=None):
    """Final uninstall: restore only the owned field and keep external edits."""
    platform = sys.platform if platform is None else platform
    archive, state = _safe(archive), _safe(state)
    record = _metadata(state)
    if record is None or record['phase'] == 'removed':
        return Result(False, '', False)
    target = discover(archive, platform, steam_root, windows_registry)
    if record['archive'] != str(archive) or record['localconfig'] != str(target.localconfig):
        raise SteamError('Steam recovery record belongs to another installation or account; settings preserved.')
    source, identity = _read(target.localconfig)
    entry = _launch(source)
    current = None if entry is None else entry.value.value
    if current != record['current'] and not (record['operation'] == 'remove' and
            record['phase'] == 'prepared' and current == record['previous']):
        # User/Steam changes win. Never restore the old whole configuration.
        return Result(False, current or '', False)
    raw = None if record['original_token'] is None else base64.b64decode(record['original_token'])
    updated = replace_launch_options(source, record['original_value'], raw)
    removal = dict(record, previous=current, current=record['original_value'],
                   phase='prepared', operation='remove')
    prepared = Plan(archive, state, target, source, identity, updated, removal,
                    platform, steam_root, windows_registry)
    result = apply(prepared, consent=consent, process=process, private=private)
    _save_record(state, dict(record, phase='removed'))
    return Result(result.changed, record['original_value'] or '', True)

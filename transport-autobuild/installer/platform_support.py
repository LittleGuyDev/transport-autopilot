"""Native platform helpers, written independently for Transport Autopilot.

Integrity format: https://www.electronjs.org/docs/latest/tutorial/asar-integrity
No game executable is launched to obtain a Node runtime.
"""
import csv
import ctypes
import hashlib
import io
import json
import ntpath
import os
from pathlib import Path
import plistlib
import re
import shutil
import struct
import subprocess
import sys

APP_ID = '940230'
FUSE_SENTINEL = b'dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX'


def steam_roots():
    roots = []
    if sys.platform == 'darwin':
        roots.append(Path.home() / 'Library/Application Support/Steam')
    elif sys.platform == 'win32':
        import winreg
        for hive, key, name in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
                                (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath'),
                                (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Valve\Steam', 'InstallPath')):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    roots.append(Path(winreg.QueryValueEx(handle, name)[0]))
            except OSError:
                pass
        for name in ('ProgramFiles(x86)', 'ProgramFiles'):
            if os.environ.get(name):
                roots.append(Path(os.environ[name]) / 'Steam')
    else:
        roots.extend((Path.home() / '.local/share/Steam', Path.home() / '.steam/steam'))
    return list(dict.fromkeys(Path(os.path.abspath(str(p))) for p in roots if p.is_dir()))


def steam_libraries():
    libraries = list(steam_roots())
    for root in list(libraries):
        config = root / 'steamapps/libraryfolders.vdf'
        if config.is_file():
            text = config.read_text(encoding='utf-8-sig')
            for value in re.findall(r'"path"\s*"((?:\\.|[^"\\])*)"', text, re.I):
                libraries.append(Path(value.replace('\\\\', '\\').replace('\\"', '"')))
    return list(dict.fromkeys(Path(os.path.abspath(str(p))) for p in libraries if p.is_dir()))


def normalize_archive(path):
    path = Path(os.path.abspath(str(Path(path).expanduser())))
    candidates = [path] if path.name == 'app.asar' else [
        path / 'Contents/Resources/app.asar', path / 'Geoplanetical.app/Contents/Resources/app.asar',
        path / 'resources/app.asar', path / 'app.asar']
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError('No Geoplanetical app.asar found at: ' + str(path))


def discover_installations():
    result = []
    for library in steam_libraries():
        apps = library / 'steamapps'
        manifest = apps / ('appmanifest_' + APP_ID + '.acf')
        install_dir = 'Geoplanetical'
        if manifest.is_file():
            match = re.search(r'"installdir"\s*"([^"\r\n]+)"', manifest.read_text(encoding='utf-8-sig'), re.I)
            if match:
                install_dir = match.group(1)
                if Path(install_dir).name != install_dir or install_dir in ('.', '..'):
                    raise ValueError('Unsafe Steam installation directory in manifest')
        try:
            result.append(normalize_archive(apps / 'common' / install_dir))
        except ValueError:
            pass
    return list(dict.fromkeys(result))


def macos_app(archive):
    archive = Path(os.path.abspath(str(archive)))
    if archive.parent.name == 'Resources' and archive.parent.parent.name == 'Contents':
        return archive.parent.parent.parent
    return None


def _windows_tasklist():
    """Resolve the OS utility without trusting the caller's cwd or environment."""
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    get_system_directory = kernel32.GetSystemDirectoryW
    get_system_directory.argtypes = [ctypes.POINTER(ctypes.c_wchar), ctypes.c_uint]
    get_system_directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_system_directory(buffer, len(buffer))
    # A return value at least as large as the buffer means it did not fit.
    # Never continue with a truncated, empty, relative or malformed path.
    if not 0 < length < len(buffer):
        raise OSError('GetSystemDirectoryW failed or returned a truncated path')
    directory = buffer[:length]
    if (buffer[length] != '\0' or '\0' in directory
            or not ntpath.isabs(directory) or not ntpath.splitdrive(directory)[0]):
        raise OSError('GetSystemDirectoryW returned an invalid system directory')
    return ntpath.join(directory, 'tasklist.exe')


def game_running(archive):
    """Return true on a matching native process; inspection failures are errors."""
    if sys.platform == 'win32':
        # Use only Windows' own utility, even when Python is launched from an
        # untrusted directory or with an attacker-controlled PATH/SystemRoot.
        result = subprocess.run([_windows_tasklist(), '/FO', 'CSV', '/NH'], capture_output=True, text=True, check=True)
        for row in csv.reader(io.StringIO(result.stdout)):
            if row and ('geoplanetical' in row[0].casefold()):
                return True
        return False
    result = subprocess.run(['/bin/ps', '-axo', 'pid=,comm='], capture_output=True, text=True, check=True)
    own = os.getpid()
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and int(fields[0]) != own:
            command = fields[1].casefold()
            if 'geoplanetical' in Path(command).name or 'geoplanetical.app/contents/' in command:
                return True
    return False


def fuse_states(binary):
    data = Path(binary).read_bytes()
    index = data.find(FUSE_SENTINEL)
    if index < 0:
        raise ValueError('Electron fuse configuration not found')
    pos = index + len(FUSE_SENTINEL)
    if pos + 2 > len(data) or data[pos] != 1:
        raise ValueError('Unsupported Electron fuse version')
    length = data[pos + 1]
    wire = data[pos + 2:pos + 2 + length]
    if len(wire) != length:
        raise ValueError('Truncated Electron fuse configuration')
    return dict(enumerate(chr(c) for c in wire))


def macos_integrity_changes(archive, old_hash, new_hash):
    app = macos_app(archive)
    if app is None:
        return {}
    path = app / 'Contents/Info.plist'
    original = path.read_bytes()
    info = plistlib.loads(original)
    entry = info.get('ElectronAsarIntegrity', {}).get('Resources/app.asar')
    if not isinstance(entry, dict) or entry.get('algorithm') != 'SHA256' or entry.get('hash') != old_hash:
        raise ValueError('The macOS embedded archive integrity does not match the current archive')
    entry['hash'] = new_hash
    fmt = plistlib.FMT_BINARY if original.startswith(b'bplist') else plistlib.FMT_XML
    return {path: plistlib.dumps(info, fmt=fmt, sort_keys=False)}


def resign_macos(app):
    """Reseal only outer bundle. Caller must back up all changed signing files."""
    app = Path(app)
    if sys.platform != 'darwin' or not shutil.which('codesign'):
        raise RuntimeError('Native macOS codesign is required for this installation')
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-',
                    '--preserve-metadata=identifier,entitlements,flags,runtime',
                    str(app)], check=True, capture_output=True, text=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)],
                   check=True, capture_output=True, text=True)


def macos_signature_paths(archive):
    """Files outer-bundle ad-hoc signing can replace, for transactional backup."""
    app = macos_app(archive)
    if app is None:
        return []
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    executable = info.get('CFBundleExecutable')
    if not executable or Path(executable).name != executable:
        raise ValueError('Unsafe CFBundleExecutable')
    return [app / 'Contents/MacOS' / executable, app / 'Contents/_CodeSignature/CodeResources']


def _pe_integrity_resources(data):
    """Locate only named INTEGRITY/ELECTRONASAR PE resource payloads."""
    def u16(pos): return struct.unpack_from('<H', data, pos)[0]
    def u32(pos): return struct.unpack_from('<I', data, pos)[0]
    if data[:2] != b'MZ':
        raise ValueError('Not a Windows executable')
    pe = u32(0x3c)
    if data[pe:pe+4] != b'PE\0\0':
        raise ValueError('Invalid Windows PE header')
    count, opt_size = u16(pe+6), u16(pe+20)
    optional = pe + 24
    magic = u16(optional)
    if magic not in (0x10b, 0x20b):
        raise ValueError('Unsupported Windows PE format')
    directory = optional + (96 if magic == 0x10b else 112)
    resource_rva, resource_size = u32(directory+16), u32(directory+20)
    sections = optional + opt_size
    def offset(rva, size=1):
        for index in range(count):
            sec = sections + index*40
            virtual_size, va, raw_size, raw = struct.unpack_from('<IIII', data, sec+8)
            delta = rva-va
            if 0 <= delta and delta+size <= raw_size and raw+delta+size <= len(data):
                return raw+delta
        raise ValueError('Invalid PE resource address')
    if not resource_rva or not resource_size:
        raise ValueError('No Windows integrity resource found')
    base = offset(resource_rva)
    def bounded(relative, size):
        if relative < 0 or relative+size > resource_size or base+relative+size > len(data):
            raise ValueError('Invalid PE resource bounds')
        return base+relative
    def entries(relative):
        pos = bounded(relative,16)
        n = u16(pos+12)+u16(pos+14)
        bounded(relative+16,n*8)
        for i in range(n):
            name, target = struct.unpack_from('<II', data,pos+16+i*8)
            if name & 0x80000000:
                p = bounded(name & 0x7fffffff, 2)
                length = u16(p)
                bounded((name & 0x7fffffff)+2,length*2)
                label = data[p+2:p+2+length*2].decode('utf-16le')
            else:
                label = name
            yield label, target
    results=[]
    for kind, target in entries(0):
        if str(kind).upper() != 'INTEGRITY' or not target & 0x80000000: continue
        for name, langs in entries(target & 0x7fffffff):
            if str(name).upper() != 'ELECTRONASAR' or not langs & 0x80000000: continue
            for language, entry in entries(langs & 0x7fffffff):
                if entry & 0x80000000: raise ValueError('Unexpected nested integrity resource')
                pos = bounded(entry,16)
                rva, size = u32(pos),u32(pos+4)
                results.append((offset(rva,size),size))
    if not results: raise ValueError('No named Windows Electron ASAR integrity resource found')
    return results


def update_windows_integrity(executable, old_hash, new_hash):
    if not all(re.fullmatch(r'[0-9a-f]{64}', value) for value in (old_hash,new_hash)):
        raise ValueError('Expected SHA256 hexadecimal archive header hashes')
    data = bytes(executable)
    updated = bytearray(data)
    for offset,size in _pe_integrity_resources(data):
        payload = data[offset:offset+size]
        text = payload.rstrip(b'\0').decode('utf-8')
        entries = json.loads(text)
        if not isinstance(entries,list): raise ValueError('Invalid Windows integrity resource')
        matches=[e for e in entries if isinstance(e,dict) and e.get('file','').replace('\\','/').lower() == 'resources/app.asar']
        if len(matches)!=1 or matches[0].get('alg','').lower()!='sha256' or matches[0].get('value')!=old_hash:
            raise ValueError('Windows embedded archive integrity does not match the current archive')
        if payload.count(old_hash.encode()) != 1:
            raise ValueError('Ambiguous Windows archive integrity resource')
        updated[offset:offset+size] = payload.replace(old_hash.encode(),new_hash.encode())
    return bytes(updated)


def windows_integrity_changes(archive, old_hash, new_hash):
    archive = Path(archive)
    if macos_app(archive): return {}
    root = archive.parent.parent
    exe = root / 'Geoplanetical.exe'
    if not exe.is_file():
        raise ValueError('Geoplanetical.exe is missing next to resources')
    return {exe:update_windows_integrity(exe.read_bytes(), old_hash,new_hash)}

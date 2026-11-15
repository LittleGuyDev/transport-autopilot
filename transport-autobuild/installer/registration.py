"""Read-only plans for native Transport Autopilot content-pack registration."""
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
import zlib

from platform_support import steam_libraries

MOD_ID = 'littleguydev-transport-autopilot'
WORKSHOP_ID = '3839206386'
APP_ID = '940230'
FOLDER = 'transport-autopilot'
FILES = ('mod.json', 'thumbnail.png', 'workshop.json')


def _safe(path):
    """Do not resolve away evidence of symlinks or Windows junctions."""
    path = Path(os.path.abspath(str(path)))
    for part in (path,) + tuple(path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Refusing symlink or reparse path: ' + str(part))
    return path


def _json(path):
    path = _safe(path)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Metadata is too large: ' + str(path))
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Metadata must be an object: ' + str(path))
    return value


def _png(path):
    path = _safe(path)
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError('Thumbnail is too large')
    data = path.read_bytes()
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('Thumbnail must be PNG')
    pos = 8
    kinds = []
    while pos < len(data):
        if pos + 12 > len(data):
            raise ValueError('Truncated PNG thumbnail')
        length = struct.unpack_from('>I', data, pos)[0]
        end = pos + 12 + length
        if end > len(data):
            raise ValueError('Truncated PNG chunk')
        kind = data[pos+4:pos+8]
        if zlib.crc32(data[pos+4:end-4]) & 0xffffffff != struct.unpack_from('>I', data, end-4)[0]:
            raise ValueError('Invalid PNG checksum')
        if not kinds:
            if kind != b'IHDR' or length != 13:
                raise ValueError('Invalid PNG header')
            width, height = struct.unpack_from('>II', data, pos+8)
            if not (0 < width <= 8192 and 0 < height <= 8192):
                raise ValueError('Invalid thumbnail dimensions')
        kinds.append(kind)
        pos = end
        if kind == b'IEND':
            if length or pos != len(data):
                raise ValueError('Invalid PNG ending')
            break
    if not kinds or kinds[-1] != b'IEND' or b'IDAT' not in kinds:
        raise ValueError('Incomplete PNG thumbnail')
    return data


def _manifest(path):
    value = _json(path)
    if value.get('id') != MOD_ID or value.get('name') != 'Transport Autopilot':
        raise ValueError('Package is not the canonical Transport Autopilot content pack')
    version = value.get('version')
    if not isinstance(version, str) or len(version.split('.')) != 3 or not all(p.isdigit() for p in version.split('.')):
        raise ValueError('Invalid content-pack version')
    if value.get('contentApi') != 1 or value.get('tier') != 'presentation' or value.get('thumbnail') != 'thumbnail.png':
        raise ValueError('Unsupported Transport Autopilot content-pack metadata')
    if value.get('requires') != [] or value.get('provides') != {}:
        raise ValueError('Transport Autopilot content pack must not install dependencies or extra content')
    if not isinstance(value.get('description'), str) or not isinstance(value.get('authors'), list) or not value['authors'] or not all(isinstance(a,str) for a in value['authors']):
        raise ValueError('Invalid content-pack description or authors')
    return value


def profile_directory():
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/Geoplanetical'
    if sys.platform == 'win32':
        if not os.environ.get('APPDATA'):
            raise ValueError('Windows APPDATA is unavailable')
        return Path(os.environ['APPDATA']) / 'Geoplanetical'
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'Geoplanetical'


def _workshop_candidates(game_archive, libraries):
    archive = _safe(game_archive)
    result = []
    for ancestor in archive.parents:
        if ancestor.name == 'steamapps':
            result.append(ancestor / 'workshop/content' / APP_ID / WORKSHOP_ID)
            break
    for library in libraries:
        result.append(_safe(library) / 'steamapps/workshop/content' / APP_ID / WORKSHOP_ID)
    return list(dict.fromkeys(result))


def _receipt(profile):
    path = _safe(profile / '.transport-autopilot-installer/native-registration.json')
    if not path.exists():
        return path, None
    value = _json(path)
    if value.get('schema') != 1 or value.get('id') != MOD_ID or value.get('folder') != FOLDER:
        raise ValueError('Unrecognized native registration receipt')
    hashes = value.get('files')
    if not isinstance(hashes,dict) or set(hashes) != set(FILES) or not all(isinstance(h,str) and len(h)==64 and all(c in '0123456789abcdef' for c in h) for h in hashes.values()):
        raise ValueError('Invalid native registration receipt file list')
    return path, value


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _owned_cleanup(profile, receipt):
    result = {}
    folder = _safe(profile / 'mods' / FOLDER)
    if receipt:
        for name, digest in receipt['files'].items():
            path = _safe(folder / name)
            if path.exists():
                if not path.is_file() or _hash(path.read_bytes()) != digest:
                    raise ValueError('Owned native-pack file changed; refusing to remove: ' + str(path))
                result[path] = None
    return result


def _duplicates(mods, folder):
    matches = []
    if mods.exists():
        if not mods.is_dir():
            raise ValueError('Profile mods path is not a directory')
        for child in mods.iterdir():
            child = _safe(child)
            if not child.is_dir():
                continue
            manifest = _safe(child / 'mod.json')
            if not manifest.is_file():
                continue
            try:
                value = _json(manifest)
            except (ValueError, UnicodeError):
                continue
            if value.get('id') == MOD_ID:
                matches.append(child)
    if any(path != folder for path in matches) or len(matches) > 1:
        raise ValueError('Duplicate Transport Autopilot content pack in profile mods; resolve it before installation')


def plan_registration(package_dir, game_archive, *, profile_dir=None, libraries=None):
    """Return (path-to-bytes-or-None plan, status), without writing anything."""
    package = _safe(package_dir)
    manifest = _manifest(package / 'mod.json')
    thumbnail = _png(package / 'thumbnail.png')
    payload = {'mod.json': (package / 'mod.json').read_bytes(), 'thumbnail.png': thumbnail,
               'workshop.json': (json.dumps({'version':1, 'publishedFileId':WORKSHOP_ID, 'modId':MOD_ID, 'sent':{}}, indent=2) + '\n').encode()}
    profile = _safe(profile_dir if profile_dir is not None else profile_directory())
    mods = _safe(profile / 'mods')
    folder = _safe(mods / FOLDER)
    _duplicates(mods, folder)
    receipt_path, receipt = _receipt(profile)
    workshop = []
    for candidate in _workshop_candidates(game_archive, steam_libraries() if libraries is None else libraries):
        candidate = _safe(candidate)
        if not candidate.exists():
            continue
        manifest_path = _safe(candidate / 'mod.json')
        if not manifest_path.is_file():
            continue  # A downloaded non-native Workshop item does not register a native pack.
        source_manifest = _manifest(manifest_path)
        if source_manifest != manifest or _png(candidate / 'thumbnail.png') != thumbnail:
            raise ValueError('Installed Workshop content pack differs from this installer package')
        workshop.append(candidate)
    if len(workshop) > 1:
        raise ValueError('Multiple matching Workshop content packs found; select one Steam library before installation')
    if workshop:
        if folder.exists() and not receipt and any(_safe(folder / name).exists() for name in FILES):
            if not all(_safe(folder / name).is_file() and (folder / name).read_bytes() == data for name,data in payload.items()):
                raise ValueError('An unowned local Transport Autopilot folder conflicts with the Workshop pack')
            # Exact existing files can be adopted and retired in the same plan.
            changes = {folder / name:None for name in FILES}
        else:
            changes = _owned_cleanup(profile, receipt)
        if receipt:
            changes[receipt_path] = None
        return changes, {'source':'workshop', 'path':str(workshop[0]), 'version':manifest['version'], 'registered':True}
    changes = {}
    if folder.exists() and not folder.is_dir():
        raise ValueError('Native content-pack location is not a folder')
    # Uninstall leaves an empty directory. Reusing it cannot overwrite any
    # content; a nonempty unowned directory still needs exact managed files.
    if folder.exists() and not receipt and any(folder.iterdir()):
        if not all(_safe(folder / name).is_file() and (folder / name).read_bytes() == data for name,data in payload.items()):
            raise ValueError('Existing native-pack folder is unowned and differs; refusing overwrite')
    if receipt:
        _owned_cleanup(profile, receipt)  # Validate every still-present owned file before replacement.
    for name,data in payload.items():
        path = _safe(folder / name)
        if not path.exists() or path.read_bytes() != data:
            changes[path] = data
    record = {'schema':1, 'id':MOD_ID, 'folder':FOLDER, 'version':manifest['version'],
              'files':{name:_hash(data) for name,data in payload.items()}}
    encoded = (json.dumps(record, indent=2, sort_keys=True) + '\n').encode()
    if not receipt_path.exists() or receipt_path.read_bytes() != encoded:
        changes[receipt_path] = encoded
    return changes, {'source':'local', 'path':str(folder), 'version':manifest['version'], 'registered':True}


def plan_removal(package_dir, game_archive, *, profile_dir=None, libraries=None):
    # Workshop downloads and user mod preferences are deliberately untouched.
    profile = _safe(profile_dir if profile_dir is not None else profile_directory())
    _safe(profile / 'mods')
    receipt_path, receipt = _receipt(profile)
    changes = _owned_cleanup(profile, receipt)
    if receipt:
        changes[receipt_path] = None
    return changes, {'source':'local' if receipt else 'none', 'registered':False, 'removed_files':len(changes)}

"""Original Transport Autopilot installer primitives; Python standard library only.

ASAR layout is implemented from Electron's documented format, not from any
third-party game installer. See SOURCES.md for the primary format references.
No discovery, game launch, or mutation happens on import.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import struct
import tempfile
from typing import Callable, Iterable
from dataclasses import dataclass


class InstallError(RuntimeError):
    """A safe precondition failed; the caller should show this to the user."""



def validate_compatibility_policy(payload):
    """Validate signed release policy independently of the selected game."""
    policy = payload.get('compatibilityPolicy')
    if policy is None:
        return
    if (not isinstance(policy, dict) or set(policy) != {'mode', 'minimumGameVersion'} or
            policy.get('mode') != 'exact-hooks' or _release_version(policy.get('minimumGameVersion')) is None):
        raise InstallError('Invalid exact-hook compatibility policy.')


def _release_version(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})', value):
        return None
    return tuple(map(int, value.split('.')))


def allows_game_version(game, payload):
    """Newer stable releases may try the same exact, fail-closed hooks."""
    validate_compatibility_policy(payload)
    version = game.get('version')
    policy = payload.get('compatibilityPolicy')
    if policy is None:
        return version in payload.get('gameVersions', [])
    parsed = _release_version(version)
    return parsed is not None and parsed >= _release_version(policy['minimumGameVersion'])


def _is_link(path: Path) -> bool:
    """Include Windows junctions/reparse points as well as POSIX symlinks."""
    if path.is_symlink():
        return True
    try:
        return bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)
    except FileNotFoundError:
        return False


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


STREAM_CHUNK_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True)
class FileContent:
    """Content supplied by a file, opened only while hashing or copying."""
    path: Path

    def __post_init__(self):
        object.__setattr__(self, 'path', Path(self.path))

    @property
    def size(self) -> int:
        return self.path.stat().st_size


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        while True:
            block = source.read(STREAM_CHUNK_SIZE)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def content_sha(content: bytes | FileContent) -> str:
    return file_sha(content.path) if isinstance(content, FileContent) else sha256(content)


def _copy_stream(source, destination, count: int | None = None):
    while count is None or count > 0:
        size = STREAM_CHUNK_SIZE if count is None else min(STREAM_CHUNK_SIZE, count)
        block = source.read(size)
        if not block:
            if count:
                raise InstallError('Source file ended during streaming copy')
            break
        destination.write(block)
        if count is not None:
            count -= len(block)


def _source_identity(path: Path) -> tuple:
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def integrity(data: bytes, block_size: int = 4194304) -> dict:
    if type(block_size) is not int or block_size <= 0:
        raise InstallError('Invalid integrity block size')
    return dict(algorithm='SHA256', hash=sha256(data), blockSize=block_size,
                blocks=[sha256(data[i:i + block_size]) for i in range(0, len(data), block_size)]
                or [sha256(b'')])


def _json(data: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InstallError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise InstallError('Invalid JSON: ' + str(exc)) from exc


def _encode_header(header: dict) -> bytes:
    raw = json.dumps(header, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    padding = b'\0' * (-len(raw) % 4)
    pickle_bytes = struct.pack('<II', 4 + len(raw) + len(padding), len(raw)) + raw + padding
    return struct.pack('<II', 4, len(pickle_bytes)) + pickle_bytes


class AsarArchive:
    """Read ASAR and replace selected files, preserving all other byte ranges.

    Replacements are appended to the original data region. This preserves links,
    deduplicated offsets, padding, and unknown entry metadata without guessing
    how a particular packaging tool laid out the unchanged entries.
    """
    def __init__(self, data: bytes):
        self.data = data
        self.source_path = None
        self.source_identity = None
        self._initialize(data[:16], len(data), lambda start, size: data[start:start + size])
        self.payload = memoryview(data)[self.payload_start:]

    @classmethod
    def from_file(cls, path: Path) -> 'AsarArchive':
        """Load only the index; no archive handle or mapping survives this call."""
        result = cls.__new__(cls)
        result.source_path = Path(path).resolve()
        result.source_identity = _source_identity(result.source_path)
        result.data = None
        result.payload = None
        with result.source_path.open('rb') as source:
            first = source.read(16)
            def read_header(start, size):
                source.seek(start)
                return source.read(size)
            result._initialize(first, result.source_identity[2], read_header)
        result._check_source()
        return result

    def _initialize(self, first: bytes, total_size: int, read_header):
        if len(first) < 16:
            raise InstallError('Truncated ASAR header')
        size, header_size, payload_size, text_size = struct.unpack('<IIII', first)
        if (size != 4 or header_size < 8 or header_size % 4 or
                header_size > total_size - 8 or payload_size != header_size - 4 or
                text_size > header_size - 8 or text_size > 64 * 1024 * 1024):
            raise InstallError('Unsupported or invalid ASAR header')
        self.header_bytes = read_header(16, text_size)
        if len(self.header_bytes) != text_size:
            raise InstallError('Truncated ASAR index')
        self.header = _json(self.header_bytes)
        self.header_size = header_size
        self.payload_start = 8 + header_size
        self.payload_size = total_size - self.payload_start
        self.entries = {}
        self._walk(self.header, '')

    def _check_source(self):
        if self.source_path is not None and _source_identity(self.source_path) != self.source_identity:
            raise InstallError('ASAR source changed after it was opened')

    def _walk(self, node: dict, name: str):
        if not isinstance(node, dict):
            raise InstallError('Invalid ASAR entry: ' + name)
        if 'files' in node:
            if not isinstance(node['files'], dict):
                raise InstallError('Invalid ASAR directory: ' + name)
            for key, child in node['files'].items():
                if not key or key in ('.', '..') or any(c in key for c in ('/', '\\', '\0')):
                    raise InstallError('Unsafe ASAR path')
                self._walk(child, name + '/' + key if name else key)
        elif 'link' in node:
            if not isinstance(node['link'], str):
                raise InstallError('Invalid ASAR link')
            self.entries[name] = node
        else:
            if type(node.get('size')) is not int or node['size'] < 0:
                raise InstallError('Invalid ASAR file size: ' + name)
            if node.get('unpacked') is not True:
                offset = node.get('offset')
                if not isinstance(offset, str) or not offset.isascii() or not offset.isdecimal():
                    raise InstallError('Invalid ASAR file offset: ' + name)
                if int(offset) + node['size'] > self.payload_size:
                    raise InstallError('ASAR file exceeds archive: ' + name)
            self.entries[name] = node

    def entry(self, name: str) -> dict:
        if name not in self.entries or 'link' in self.entries[name]:
            raise InstallError('Missing or unsupported ASAR file: ' + name)
        return self.entries[name]

    def read(self, name: str, unpacked_root: Path | None = None) -> bytes:
        node = self.entry(name)
        if node.get('unpacked') is True:
            if unpacked_root is None:
                raise InstallError('Unpacked file requires a directory: ' + name)
            root = Path(unpacked_root).resolve()
            path = root.joinpath(*name.split('/'))
            if not _within(path.resolve(), root) or _is_link(path):
                raise InstallError('Unsafe unpacked file: ' + name)
            data = path.read_bytes()
        else:
            offset = int(node['offset'])
            self._check_source()
            if self.source_path is None:
                data = bytes(self.payload[offset:offset + node['size']])
            else:
                with self.source_path.open('rb') as source:
                    source.seek(self.payload_start + offset)
                    data = source.read(node['size'])
                self._check_source()
        if len(data) != node['size']:
            raise InstallError('ASAR file size mismatch: ' + name)
        if 'integrity' in node:
            info = node['integrity']
            if not isinstance(info, dict) or info.get('algorithm') != 'SHA256':
                raise InstallError('Unsupported ASAR integrity: ' + name)
            calculated = integrity(data, info.get('blockSize'))
            if any(info.get(key) != value for key, value in calculated.items()):
                raise InstallError('ASAR integrity mismatch: ' + name)
        return data

    def plan_replace(self, replacements: dict[str, bytes],
                     unpacked_sizes: dict[str, bytes] | None = None) -> 'ArchiveEdit':
        header = copy.deepcopy(self.header)
        appends = []
        offset = self.payload_size
        if set(replacements) & set(unpacked_sizes or {}):
            raise InstallError('File cannot be both packed and unpacked')
        for name, data in {**replacements, **(unpacked_sizes or {})}.items():
            old = self.entry(name)
            unpacked = old.get('unpacked') is True
            if unpacked != (name in (unpacked_sizes or {})):
                raise InstallError('ASAR storage kind does not match: ' + name)
            node = header
            for part in name.split('/'):
                node = node['files'][part]
            node['size'] = len(data)
            if not unpacked:
                node['offset'] = str(offset)
                appends.append(data)
                offset += len(data)
            node['integrity'] = integrity(data, old.get('integrity', {}).get('blockSize', 4194304))
        return ArchiveEdit(self, header, appends)

    def replace(self, replacements: dict[str, bytes], unpacked_sizes: dict[str, bytes] | None = None) -> bytes:
        """Small-fixture compatibility API; production callers use plan_replace."""
        if self.source_path is not None:
            raise InstallError('Use plan_replace().write_to() for file-backed archives')
        edit = self.plan_replace(replacements, unpacked_sizes)
        return edit.encoded_header + bytes(self.payload) + b''.join(edit.appends)


class ArchiveEdit:
    """A new index plus a streaming copy plan for the unchanged data region."""
    def __init__(self, source: AsarArchive, header: dict, appends: list[bytes]):
        self.source = source
        self.header = header
        self.encoded_header = _encode_header(header)
        text_size = struct.unpack_from('<I', self.encoded_header, 12)[0]
        self.header_bytes = self.encoded_header[16:16 + text_size]
        self.appends = tuple(appends)
        self.size = len(self.encoded_header) + source.payload_size + sum(map(len, appends))

    def write_to(self, path: Path) -> FileContent:
        path = Path(path)
        if self.source.source_path is not None and path.resolve() == self.source.source_path:
            raise InstallError('Materialize an archive edit to a separate staging file')
        self.source._check_source()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.ta-', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as destination:
                destination.write(self.encoded_header)
                if self.source.source_path is not None:
                    with self.source.source_path.open('rb') as source:
                        source.seek(self.source.payload_start)
                        _copy_stream(source, destination, self.source.payload_size)
                else:
                    for start in range(0, self.source.payload_size, STREAM_CHUNK_SIZE):
                        destination.write(self.source.payload[start:start + STREAM_CHUNK_SIZE])
                for data in self.appends:
                    destination.write(data)
                destination.flush()
                os.fsync(destination.fileno())
            self.source._check_source()
            os.replace(temporary, path)
        finally:
            discard_temporary(temporary)
        return FileContent(path)


def apply_section(original: bytes, section: dict) -> bytes:
    """Apply only explicit owned payload replacements, once per exact anchor."""
    try:
        text = original.decode('utf-8')
    except UnicodeError as exc:
        raise InstallError('Game script is not UTF-8') from exc
    for old, new in section.get('patches', []):
        count = text.count(old)
        if not old or count != 1:
            raise InstallError(f'Unsupported game script: expected one exact anchor, found {count}: {old[:90]}')
        text = text.replace(old, new, 1)
    return (section.get('prefix', '') + text + section.get('suffix', '')).encode('utf-8')


def reverse_section(current: bytes, section: dict) -> bytes:
    """Remove one exact owned section while retaining every unrelated byte.

    A prefix/suffix may be surrounded by another mod's wrapper. We identify only
    exact payload bytes, never broad marker or regular-expression ranges.
    """
    try:
        text = current.decode('utf-8')
    except UnicodeError as exc:
        raise InstallError('Game script is not UTF-8') from exc
    for owned in (section.get('prefix', ''), section.get('suffix', '')):
        if owned:
            if text.count(owned) != 1:
                raise InstallError('Owned payload is missing, duplicated, or modified')
            text = text.replace(owned, '', 1)
    for old, new in reversed(section.get('patches', [])):
        if not new or text.count(new) != 1:
            raise InstallError('Owned patch is missing, duplicated, or modified: ' + new[:90])
        text = text.replace(new, old, 1)
    baseline = text.encode('utf-8')
    # An exact round trip rules out accidental destructive inverse matching.
    regenerated = apply_section(baseline, section)
    # Other mods may wrap either edge, so compare the owned inverse again rather
    # than demand that our own prefix is the outermost wrapper.
    if reverse_section_bytes_only(regenerated, section) != baseline:
        raise InstallError('Owned payload failed exact inverse validation')
    return baseline


def reverse_section_bytes_only(current: bytes, section: dict) -> bytes:
    """Internal exact inverse used for round-trip validation."""
    text = current.decode('utf-8')
    for owned in (section.get('prefix', ''), section.get('suffix', '')):
        if owned:
            if text.count(owned) != 1:
                raise InstallError('Ambiguous owned payload')
            text = text.replace(owned, '', 1)
    for old, new in reversed(section.get('patches', [])):
        if not new or text.count(new) != 1:
            raise InstallError('Ambiguous owned patch')
        text = text.replace(new, old, 1)
    return text.encode('utf-8')


def transition_section(current: bytes, old: dict, new: dict) -> bytes:
    """Upgrade an exact owned section in place, retaining other mod wrappers."""
    # Validate the full old shape before changing anything.
    reverse_section(current, old)
    text = current.decode('utf-8')
    for before, after in reversed(old.get('patches', [])):
        if not after or text.count(after) != 1:
            raise InstallError('Ambiguous old payload patch')
        text = text.replace(after, before, 1)
    for before, after in new.get('patches', []):
        if not before or text.count(before) != 1:
            raise InstallError('Unsupported game anchor during upgrade: ' + before[:90])
        text = text.replace(before, after, 1)
    for key in ('prefix', 'suffix'):
        before, after = old.get(key, ''), new.get(key, '')
        if before:
            if text.count(before) != 1:
                raise InstallError('Ambiguous old payload block')
            text = text.replace(before, after, 1)
        elif after:
            text = after + text if key == 'prefix' else text + after
    result = text.encode('utf-8')
    reverse_section(result, new)
    return result


def reverse_own_payload(app: bytes, sim: bytes, migrations: Iterable[dict]) -> tuple[bytes, bytes, dict]:
    """Recognize exactly one full owned release using independently supplied specs.

    Each migration is a complete {app: section, sim: section, ...metadata} record.
    Both scripts must match the same release. Partial or unknown releases fail
    closed; callers decide whether an entirely unmodified install is eligible.
    """
    matches = []
    seen = set()
    for migration in migrations:
        fingerprint = sha256(json.dumps({key: migration[key] for key in ('app', 'sim')},
                                         sort_keys=True, separators=(',', ':')).encode())
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        try:
            base_app = reverse_section(app, migration['app'])
            base_sim = reverse_section(sim, migration['sim'])
        except InstallError:
            continue
        if any(marker in base_app or marker in base_sim for marker in
               (b'__geoTransport', b'__transportAutobuildUI')):
            continue
        matches.append((base_app, base_sim, migration))
    if len(matches) != 1:
        raise InstallError('Unknown, partial, modified, or ambiguous Transport Autopilot payload')
    return matches[0]


def discard_temporary(path):
    """Remove only our temporary file, including a read-only Windows copy."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except PermissionError:
        # atomic_write copies the destination's mode before replacement. If
        # Windows refuses the replacement, its read-only temporary copy must
        # be made writable before deletion; the destination remains untouched.
        mode = stat.S_IMODE(os.stat(path).st_mode)
        os.chmod(path, mode | stat.S_IWUSR)
        os.unlink(path)


def atomic_write(path: Path, data: bytes | FileContent, mode: int | None = None,
                 before_replace: Callable[[], None] | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    # A destination can fit Windows' path limit while a copy of its long name
    # plus a random suffix does not. Keep the same-directory temporary name short.
    fd, temporary = tempfile.mkstemp(prefix='.ta-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            if isinstance(data, FileContent):
                with data.path.open('rb') as source:
                    _copy_stream(source, stream)
            else:
                stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, stat.S_IMODE(mode))
        if before_replace:
            before_replace()
        os.replace(temporary, path)
    finally:
        discard_temporary(temporary)


class Transaction:
    """Hash-checked multi-file install with durable backups and rollback journal.

    Callers must stop the game and provide the exact permitted paths. Receipts
    are never trusted to authorize additional paths. Interrupted transactions
    require explicit recover(), which rejects unexpected current file contents.
    """
    def __init__(self, state_dir: Path, allowed_paths: Iterable[Path]):
        self.state_dir = Path(state_dir).absolute()
        self.allowed = {str(Path(p).absolute()) for p in allowed_paths}
        self.receipt = self.state_dir / 'receipt.json'
        self.journal = self.state_dir / 'pending.json'

    def _safe(self, path: str) -> Path:
        p = Path(path)
        if str(p) not in self.allowed or _is_link(p):
            raise InstallError('Unapproved or symlink transaction target: ' + str(p))
        for parent in p.parents:
            if _is_link(parent):
                raise InstallError('Symlink parent of transaction target: ' + str(p))
        return p

    def _load(self, path: Path) -> dict | None:
        self._check_state()
        if not path.exists():
            return None
        receipt = _json(path.read_bytes())
        if receipt.get('schema') != 1 or not isinstance(receipt.get('files'), list):
            raise InstallError('Unsupported installer receipt')
        paths = set()
        for entry in receipt['files']:
            self._safe(entry['path'])
            if entry['path'] in paths:
                raise InstallError('Duplicate path in installer receipt')
            paths.add(entry['path'])
            for key in ('before', 'after'):
                value = entry.get(key)
                if value is not None and (not isinstance(value, str) or len(value) != 64 or
                                          any(c not in '0123456789abcdef' for c in value)):
                    raise InstallError('Invalid hash in installer receipt')
        return receipt

    def _check_state(self):
        paths = (self.state_dir, *self.state_dir.parents, self.receipt,
                 self.journal, self.state_dir / 'backups')
        if any(_is_link(p) for p in paths):
            raise InstallError('Installer state must not use symlinks or reparse points')

    def _current(self, path: Path) -> str | None:
        return file_sha(path) if path.exists() else None

    def _backup(self, entry: dict) -> FileContent | None:
        self._check_state()
        if entry['before'] is None:
            return None
        path = self.state_dir / 'backups' / entry['before']
        if _is_link(path) or not path.is_file():
            raise InstallError('Missing installer backup: ' + str(path))
        if file_sha(path) != entry['before']:
            raise InstallError('Installer backup checksum mismatch')
        return FileContent(path)

    def status(self) -> dict:
        if self.journal.exists():
            return dict(status='interrupted', message='Run recovery before changing this installation.')
        receipt = self._load(self.receipt)
        if receipt is None:
            return dict(status='not-installed')
        changed = [e['path'] for e in receipt['files'] if self._current(self._safe(e['path'])) != e['after']]
        return dict(status='modified' if changed else 'installed', identity=receipt.get('identity'), changed=changed)

    def baselines(self) -> dict[Path, FileContent | None]:
        receipt = self._load(self.receipt)
        if receipt is None:
            return {}
        if self.status()['status'] != 'installed':
            raise InstallError('Installed files changed; restore or recover before upgrading')
        return {self._safe(e['path']): self._backup(e) for e in receipt['files']}

    def _commit(self, changes: dict[str, bytes | FileContent | None], final_receipt: dict | None,
                post_apply: Callable[[], None] | None = None,
                expected_before: dict[str, str | None] | None = None,
                pre_apply: Callable[[], None] | None = None) -> bool:
        prior_receipt = self._load(self.receipt)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._check_state()
        files = []
        for path, data in changes.items():
            p = self._safe(path)
            before = FileContent(p) if p.exists() else None
            digest = content_sha(before) if before is not None else None
            if expected_before is not None and digest != expected_before[path]:
                raise InstallError('File changed after planning: ' + path)
            if before is not None:
                backup = self.state_dir / 'backups' / digest
                atomic_write(backup, before)
                if file_sha(backup) != digest:
                    raise InstallError('File changed while its backup was written: ' + path)
            files.append(dict(path=path, before=digest,
                              after=content_sha(data) if data is not None else None,
                              mode=p.stat().st_mode if p.exists() else None))
        pending = dict(schema=1, files=files, priorReceipt=prior_receipt)
        atomic_write(self.journal, json.dumps(pending, indent=2).encode())
        try:
            for entry in files:
                p = self._safe(entry['path'])
                if self._current(p) != entry['before']:
                    raise InstallError('File changed during installation: ' + str(p))
                def guard(p=p, entry=entry):
                    # Materializing a multi-gigabyte temporary copy takes time.
                    # Recheck ownership and the caller's process condition only
                    # after it is flushed, immediately before the real write.
                    p = self._safe(entry['path'])
                    if self._current(p) != entry['before']:
                        raise InstallError('File changed while preparing replacement: ' + str(p))
                    if pre_apply:
                        pre_apply()
                data = changes[str(p)]
                if data is None:
                    guard()
                    p.unlink(missing_ok=True)
                else:
                    atomic_write(p, data, entry['mode'], before_replace=guard)
            if post_apply:
                post_apply()
            if any(self._current(self._safe(e['path'])) != e['after'] for e in files):
                raise InstallError('Post-install verification failed')
            if final_receipt is None:
                self.receipt.unlink(missing_ok=True)
            else:
                atomic_write(self.receipt, json.dumps(final_receipt, indent=2).encode())
            self.journal.unlink()
            return True
        except BaseException:
            self.recover()
            raise

    def install(self, changes: dict[Path, bytes | FileContent | None], identity: dict,
                post_apply: Callable[[], None] | None = None,
                expected_before: dict[Path, str | None] | None = None,
                pre_apply: Callable[[], None] | None = None) -> bool:
        if self.journal.exists():
            raise InstallError('Interrupted transaction exists; recover it first')
        self._check_state()
        current = self._load(self.receipt)
        normalized = {str(Path(p).absolute()): data for p, data in changes.items()}
        expected = None if expected_before is None else {
            str(Path(p).absolute()): digest for p, digest in expected_before.items()}
        if expected is not None and set(expected) != set(normalized):
            raise InstallError('Expected hashes must cover every transaction target')
        for path in normalized:
            self._safe(path)
        if current:
            if self.status()['status'] != 'installed':
                raise InstallError('Installed files changed; refusing to overwrite')
            if set(normalized) != {e['path'] for e in current['files']}:
                raise InstallError('Upgrade target set differs; uninstall before changing installation layout')
            if current.get('identity') == identity and all(self._current(Path(p)) == (content_sha(data) if data is not None else None) for p, data in normalized.items()):
                return False
            originals = {e['path']: e for e in current['files']}
            # Verify retained original backups before proceeding with an upgrade.
            for entry in current['files']:
                self._backup(entry)
        else:
            originals = {}
        final_files = []
        for path, data in normalized.items():
            p = self._safe(path)
            if path in originals:
                before = originals[path]['before']
                mode = originals[path]['mode']
            else:
                previous = FileContent(p) if p.exists() else None
                before = content_sha(previous) if previous is not None else None
                mode = p.stat().st_mode if p.exists() else None
            final_files.append(dict(path=path, before=before, after=content_sha(data) if data is not None else None, mode=mode))
        final_receipt = dict(schema=1, identity=identity, files=final_files)
        return self._commit(normalized, final_receipt, post_apply, expected, pre_apply)

    def recover(self) -> bool:
        record = self._load(self.journal)
        if record is None:
            return False
        # Check every target and backup before restoring any target.
        for entry in record['files']:
            if self._current(self._safe(entry['path'])) not in (entry['before'], entry['after']):
                raise InstallError('Recovery found externally changed file: ' + entry['path'])
            self._backup(entry)
        for entry in reversed(record['files']):
            path = self._safe(entry['path'])
            if self._current(path) == entry['before']:
                continue
            data = self._backup(entry)
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, data, entry['mode'])
        prior = record.get('priorReceipt')
        if prior is None:
            self.receipt.unlink(missing_ok=True)
        else:
            atomic_write(self.receipt, json.dumps(prior, indent=2).encode())
        self.journal.unlink()
        return True

    def uninstall(self) -> bool:
        if self.journal.exists():
            raise InstallError('Interrupted transaction exists; recover it first')
        record = self._load(self.receipt)
        if record is None:
            return False
        if self.status()['status'] != 'installed':
            raise InstallError('Installed files changed; refusing to overwrite during uninstall')
        # Make all restore bytes available before mutating any file.
        changes = {e['path']: self._backup(e) for e in record['files']}
        return self._commit(changes, None)

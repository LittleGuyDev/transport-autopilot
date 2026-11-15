#!/usr/bin/env python3
"""Build a manual-only Transport Autopilot package from this source checkout.

Official releases carry a publisher signature and enable automatic updates.
This contributor build intentionally omits every updater/authentication file.
"""

from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'transport-autobuild' / 'dist'
OUTPUT = ROOT / 'build'
FOLDER = OUTPUT / 'Transport-Autopilot'

# An allowlist keeps game files, private signing material and generated updater
# helpers out of contributor packages even if they appear beside the source.
PACKAGE_FILES = (
    'transport-payload.json', 'mod.json', 'thumbnail.png',
    'install.py', 'core.py', 'platform_support.py', 'registration.py',
    'legacy_payloads.json', 'install.sh', 'install.command', 'install.bat',
    'uninstall.bat', 'uninstall.command', 'SOURCES.md', 'LEGACY-PAYLOADS.md',
    'README.txt', 'VALIDATION.md', 'LICENSE',
)


def main():
    subprocess.run([sys.executable, str(ROOT / 'transport-autobuild' / 'build.py')], check=True)
    version = json.loads((SOURCE / 'mod.json').read_text(encoding='utf-8'))['version']
    if not isinstance(version, str) or not version.replace('.', '').isdigit():
        raise ValueError('Unexpected version in mod.json')
    OUTPUT.mkdir(exist_ok=True)
    FOLDER.mkdir(exist_ok=True)
    expected = set(PACKAGE_FILES) | {'SHA256SUMS.txt'}
    extra = {path.name for path in FOLDER.iterdir()} - expected - {'__pycache__'}
    if extra:
        raise ValueError('Unexpected files in generated package directory: ' + ', '.join(sorted(extra)))

    checksums = []
    for name in PACKAGE_FILES:
        source = ROOT / 'LICENSE' if name == 'LICENSE' else SOURCE / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('Missing or unsafe package source: ' + str(source))
        data = source.read_bytes()
        shutil.copy2(source, FOLDER / name)
        checksums.append('{}  {}\n'.format(hashlib.sha256(data).hexdigest(), name))
    (FOLDER / 'SHA256SUMS.txt').write_text(''.join(sorted(checksums)), encoding='utf-8')

    # A complete updater is never present in this manual-only package.
    if any((FOLDER / name).exists() for name in (
            'auto_update.py', 'update_signature.py', 'update_trust.py',
            'update_setup.py', 'update_launcher.py', 'steam_setup.py',
            'release-manifest.json', 'release-manifest.sig')):
        raise ValueError('Contributor build must not contain automatic-update files')

    destination = OUTPUT / ('Transport-Autopilot-{}-unsigned.zip'.format(version))
    with tempfile.NamedTemporaryFile(prefix='.transport-autopilot-', suffix='.zip',
                                     dir=OUTPUT, delete=False) as pending:
        temporary = Path(pending.name)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(expected):
                archive.write(FOLDER / name, 'Transport-Autopilot/' + name)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError('Generated package ZIP failed its integrity check')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(destination)


if __name__ == '__main__':
    main()

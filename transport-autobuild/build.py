#!/usr/bin/env python3
"""Build the original Auto Transport Construction payload and local installer."""
from pathlib import Path
import json
import base64
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIST = HERE / 'dist'


def build():
    runtime = (HERE / 'sim.js').read_text()
    native_adapter = (HERE / 'native-transport.js').read_text()
    ui = (HERE / 'ui.js').read_text()
    manager = (HERE / 'manager-ui.js').read_text()
    activation = (HERE / 'worker-activation.js').read_text()
    manifest = json.loads((HERE / 'native-pack/mod.json').read_text())
    thumbnail = (HERE / 'native-pack/thumbnail.png').read_bytes()
    spec = dict(modId='geotransport', name='Auto Transport Construction', version=manifest['version'], order=50,
                gameVersion='1.1.4 (native Steam build 26709373; exact anchors checked)', blockSize=4194304,
                nativePack=dict(manifest=manifest, thumbnailBase64=base64.b64encode(thumbnail).decode('ascii'),
                                workshopItemId='3839206386', folder='transport-autopilot'),
                app=dict(prefix='\n' + manager + '\n', patches=[], suffix='\n' + ui + '\n'),
                sim=dict(prefix='\n' + runtime + '\nglobalThis.__geoTransportRuntime = __geoTransportRuntime;\n' + activation + '\n', patches=[], suffix=''))
    hooks = json.loads((HERE / 'native-hooks.json').read_text())
    for side in ('app', 'sim'):
        spec[side]['patches'] = [[a, b.replace('@@native-transport.js@@', native_adapter)] for a, b in hooks[side]]
    DIST.mkdir(exist_ok=True)
    (DIST / 'geotransport.json').write_text(json.dumps(spec, separators=(',', ':')) + '\n')
    for name in ['mod.json', 'thumbnail.png']:
        shutil.copy2(HERE / 'native-pack' / name, DIST / name)
    # This distribution uses only our independent, single-product installer.
    # The root installer belongs to the separate legacy Geo-mod toolkit.
    payload = dict(schema=1, id=manifest['id'], name='Transport Autopilot',
                   version=manifest['version'], gameVersions=['1.1.4'], compatibilityPolicy=dict(mode='exact-hooks', minimumGameVersion='1.1.4'),
                   app=spec['app'], sim=spec['sim'])
    (DIST / 'transport-payload.json').write_text(json.dumps(payload, separators=(',', ':')) + '\n')
    for name in ['install.py', 'core.py', 'platform_support.py', 'registration.py',
                 'legacy_payloads.json', 'install.sh', 'install.command',
                 'install.bat', 'uninstall.bat', 'uninstall.command',
                 'SOURCES.md', 'LEGACY-PAYLOADS.md']:
        shutil.copy2(HERE / 'installer' / name, DIST / name)
    for name in ['README.txt', 'VALIDATION.md']:
        if (HERE / name).exists():
            shutil.copy2(HERE / name, DIST / name)
    for source, target in [('engine.py', 'auto_update.py'), ('signature.py', 'update_signature.py'),
                           ('trust.py', 'update_trust.py'), ('setup.py', 'update_setup.py'),
                           ('launcher.py', 'update_launcher.py'), ('steam.py', 'steam_setup.py')]:
        shutil.copy2(ROOT / 'updater' / source, DIST / target)
    print(DIST)


if __name__ == '__main__':
    build()

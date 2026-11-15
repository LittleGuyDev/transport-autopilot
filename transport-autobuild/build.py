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
    handlers = []
    descriptors = []
    for name, method, mutation in [('transport_auto_state', 'state', 'none'),
                                    ('transport_auto_config', 'configure', 'gameplay'),
                                    ('transport_auto_run', 'run', 'gameplay')]:
        args = 't[0]' if method == 'configure' else ''
        call = 'e' + (',' + args if args else '')
        handlers.append(name + '(e,t,a){let r=globalThis.__geoTransportRuntime.' + method + '(' + call + ');return Fe(a,r.ok!==false,r.ok!==false?"transport.auto.ok":(r.reason||"transport.auto.failed"),r.state||{message:r.message||r.reason||"Automation unavailable"})},')
        params = '[{name:"json",type:"string",required:!0}]' if method == 'configure' else '[]'
        usage = '<json>' if method == 'configure' else ''
        descriptors.append('Sg(' + ','.join(json.dumps(x) for x in [name, 'general', 'transport.auto.' + method, usage]) + ',' + params + ',{mutation:' + json.dumps(mutation) + '}),')
    manager_detail = 'R("item").disabled=l||!x}'
    boot_start = 'let u=await s.boot({playerKey:n,savePayload:t||null,'
    app_patches = [
        [manager_detail, 'R("item").disabled=l||!x;try{globalThis.__geoTransportManager?.render(e,ee,a)}catch(ge){console.warn("[transport-autobuild] manager entry",ge)}}'],
        [boot_start, boot_start + 'transportAutopilotEnabled:globalThis.__geoTransportLaunch?.enabled===true,'],
    ]
    spec = dict(modId='geotransport', name='Auto Transport Construction', version=manifest['version'], order=50,
                gameVersion='1.1.3 (native Steam build 26651281; exact anchors checked)', blockSize=4194304,
                nativePack=dict(manifest=manifest, thumbnailBase64=base64.b64encode(thumbnail).decode('ascii'),
                                workshopItemId='3839206386', folder='transport-autopilot'),
                app=dict(prefix='\n' + manager + '\n', patches=app_patches, suffix='\n' + ui + '\n'),
                sim=dict(prefix='\n' + runtime + '\nglobalThis.__geoTransportRuntime = __geoTransportRuntime;\n' + activation + '\n', patches=[
                    ['var CLo=Object.freeze({', native_adapter + '\nvar CLo=Object.freeze({' + ''.join(handlers)],
                    ['Za("money","economy","console.help.money",', ''.join(descriptors) + 'Za("money","economy","console.help.money",'],
                    ['function Pvt(e){let t=e._pendingViewDelta;', 'function Pvt(e){try{globalThis.__geoTransportRuntime.tick(e)}catch(x){console.warn("[auto-transport]",x)}let t=e._pendingViewDelta;'],
                    ['this.sim=b,', 'this.sim=b,globalThis.__geoTransportRuntime.bindActivation(b,t.transportAutopilotEnabled===true),'],
                ], suffix=''))
    DIST.mkdir(exist_ok=True)
    (DIST / 'geotransport.json').write_text(json.dumps(spec, separators=(',', ':')) + '\n')
    for name in ['mod.json', 'thumbnail.png']:
        shutil.copy2(HERE / 'native-pack' / name, DIST / name)
    # This distribution uses only our independent, single-product installer.
    # The root installer belongs to the separate legacy Geo-mod toolkit.
    payload = dict(schema=1, id=manifest['id'], name='Transport Autopilot',
                   version=manifest['version'], gameVersions=['1.1.3'],
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

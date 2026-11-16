'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');
execFileSync('python3', [path.join(__dirname, 'build.py')]);
const payload = JSON.parse(fs.readFileSync(path.join(__dirname, 'dist/geotransport.json'), 'utf8'));
assert.equal(payload.version, '1.1.1');
assert.deepEqual(payload.nativePack.manifest, JSON.parse(fs.readFileSync(path.join(__dirname, 'native-pack/mod.json'), 'utf8')));
assert.equal(payload.nativePack.thumbnailBase64, fs.readFileSync(path.join(__dirname, 'native-pack/thumbnail.png')).toString('base64'));
// Real game bundles stay local. Set these paths to check the generated patch anchors.
const nativeBundles = [['app', process.env.GEOPLANETICAL_APP_BUNDLE],
                       ['sim', process.env.GEOPLANETICAL_SIM_BUNDLE]];
for (const [side, filename] of nativeBundles) {
  if (!filename) continue;
  let body = fs.readFileSync(path.resolve(filename), 'utf8');
  for (const [before, after] of payload[side].patches) {
    assert.equal(body.split(before).length - 1, 1, filename + ': unique product anchor'); body = body.replace(before, after);
  }
  assert.ok(body.includes(side === 'app' ? 'transportAutopilotEnabled:globalThis.__geoTransportLaunch?.enabled===true' : 'bindActivation(b,t.transportAutopilotEnabled===true)'), 'generated host/worker launch binding');
  console.log('PASS optional native ' + side + ' bundle anchors');
}
if (!nativeBundles.some(([, filename]) => filename)) {
  console.log('SKIP optional native anchors: set GEOPLANETICAL_APP_BUNDLE and/or GEOPLANETICAL_SIM_BUNDLE');
}
const source = fs.readFileSync(path.join(__dirname, 'manager-ui.js'), 'utf8');
const ID = 'littleguydev-transport-autopilot';
const nativeSnapshot = (status = 'active', mode = 'normal') => ({ contentApi: 1, mode, assets: { flags: {} }, mods: [{ id: ID, status }], order: status === 'active' ? [ID] : [] });
function setup(snapshot, extra = {}) {
  let reads = 0;
  const mods = { list() { throw new Error('Launch policy must never query live preferences.'); }, savePreferences() { throw new Error('Native preferences must not be wrapped.'); } };
  Object.defineProperty(mods, 'snapshot', { get() { reads++; return snapshot; } });
  const window = { desktop: { mods }, ...extra };
  const context = vm.createContext({ window, console });
  vm.runInContext(source, context);
  return { context, window, mods, get reads() { return reads; }, policy: context.__geoTransportLaunch, manager: context.__geoTransportManager };
}
for (const [name, snapshot, expected] of [
  ['active native pack', nativeSnapshot(), true],
  ['disabled native pack', nativeSnapshot('disabled'), false],
  ['incompatible native pack', nativeSnapshot('incompatible'), false],
  ['unloaded native pack', nativeSnapshot('unloaded'), false],
  ['absent pack', { ...nativeSnapshot(), mods: [] }, false],
  ['other active mod', { ...nativeSnapshot(), mods: [{ id: 'other-mod', status: 'active' }] }, false],
  ['missing snapshot', null, false],
  ['recovery launch', nativeSnapshot('active', 'recovery'), false],
  ['mods disabled for launch', nativeSnapshot('active', 'disabled'), false],
  ['missing launch mode', { ...nativeSnapshot(), mode: undefined }, false],
  ['explicit disabled marker', { ...nativeSnapshot(), disabled: true }, false],
  ['duplicate canonical identity', { ...nativeSnapshot(), mods: [{ id: ID, status: 'active' }, { id: ID, status: 'disabled' }] }, false]
]) {
  const env = setup(snapshot); assert.equal(env.policy.enabled, expected, name);
  assert.equal(env.policy.version, '1.1.1'); assert.equal(Object.isFrozen(env.policy), true); assert.equal(env.policy, env.window.__geoTransportLaunch);
  assert.equal(env.reads, 1, name + ': snapshot is captured once'); assert.equal(env.manager.wrap, undefined, 'no fake native preference facade');
}
const env = setup(nativeSnapshot());
const originalList = env.mods.list, originalSave = env.mods.savePreferences;
assert.equal(env.mods.list, originalList); assert.equal(env.mods.savePreferences, originalSave);
// Changing desired preferences or even the backing test snapshot cannot rewrite the launch decision.
env.mods.preferences = { order: [ID], disabled: [ID] };
const mutable = nativeSnapshot(); const immutable = setup(mutable);
mutable.mode = 'disabled'; mutable.mods[0].status = 'disabled'; mutable.order.length = 0;
assert.equal(immutable.policy.enabled, true, 'the launch decision is immutable');
vm.runInContext(source, immutable.context); assert.equal(immutable.reads, 1, 'a repeated script does not recapture a changed snapshot');
assert.throws(() => { immutable.policy.enabled = false; }, TypeError);
const noDesktop = setup(null, { desktop: undefined }); assert.equal(noDesktop.policy.enabled, false);
const throwingWindow = {}; Object.defineProperty(throwingWindow, 'desktop', { get() { throw new Error('unavailable bridge'); } });
const inaccessible = vm.createContext({ window: throwingWindow }); vm.runInContext(source, inaccessible); assert.equal(inaccessible.__geoTransportLaunch.enabled, false);

class Element {
  constructor() { this.children = []; this.attrs = {}; this.hidden = false; this.disabled = false; this.textContent = 'native content'; this.className = 'native-class'; }
  setAttribute(name, value) { this.attrs[name] = value; }
  querySelector(selector) { return this.children.find(child => selector === '[data-' + child.selector + '="' + child.key + '"]') || null; }
}
const overlay = new Element(), detail = new Element(); detail.selector = 'mods-part'; detail.key = 'detail'; overlay.children.push(detail);
const controls = {};
for (const action of ['toggle', 'earlier', 'later', 'item', 'edit', 'duplicate']) { const button = new Element(); button.selector = 'action'; button.key = action; detail.children.push(button); controls[action] = button; }
const nativeBefore = JSON.stringify({ text: detail.textContent, className: detail.className, attrs: detail.attrs });
env.manager.render(overlay, { id: ID, status: 'active' }, () => { throw new Error('manager never reads campaign settings'); });
for (const action of ['edit', 'duplicate']) { assert.equal(controls[action].hidden, true); assert.equal(controls[action].disabled, true); }
for (const action of ['toggle', 'earlier', 'later', 'item']) { assert.equal(controls[action].hidden, false, action + ' remains native'); assert.equal(controls[action].disabled, false); }
assert.equal(JSON.stringify({ text: detail.textContent, className: detail.className, attrs: detail.attrs }), nativeBefore, 'native description, status and classes remain untouched');
// Native ie() restores its own controls for every selected row; our hook leaves other rows alone.
for (const button of Object.values(controls)) { button.hidden = false; button.disabled = false; }
env.manager.render(overlay, { id: 'ordinary-mod' });
assert.equal(controls.duplicate.hidden, false); assert.equal(controls.edit.hidden, false);
env.manager.render(overlay, null); env.manager.render(null, { id: ID });
assert.equal(env.reads, 1); assert.equal(overlay.children.length, 1, 'no campaign form or synthetic native row is injected');

(async () => {
  const nativeRoot = process.env.GEOPLANETICAL_MODS_SOURCE;
  if (nativeRoot) {
    const { validateModManifest } = require(path.join(nativeRoot, 'mod-manifest'));
    const { planModLoad, prepareModRegistry } = require(path.join(nativeRoot, 'mod-registry'));
    const { prepareModContent } = require(path.join(nativeRoot, 'mod-scanner'));
    const checked = validateModManifest({ id: ID, version: '1.1.1', name: 'Transport Autopilot', description: 'Automatic transport construction.', contentApi: 1, tier: 'presentation', provides: {} });
    assert.equal(checked.ok, true);
    const candidate = { ...checked, source: { kind: 'workshop', itemId: '3839206386' } };
    const prepared = await prepareModRegistry(planModLoad([candidate], { version: 1, order: [ID], disabled: [] }), row => prepareModContent('/unused-empty-pack', row.manifest, () => { throw new Error('Empty native pack never decodes assets.'); }));
    const launch = { ...prepared.snapshot, mode: 'normal' };
    assert.equal(launch.mods[0].status, 'active'); assert.equal(setup(launch).policy.enabled, true);
    const disabled = planModLoad([candidate], { version: 1, order: [ID], disabled: [ID] });
    assert.equal(disabled.reports[0].status, 'disabled'); assert.equal(setup({ ...launch, mods: disabled.reports }).policy.enabled, false);
    console.log('PASS native manifest validation, empty-content preparation and real enabled/disabled launch reports');
  } else console.log('SKIP optional native backend contract check: set GEOPLANETICAL_MODS_SOURCE to its electron/mods directory');
  console.log('PASS native manager policy: frozen startup snapshot, fail-closed modes/status, original preferences/controls, no campaign settings, own Edit/Duplicate hidden');
})().catch(error => { console.error(error); process.exitCode = 1; });

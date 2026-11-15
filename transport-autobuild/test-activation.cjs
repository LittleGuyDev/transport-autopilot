'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const activation = fs.readFileSync(path.join(__dirname, 'worker-activation.js'), 'utf8');
function guarded(runtime) {
  const context = vm.createContext({ __geoTransportRuntime: runtime });
  vm.runInContext(activation, context); return context;
}
function assertDisabled(runtime, sim) {
  const before = JSON.stringify(sim);
  for (const name of ['state', 'configure', 'run']) {
    const result = runtime[name](sim, { enabled: true, budget: 99 });
    assert.equal(result.ok, false, name); assert.equal(result.reason, 'mod_disabled', name); assert.match(result.message, /restart the game/);
  }
  assert.equal(runtime.tick(sim), undefined); assert.equal(JSON.stringify(sim), before, 'disabled calls never touch campaign data');
}
const calls = [], marker = {}, nativeAdapter = {};
const stub = { adapter: nativeAdapter, setNative(value) { this.adapter = value; return value; } };
for (const name of ['state', 'configure', 'run', 'tick']) stub[name] = function (...args) { calls.push({ name, receiver: this, args }); return marker; };
const originalSetNative = stub.setNative;
const context = guarded(stub), sim = { country: { _transportAutobuild: { config: { enabled: true, budget: 4 } } } }, other = {};
assertDisabled(stub, sim); assertDisabled(stub, other); assert.equal(calls.length, 0, 'default-denied simulations never reach original methods');
const beforeBinding = JSON.stringify(sim);
assert.equal(stub.bindActivation(sim, true), true); assert.equal(JSON.stringify(sim), beforeBinding, 'activation is not serialized into a campaign');
const params = { budget: 6 }, receiver = { name: 'test receiver' };
for (const name of ['state', 'configure', 'run', 'tick']) {
  assert.equal(stub[name].call(receiver, sim, params, 'extra'), marker);
  const call = calls.at(-1); assert.equal(call.name, name); assert.equal(call.receiver, receiver); assert.deepEqual(call.args, [sim, params, 'extra']);
}
assertDisabled(stub, other); assert.equal(stub.setNative, originalSetNative, 'adapter integration retains its original method');
const replacement = {}; assert.equal(stub.setNative(replacement), replacement); assert.equal(stub.adapter, replacement);
assert.equal(stub.bindActivation(sim, 'true'), false, 'only an explicit boolean true enables the runtime'); assertDisabled(stub, sim);
stub.bindActivation(sim, true); const wrapped = stub.state; vm.runInContext(activation, context); assert.equal(stub.state, wrapped, 'repeated installation does not replace the gate or forget bindings'); assert.equal(stub.state(sim), marker);
stub.bindActivation(sim, false); assertDisabled(stub, sim);
assert.throws(() => stub.bindActivation(null, true), /simulation is required/);
assert.equal(stub.state(null).reason, 'mod_disabled');

// Exercise the real engine helper as well: disabled calls cannot lazily create its
// persisted country record or change an already-enabled save's settings/history.
const realContext = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, 'sim.js'), 'utf8'), realContext);
const runtime = realContext.__geoTransportRuntime;
vm.runInContext(activation, realContext);
function fixture(saved) {
  const country = { key: '840', provs: [], ...(saved ? { _transportAutobuild: JSON.parse(JSON.stringify(saved)) } : {}) };
  return { playerKey: '840', player: country, countries: new Map([['840', country]]), day: 1, construction: [],
    world: { provByKey: new Map() }, transport: { network: { assets: [], calibration: { status: 'calibrated' } } },
    finance: { sovereigns: { '840': { cashAccountId: 'cash' } }, accounts: { cash: { balanceBn: 1000 } } },
    transportSnapshot() { return { surface: { provinces: [] }, deliveryRate: 1 }; },
    transportProjectQuote() { throw new Error('No project should be quoted by this fixture.'); },
    startTransportProject() { throw new Error('No project should be started by this fixture.'); }
  };
}
const fresh = fixture(); assertDisabled(runtime, fresh); assert.equal(Object.hasOwn(fresh.player, '_transportAutobuild'), false, 'disabled state/configure/run/tick create no hidden record');
const saved = { config: { enabled: true, budget: 7, reserve: 3, target: 0.8, kinds: ['road'] }, history: [{ id: 'existing', day: 1 }], lastReviewDay: 0 };
const loaded = fixture(saved), savedBefore = JSON.stringify(loaded.player._transportAutobuild);
assertDisabled(runtime, loaded); assert.equal(JSON.stringify(loaded.player._transportAutobuild), savedBefore);
runtime.bindActivation(loaded, true); assert.equal(JSON.stringify(loaded.player._transportAutobuild), savedBefore, 'activation never rewrites saved config');
const result = runtime.state(loaded); assert.equal(result.ok, true); assert.equal(result.state.config.enabled, true); assert.equal(result.state.config.budget, 7); assert.equal(result.state.config.reserve, 3);
const configured = runtime.configure(loaded, { enabled: false }); assert.equal(configured.ok, true); assert.equal(configured.state.config.budget, 7); assert.equal(configured.state.config.enabled, false);
assert.equal(runtime.run(loaded).ok, true, 'enabled native session forwards the real engine method');
assertDisabled(runtime, fresh); assert.equal(Object.hasOwn(fresh.player, '_transportAutobuild'), false, 'another simulation remains disabled');
const persisted = JSON.parse(JSON.stringify(loaded.player._transportAutobuild));
const restored = fixture(persisted); assertDisabled(runtime, restored); runtime.bindActivation(restored, true); assert.equal(runtime.state(restored).state.config.budget, 7, 'restored campaigns need a fresh startup binding, then retain settings');
runtime.bindActivation(loaded, false); assertDisabled(runtime, loaded); assert.equal(loaded.player._transportAutobuild.config.budget, 7, 'disabling preserves saved settings for a future launch');
console.log('PASS per-simulation native activation: default-denied methods/ticks, preserved receiver/arguments/adapter exports, no serialized gate, existing and restored campaign settings retained');

/* Bind the immutable native launch policy to each simulation outside campaign saves. */
;(function () {
  'use strict';
  var runtime = globalThis.__geoTransportRuntime;
  if (!runtime || typeof runtime.bindActivation === 'function') return;
  var enabled = new WeakMap();
  runtime.bindActivation = function (sim, value) {
    if (!sim || (typeof sim !== 'object' && typeof sim !== 'function')) throw new TypeError('A simulation is required.');
    var active = value === true; enabled.set(sim, active); return active;
  };
  function disabled() {
    return { ok: false, reason: 'mod_disabled', message: 'Transport Autopilot is disabled in this session. Enable it in Manage mods and restart the game.' };
  }
  ['state', 'configure', 'run', 'tick'].forEach(function (name) {
    var original = runtime[name];
    if (typeof original !== 'function') return;
    runtime[name] = function (sim) {
      if (enabled.get(sim) !== true) return name === 'tick' ? undefined : disabled();
      return original.apply(this, arguments);
    };
  });
})();

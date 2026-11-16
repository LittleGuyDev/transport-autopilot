/* Native mod startup policy. Native preferences take effect on the next game launch. */
;(function () {
  'use strict';
  var host = globalThis, browser = typeof window === 'undefined' ? host : window;
  if (host.__geoTransportLaunch) return;
  var ID = 'littleguydev-transport-autopilot', VERSION = '1.1.1';
  var snapshot = null;
  try { snapshot = browser.desktop && browser.desktop.mods && browser.desktop.mods.snapshot; } catch (_) {}
  var matches = snapshot && Array.isArray(snapshot.mods) ? snapshot.mods.filter(function (row) { return row && row.id === ID; }) : [];
  var policy = Object.freeze({ id: ID, version: VERSION, mode: snapshot && snapshot.mode || 'unavailable',
    enabled: !!(snapshot && snapshot.mode === 'normal' && snapshot.disabled !== true && matches.length === 1 && matches[0].status === 'active') });
  host.__geoTransportLaunch = policy;
  browser.__geoTransportLaunch = policy;
  // Compiled transport code has no editable or duplicable native content. Leave native
  // enable/disable, load order, status, description and Workshop navigation untouched.
  var manager = Object.freeze({ id: ID, version: VERSION, render: function (overlay, entry) {
    if (!entry || entry.id !== ID || !overlay || typeof overlay.querySelector !== 'function') return;
    var detail = overlay.querySelector('[data-mods-part="detail"]');
    if (!detail) return;
    ['edit', 'duplicate'].forEach(function (action) {
      var button = detail.querySelector('[data-action="' + action + '"]');
      if (button) { button.hidden = true; button.disabled = true; }
    });
  } });
  host.__geoTransportManager = manager;
  browser.__geoTransportManager = manager;
})();

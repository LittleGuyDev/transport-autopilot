/* Transport Auto Build: renderer interface. Uses the existing campaign facade. */
;(function () {
  'use strict';
  if (typeof window === 'undefined' || typeof document === 'undefined' || window.__transportAutobuildUI || !window.__geoTransportLaunch || window.__geoTransportLaunch.enabled !== true) return;
  var DEFAULT = { enabled: false, budget: 1, reserve: 1, target: 0.8, landPolicy: 'demand', kinds: ['road', 'motorway', 'rail'] };
  var labels = { road: 'Roads', motorway: 'Freeways', rail: 'Rail', gas_pipeline: 'Gas pipelines', oil_pipeline: 'Oil pipelines', port: 'Ports' };
  var root, panel, lastFocus, state = null, busy = false, loaded = false, dirty = false, opened = false;
  var owner = null, generation = 0, lastDay = null, lastEpoch = null, ownerKey = null;
  var activeTab = 'overview', EPS = 1e-8, targetDisplay = '20', savedTarget = DEFAULT.target;
  var number = new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 });
  function geo() { return window.__geo; }
  function sim() { var g = geo(); return g && typeof g.getSim === 'function' ? g.getSim() : null; }
  function finite(value) { return value != null && Number.isFinite(Number(value)); }
  function fmt(value) {
    if (!finite(value)) return '—';
    var n = Number(value);
    return n !== 0 && Math.abs(n) < .001 ? (n < 0 ? '−' : '') + '<0.001' : number.format(n);
  }
  var currencyNumber = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });
  function currency() { var code = state && state.budget && state.budget.currency; return typeof code === 'string' && code.trim() ? code.trim() : 'treasury currency'; }
  function money(value) {
    if (!finite(value)) return '—';
    var amount = Number(value), a = Math.abs(amount), scale = a >= 1 || a === 0 ? 1 : a >= .001 ? 1000 : a >= .000001 ? 1000000 : 1000000000;
    var suffix = scale === 1 ? 'B' : scale === 1000 ? 'M' : scale === 1000000 ? 'K' : '';
    var n = amount * scale;
    return currency() + ' ' + (n !== 0 && Math.abs(n) < .01 ? (n < 0 ? '−' : '') + '<0.01' : currencyNumber.format(n)) + suffix;
  }
  function pct(value) {
    if (!finite(value)) return '—';
    var n = Math.max(0, Math.min(100, Number(value) * 100));
    return n > 0 && n < .1 ? '<0.1%' : n < 100 && n > 99.9 ? '>99.9%' : new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 }).format(n) + '%';
  }
  function text(selector, value) { var el = root.querySelector(selector); if (el) el.textContent = value; }
  function element(tag, className, value) { var el = document.createElement(tag); if (className) el.className = className; if (value != null) el.textContent = value; return el; }
  function config(value) {
    value = value || {}; var kinds = value.kinds;
    if (!Array.isArray(kinds)) kinds = kinds && typeof kinds === 'object' ? Object.keys(kinds).filter(function (k) { return kinds[k]; }) : DEFAULT.kinds.slice();
    return { enabled: value.enabled === true, budget: finite(value.budget) ? Number(value.budget) : DEFAULT.budget,
      reserve: finite(value.reserve) ? Number(value.reserve) : DEFAULT.reserve,
      target: finite(value.target) ? Number(value.target) : DEFAULT.target,
      landPolicy: value.landPolicy === 'expand' ? 'expand' : 'demand',
      kinds: Object.keys(labels).filter(function (k) { return kinds.indexOf(k) >= 0; }) };
  }
  function errorMessage(reason) {
    var messages = { no_campaign: 'Load a campaign to manage automatic construction.', no_game: 'Load a campaign to manage automatic construction.', disabled: 'Save your settings with automatic building on, and wait for a transport report before starting projects.', invalid_config: 'Enter a valid budget and minimum cash, choose 5–50% room for growth, and select a construction type when automatic building is on.', runtime: 'The construction check could not finish. Some projects may have started. Update the report and review Projects before trying again.', internal: 'Could not refresh construction. Try again.', campaign_changed: 'The campaign changed. Open this panel again to continue.' };
    return messages[reason] || 'Could not complete this action. Refresh and try again.';
  }
  function notice(message, bad) { text('[data-gta-notice]', message || ''); root.querySelector('[data-gta-notice]').classList.toggle('gta-error', !!bad); }
  function bindCampaign(current) {
    owner = current; lastDay = current ? current.day : null; lastEpoch = current && current.status ? current.status.sessionEpoch : null; ownerKey = current && current.playerKey;
  }
  function sameCampaign(current) { return current === owner && (!current || current.playerKey === ownerKey) && (!current || !current.status || lastEpoch == null || current.status.sessionEpoch === lastEpoch); }
  async function settingsRequest(getSession, name, args) {
    var current = typeof getSession === 'function' && getSession();
    if (!current || typeof current.command !== 'function' || !current.playerKey) throw new Error('no_campaign');
    var epoch = current.status && current.status.sessionEpoch, key = current.playerKey;
    var result = await current.command('executeDevConsoleCommand', [{ name: name, args: args || [] }]);
    if (getSession() !== current || key !== current.playerKey || epoch !== (current.status && current.status.sessionEpoch)) throw new Error('campaign_changed');
    if (!result || result.ok === false) throw new Error(result && result.reason || 'internal');
    var data = result && (result.data || result.state);
    if (data && data.state) data = data.state;
    if (!data && result && result.config) data = result;
    if (!data || !data.config) throw new Error('internal');
    return data;
  }
  function validateSettings(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || typeof value.enabled !== 'boolean' || !Array.isArray(value.kinds)) throw new Error('invalid_config');
    if (Object.keys(value).some(function (key) { return ['enabled', 'budget', 'reserve', 'target', 'landPolicy', 'kinds'].indexOf(key) < 0; })) throw new Error('invalid_config');
    [['budget', 0, 1e6], ['reserve', 0, 1e9], ['target', .5, .95]].forEach(function (field) {
      var v = value[field[0]]; if (typeof v !== 'number' || !Number.isFinite(v) || v < field[1] || v > field[2]) throw new Error('invalid_config');
    });
    if (value.landPolicy !== undefined && ['demand', 'expand'].indexOf(value.landPolicy) < 0) throw new Error('invalid_config');
    if (value.kinds.some(function (kind) { return !Object.prototype.hasOwnProperty.call(labels, kind); }) || value.enabled && !value.kinds.length) throw new Error('invalid_config');
    return config(value);
  }
  async function command(name, args) {
    var current = sim(); if (!current || typeof current.command !== 'function' || !current.playerKey) throw new Error('no_campaign');
    if (!sameCampaign(current)) throw new Error('campaign_changed');
    var data = await settingsRequest(function () { return sameCampaign(sim()) ? sim() : null; }, name, args);
    return data;
  }
  function populate() {
    var c = config(state && state.config); root.querySelector('[data-gta-enabled]').checked = c.enabled;
    root.querySelector('[data-gta-budget]').value = c.budget; root.querySelector('[data-gta-reserve]').value = c.reserve;
    root.querySelector('[data-gta-land-policy]').value = c.landPolicy;
    savedTarget = c.target; targetDisplay = String(Number(((1 - c.target) * 100).toFixed(8)));
    root.querySelector('[data-gta-target]').value = targetDisplay;
    Object.keys(labels).forEach(function (kind) { root.querySelector('[data-gta-kind="' + kind + '"]').checked = c.kinds.indexOf(kind) >= 0; });
    dirty = false;
  }
  function readConfig() {
    var rawBudget = root.querySelector('[data-gta-budget]').value, rawReserve = root.querySelector('[data-gta-reserve]').value, rawBuffer = root.querySelector('[data-gta-target]').value;
    if (String(rawBudget).trim() === '' || String(rawReserve).trim() === '' || String(rawBuffer).trim() === '') throw new Error('invalid_config');
    var budget = Number(rawBudget), reserve = Number(rawReserve), buffer = Number(rawBuffer);
    if (!Number.isFinite(budget) || budget < 0 || !Number.isFinite(reserve) || reserve < 0 || !Number.isFinite(buffer) || buffer < 5 || buffer > 50) throw new Error('invalid_config');
    var kinds = Object.keys(labels).filter(function (kind) { return root.querySelector('[data-gta-kind="' + kind + '"]').checked; });
    var enabled = root.querySelector('[data-gta-enabled]').checked; if (enabled && !kinds.length) throw new Error('invalid_config');
    return validateSettings({ enabled: enabled, landPolicy: root.querySelector('[data-gta-land-policy]').value, budget: budget, reserve: reserve, target: String(rawBuffer) === targetDisplay ? savedTarget : (100 - buffer) / 100, kinds: kinds });
  }
  function showTab(name, focus) {
    if (['overview', 'settings', 'projects'].indexOf(name) < 0) return;
    activeTab = name;
    root.querySelectorAll('[data-gta-tab]').forEach(function (tab) {
      var selected = tab.getAttribute('data-gta-tab') === name;
      tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
      if (selected && focus) tab.focus();
    });
    root.querySelectorAll('[data-gta-page]').forEach(function (page) { page.hidden = page.getAttribute('data-gta-page') !== name; });
    root.querySelector('[data-gta-save-actions]').hidden = name !== 'settings';
    root.querySelector('[data-gta-build-actions]').hidden = name === 'settings';
    root.querySelector('.gta-body').scrollTop = 0;
  }
  function ready() { return !!(state && state.demand && state.demand.calibrated !== false && (!state.delivery || state.delivery.ready !== false)); }
  function activeItems() { return state && state.projects && Array.isArray(state.projects.items) ? state.projects.items : []; }
  function blockerInfo(blocker) {
    var reason = String(blocker.reason || ''), kind = /^(sea|gas_terminal|oil_terminal)$/.test(blocker.mode) ? 'port' : blocker.mode;
    var saved = config(state && state.config), selected = kind === 'land' ? saved.kinds.some(function (k) { return ['road', 'motorway', 'rail'].indexOf(k) >= 0; }) : saved.kinds.indexOf(kind) >= 0;
    var foreign = /foreign|external/.test(String(blocker.scope)) || /foreign/.test(reason);
    var ids = (blocker.projectIds || []).concat(blocker.sharedProjectIds || []).map(String);
    var projects = activeItems().filter(function (item) { return ids.indexOf(String(item.id)) >= 0; });
    var group = 'external', explanation, link;
    if (foreign) explanation = reason === 'foreign_serial_capacity' ? 'A section in another country is limiting deliveries. Expanding your section alone would not help.' : 'This part of the route is controlled by another country. The mod cannot build there.';
    else if (reason === 'serial_upgrade_required') explanation = 'Several sections need expanding together. The mod cannot arrange these upgrades automatically.';
    else if (/queue|pending/.test(reason) && projects.length) {
      group = 'building'; explanation = blocker.queueEvidence === 'unmapped' ? 'Related construction is not yet included in the route report. Waiting for an updated report before deciding whether more work will help.' : reason === 'queued_shared_route' ? 'Waiting for related construction to finish before deciding whether more work will help.' : 'Construction is underway. Delivery results update after work finishes and the game checks routes again.'; link = 'projects';
    } else if (/queue|pending/.test(reason)) explanation = 'No matching active project was found. Update the report before checking for more construction.';
    else if (reason === 'target_capacity_sufficient') explanation = 'This section already has enough space for the building target. Another part of the route may still limit deliveries.';
    else if (blocker.actionable || /disabled|not_enabled/.test(reason)) {
      group = 'actionable';
      explanation = selected ? 'The mod can consider an expansion here, within your budget and available cash.' : 'Allow ' + (labels[kind] || 'a land transport type').toLowerCase() + ' in Settings, then save to allow construction here.';
      if (!selected) link = 'settings';
    } else if (reason === 'selected_route_unavailable') explanation = 'Waiting for a reliable route report before deciding what to build.';
    else if (/no_route|unavailable_route/.test(reason)) explanation = 'No usable route is available. This mod cannot create the missing connection.';
    else if (reason === 'missing_terminal') explanation = 'A loading facility is missing. This mod cannot add the required connection here.';
    else explanation = 'The mod does not support the construction needed here.';
    return { blocker: blocker, group: group, explanation: explanation, link: link, projects: projects };
  }
  function problems() {
    return ready() && state.delivery && Array.isArray(state.delivery.blockers) ? state.delivery.blockers.filter(function (b) { return finite(b.unmetLoad) && Number(b.unmetLoad) > EPS; }).sort(function (a, b) { return Number(b.unmetLoad) - Number(a.unmetLoad); }).map(blockerInfo) : [];
  }
  function currentExplanation() {
    if (!state) return { title: 'Loading transport information…', message: 'Your saved settings and latest report will appear here.' };
    if (!state.config.enabled) return { title: 'Automatic building is off', message: 'Turn it on in Settings and save to allow new construction. Existing projects continue.' };
    if (!ready()) return { title: 'Waiting for a transport report', message: 'The game needs to check the transport network before the mod can decide what to build.' };
    var status = state.currentStatus || state.status || {}, code = status.code, list = problems();
    var messages = {
      expanding: ['More land construction is allowed', 'Keep expanding is selected. The mod can add roads, freeways and railways even when current transport needs are covered, within your saved limits.'],
      authority: ['Waiting for government control', 'Automatic building resumes when you control the government.'],
      budget: ['No room in the monthly budget', 'Existing transport work or the next suitable project uses the available allowance. Review your budget in Settings. Existing work continues.'],
      reserve: ['Not enough cash for new work', 'New projects must leave your minimum cash after allowing for unpaid construction costs. Existing work continues.'],
      batch: ['Waiting before starting more projects', 'The mod has reached its daily start limit or the limit on active projects. It will check again on the next game day.'],
      waiting: ['No suitable project fits right now', 'The last check found no project that fits the remaining need, budget and available cash. The mod will check again next game day.'],
      rejected: ['The game could not start new construction', 'No further projects were added in that check. Existing work continues; try again after the game updates.'],
      error: ['The last construction check could not finish', 'Some work may already have started. Review Projects and update the report before trying again.']
    };
    if (messages[code]) return { title: messages[code][0], message: messages[code][1] };
    if (list.some(function (b) { return b.group === 'actionable'; }) || Number(state.demand.additionalCapacity) > EPS) return { title: 'There is construction to consider', message: 'The mod checks each game day and starts suitable work within your saved limits. You can also check and build now.' };
    if (Number(state.projects && state.projects.active) > 0) return { title: 'Construction is underway', message: 'Existing work is counted when planning more construction. Actual deliveries improve only after work finishes and routes update.' };
    if (list.length) return { title: 'Remaining problems cannot be fixed here', message: list.every(function (b) { return /foreign|external/.test(String(b.blocker.scope)) || /foreign/.test(String(b.blocker.reason)); }) ? 'The reported problems are in other countries or need a foreign section expanded. More construction at home alone would not help.' : 'Review the remaining problems below. The mod will keep checking for useful construction each game day.' };
    if (state.delivery && state.delivery.fulfilled === false) return { title: 'No suitable expansion found in this report', message: 'Some transport needs are still unmet, but the report does not identify a construction project the mod can start.' };
    return { title: 'No new construction needed right now', message: 'The latest report shows no suitable expansion is needed. Automatic checks continue each game day.' };
  }
  function updateActions() {
    root.querySelectorAll('[data-gta-edit]').forEach(function (input) { input.disabled = busy || !loaded; });
    root.querySelector('[data-gta-save]').disabled = busy || !loaded || !dirty;
    root.querySelector('[data-gta-discard]').disabled = busy || !dirty;
    root.querySelector('[data-gta-refresh]').disabled = busy;
    root.querySelector('[data-gta-run]').disabled = busy || !loaded || dirty || !state || state.config.enabled !== true || !ready();
    text('[data-gta-unsaved]', dirty ? 'Unsaved changes — automatic building still uses your saved settings.' : 'Settings are saved.');
    updateDraftHelp();
    text('[data-gta-draft-state]', root.querySelector('[data-gta-enabled]').checked ? 'On' : 'Off');
    text('[data-gta-busy]', busy ? 'Working…' : '');
    text('[data-gta-action-help]', dirty ? 'Save or discard your changes in Settings before starting construction.' : state && !state.config.enabled ? 'Turn on automatic building in Settings to start new projects.' : !ready() ? 'Construction is available after the first transport report.' : 'Checks now and may start paid construction using your saved settings.');
  }
  function updateDraftHelp() {
    var expand = root.querySelector('[data-gta-land-policy]').value === 'expand';
    text('[data-gta-land-policy-help]', expand ? 'Continue the selected roads, freeways and railways toward the game’s construction limits, even when current transport needs are met. Types take turns when eligible. Pipelines and ports still build only when needed.' : 'Build only when more transport space is needed, including your room for growth. The mod may use less than your monthly allowance.');
    text('[data-gta-growth-help]', 'At ' + root.querySelector('[data-gta-target]').value + '%, aim to leave that share of transport space unused.' + (expand ? ' Keep expanding does not stop land construction at this target; pipelines and ports still use it.' : ' This is a building target, not a promise that every route has spare space.'));
  }
  function renderBlockers(list) {
    ['actionable', 'building', 'external'].forEach(function (group) {
      var items = list.filter(function (b) { return b.group === group; }), holder = root.querySelector('[data-gta-group="' + group + '"]'); holder.replaceChildren();
      text('[data-gta-count="' + group + '"]', String(items.length));
      if (!items.length) holder.appendChild(element('p', 'gta-muted', !ready() ? 'Waiting for a transport report.' : group === 'actionable' ? 'No reported route problems can be addressed here right now.' : group === 'building' ? 'No reported route problem is linked to current construction.' : 'No other significant route problems reported.'));
      var overflow;
      items.forEach(function (info, i) {
        var destination = holder;
        if (i >= 3) {
          if (!overflow) { overflow = element('details', 'gta-more'); overflow.appendChild(element('summary', '', 'Show ' + (items.length - 3) + ' more')); holder.appendChild(overflow); }
          destination = overflow;
        }
        var row = element('article', 'gta-problem'), b = info.blocker;
        row.appendChild(element('h4', '', String(b.label || labels[b.mode] || 'Transport route').replace(/^air(?= —|$)/, 'Air transport')));
        row.appendChild(element('p', '', info.explanation));
        if (info.link) {
          var button = element('button', 'k gta-link', info.link === 'settings' ? 'Choose allowed construction' : 'View related projects'); button.type = 'button';
          if (info.link === 'projects') button.setAttribute('data-gta-show-projects', '');
          button.addEventListener('click', function () { showTab(info.link, true); if (info.projects.length) { var project = root.querySelector('[data-gta-project-id="' + info.projects[0].id.replace(/[^a-zA-Z0-9_-]/g, '_') + '"]'); if (project) { project.focus(); project.scrollIntoView && project.scrollIntoView({block:'nearest'}); } } }); row.appendChild(button);
        }
        destination.appendChild(row);
      });
    });
    var raw = root.querySelector('[data-gta-route-details]'); raw.replaceChildren();
    list.forEach(function (info) { var row = element('div', 'gta-kv'); row.appendChild(element('span', '', info.blocker.label || 'Transport route')); row.appendChild(element('b', '', fmt(info.blocker.unmetLoad))); raw.appendChild(row); });
  }
  function renderProjects() {
    var items = activeItems(), rows = root.querySelector('[data-gta-projects]'); rows.replaceChildren();
    if (!items.length) rows.appendChild(element('p', 'gta-empty', 'No transport projects are underway. The Overview explains whether more construction is needed.'));
    items.forEach(function (item) {
      var row = element('article', 'gta-project'); row.tabIndex = -1; row.setAttribute('data-gta-project-id', String(item.id).replace(/[^a-zA-Z0-9_-]/g, '_'));
      var title = item.label || labels[item.kind] || 'Transport construction';
      row.appendChild(element('h4', '', title));
      row.appendChild(element('p', 'gta-muted', (item.automatic ? 'Automatic' : 'Started manually') + (item.provinceName || item.provinceKey ? ' · ' + (item.provinceName || item.provinceKey) : '') + ' · Project ' + item.id));
      var facts = element('div', 'gta-project-facts');
      facts.appendChild(element('span', '', 'Estimated monthly cost: ' + money(item.monthly)));
      facts.appendChild(element('span', '', 'Estimated time left: ' + (finite(item.monthsLeft) ? Number(item.monthsLeft) < 1 ? 'less than 1 month' : fmt(item.monthsLeft) + ' months' : 'not available')));
      if (Number(item.km) > 0) facts.appendChild(element('span', '', fmt(item.km) + ' km')); row.appendChild(facts); rows.appendChild(row);
    });
    var history = state && Array.isArray(state.history) ? state.history : [], holder = root.querySelector('[data-gta-history-list]'); holder.replaceChildren();
    text('[data-gta-history]', history.length ? 'Showing ' + history.length + ' recent automatic starts (up to 32 retained). These records do not confirm completion.' : 'No recent automatic starts recorded.');
    history.slice().reverse().forEach(function (item) {
      var row = element('div', 'gta-history-row'); row.appendChild(element('strong', '', item.label || labels[item.kind] || 'Transport construction'));
      row.appendChild(element('span', '', 'Game day ' + fmt(item.day) + ' · ' + money(item.cost) + ' total' + (item.provinceName || item.provinceKey ? ' · ' + (item.provinceName || item.provinceKey) : ''))); holder.appendChild(row);
    });
  }
  function render() {
    if (!root) return;
    var d = state && state.demand || {}, delivery = state && state.delivery || {}, b = state && state.budget || {}, p = state && state.projects || {};
    var rate = ready() && Number(delivery.requestedLoad) !== 0 ? finite(delivery.rate) ? Number(delivery.rate) : finite(d.deliveryRate) ? Number(d.deliveryRate) : null : null;
    if (rate != null && Math.abs(1 - rate) <= EPS) rate = 1;
    var missing = rate == null ? null : Math.max(0, 1 - rate), explanation = currentExplanation();
    text('[data-gta-saved-state]', !state ? 'Loading…' : 'Automatic building: ' + (state.config.enabled ? 'On' : 'Off'));
    text('[data-gta-status-title]', explanation.title); text('[data-gta-status]', explanation.message);
    text('[data-gta-delivery]', pct(rate)); text('[data-gta-missing]', pct(missing));
    text('[data-gta-delivery-summary]', !ready() ? 'Waiting for a transport report' : Number(delivery.requestedLoad) === 0 ? 'No transport requested in the latest report' : missing > EPS ? pct(missing) + ' still needs transport' : 'Current transport needs are met');
    text('[data-gta-active]', state ? String(p.active || 0) : '—');
    text('[data-gta-project-count]', state ? (p.automatic || 0) + ' automatic · ' + Math.max(0, (p.active || 0) - (p.automatic || 0)) + ' started manually' : 'Loading projects…');
    text('[data-gta-planned]', money(b.monthlyCommitted)); text('[data-gta-budget-summary]', 'Budget: ' + money(b.monthlyCap) + ' / month');
    text('[data-gta-budget-label]', 'Monthly construction limit (' + currency() + ' billions)');
    text('[data-gta-reserve-label]', 'Minimum cash to keep (' + currency() + ' billions)');
    updateDraftHelp();
    var coverage = !ready() ? 'Waiting for the first land transport report.' : Number(d.additionalCapacity) > EPS ? 'More road or railway space is needed to reach your growth target.' : Number(d.pendingCapacity) > EPS ? 'Existing roads, railways and current construction cover your land transport target.' : 'Your roads and railways have enough space for the current land transport target.';
    var landSummary = config(state && state.config).landPolicy === 'expand' ? 'Land building: Keep expanding. Selected land types can be built beyond current demand, within your limits.' : 'Land building: Build when needed. More roads, freeways or railways start only when the current network needs more space.';
    text('[data-gta-coverage]', coverage + ' ' + landSummary);
    text('[data-gta-land-summary]', landSummary);
    text('[data-gta-load]', ready() ? fmt(d.load) : '—'); text('[data-gta-capacity]', ready() ? fmt(d.capacity) : '—'); text('[data-gta-pending]', ready() ? fmt(d.pendingCapacity) : '—');
    text('[data-gta-utilization]', ready() ? pct(d.utilization) : '—'); text('[data-gta-target-capacity]', ready() ? fmt(d.targetCapacity) : '—');
    text('[data-gta-requested]', ready() ? fmt(delivery.requestedLoad) : '—'); text('[data-gta-delivered-load]', ready() ? fmt(delivery.deliveredLoad) : '—'); text('[data-gta-unmet-load]', ready() ? fmt(delivery.unmetLoad) : '—');
    [['cap','monthlyCap'],['committed','monthlyCommitted'],['available','monthlyAvailable'],['cash','cash'],['cash-available','cashAvailable'],['outstanding','outstanding'],['reserve-value','reserve']].forEach(function (pair) { text('[data-gta-' + pair[0] + ']', money(b[pair[1]])); });
    var review = state && state.review || {};
    text('[data-gta-report-age]', ready() && finite(d.solveDay) ? 'Transport report: game day ' + fmt(d.solveDay) : 'Transport report: waiting');
    text('[data-gta-review-age]', finite(review.day) ? 'Last construction check: game day ' + fmt(review.day) + (review.stale ? ' · Report or settings changed since then' : '') : 'No construction check recorded yet');
    renderBlockers(problems()); renderProjects(); updateActions();
  }
  async function action(kind) {
    if (busy || !opened) return; var token = generation; busy = true; notice(''); updateActions();
    try {
      var name = 'transport_auto_state', args = [];
      if (kind === 'save') { var c = readConfig(); name = 'transport_auto_config'; args = [JSON.stringify(c)]; }
      if (kind === 'run') { if (dirty || !state || state.config.enabled !== true || !ready()) throw new Error('disabled'); name = 'transport_auto_run'; }
      var result = await command(name, args);
      if (token !== generation) return;
      state = result; loaded = true;
      if (kind === 'save' || !dirty) populate(); render();
      var run = state.lastRun;
      notice(kind === 'save' ? 'Settings saved. Automatic building is ' + (state.config.enabled ? 'on; checks continue each game day.' : 'off. Existing projects continue.') : kind === 'run' ? run && run.started > 0 ? 'Started ' + run.started + ' project' + (run.started === 1 ? '' : 's') + '. Estimated combined monthly cost: ' + money(run.monthlyCost) + '.' : 'No projects started. ' + currentExplanation().title + '.' : 'Report updated. No construction was started by this button.');
      lastDay = owner && owner.day;
    } catch (error) { if (token === generation) { notice(errorMessage(error.message), true); if (error.message === 'campaign_changed') { loaded = false; state = null; dirty = false; render(); } } }
    finally { if (token === generation) { busy = false; updateActions(); } }
  }
  function close() { if (!opened) return; opened = false; generation++; busy = false; root.hidden = true; var focus = lastFocus; if (focus && focus.isConnected && typeof focus.focus === 'function') focus.focus(); }
  function open() {
    mount(); if (opened) { root.querySelector('[data-gta-tab="' + activeTab + '"]').focus(); return; }
    var current = sim(), keepDraft = dirty && sameCampaign(current) && loaded;
    generation++; opened = true; busy = false; lastFocus = document.activeElement;
    if (!keepDraft) { loaded = false; dirty = false; state = null; bindCampaign(current); populate(); activeTab = 'overview'; }
    render(); notice(''); root.hidden = false; showTab(keepDraft ? 'settings' : activeTab, true); action('refresh');
  }
  function mount() {
    if (root) return;
    var css = element('style'); css.id = 'gta-style'; css.textContent = `
/* Native 1.1.3 surfaces and controls; fallbacks mirror the shipped design tokens. */
#gta-dialog[hidden]{display:none!important}
#gta-dialog{position:fixed;inset:0;z-index:10090;display:flex;align-items:center;justify-content:center;padding:var(--gap-4,20px);background:rgba(8,9,11,.55);color:var(--text-2,#aba79e);font:var(--fw-regular,400) var(--fs-body,12px)/var(--lh-read,20px) var(--font-body,"Noto Sans",sans-serif);color-scheme:dark;box-sizing:border-box}
#gta-dialog *{box-sizing:border-box}
#gta-dialog .gta-panel{display:flex;flex-direction:column;width:var(--dialog-w-wide,min(920px,94vw));max-width:100%;max-height:88vh;overflow:hidden;border:0;border-radius:var(--r-plate,6px);background:var(--body,#212326);box-shadow:var(--slab,inset 0 1px 0 rgba(255,255,255,.115),0 0 0 1px rgba(0,0,0,.7)),var(--shadow,0 18px 56px rgba(0,0,0,.5))}
#gta-dialog .gta-head,#gta-dialog .gta-foot{flex:none;display:flex;align-items:center;gap:var(--gap-3,12px);padding:var(--gap-3,12px) var(--gap-4,20px)}
#gta-dialog .gta-head{align-items:flex-start;justify-content:space-between;box-shadow:inset 0 -1px 0 var(--joint,rgba(0,0,0,.7))}
#gta-dialog .gta-head>div{min-width:0}
#gta-dialog .gta-eyebrow{margin-bottom:var(--gap-1,4px);color:var(--text-3,#8b887f);font:var(--fw-semi,600) var(--fs-plate,10px)/var(--lh-plate,16px) var(--font-display,"Noto Sans",sans-serif);letter-spacing:var(--tr-plate,.14em);text-transform:uppercase}
#gta-dialog h2{margin:0;color:var(--text,#ece9e3);font:var(--fw-semi,600) var(--fs-lead,16px)/var(--lh-lead,24px) var(--font-display,"Noto Sans",sans-serif)}
#gta-dialog h3{margin:0 0 var(--gap-3,12px);color:var(--text,#ece9e3);font:var(--fw-semi,600) var(--fs-plate,10px)/var(--lh-plate,16px) var(--font-display,"Noto Sans",sans-serif);letter-spacing:var(--tr-plate,.14em);text-transform:uppercase}
#gta-dialog p{margin:var(--gap-1,4px) 0 var(--gap-3,12px)}
#gta-dialog .gta-head p{margin:var(--gap-1,4px) 0 0}
#gta-dialog .gta-body{min-height:0;overflow:auto;overscroll-behavior:contain;padding:var(--gap-4,20px)}
#gta-dialog .gta-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--gap-4,20px);min-width:0}
#gta-dialog .gta-box{min-width:0;padding:var(--gap-3,12px);background:var(--well,#141517);border-radius:var(--r-cell,4px);box-shadow:var(--sunk,inset 0 1px 2px rgba(0,0,0,.45),inset 0 0 0 1px rgba(0,0,0,.7))}
#gta-dialog .gta-wide{grid-column:1/-1}
#gta-dialog .gta-muted,#gta-dialog .gta-note{color:var(--text-2,#aba79e)}
#gta-dialog .gta-note{font-size:var(--fs-body,12px);line-height:var(--lh-read,20px);margin-top:var(--gap-2,8px)}
#gta-dialog .gta-error{color:var(--neg,#d3826e)}
#gta-dialog .gta-land-title{margin-top:var(--gap-4,20px);padding-top:var(--gap-3,12px);box-shadow:inset 0 1px 0 var(--joint,rgba(0,0,0,.7))}
#gta-dialog label{cursor:pointer}
#gta-dialog .gta-enable{display:flex;align-items:center;justify-content:space-between;gap:var(--gap-3,12px);min-height:var(--h-cell,44px);margin-bottom:var(--gap-3,12px);color:var(--text,#ece9e3);font:var(--fw-semi,600) var(--fs-read,13px)/var(--lh-read,20px) var(--font-body,"Noto Sans",sans-serif)}
#gta-dialog .gta-kinds{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:var(--gap-1,4px);margin-bottom:var(--gap-3,12px)}
#gta-dialog .gta-kinds label{display:flex;align-items:center;gap:var(--gap-2,8px);min-width:0;min-height:var(--h-ctl,32px);padding:var(--gap-1,4px) var(--gap-2,8px);border-radius:var(--r-cell,4px);color:var(--text-2,#aba79e);box-shadow:inset 0 0 0 1px var(--joint,rgba(0,0,0,.7));line-height:var(--lh-body,16px)}
#gta-dialog .gta-kinds label:has(input:checked){background:var(--body-2,#292b2f);color:var(--text,#ece9e3);box-shadow:var(--posed,inset 0 1px 0 rgba(255,255,255,.115),inset 0 -1px 0 rgba(0,0,0,.55),0 0 0 1px rgba(0,0,0,.7))}
#gta-dialog input[type=checkbox]{position:relative;appearance:none;flex:none;width:14px;height:14px;margin:0;border:1px solid var(--hairline-2,rgba(236,233,227,.16));border-radius:var(--r-well,2px);background:var(--well,#141517);cursor:pointer}
#gta-dialog input[type=checkbox]::after{content:"";position:absolute;left:3px;top:1px;width:5px;height:8px;border:solid var(--mark-you,#d6a35b);border-width:0 2px 2px 0;transform:rotate(45deg);opacity:0}
#gta-dialog input[type=checkbox]:checked::after{opacity:1}
#gta-dialog input.gta-switch{width:46px;height:var(--h-row,28px);border:0;border-radius:var(--r-cell,4px);box-shadow:var(--sunk,inset 0 1px 2px rgba(0,0,0,.45),inset 0 0 0 1px rgba(0,0,0,.7))}
#gta-dialog input.gta-switch::after{left:2px;top:2px;width:21px;height:calc(100% - 4px);border:0;border-radius:var(--r-well,2px);background:var(--body-2,#292b2f);box-shadow:var(--posed,inset 0 1px 0 rgba(255,255,255,.115),0 1px 2px rgba(0,0,0,.7));transform:none;opacity:1;transition:transform .17s,background-color .16s}
#gta-dialog input.gta-switch:checked::after{transform:translateX(21px);background:var(--mark-you,#d6a35b)}
#gta-dialog .gta-field{display:grid;grid-template-columns:minmax(0,1fr) 112px;align-items:center;gap:var(--gap-3,12px);margin:var(--gap-3,12px) 0}
#gta-dialog .gta-policy{display:grid;gap:8px;margin:12px 0}
#gta-dialog .gta-policy select{width:100%;min-width:0;min-height:40px;padding:8px 12px;border:1px solid var(--joint,#444);border-radius:3px;background:var(--body,#212326);color:var(--text,#ece9e3);font:inherit}
#gta-dialog .gta-field input{width:100%;min-width:0;height:var(--h-ctl,32px);padding:0 var(--gap-3,12px);border:0;border-radius:var(--r-well,2px);background:var(--body,#212326);color:var(--text,#ece9e3);box-shadow:var(--sunk,inset 0 1px 2px rgba(0,0,0,.45),inset 0 0 0 1px rgba(0,0,0,.7));font:var(--fw-medium,500) var(--fs-read,13px)/var(--lh-read,20px) var(--num,"Noto Sans",sans-serif);text-align:right;font-variant-numeric:tabular-nums}
#gta-dialog .gta-actions{display:flex;align-items:center;flex-wrap:wrap;gap:var(--gap-2,8px);margin-top:var(--gap-3,12px)}
#gta-dialog button{--k-face:var(--body-2,#292b2f);--k-ink:var(--text-2,#aba79e);flex:none;display:inline-flex;align-items:center;justify-content:center;height:var(--h-ctl,32px);padding:0 var(--gap-3,12px);border:0;border-radius:var(--r-cell,4px);background:var(--k-face);color:var(--k-ink);box-shadow:var(--posed,inset 0 1px 0 rgba(255,255,255,.115),inset 0 -1px 0 rgba(0,0,0,.55),0 0 0 1px rgba(0,0,0,.7),0 1px 2px rgba(0,0,0,.7));font:var(--fw-semi,600) var(--fs-body,12px)/1 var(--font-display,"Noto Sans",sans-serif);cursor:pointer;transition:background-color .12s,color .12s,box-shadow .06s,transform .06s}
#gta-dialog button.gta-primary{--k-ink:var(--mark-you,#d6a35b);box-shadow:var(--posed-hi,inset 0 1px 0 rgba(255,255,255,.115),inset 0 -1px 0 rgba(0,0,0,.55),0 0 0 1px rgba(0,0,0,.7),0 2px 5px -1px rgba(0,0,0,.7))}
#gta-dialog button:hover:not(:disabled){background:linear-gradient(var(--hover-veil,rgba(255,255,255,.04)),var(--hover-veil,rgba(255,255,255,.04))),var(--k-face);color:var(--text,#ece9e3)}
#gta-dialog button.gta-primary:hover:not(:disabled){color:var(--mark-you,#d6a35b)}
#gta-dialog button:active:not(:disabled){transform:translateY(1px);box-shadow:var(--pressed,inset 0 1px 0 rgba(0,0,0,.55),inset 0 -1px 0 rgba(255,255,255,.115),0 0 0 1px rgba(0,0,0,.7))}
#gta-dialog button:disabled{--k-face:transparent;--k-ink:var(--text-2,#aba79e);color:var(--text-2,#aba79e);box-shadow:inset 0 0 0 1px var(--joint,rgba(0,0,0,.7));opacity:1;cursor:default}
#gta-dialog input:disabled{cursor:default;color:var(--text-2,#aba79e)}
#gta-dialog input.gta-switch:disabled::after{background:var(--text-3,#8b887f);box-shadow:none}
#gta-dialog button:focus-visible,#gta-dialog input:focus-visible{outline:2px solid var(--mark-you,#d6a35b);outline-offset:-2px}
#gta-dialog .gta-kv{display:flex;justify-content:space-between;align-items:baseline;gap:var(--gap-3,12px);padding:var(--gap-2,8px) 0;box-shadow:inset 0 -1px 0 var(--joint,rgba(0,0,0,.7))}
#gta-dialog .gta-kv>span{min-width:0}
#gta-dialog .gta-kv b{flex:none;color:var(--text,#ece9e3);font:var(--fw-semi,600) var(--fs-body,12px)/var(--lh-read,20px) var(--num,"Noto Sans",sans-serif);font-variant-numeric:tabular-nums;white-space:nowrap}
#gta-dialog .gta-kv b[data-gta-delivery]{font-size:var(--fs-hero,21px);line-height:var(--lh-hero,28px)}
#gta-dialog .gta-foot{box-shadow:inset 0 1px 0 var(--joint,rgba(0,0,0,.7))}
#gta-dialog .gta-notice{flex:1;min-width:0;line-height:var(--lh-body,16px)}
#gta-dialog .gta-notice:empty,#gta-dialog [data-gta-unsaved]:empty{display:none}
#gta-dialog [data-gta-unsaved]{margin:var(--gap-2,8px) 0 0;color:var(--mark-you,#d6a35b);font-size:var(--fs-body,12px)}
#gta-dialog .gta-table-wrap{min-width:0;max-width:100%;overflow:auto}
#gta-dialog .gta-table{width:100%;border-collapse:collapse;text-align:left;font-size:var(--fs-body,12px);line-height:var(--lh-read,20px)}
#gta-dialog th,#gta-dialog td{padding:var(--gap-2,8px);border-bottom:1px solid var(--joint,rgba(0,0,0,.7));vertical-align:top}
#gta-dialog th{color:var(--text-3,#8b887f);font-weight:var(--fw-semi,600);font-size:var(--fs-plate,10px);letter-spacing:.04em}
#gta-dialog td{color:var(--text-2,#aba79e)}
#gta-dialog td:first-child{color:var(--text,#ece9e3)}
#gta-dialog td:not(:first-child){white-space:nowrap}
@media(max-width:720px){#gta-dialog{padding:var(--gap-2,8px)}#gta-dialog .gta-panel{max-height:calc(100dvh - 16px);width:100%}#gta-dialog .gta-head,#gta-dialog .gta-foot,#gta-dialog .gta-body{padding:var(--gap-3,12px)}#gta-dialog .gta-grid{grid-template-columns:minmax(0,1fr);gap:var(--gap-3,12px)}#gta-dialog .gta-wide{grid-column:auto}#gta-dialog .gta-foot{flex-wrap:wrap}#gta-dialog .gta-notice{flex-basis:100%}}
@media(max-width:380px){#gta-dialog .gta-kinds{grid-template-columns:repeat(2,minmax(0,1fr))}#gta-dialog .gta-field{grid-template-columns:minmax(0,1fr) 96px}}

#gta-dialog [hidden]{display:none!important}
#gta-dialog .gta-panel{width:min(960px,94vw);height:min(800px,90vh);max-height:90vh}
#gta-dialog h3{text-transform:none;letter-spacing:0;font-size:var(--fs-read,13px);line-height:1.4;margin:0 0 10px}
#gta-dialog h4{font:inherit;font-weight:600;color:var(--text,#ece9e3);margin:0 0 5px}
#gta-dialog .gta-head{align-items:center}
#gta-dialog .gta-head h2{font-size:var(--fs-hero,21px)}
#gta-dialog .gta-badge{display:inline-block;margin-top:6px;color:var(--mark-you,#d6a35b)}
#gta-dialog .gta-tabs{display:flex;gap:6px;padding:0 20px 12px;border-bottom:1px solid var(--joint,rgba(0,0,0,.7))}
#gta-dialog .gta-tabs button{flex:1;background:transparent;box-shadow:none;border-radius:0;border-bottom:2px solid transparent}
#gta-dialog .gta-tabs button[aria-selected=true]{color:var(--mark-you,#d6a35b);border-bottom-color:var(--mark-you,#d6a35b);background:var(--well,#141517)}
#gta-dialog .gta-body{flex:1;padding:20px}
#gta-dialog .gta-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-bottom:20px}
#gta-dialog .gta-metric{min-width:0;padding-right:12px;border-right:1px solid var(--joint,rgba(0,0,0,.7))}
#gta-dialog .gta-metric:last-child{border:0}
#gta-dialog .gta-metric strong{display:block;color:var(--text,#ece9e3);font-size:var(--fs-hero,21px);margin:6px 0;overflow-wrap:anywhere}
#gta-dialog .gta-metric small{display:block;font:inherit;font-size:var(--fs-small,11px)}
#gta-dialog .gta-situation{padding:16px;border-left:3px solid var(--mark-you,#d6a35b);background:var(--well,#141517);margin-bottom:18px}
#gta-dialog .gta-situation p:last-child{margin-bottom:0}
#gta-dialog .gta-section{margin:20px 0}
#gta-dialog .gta-section-title{display:flex;justify-content:space-between;gap:10px}
#gta-dialog .gta-section-title span{color:var(--text-2,#aba79e)}
#gta-dialog .gta-problem,#gta-dialog .gta-project{padding:12px 0;border-bottom:1px solid var(--joint,rgba(0,0,0,.7));overflow-wrap:anywhere}
#gta-dialog .gta-problem p{margin-bottom:6px}
#gta-dialog .gta-link{color:var(--mark-you,#d6a35b);padding-left:0;box-shadow:none;background:transparent;text-align:left;white-space:normal;height:auto;min-height:32px}
#gta-dialog details{margin:12px 0}
#gta-dialog summary{cursor:pointer;padding:10px 0;color:var(--text,#ece9e3);font-weight:600}
#gta-dialog summary:focus-visible{outline:2px solid var(--mark-you,#d6a35b);outline-offset:2px}
#gta-dialog .gta-more{padding-left:12px}
#gta-dialog .gta-age{display:flex;flex-wrap:wrap;gap:8px 20px;margin-top:16px;font-size:var(--fs-small,11px)}
#gta-dialog .gta-foot{display:block;padding:12px 20px}
#gta-dialog .gta-footer-actions{display:flex;align-items:center;gap:10px}
#gta-dialog .gta-footer-actions>span{flex:1;min-width:0}
#gta-dialog .gta-foot p{margin:7px 0 0;font-size:var(--fs-small,11px)}
#gta-dialog .gta-notice:not(:empty){margin-top:8px;color:var(--text,#ece9e3)}
#gta-dialog .gta-notice.gta-error{color:var(--neg,#d3826e)}
#gta-dialog fieldset{min-width:0;margin:0 0 20px;padding:0;border:0}
#gta-dialog legend{color:var(--text,#ece9e3);font-weight:600;padding:0 0 10px}
#gta-dialog .gta-choice-group{margin:12px 0}
#gta-dialog .gta-choice-group>span{display:block;margin-bottom:6px}
#gta-dialog .gta-choices{display:flex;flex-wrap:wrap;gap:8px 18px}
#gta-dialog .gta-choices label{display:flex;align-items:center;gap:8px;min-height:32px;color:var(--text,#ece9e3)}
#gta-dialog .gta-enable{margin-bottom:6px;justify-content:flex-start}
#gta-dialog .gta-enable>span:first-child{flex:1}
#gta-dialog .gta-field{margin-bottom:5px}
#gta-dialog .gta-help{max-width:66ch;font-size:var(--fs-small,11px);margin-bottom:18px}
#gta-dialog .gta-project-facts{display:flex;flex-wrap:wrap;gap:8px 24px}
#gta-dialog .gta-project:focus{outline:2px solid var(--mark-you,#d6a35b);outline-offset:2px}
#gta-dialog .gta-history-row{display:flex;flex-direction:column;gap:4px;padding:10px 0;border-bottom:1px solid var(--joint,rgba(0,0,0,.7));overflow-wrap:anywhere}
#gta-dialog .gta-history-row strong{color:var(--text,#ece9e3)}
#gta-dialog .gta-kv{align-items:flex-start}
#gta-dialog .gta-kv>span{overflow-wrap:anywhere}
@media(max-width:600px){#gta-dialog .gta-panel{width:100%;height:94dvh;max-height:94dvh}#gta-dialog .gta-head,#gta-dialog .gta-foot,#gta-dialog .gta-body{padding:12px}#gta-dialog .gta-tabs{padding:0 12px 8px}#gta-dialog .gta-metrics{gap:8px}#gta-dialog .gta-metric{padding-right:6px}#gta-dialog .gta-metric strong{font-size:18px}#gta-dialog .gta-grid{grid-template-columns:1fr}#gta-dialog .gta-footer-actions{flex-wrap:wrap}#gta-dialog .gta-footer-actions>span{flex-basis:100%}#gta-dialog .gta-field{grid-template-columns:minmax(0,1fr) 90px}}
@media(prefers-reduced-motion:reduce){#gta-dialog *{transition:none!important;scroll-behavior:auto!important}}
`;
    document.head.appendChild(css);
    root = element('div'); root.id = 'gta-dialog'; root.hidden = true;
    root.innerHTML = `<section class="gta-panel" role="dialog" aria-modal="true" aria-labelledby="gta-title" tabindex="-1">
<header class="gta-head"><div><h2 id="gta-title">Transport Autopilot</h2><span class="gta-badge" data-gta-saved-state></span></div><button type="button" class="k" data-gta-close aria-label="Close Transport Autopilot">Close</button></header>
<nav class="gta-tabs" role="tablist" aria-label="Transport Autopilot pages"><button type="button" class="k" id="gta-tab-overview" role="tab" aria-controls="gta-page-overview" data-gta-tab="overview">Overview</button><button type="button" class="k" id="gta-tab-settings" role="tab" aria-controls="gta-page-settings" data-gta-tab="settings">Settings</button><button type="button" class="k" id="gta-tab-projects" role="tab" aria-controls="gta-page-projects" data-gta-tab="projects">Projects</button></nav>
<div class="gta-body">
<section id="gta-page-overview" role="tabpanel" aria-labelledby="gta-tab-overview" data-gta-page="overview">
<div class="gta-metrics"><div class="gta-metric"><span>Transport needs met</span><strong data-gta-delivery>—</strong><small data-gta-delivery-summary></small></div><div class="gta-metric"><span>Projects underway</span><strong data-gta-active>—</strong><button type="button" class="k gta-link" data-gta-go="projects">View projects</button></div><div class="gta-metric"><span>Planned monthly cost</span><strong data-gta-planned>—</strong><small data-gta-budget-summary></small></div></div>
<div class="gta-situation" role="status" aria-live="polite"><h3 data-gta-status-title></h3><p data-gta-status></p><p data-gta-coverage></p></div>
<div data-gta-blockers><section class="gta-section"><h3 class="gta-section-title">You can address this <span data-gta-count="actionable"></span></h3><div data-gta-group="actionable"></div></section>
<details class="gta-section"><summary>Linked to current construction <span data-gta-count="building"></span></summary><div data-gta-group="building"></div></details>
<details class="gta-section"><summary>Cannot be fixed here <span data-gta-count="external"></span></summary><div data-gta-group="external"></div></details></div>
<details class="gta-section"><summary>Detailed figures</summary><p>Delivery figures describe the latest game report. Construction in progress is counted only when planning future work.</p><div class="gta-kv"><span>Transport still needed</span><b data-gta-missing></b></div><div class="gta-kv"><span>Requested transport</span><b data-gta-requested></b></div><div class="gta-kv"><span>Delivered transport</span><b data-gta-delivered-load></b></div><div class="gta-kv"><span>Undelivered transport</span><b data-gta-unmet-load></b></div><h3 class="gta-land-title">Roads and railways</h3><div class="gta-kv"><span>Transport space in use</span><b data-gta-load></b></div><div class="gta-kv"><span>Available transport space</span><b data-gta-capacity></b></div><div class="gta-kv"><span>Space being added by construction</span><b data-gta-pending></b></div><div class="gta-kv"><span>Space needed for your growth target</span><b data-gta-target-capacity></b></div><div class="gta-kv"><span>Share of space in use</span><b data-gta-utilization></b></div><details><summary>Remaining transport by problem</summary><p>Amounts use the game's transport units. The same delivery can appear under several problems, so these rows should not be added together. Negligible calculation leftovers are omitted.</p><div data-gta-route-details></div></details></details>
<div class="gta-age"><span data-gta-report-age></span><span data-gta-review-age></span></div></section>
<section id="gta-page-settings" role="tabpanel" aria-labelledby="gta-tab-settings" data-gta-page="settings" hidden>
<label class="gta-enable"><span>Build automatically</span><span data-gta-draft-state></span><input class="gta-switch" type="checkbox" data-gta-edit data-gta-enabled aria-describedby="gta-enable-help"></label><p class="gta-help" id="gta-enable-help">Checks each game day, even with this window closed. Turning it off leaves existing projects running. Changes take effect when you save.</p>
<div class="gta-grid"><div><fieldset><legend>What it can build</legend><p class="gta-help">Choose what the mod may build where your country has permission. Selecting a type does not start construction.</p><label class="gta-policy"><span>Road and railway building</span><select class="k-input" data-gta-edit data-gta-land-policy aria-describedby="gta-land-policy-help"><option value="demand">Build when needed</option><option value="expand">Keep expanding</option></select></label><p class="gta-help" id="gta-land-policy-help" data-gta-land-policy-help></p><div class="gta-choice-group"><span>Roads and railways</span><div class="gta-choices"><label><input type="checkbox" data-gta-edit data-gta-kind="road">Roads</label><label><input type="checkbox" data-gta-edit data-gta-kind="motorway">Freeways</label><label><input type="checkbox" data-gta-edit data-gta-kind="rail">Railways</label></div></div><div class="gta-choice-group"><span>Pipelines</span><div class="gta-choices"><label><input type="checkbox" data-gta-edit data-gta-kind="gas_pipeline">Gas pipelines</label><label><input type="checkbox" data-gta-edit data-gta-kind="oil_pipeline">Oil pipelines</label></div></div><div class="gta-choices"><label><input type="checkbox" data-gta-edit data-gta-kind="port">Ports</label></div><p class="gta-help">Ports includes supported oil and gas loading facilities.</p></fieldset></div>
<div><fieldset><legend>Budget and growth</legend><label class="gta-field"><span data-gta-budget-label></span><input class="k-input" type="number" min="0" max="1000000" step="any" data-gta-edit data-gta-budget required aria-describedby="gta-budget-help"></label><p class="gta-help" id="gta-budget-help">Maximum planned monthly cost, shared with transport work you started yourself. The mod may spend less. Actual monthly bills can vary. 0.1 billion = 100 million.</p><label class="gta-field"><span data-gta-reserve-label></span><input class="k-input" type="number" min="0" max="1000000000" step="any" data-gta-edit data-gta-reserve required aria-describedby="gta-reserve-help"></label><p class="gta-help" id="gta-reserve-help">Before approving new work, allow for this amount and unpaid construction costs. Other government spending can still reduce your cash.</p><label class="gta-field"><span>Room for growth (%)</span><input class="k-input" type="number" min="5" max="50" step="any" data-gta-edit data-gta-target required aria-describedby="gta-growth-help"></label><p class="gta-help" id="gta-growth-help" data-gta-growth-help></p></fieldset></div></div>
<details><summary>Spending details</summary><div class="gta-grid"><div><div class="gta-kv"><span>Monthly construction limit</span><b data-gta-cap></b></div><div class="gta-kv"><span>Planned monthly cost</span><b data-gta-committed></b></div><div class="gta-kv"><span>Monthly room for new work</span><b data-gta-available></b></div></div><div><div class="gta-kv"><span>Cash available today</span><b data-gta-cash></b></div><div class="gta-kv"><span>Minimum cash to keep</span><b data-gta-reserve-value></b></div><div class="gta-kv"><span>Unpaid construction costs</span><b data-gta-outstanding></b></div><div class="gta-kv"><span>Cash available for new work</span><b data-gta-cash-available></b></div></div></div><p data-gta-currency-note>These figures use saved settings. B = billion, M = million, K = thousand. The budget includes manually started transport projects. Lowering it never cancels existing work. Your minimum cash is a check before approving projects; it does not set money aside.</p></details></section>
<section id="gta-page-projects" role="tabpanel" aria-labelledby="gta-tab-projects" data-gta-page="projects" hidden><h3>Construction underway</h3><p data-gta-project-count></p><p class="gta-help" data-gta-land-summary></p><div data-gta-projects></div><details class="gta-section"><summary>Recent automatic starts</summary><p data-gta-history></p><div data-gta-history-list></div></details></section>
</div>
<footer class="gta-foot"><div class="gta-footer-actions" data-gta-save-actions hidden><span data-gta-unsaved></span><button type="button" class="k" data-gta-discard>Discard changes</button><button type="button" class="k k-hero gta-primary" data-gta-save>Save settings</button></div><div data-gta-build-actions><div class="gta-footer-actions"><button type="button" class="k" data-gta-refresh>Update report</button><span data-gta-busy></span><button type="button" class="k k-hero gta-primary" data-gta-run>Check and build now</button></div><p data-gta-action-help></p></div><div class="gta-notice" role="status" aria-live="polite" data-gta-notice></div></footer></section>`;
    panel = root.querySelector('.gta-panel'); document.body.appendChild(root);
    root.querySelector('[data-gta-close]').addEventListener('click', close);
    root.addEventListener('click', function (event) { if (event.target === root) close(); });
    root.querySelectorAll('[data-gta-edit]').forEach(function (input) { function changed() { dirty = true; updateActions(); } input.addEventListener('input', changed); input.addEventListener('change', changed); });
    root.querySelector('[data-gta-discard]').addEventListener('click', function () { populate(); render(); notice('Unsaved changes discarded. Saved settings are unchanged.'); });
    root.querySelectorAll('[data-gta-go]').forEach(function (button) { button.addEventListener('click', function () { showTab(button.getAttribute('data-gta-go'), true); }); });
    root.querySelectorAll('[data-gta-tab]').forEach(function (tab, index) {
      tab.addEventListener('click', function () { showTab(tab.getAttribute('data-gta-tab')); });
      tab.addEventListener('keydown', function (event) { var names = ['overview', 'settings', 'projects'], next = event.key === 'ArrowRight' ? (index + 1) % 3 : event.key === 'ArrowLeft' ? (index + 2) % 3 : event.key === 'Home' ? 0 : event.key === 'End' ? 2 : -1; if (next >= 0) { event.preventDefault(); event.stopPropagation(); showTab(names[next], true); } });
    });
    ['save', 'run', 'refresh'].forEach(function (name) { root.querySelector('[data-gta-' + name + ']').addEventListener('click', function () { action(name); }); });
    window.addEventListener('keydown', function (event) {
      if (!opened) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); close(); return; }
      if (event.key !== 'Tab') return;
      var focusable = Array.prototype.slice.call(panel.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),summary,[tabindex="0"]')).filter(function (node) { if (node.tabIndex < 0) return false; for (var parent = node; parent && parent !== panel; parent = parent.parentNode) { if (parent.hidden) return false; if (parent.tagName === 'DETAILS' && !parent.open && node !== parent.querySelector('summary')) return false; } return true; });
      var first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !panel.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    }, true);
  }
  function injectEntry(ui) {
    if (!ui || !ui.transportRoomActive || !ui._tr || ui._tr.tool !== 'build') return;
    var left = document.querySelector('#transportroom-overlay [data-tr-left]'); if (!left || left.querySelector('[data-gta-entry]')) return;
    var box = element('section', 'sh-well'); box.setAttribute('data-gta-entry', '');
    var button = element('button', 'k', 'Transport Autopilot'); button.type = 'button'; button.style.width = '100%'; button.addEventListener('click', open); box.appendChild(button);
    box.appendChild(element('p', 'rr-help', 'Set a monthly budget for land transport, pipelines, and ports.')); left.appendChild(box);
  }
  function install() {
    var quitPrompt = document.getElementById('quit-overlay');
    if (opened && quitPrompt && !quitPrompt.classList.contains('hidden')) close();
    var g = geo(), ui = g && g.ui;
    if (ui && typeof ui._trRenderLeft === 'function' && !ui._trRenderLeft.__gtaWrapped) {
      var original = ui._trRenderLeft;
      var wrapped = function () { var result = original.apply(this, arguments); try { injectEntry(this); } catch (error) { console.warn('[transport-autobuild] entry', error); } return result; };
      wrapped.__gtaWrapped = true; ui._trRenderLeft = wrapped;
    }
    if (ui) injectEntry(ui);
    var entries = window.__geoSearchItems || (window.__geoSearchItems = []);
    if (!entries.some(function (entry) { return entry && entry.id === 'transport-autobuild'; })) entries.push({ id: 'transport-autobuild', label: 'Transport Autopilot', sub: 'Monthly budget for land transport, pipelines, and ports', kind: 'Mod', run: open });
    var pane = document.getElementById('pane-gameplay');
    if (pane && !pane.querySelector('[data-gta-settings]')) {
      var row = element('div', 'set-row'); row.setAttribute('data-gta-settings', '');
      var label = element('div', 'set-label'); label.appendChild(element('span', 'set-name', 'Transport Autopilot')); label.appendChild(element('span', 'set-desc', 'Manage automatic land transport, pipelines, and ports with a monthly budget.')); row.appendChild(label);
      var ctrl = element('div', 'set-ctrl'), button = element('button', 'k', 'Open'); button.type = 'button'; button.addEventListener('click', open); ctrl.appendChild(button); row.appendChild(ctrl); pane.appendChild(row);
    }
  }
  window.__transportAutobuildUI = { open: open, close: close, install: install, version: '1.1.1', settings: {
    read: function (getSession) { return settingsRequest(getSession, 'transport_auto_state'); },
    save: function (getSession, value) { return settingsRequest(getSession, 'transport_auto_config', [JSON.stringify(validateSettings(value))]); }
  } };
  install();
  setInterval(function () { try { install(); } catch (error) { console.warn('[transport-autobuild] interface', error); } }, 1500);
  setInterval(function () {
    if (!opened || busy || dirty) return;
    var current = sim();
    if (!sameCampaign(current)) { loaded = false; state = null; render(); notice('The campaign changed. Close and reopen this panel to continue.', true); return; }
    if (current && current.day !== lastDay) action('refresh');
  }, 5000);
})();

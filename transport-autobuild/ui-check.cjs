'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Node {
  constructor(tag, document) { this.tagName = tag.toUpperCase(); this.document = document; this.children = []; this.attrs = {}; this.style = {}; this.events = {}; this.disabled = false; this.checked = false; this.value = ''; this.hidden = false; this._classes = new Set(); this.isConnected = true; }
  set className(value) { this._classes = new Set(value.split(/\s+/).filter(Boolean)); }
  get className() { return [...this._classes].join(' '); }
  get classList() { return { toggle: (key, enabled) => { if (enabled) this._classes.add(key); else this._classes.delete(key); }, contains: key => this._classes.has(key) }; }
  setAttribute(key, value) { this.attrs[key] = String(value); if (key === 'id') this.id = String(value); if (key === 'class') this.className = value; if (key === 'disabled') this.disabled = true; }
  getAttribute(key) { return this.attrs[key]; }
  appendChild(node) { this.children.push(node); node.parentNode = this; return node; }
  replaceChildren(...nodes) { this.children = []; nodes.forEach(node => this.appendChild(node)); }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  addEventListener(key, listener) { (this.events[key] ||= []).push(listener); }
  fire(key) { if (key === 'click' && this.disabled) return; for (const fn of this.events[key] || []) fn({ target: this }); }
  focus() { this.document.activeElement = this; }
  set innerHTML(html) {
    this.children = []; const stack = [this]; const tags = /<\/?([\w-]+)([^>]*)>/g; let match;
    while ((match = tags.exec(html))) {
      if (match[0][1] === '/') { if (stack.length > 1) stack.pop(); continue; }
      const node = new Node(match[1], this.document); const attrs = /([\w-]+)(?:="([^"]*)")?/g; let attribute;
      while ((attribute = attrs.exec(match[2]))) node.setAttribute(attribute[1], attribute[2] || '');
      stack.at(-1).appendChild(node); if (!['input','br','hr','img','meta','link'].includes(match[1])) stack.push(node);
    }
  }
  matches(selector) {
    const not = selector.match(/:not\(:disabled\)/); if (not && this.disabled) return false; selector = selector.replace(':not(:disabled)', '');
    const id = selector.match(/#([\w-]+)/); if (id && this.id !== id[1]) return false;
    const cls = selector.match(/\.([\w-]+)/); if (cls && !this._classes.has(cls[1])) return false;
    const tag = selector.match(/^[\w-]+/); if (tag && this.tagName.toLowerCase() !== tag[0]) return false;
    const attributes = [...selector.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)];
    return attributes.every(([,key,value]) => key in this.attrs && (value === undefined || this.attrs[key] === value));
  }
  querySelectorAll(selector) { const parts = selector.split(',').map(value => value.trim().split(/\s+/).at(-1)); const result = []; function visit(node) { for (const child of node.children) { if (parts.some(part => child.matches(part))) result.push(child); visit(child); } } visit(this); return result; }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
const document = { activeElement: null, createElement(tag) { return new Node(tag, this); }, querySelector(selector) { return this.body.querySelector(selector); }, getElementById(id) { return this.body.querySelector('#' + id); } };
document.body = new Node('body', document); document.head = new Node('head', document); document.activeElement = document.body;
const overlay = document.createElement('div'); overlay.id = 'transportroom-overlay'; const left = document.createElement('aside'); left.setAttribute('data-tr-left', ''); overlay.appendChild(left); document.body.appendChild(overlay);
const settings = document.createElement('div'); settings.id = 'pane-gameplay'; document.body.appendChild(settings);
let publicState = { config: { enabled:false, budget:1, reserve:1, target:.8, kinds:['road','motorway','rail'] }, status:{message:'Automatic construction is off.'}, demand:{calibrated:true,load:80,capacity:100,pendingCapacity:0,targetCapacity:100,additionalCapacity:.00007,utilization:.8,deliveryRate:1}, budget:{currency:'EUR',monthlyCap:1,monthlyCommitted:.0007,monthlyAvailable:1,cash:5,reserve:1,outstanding:0,cashAvailable:4}, projects:{active:0,automatic:0,items:[]},history:[] };
const calls = [], intervals = [], events = {};
const sim = { playerKey:'840',day:0,status:{sessionEpoch:1},async command(name,args){ calls.push([name,args]); assert.equal(name,'executeDevConsoleCommand'); if(args[0].name==='transport_auto_config'){ assert.ok(args[0].args[0].length<=256); publicState.config=JSON.parse(args[0].args[0]); } return {ok:true,data:publicState}; } };
let originals=0; const ui={transportRoomActive:true,_tr:{tool:'build'},_trRenderLeft(){ originals++; left.replaceChildren(); return 42; }};
const window={__geoTransportLaunch:{enabled:true},__geo:{ui,getSim:()=>sim},addEventListener(type,listener){(events[type]||=[]).push(listener);}};
for (const policy of [undefined, {enabled:false}]) {
  const inactive={__geoTransportLaunch:policy}; let timers=0;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'ui.js'),'utf8'),{window:inactive,document,setInterval(){timers++;}});
  assert.equal(inactive.__transportAutobuildUI,undefined,'native disabled/missing pack never installs construction UI'); assert.equal(timers,0);
}
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'ui.js'),'utf8'), {window,document,Intl,Number,JSON,Object,Array,String,console,setInterval(fn,ms){intervals.push({fn,ms});}});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  assert.equal(ui._trRenderLeft(),42); assert.equal(originals,1); assert.equal(left.querySelectorAll('[data-gta-entry]').length,1);
  window.__transportAutobuildUI.install(); assert.equal(left.querySelectorAll('[data-gta-entry]').length,1); assert.equal(window.__geoSearchItems.length,1); assert.ok(settings.querySelector('[data-gta-settings]'));
  window.__transportAutobuildUI.open(); await tick();
  const root=document.getElementById('gta-dialog'); const el=selector=>root.querySelector(selector);
  const css=document.head.querySelector('#gta-style').textContent;
  for (const token of ['--body','--body-2','--well','--text','--text-2','--mark-you','--font-body','--font-display','--r-plate','--h-ctl']) assert.ok(css.includes('var('+token), 'native design token '+token+' is inherited');
  assert.doesNotMatch(css, /#17212b|#101923|#263746|system-ui/, 'standalone blue palette and system font are removed');
  assert.ok(el('[data-gta-save]').classList.contains('k-hero'), 'save uses the native primary action role');
  for (const field of ['budget','reserve','target']) assert.ok(el('[data-gta-'+field+']').classList.contains('k-input'), 'numeric fields use native input role');
  assert.ok(el('[data-gta-enabled]').classList.contains('gta-switch'), 'enabled setting keeps a real checkbox with native switch styling');
  const body=el('.gta-body'), foot=el('.gta-foot');
  assert.ok(body.contains(el('[data-gta-budget]'))&&body.contains(el('[data-gta-projects]')), 'settings and records share the scrollable dialog body');
  assert.ok(foot.contains(el('[data-gta-refresh]'))&&foot.contains(el('[data-gta-notice]')), 'refresh and feedback stay in the dialog footer');
  assert.equal(el('[data-gta-budget-label]').textContent,'Monthly construction limit (EUR billions)');
  assert.equal(el('[data-gta-reserve-label]').textContent,'Minimum cash to keep (EUR billions)');
  assert.equal(el('[data-gta-cap]').textContent,'EUR 1B');
  assert.equal(el('[data-gta-cash]').textContent,'EUR 5B');
  assert.equal(el('[data-gta-committed]').textContent,'EUR 700K','small cost does not round to zero');
  assert.match(el('[data-gta-coverage]').textContent,/More road or railway space/,'tiny positive need still changes the capacity explanation');
  for (const name of ['overview','settings','projects']) assert.ok(el('[data-gta-tab="'+name+'"]'),name+' tab is mounted');
  assert.equal(el('[data-gta-saved-state]').textContent,'Automatic building: Off');
  assert.equal(el('[data-gta-target]').attrs.min,'5'); assert.equal(el('[data-gta-target]').attrs.max,'50');
  for (const kind of ['gas_pipeline','oil_pipeline','port']) assert.equal(el('[data-gta-kind="'+kind+'"]').checked,false,'existing settings do not authorize new transport types');
  assert.equal(calls.length,1); assert.equal(el('[data-gta-enabled]').checked,false); assert.equal(el('[data-gta-run]').disabled,true); assert.equal(Number(el('[data-gta-target]').value),20);
  intervals.find(x=>x.ms===5000).fn(); await tick(); assert.equal(calls.length,1,'paused campaign does not poll worker');
  el('[data-gta-enabled]').checked=true; el('[data-gta-enabled]').fire('change'); assert.equal(el('[data-gta-run]').disabled,true,'unsaved settings cannot queue'); assert.equal(calls.length,1);
  el('[data-gta-save]').fire('click'); await tick(); assert.equal(calls.length,2); assert.equal(publicState.config.enabled,true); assert.equal(publicState.config.target,.8); assert.deepEqual(publicState.config.kinds,['road','motorway','rail'],'saving original settings never silently enables upgrades'); assert.equal(el('[data-gta-run]').disabled,false);
  el('[data-gta-run]').fire('click'); await tick(); assert.equal(calls[2][1][0].name,'transport_auto_run');
  el('[data-gta-budget]').value=''; el('[data-gta-budget]').fire('input'); el('[data-gta-save]').fire('click'); await tick(); assert.equal(calls.length,3,'empty budget is not submitted'); assert.match(el('[data-gta-notice]').textContent,/Enter a valid budget/);
  el('[data-gta-budget]').value='2'; el('[data-gta-budget]').fire('input');
  for (const unsupported of ['0', '4', '51', '80']) { el('[data-gta-target]').value=unsupported; el('[data-gta-target]').fire('input'); el('[data-gta-save]').fire('click'); await tick(); assert.equal(calls.length,3,'unsupported free capacity buffer is not submitted'); }
  el('[data-gta-target]').value='20'; el('[data-gta-target]').fire('input'); el('[data-gta-save]').fire('click'); await tick(); assert.equal(calls.length,4);
  delete publicState.budget.currency;
  sim.day=1; intervals.find(x=>x.ms===5000).fn(); await tick(); assert.equal(calls.length,5,'new simulation day refreshes open dialog');
  assert.equal(el('[data-gta-cap]').textContent,'treasury currency 1B','missing code never implies USD');
  const escape={key:'Escape',preventDefault(){},stopImmediatePropagation(){}}; events.keydown.forEach(fn=>fn(escape)); assert.equal(root.hidden,true);
  sim.day=2; intervals.find(x=>x.ms===5000).fn(); await tick(); assert.equal(calls.length,5,'closed panel does not poll');
  assert.equal(ui._trRenderLeft(),42); assert.equal(left.querySelectorAll('[data-gta-entry]').length,1);
  window.__transportAutobuildUI.open(); await tick(); const beforeQuit=calls.length;
  const quitPrompt=document.createElement('div'); quitPrompt.id='quit-overlay'; quitPrompt.className='hidden'; document.body.appendChild(quitPrompt);
  window.__transportAutobuildUI.install(); assert.equal(root.hidden,false,'hidden quit prompt leaves panel open');
  quitPrompt.className=''; window.__transportAutobuildUI.install();
  assert.equal(root.hidden,true,'visible native quit prompt closes custom panel');
  assert.equal(quitPrompt.classList.contains('hidden'),false,'native quit prompt is untouched');
  assert.equal(calls.length,beforeQuit,'quit prompt handoff never writes to worker');
  quitPrompt.className='hidden';
  publicState.demand={calibrated:true,load:62.58,capacity:100,pendingCapacity:0,targetCapacity:78.225,additionalCapacity:0,utilization:.6258,deliveryRate:.958703};
  publicState.delivery={rate:.958703,requestedLoad:10,deliveredLoad:9.58703,unmetLoad:.41297,fulfilled:false,blockers:[
    {id:'gas-owned',label:'Gas route',mode:'gas_pipeline',ownerName:'Luxembourg',scope:'owned',unmetLoad:.1,actionable:false,reason:'disabled_type'},
    {id:'italy',label:'Italian gas pipeline',mode:'gas_pipeline',ownerName:'Italy',scope:'foreign',unmetLoad:.48986,actionable:false,reason:'foreign_controlled'},
    {id:'port-queued',label:'Port expansion',mode:'sea',ownerName:'Luxembourg',scope:'owned',unmetLoad:.02,actionable:false,reason:'demand_covered_by_queue'},
    {id:'road-owned',label:'Land connection',mode:'road',ownerName:'Luxembourg',scope:'owned',unmetLoad:.03,actionable:true,reason:'capacity_shortfall'},
    {id:'shared-serial',label:'Gas pipeline — Jordan / Italy',mode:'gas_pipeline',ownerName:'Jordan / Italy',scope:'shared',unmetLoad:.48986,actionable:false,reason:'foreign_serial_capacity'},
    {id:'owned-serial',label:'Domestic gas route',mode:'gas_pipeline',ownerName:'Luxembourg',scope:'owned',unmetLoad:.1,actionable:false,reason:'serial_upgrade_required'},
    {id:'gas-terminal',label:'Gas terminal',mode:'gas_terminal',ownerName:'Luxembourg',scope:'owned',unmetLoad:.02,actionable:true,reason:'expandable'},
    {id:'oil-terminal',label:'Oil terminal',mode:'oil_terminal',ownerName:'Luxembourg',scope:'owned',unmetLoad:.01,actionable:true,reason:'expandable'}]};
  publicState.status.message='Land capacity meets the target; delivery is limited by other routes.';
  window.__transportAutobuildUI.open(); await tick();
  assert.equal(el('[data-gta-delivery]').textContent,'95.9%');
  assert.equal(el('[data-gta-missing]').textContent,'4.1%');
  assert.match(el('[data-gta-delivery-summary]').textContent,/still needs transport/,'partial delivery never claims total coverage');
  assert.match(el('[data-gta-coverage]').textContent,/roads and railways have enough space/);
  const treeText=node=>[node.textContent||'',...node.children.map(treeText)].join(' ');
  const blockers=treeText(el('[data-gta-blockers]'));
  for (const label of ['Allow gas pipelines','controlled by another country','No matching active project','consider an expansion','Italy']) assert.ok(blockers.includes(label),label+' is visible');
  const foreignGroup=treeText(el('[data-gta-group="external"]'));
  assert.match(foreignGroup,/Expanding your section alone would not help/);
  assert.match(foreignGroup,/Several sections need expanding together/);
  const actionableGroup=treeText(el('[data-gta-group="actionable"]'));
  assert.match(actionableGroup,/Allow ports in Settings/,'terminal upgrades require Ports opt-in');
  assert.equal(root.querySelectorAll('[data-gta-kind]').length,6);
  assert.equal(el('[data-gta-land-policy]').value,'demand','legacy saves keep demand-based land construction');
  const beforeOptIn=calls.length;
  for (const kind of ['gas_pipeline','oil_pipeline','port']) { const field=el('[data-gta-kind="'+kind+'"]'); assert.equal(field.checked,false); field.checked=true; field.fire('change'); }
  assert.equal(calls.length,beforeOptIn,'new types require explicit Save'); assert.equal(el('[data-gta-run]').disabled,true);
  el('[data-gta-save]').fire('click'); await tick();
  assert.deepEqual(publicState.config.kinds,['road','motorway','rail','gas_pipeline','oil_pipeline','port']);
  el('[data-gta-refresh]').fire('click'); await tick();
  for (const kind of publicState.config.kinds) assert.equal(el('[data-gta-kind="'+kind+'"]').checked,true,'saved selection persists across refresh');
  const beforePolicy=calls.length;
  el('[data-gta-land-policy]').value='expand'; el('[data-gta-land-policy]').fire('change');
  assert.equal(calls.length,beforePolicy,'choosing an expansion mode does not save or build');
  assert.equal(publicState.config.landPolicy,'demand','draft policy does not alter saved behavior');
  assert.equal(el('[data-gta-run]').disabled,true);
  assert.match(el('[data-gta-land-policy-help]').textContent,/even when current transport needs are met/);
  el('[data-gta-discard]').fire('click'); assert.equal(el('[data-gta-land-policy]').value,'demand');
  el('[data-gta-land-policy]').value='invalid'; el('[data-gta-land-policy]').fire('change');
  el('[data-gta-save]').fire('click'); await tick(); assert.equal(calls.length,beforePolicy,'invalid land policy is never submitted');
  el('[data-gta-land-policy]').value='expand'; el('[data-gta-land-policy]').fire('change');
  el('[data-gta-save]').fire('click'); await tick(); assert.equal(publicState.config.landPolicy,'expand');
  assert.equal(calls.at(-1)[1][0].name,'transport_auto_config','saving expansion does not immediately run construction');
  assert.match(el('[data-gta-coverage]').textContent,/Keep expanding/);
  assert.match(el('[data-gta-land-summary]').textContent,/Keep expanding/);
  el('[data-gta-refresh]').fire('click'); await tick(); assert.equal(el('[data-gta-land-policy]').value,'expand');
  window.__transportAutobuildUI.close();
  const shared = window.__transportAutobuildUI.settings;
  const closedCalls = calls.length;
  assert.equal((await shared.read(() => sim)).config.enabled, true, 'manager can read settings without opening the dialog');
  assert.equal(root.hidden, true, 'shared settings do not open a second modal');
  const saved = await shared.save(() => sim, {enabled:false,budget:.2,reserve:.1,target:.75,kinds:['road','port']});
  assert.equal(saved.config.enabled,false); assert.equal(saved.config.budget,.2); assert.deepEqual(publicState.config.kinds,['road','port']);
  assert.equal(calls.length,closedCalls+2); assert.equal(calls.at(-1)[1][0].name,'transport_auto_config');
  for (const invalid of [{...saved.config,landPolicy:'always'},{...saved.config,landPolicy:null},{...saved.config,budget:1e6+1},{...saved.config,target:.96},{...saved.config,kinds:['unknown']},{...saved.config,enabled:true,kinds:[]}]) {
    assert.throws(() => shared.save(() => sim, invalid), /invalid_config/);
  }
  assert.equal(calls.length,closedCalls+2,'invalid shared settings never reach the worker');
  await assert.rejects(shared.read(() => null), /no_campaign/);
  let sharedSession=sim; const realCommand=sim.command;
  sim.command=async()=>{sharedSession=null;return {ok:true,data:publicState};};
  await assert.rejects(shared.read(() => sharedSession), /campaign_changed/);
  sim.command=async()=>{sim.status.sessionEpoch++;return {ok:true,data:publicState};};
  await assert.rejects(shared.read(() => sim), /campaign_changed/);
  sim.command=async()=>{sim.playerKey='442';return {ok:true,data:publicState};};
  await assert.rejects(shared.read(() => sim), /campaign_changed/);
  sim.command=async()=>({ok:false,reason:'internal',data:publicState});
  await assert.rejects(shared.read(() => sim), /internal/);
  sim.command=realCommand;
  console.log('PASS: native UI mounting/discovery, three tabs, saved-state header, controls and shared settings bridge, default off, save/run gating, compact config, input validation, paused/closed polling, native rerender and quit handoff, Escape, currency fallback, tiny positive capacity, grouped blocker actions, six explicit type permissions and serial route limitations.');
})().catch(error=>{console.error(error);process.exitCode=1;});

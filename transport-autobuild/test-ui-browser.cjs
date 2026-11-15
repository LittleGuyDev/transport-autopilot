'use strict';
// Isolated browser acceptance: no game connection and no installed files are changed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const playwrightCandidates = [process.env.PLAYWRIGHT_MODULE, 'playwright'].filter(Boolean);
let chromium;
for (const candidate of playwrightCandidates) { try { chromium = require(candidate).chromium; break; } catch {} }
if (!chromium) throw Error('Playwright is required (set PLAYWRIGHT_MODULE to its module path).');
const browserCandidates = [process.env.CHROMIUM_PATH].filter(Boolean);
const executablePath = browserCandidates.find(candidate => fs.existsSync(candidate));
const output = process.env.UI_ACCEPTANCE_OUTPUT || path.join(os.tmpdir(), 'geoplanetical-transport-ui-acceptance');
const fixture = {
 config:{enabled:false,budget:1,reserve:1,target:.8,kinds:['road','motorway','rail']},
 status:{message:'Automatic construction is off.'},
 demand:{calibrated:true,load:62.58,capacity:100,pendingCapacity:2,targetCapacity:78.225,additionalCapacity:0,utilization:.6258,deliveryRate:.958703},
 delivery:{ready:true,rate:.958703,requestedLoad:10,deliveredLoad:9.58703,unmetLoad:.41297,fulfilled:false,blockers:[
  {id:'disabled-gas',label:'Domestic gas pipeline',mode:'gas_pipeline',ownerName:'Luxembourg',scope:'owned',unmetLoad:.1,actionable:false,reason:'disabled_type',projectIds:[],sharedProjectIds:[]},
  {id:'foreign',label:'Italian gas pipeline',mode:'gas_pipeline',ownerName:'Italy',scope:'foreign',unmetLoad:.48986,actionable:false,reason:'foreign_controlled',projectIds:[],sharedProjectIds:[]},
  {id:'queued',label:'Port expansion',mode:'sea',ownerName:'Luxembourg',scope:'owned',unmetLoad:.02,actionable:false,reason:'demand_covered_by_queue',projectIds:['p-direct'],sharedProjectIds:[],queueEvidence:'direct'},
  {id:'expandable',label:'Land connection',mode:'road',ownerName:'Luxembourg',scope:'owned',unmetLoad:.03,actionable:true,reason:'capacity_shortfall',projectIds:[],sharedProjectIds:[]},
  {id:'serial-foreign',label:'Gas pipeline — Jordan / Italy',mode:'gas_pipeline',ownerName:'Jordan / Italy',scope:'shared',unmetLoad:.48986,actionable:false,reason:'foreign_serial_capacity',projectIds:[],sharedProjectIds:[]},
  {id:'shared-queue',label:'Shared-network oil pipeline',mode:'oil_pipeline',ownerName:'Luxembourg',scope:'owned',unmetLoad:.004,actionable:false,reason:'queued_shared_route',projectIds:[],sharedProjectIds:['p-shared'],queueEvidence:'shared'},
  {id:'sufficient',label:'Target capacity sufficient route',mode:'road',ownerName:'Luxembourg',scope:'owned',unmetLoad:.00007,actionable:false,reason:'target_capacity_sufficient',projectIds:[],sharedProjectIds:[]}
 ]},
 budget:{currency:'EUR',monthlyCap:1,monthlyCommitted:.0007,monthlyAvailable:.9993,cash:5,reserve:1,outstanding:.00002,cashAvailable:4},
 projects:{active:2,automatic:1,items:[
  {id:'p-direct',label:'Harbor expansion',kind:'port',automatic:true,provinceName:'Luxembourg',km:.00007,monthly:.0007,monthsLeft:12},
  {id:'p-shared',label:'Shared-network extension',kind:'oil_pipeline',automatic:false,provinceName:'Central district',km:12,monthly:.003,monthsLeft:4}
 ]},
 history:Array.from({length:32},(_,i)=>({day:i,kind:'road',cost:.0007})),
 historyInfo:{limit:32,retained:32},review:{day:3220,stale:false},lastRun:null
};
async function startPage(browser, viewport={width:1440,height:1000}) {
 const page = await browser.newPage({viewport});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.setContent('<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;background:#17191c;color:#eee;font:14px Arial,sans-serif}</style></head><body><div id="transportroom-overlay"><aside data-tr-left></aside></div><div id="pane-gameplay"></div></body></html>');
 await page.evaluate(initial=>{
  window.fixtureState=initial;window.fixtureCalls=[];window.fixtureRunResult=null;
  window.fixtureSim={playerKey:'442',day:3220,status:{sessionEpoch:1},async command(name,args){
   if(name!=='executeDevConsoleCommand')throw Error('Unexpected command');
   const request=args[0];window.fixtureCalls.push(request);
   if(request.name==='transport_auto_config')window.fixtureState.config=JSON.parse(request.args[0]);
   if(request.name==='transport_auto_run'&&window.fixtureRunResult)Object.assign(window.fixtureState,window.fixtureRunResult);
   if(!['transport_auto_state','transport_auto_config','transport_auto_run'].includes(request.name))throw Error('Unexpected fixture action');
   return {ok:true,data:JSON.parse(JSON.stringify(window.fixtureState))};
  }};
  window.__geoTransportLaunch={enabled:true};
  window.__geo={ui:{transportRoomActive:true,_tr:{tool:'build'},_trRenderLeft(){document.querySelector('[data-tr-left]').replaceChildren();}},getSim:()=>window.fixtureSim};
 },structuredClone(fixture));
 await page.addScriptTag({content:fs.readFileSync(path.join(__dirname,'ui.js'),'utf8')});
 await page.evaluate(()=>{window.__geo.ui._trRenderLeft();window.__transportAutobuildUI.open();});
 await page.locator('[data-gta-run]').waitFor();
 await page.waitForFunction(()=>window.fixtureCalls.length===1&&!document.querySelector('[data-gta-refresh]').disabled);
 return {page,errors};
}
async function settle(page){await page.waitForFunction(()=>!document.querySelector('[data-gta-refresh]').disabled);}
async function commands(page){return page.evaluate(()=>window.fixtureCalls.map(call=>call.name));}
async function tab(page,name){await page.locator('[data-gta-tab="'+name+'"]').click();await page.locator('[data-gta-page="'+name+'"]').waitFor({state:'visible'});}
async function assertFits(page,label){
 const measurements=await page.evaluate(()=>{
  const panel=document.querySelector('.gta-panel');
  const visible=[panel,...panel.querySelectorAll('*')].filter(node=>node.getClientRects().length&&getComputedStyle(node).display!=='none');
  const overflowing=visible.filter(node=>node.clientWidth>0&&node.scrollWidth>node.clientWidth+2&&getComputedStyle(node).overflowX!=='hidden').map(node=>({tag:node.tagName,classes:node.className,client:node.clientWidth,scroll:node.scrollWidth}));
  return {viewport:innerWidth,document:document.documentElement.scrollWidth,panel:panel.getBoundingClientRect().toJSON(),overflowing};
 });
 assert.ok(measurements.document<=measurements.viewport,label+': no horizontal document overflow');
 assert.ok(measurements.panel.left>=0&&measurements.panel.right<=measurements.viewport+1,label+': panel fits viewport');
 assert.deepEqual(measurements.overflowing,[],label+': no horizontal content overflow');
 return measurements;
}
(async()=>{
 fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({headless:true,...(executablePath?{executablePath}:{})});
 const report={fixture:'isolated public-state fixture; no live game or installed files',output,checks:[],screenshots:[],layouts:[]};
 try {
  const {page,errors}=await startPage(browser);
  // Final interaction/semantic cases are intentionally driven through the real DOM.
  for(const name of ['overview','settings','projects'])assert.equal(await page.locator('[data-gta-tab="'+name+'"]').count(),1,'one '+name+' tab');
  assert.equal(await page.locator('[data-gta-page="overview"]').isVisible(),true);
  assert.equal(await page.locator('[data-gta-run]').isDisabled(),true,'saved disabled state prevents runs');
  assert.equal(await page.locator('[data-gta-kind]').count(),6,'all supported transport permissions remain explicit');
  report.checks.push('three navigation tabs; disabled saved state');
  await tab(page,'settings');
  assert.equal(await page.locator('[data-gta-land-policy]').inputValue(),'demand','legacy save defaults to demand-based construction');
  await page.locator('[data-gta-land-policy]').selectOption('expand');
  await page.locator('[data-gta-enabled]').check();
  assert.match(await page.locator('[data-gta-saved-state]').innerText(),/Off/,'unsaved enable does not change saved-state badge');
  assert.equal(await page.locator('[data-gta-run]').isDisabled(),true,'unsaved enabling cannot run');
  assert.deepEqual(await commands(page),['transport_auto_state'],'editing settings does not call the worker');
  await page.locator('[data-gta-budget]').fill('2.5');
  await page.locator('[data-gta-close]').click();
  await page.evaluate(()=>window.__transportAutobuildUI.open());await settle(page);
  await tab(page,'settings');
  assert.equal(await page.locator('[data-gta-budget]').inputValue(),'2.5','same-campaign close/reopen retains draft');
  assert.equal(await page.locator('[data-gta-enabled]').isChecked(),true,'same-campaign close/reopen retains enabled draft');
  assert.equal(await page.locator('[data-gta-land-policy]').inputValue(),'expand','same-campaign close/reopen retains expansion draft');
  assert.equal(await page.locator('[data-gta-run]').isDisabled(),true,'reopened unsaved draft remains unable to run');
  await tab(page,'overview');await page.locator('[data-gta-refresh]').click();await settle(page);await tab(page,'settings');
  assert.equal(await page.locator('[data-gta-budget]').inputValue(),'2.5','refresh retains draft');
  assert.equal((await commands(page)).filter(name=>name==='transport_auto_run').length,0,'refresh never starts construction');
  await page.locator('[data-gta-save]').click();await settle(page);
  assert.equal(await page.evaluate(()=>window.fixtureState.config.budget),2.5);
  assert.equal(await page.evaluate(()=>window.fixtureState.config.enabled),true);
  assert.equal(await page.evaluate(()=>window.fixtureState.config.landPolicy),'expand','expansion is saved only with Save settings');
  assert.match(await page.locator('[data-gta-coverage]').textContent(),/Keep expanding/);
  report.checks.push('legacy demand default; expansion draft persists; explicit save controls expansion');
  assert.equal(await page.locator('[data-gta-run]').isDisabled(),false,'saved enabling allows explicit run');
  assert.deepEqual(await page.evaluate(()=>window.fixtureState.config.kinds),['road','motorway','rail'],'saving does not silently opt into new transport kinds');
  report.checks.push('unsaved vs saved enable boundary; no implicit builds; draft retained on reopen/refresh');
  await page.locator('[data-gta-budget]').fill('9');
  await page.locator('[data-gta-close]').click();
  await page.evaluate(()=>{window.fixtureSim={...window.fixtureSim,playerKey:'400',status:{sessionEpoch:2}};window.fixtureState.config.budget=.75;window.fixtureState.config.target=.805;window.__transportAutobuildUI.open();});await settle(page);
  await tab(page,'settings');
  assert.equal(await page.locator('[data-gta-budget]').inputValue(),'0.75','new campaign clears previous draft');
  assert.equal(Number(await page.locator('[data-gta-target]').inputValue()),19.5,'fractional saved growth target displays without integer rounding');
  await page.locator('[data-gta-budget]').fill('0.95');await page.locator('[data-gta-save]').click();await settle(page);
  assert.equal(await page.evaluate(()=>window.fixtureState.config.target),.805,'budget-only save preserves untouched fractional growth target');
  assert.equal(await page.evaluate(()=>window.fixtureState.config.landPolicy),'expand','budget-only save preserves expansion mode');
  report.checks.push('campaign change clears draft; budget-only save preserves fractional target');
  await tab(page,'overview');
  assert.match(await page.locator('[data-gta-saved-state]').innerText(),/On/,'header reflects saved enabled state');
  assert.equal(await page.locator('[data-gta-delivery]').innerText(),'95.9%','headline delivery uses readable precision');
  assert.equal(await page.locator('[data-gta-count="actionable"]').innerText(),'2');
  assert.equal(await page.locator('[data-gta-count="building"]').innerText(),'2');
  assert.equal(await page.locator('[data-gta-count="external"]').innerText(),'3');
  await page.locator('details').evaluateAll(nodes=>nodes.forEach(node=>node.open=true));
  const sufficient=page.locator('[data-gta-group="external"] article').filter({hasText:'Target capacity sufficient route'});
  assert.match(await sufficient.innerText(),/enough space/);assert.doesNotMatch(await sufficient.innerText(),/Construction is underway|queued/);
  assert.equal(await sufficient.locator('button').count(),0,'capacity sufficient is not evidence of a queued project');
  assert.match(await page.locator('[data-gta-route-details]').innerText(),/<0\.001/,'tiny nonzero demand stays nonzero in details');
  const related=page.locator('[data-gta-show-projects]');assert.equal(await related.count(),2,'only matched direct/shared queue evidence gets project links');
  await related.first().click();assert.equal(await page.locator('[data-gta-page="projects"]').isVisible(),true,'related project links open Projects');
  assert.match(await page.locator('[data-gta-projects]').innerText(),/EUR 700K/,'small money uses human scale');
  assert.match(await page.locator('[data-gta-projects]').innerText(),/<0\.001 km/,'small length does not become zero');
  assert.match(await page.locator('[data-gta-history]').innerText(),/32.*up to 32 retained.*do not confirm completion/);
  report.checks.push('grouped blockers; direct/shared evidence links; sufficient capacity not queued; readable nonzero details; bounded history');
  await tab(page,'overview');
  await page.locator('[data-gta-tab="overview"]').focus();await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('[data-gta-page="settings"]').isVisible(),true,'arrow key navigates tabs');
  assert.equal(await page.locator('[data-gta-tab="settings"]').getAttribute('aria-selected'),'true');
  await page.keyboard.press('Home');
  assert.equal(await page.locator('[data-gta-page="overview"]').isVisible(),true,'Home returns to first tab');
  await page.evaluate(()=>{window.fixtureRunResult={lastRun:{day:3220,started:2,cost:.003,monthlyCost:.0007,ok:true}};});
  await page.locator('[data-gta-run]').click();await settle(page);
  assert.match(await page.locator('[data-gta-notice]').innerText(),/Started 2 projects.*EUR 700K/,'run feedback gives exact starts and monthly amount');
  await page.evaluate(()=>{window.fixtureRunResult={lastRun:{day:3220,started:0,cost:0,monthlyCost:0,ok:true},status:{code:'budget'}};});
  await page.locator('[data-gta-run]').click();await settle(page);
  assert.match(await page.locator('[data-gta-notice]').innerText(),/No projects started.*monthly budget/,'no-start feedback gives current blocker');
  await page.evaluate(()=>{window.fixtureState.delivery.ready=false;window.fixtureState.delivery.rate=1;window.fixtureState.delivery.fulfilled=true;});
  await page.locator('[data-gta-refresh]').click();await settle(page);
  assert.equal(await page.locator('[data-gta-run]').isDisabled(),true,'unready report disables new construction');
  assert.equal(await page.locator('[data-gta-delivery]').innerText(),'—','unready report cannot display success percentage');
  assert.match(await page.locator('[data-gta-status-title]').innerText(),/Waiting for a transport report/);
  assert.doesNotMatch(await page.locator('[data-gta-delivery-summary]').innerText(),/met$|fulfilled|100%/);
  report.checks.push('specific start/no-start feedback; unready report never claims successful delivery');
  await page.evaluate(()=>{window.fixtureState.delivery.ready=true;window.fixtureState.delivery.requestedLoad=0;window.fixtureState.delivery.blockers=[];window.fixtureState.status={};window.fixtureState.review={day:3219,stale:true};});
  await page.locator('[data-gta-refresh]').click();await settle(page);
  assert.equal(await page.locator('[data-gta-delivery]').innerText(),'—','zero requested load is not rendered as 100% success');
  assert.match(await page.locator('[data-gta-delivery-summary]').innerText(),/No transport requested/);
  assert.match(await page.locator('[data-gta-review-age]').innerText(),/3,219.*changed since then/,'outdated review is labeled');
  report.checks.push('accessible keyboard tabs; no-demand state; stale review label');
  // Restore a representative state for visual review at every page and text size.
  await page.evaluate(initial=>{window.fixtureState=initial;window.fixtureState.config.landPolicy='expand';window.fixtureState.config.enabled=true;window.fixtureState.currentStatus={code:'expanding'};},structuredClone(fixture));
  await page.locator('[data-gta-refresh]').click();await settle(page);
  for(const size of [{name:'wide',width:1440,height:1000,large:false},{name:'narrow',width:390,height:844,large:false},{name:'large-text',width:680,height:1000,large:true}]){
   await page.setViewportSize({width:size.width,height:size.height});
   if(size.large)await page.addStyleTag({content:'#gta-dialog{--fs-body:18px;--fs-read:20px;--fs-lead:24px;--fs-hero:32px;--fs-plate:15px;--lh-body:26px;--lh-read:30px;--lh-lead:36px;--lh-hero:42px}'});
   for(const name of ['overview','settings','projects']){
    await tab(page,name);await page.locator('[data-gta-page="'+name+'"] details').evaluateAll(nodes=>nodes.forEach(node=>node.open=true));
    report.layouts.push({name:size.name+'-'+name,...await assertFits(page,size.name+' '+name)});
    const file=size.name+'-'+name+'.png';await page.screenshot({path:path.join(output,file)});report.screenshots.push(file);
   }
  }
  await page.keyboard.press('Escape');assert.equal(await page.locator('#gta-dialog').isVisible(),false,'Escape closes dialog');
  report.checks.push('wide/narrow/large text layouts on all three pages; Escape');
  assert.deepEqual(errors,[],'no browser runtime errors');
  await page.close();
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
  console.log('PASS browser UI acceptance: '+report.checks.join('; '));
  console.log('Artifacts: '+output);
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

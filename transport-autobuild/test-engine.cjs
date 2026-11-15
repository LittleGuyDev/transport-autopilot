#!/usr/bin/env node
'use strict';
// Real-engine acceptance harness. Only temporary runtimes and explicit evidence files are written.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const defaults = path.join(os.homedir(),'Library/Application Support/Steam/steamapps/common/Geoplanetical/Geoplanetical.app/Contents/Resources/app.asar.unpacked');
const args = process.argv.slice(2);
const option = (key, fallback) => { const at=args.indexOf(key); return at < 0 ? fallback : args[at+1]; };
const assetRoot = path.resolve(option('--assets',process.env.GEO_ASSET_ROOT || path.join(defaults,'renderer')));
const platformFile = path.resolve(option('--platform',process.env.GEO_PLATFORM_FILE || path.join(defaults,'electron/simulation-node-platform.js')));
const bundle = path.resolve(option('--bundle',process.env.GEO_TEST_BUNDLE || path.join(assetRoot,'simulation-worker.bundle.js')));
const output = option('--output',null);
const probeOnly = args.includes('--probe');
const activation = option('--activation','enabled');
assert.ok(['enabled','disabled','missing'].includes(activation),'--activation must be enabled, disabled, or missing');
const country = option('--country','442');
const timeout = Number(option('--timeout-ms','420000'));
const {createNodeSimulationWorker}=require(platformFile);
const temp = fs.mkdtempSync(path.join(os.tmpdir(),'geotransport-engine-'));
const bundleSource=fs.readFileSync(bundle,'utf8');
const bundleSha256=require('node:crypto').createHash('sha256').update(bundleSource).digest('hex');
const evidence={bundle,bundleSha256,assetRoot,country,probeOnly,activation,started:new Date().toISOString(),assertions:[],observations:{}};
let worker,epoch=0,requestId=0;
const pending=new Map();
function patchOnce(source,anchor,replacement) {
  assert.equal(source.split(anchor).length-1,1,`unique test probe anchor: ${anchor}`);
  return source.replace(anchor,replacement);
}
// This probe is injected into the disposable test worker, never the shipped bundle.
function instrument(source) {
  const method=`tab_test_probe(e,t,a){let k=String(t[0]||'summary'),c=e.player,n=e.transport.network,s=e.transportSnapshot(),owned=n.assets.filter(x=>x.actorId===c.key&&x.assetClass==='surface_province');if(k==='force-demand'){const view=YR(e),slotIndex=view.actors.get(String(c.key))?.slots?.land,slot=view.slots[slotIndex],access=X7e();let fixture;globalThis.__tabOriginalTransport={lastSolve:e.transport.lastSolve,lastDemands:e.transport.lastDemands,statsByActor:e.transport.statsByActor};const fixtureDemands=e.transport.lastDemands?.length?e.transport.lastDemands:[...e.countries.keys()].filter(key=>String(key)!==String(c.key)).map(key=>({id:"acceptance:land:"+c.key+":"+key,goodId:"beverages",actorId:String(c.key),exporterActorId:String(c.key),importerActorId:String(key),originAssetId:"national:"+c.key,destinationAssetId:"national:"+key,quantity:1,commercialQuantity:1,baseValue:1,allowedModes:["land"],profile:{network:"freight",form:"food_cargo",modes:["land"],requirements:["food_grade"],priority:50,usdPerTonne:1200,bodyAvailability:["earth"],interbodyEligible:false}}));for(const original of fixtureDemands){const plan=bPe(e,view,original,access);if(!plan?.available||!plan.slots.includes(slotIndex)||!(Number(original.quantity)>0))continue;const trial=Lat(e,[original],{compactResults:!0}),load=Number(trial.resultVectors?.requestedLoad?.[0]);if(!(load>0))continue;const ratio=slot.capacity*.98/load,demand={...original,quantity:Number(original.quantity)*ratio};if(Number.isFinite(Number(original.baseValue)))demand.baseValue=Number(original.baseValue)*ratio;const solve=Lat(e,[demand],{compactResults:!0});e.transport.lastDemands=[demand];e.transport.lastSolve=solve;e.transport.statsByActor=cMe(e,solve,[demand]).statsByActor;fixture={kind:'native scaled own-land demand solve on disposable test demand',demandId:original.id,originalQuantity:original.quantity,fixtureQuantity:demand.quantity,slotId:slot.id,capacity:slot.capacity,requestedLoad:solve.resultVectors.requestedLoad[0]};break;}return Fe(a,!!fixture,'tab.test',{changed:!!fixture,fixture,diagnostic:fixture?undefined:{demandCount:e.transport.lastDemands?.length,slotIndex,slot,samples:e.transport.lastDemands?.slice(0,2)}})}if(k==='restore-demand'){Object.assign(e.transport,globalThis.__tabOriginalTransport);delete globalThis.__tabOriginalTransport;return Fe(a,!0,'tab.test',{changed:!0})}let projects=(e.construction||[]).filter(x=>x.owner===c.key&&x.kind==='transport').map(x=>({id:x.id,owner:x.owner,kind:x.kind,projectType:x.projectType,targetId:x.targetId,phase:x.phase,totalCost:x.totalCost,paid:x.paid,monthsLeft:x.monthsLeft,monthsTotal:x.monthsTotal,plan:x.transportPlan}));let v=k==='assets'?{calibration:n.calibration,assetCount:owned.length,assets:owned.slice(0,6).map(x=>({raw:x,effectiveLand:ms('asset',x,'land')}))}:k==='projects'?{projects}:k==='quote'?e.transportProjectQuote(c,{projectType:'surface_program',terrainId:'plain',provinceKey:s.surface.provinces[0].provinceKey,surfaceKind:'road',lengthKm:1}):{day:e.day,countryKey:c.key,treasuryBn:ya(e,c),treasuryProjection:c.treasury,countryCurrency:c.ccyCode,currency:c.ccy,cashAccount:e.finance.accounts[e.finance.sovereigns[c.key].cashAccountId],surface:s.surface,requestedLoad:s.requestedLoad,deliveredLoad:s.deliveredLoad,pressure:s.pressure,bottleneck:s.bottleneck,solveDay:s.solveDay,automationReviewDay:c._transportAutobuild?.lastReviewDay??null,automationRecord:JSON.stringify(c._transportAutobuild)||null,projectCount:projects.length};return Fe(a,!0,'tab.test',{changed:!1,...v})},`;
  source=patchOnce(source,'var CLo=Object.freeze({','var CLo=Object.freeze({'+method);
  return patchOnce(source,'Za("money","economy","console.help.money",','Sg("tab_test_probe","general","","",[],{mutation:"none"}),Za("money","economy","console.help.money",');
}
function rpc(command,payload={}) {
  const id=++requestId;
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{pending.delete(id);reject(new Error(`Timeout ${command}`));},timeout);
    pending.set(id,{resolve,reject,timer});
    worker.postMessage({protocolVersion:2,type:'request',requestId:id,sessionEpoch:epoch,command,payload});
  });
}
async function consoleCommand(name,args=[]) {
  const response=await rpc('domain.call',{methodId:'executeDevConsoleCommand',args:[{name,args:args.map(String)}]});
  const result=response.result;
  assert.ok(result&&result.ok,`${name}: ${JSON.stringify(result)}`);
  return result.data;
}
function passed(message) { evidence.assertions.push(message); process.stdout.write(`PASS ${message}\n`); }
function configOf(value) { return value.config || value.state?.config; }
async function state() { const v=await consoleCommand('transport_auto_state'); return v.state || v; }
async function probe(kind) { return consoleCommand('tab_test_probe',[kind]); }
function startWorker() {
  epoch=0;
  worker=createNodeSimulationWorker('simulation-worker.bundle.js',temp);
  worker.on('message',m=>{
    if(m.type!=='response')return;
    const p=pending.get(m.requestId); if(!p)return;
    pending.delete(m.requestId);clearTimeout(p.timer);epoch=m.sessionEpoch||epoch;
    m.ok?p.resolve(m.payload):p.reject(new Error(JSON.stringify(m.error)));
  });
  const failAll=error=>{for(const p of pending.values()){clearTimeout(p.timer);p.reject(error);}pending.clear();};
  worker.on('error',failAll);worker.on('exit',code=>failAll(new Error(`Worker exited ${code}`)));
}
async function main() {
  for(const entry of fs.readdirSync(assetRoot)) {
    if(entry==='simulation-worker.bundle.js')continue;
    fs.symlinkSync(path.join(assetRoot,entry),path.join(temp,entry));
  }
  fs.writeFileSync(path.join(temp,'simulation-worker.bundle.js'),instrument(bundleSource));
  startWorker();
  process.stdout.write(`Booting isolated fresh ${country} seed 17\n`);
  await rpc('session.boot',{playerKey:country,seed:17,...activation==='missing'?{}:{transportAutopilotEnabled:activation==='enabled'}});
  passed('fresh engine boot');
  evidence.observations.initial=await probe('summary');
  evidence.observations.assets=await probe('assets');
  evidence.observations.quote=await probe('quote');
  if(probeOnly)return;
  if(activation!=='enabled') {
    const before=await probe('summary');
    for(const name of ['transport_auto_state','transport_auto_config','transport_auto_run']) {
      const response=await rpc('domain.call',{methodId:'executeDevConsoleCommand',args:[{name,args:name==='transport_auto_config'?[JSON.stringify({enabled:true})]:[]}]});
      assert.equal(response.result.ok,false,name+' is disabled'); assert.match(JSON.stringify(response.result),/disabled in this session/);
    }
    const after=await probe('summary');assert.equal(after.automationRecord,before.automationRecord);
    await rpc('clock.advanceDays',{days:1});const day=await probe('summary');
    assert.equal(day.automationRecord,before.automationRecord);assert.equal(day.automationReviewDay,null);assert.equal(day.projectCount,before.projectCount);
    passed('disabled or missing launch policy blocks all commands and the real daily hook without creating a campaign record');
    return;
  }
  const initial=await state(); evidence.observations.initialState=initial;
  assert.equal(configOf(initial).enabled,false);passed('automation defaults disabled');
  const config={enabled:true,landPolicy:'demand',budget:0.02,reserve:0.1,target:0.8,kinds:['road','motorway','rail']};
  assert.ok(JSON.stringify(config).length<256);
  await consoleCommand('transport_auto_config',[JSON.stringify(config)]);
  assert.deepEqual(configOf(await state()),config);passed('configuration reaches actual simulation');
  evidence.observations.demandFixture=await probe('force-demand');
  const pre=await probe('summary');
  const run=await consoleCommand('transport_auto_run');evidence.observations.run=run;
  const projects1=await probe('projects');evidence.observations.projectsAfterRun=projects1;
  const commitments=projects1.projects.filter(p=>p.projectType==='surface_program').reduce((a,p)=>a+Math.max(0,p.totalCost-p.paid),0);
  const monthly=projects1.projects.filter(p=>p.projectType==='surface_program').reduce((a,p)=>a+p.totalCost/p.monthsTotal,0);
  assert.ok(monthly<=config.budget+1e-7);
  assert.ok(commitments<=Math.max(0,pre.treasuryBn-config.reserve)+1e-7);
  assert.ok(projects1.projects.length>0,'pressure fixture must produce native construction');
  passed('actual queued project commitments respect monthly cap and full cash reserve');
  for(const p of projects1.projects) {
    assert.equal(p.owner,country);assert.equal(p.kind,'transport');assert.ok(Number.isFinite(p.totalCost)&&p.totalCost>0);
    assert.ok(Number.isFinite(p.monthsTotal)&&p.monthsTotal>0);assert.ok(p.plan);
  }
  await consoleCommand('transport_auto_run');
  const projects2=await probe('projects');
  assert.equal(projects2.projects.length,projects1.projects.length);passed('repeat run creates no duplicate projects');
  await probe('restore-demand');
  const beforeAdvance=await state();
  await rpc('clock.advanceDays',{days:1});
  const afterAdvance=await state();const afterDay=await probe('summary');
  assert.equal(afterDay.day,pre.day+1);
  assert.equal(afterDay.automationReviewDay,afterDay.day,'daily hook reviewed new engine day');evidence.observations.beforeAdvance=beforeAdvance;evidence.observations.afterAdvance=afterAdvance;
  passed('real daily engine cycle advances with automation enabled');
  const savedState=await state(),savedProjects=await probe('projects');
  const snapshot=await rpc('save.snapshot');
  evidence.observations.snapshotParts=snapshot.artifact.parts.map(p=>({id:p.id,compression:p.compression,rawBytes:p.rawBytes,storedBytes:p.bytes.byteLength}));
  // Host capture may use zstd on new Node releases; the load protocol accepts deflate/gzip/identity.
  const loadParts=snapshot.artifact.parts.map(p=>{
    if(p.compression!=='zstd')return p;
    const raw=require('node:zlib').zstdDecompressSync(Buffer.from(p.bytes));
    assert.equal(raw.byteLength,p.rawBytes);
    return {...p,compression:'identity',bytes:raw.buffer.slice(raw.byteOffset,raw.byteOffset+raw.byteLength)};
  });
  assert.ok(snapshot.artifact?.parts?.length&&snapshot.meta);passed('real save artifact captured');
  // A second fresh worker avoids retaining two full worlds while restoring this synthetic campaign.
  await worker.terminate();
  startWorker();
  await rpc('session.boot',{playerKey:country,transportAutopilotEnabled:true,savePayload:{formatVersion:3,parts:loadParts,meta:snapshot.meta}});
  assert.deepEqual(configOf(await state()),config);passed('configuration survives engine save artifact restore');
  const restored=await state(),restoredProjects=await probe('projects');
  assert.deepEqual(restored.history,savedState.history);
  assert.deepEqual(restored.projects.items,savedState.projects.items);
  assert.deepEqual(restoredProjects.projects,savedProjects.projects);
  assert.equal(restored.demand.pendingCapacity,savedState.demand.pendingCapacity);
  assert.equal(restored.budget.currency,savedState.budget.currency);
  passed('native projects, automatic tags, pending capacity, currency and history survive restore');
  evidence.observations.restored=restored;
  await worker.terminate();startWorker();
  await rpc('session.boot',{playerKey:country,transportAutopilotEnabled:false,savePayload:{formatVersion:3,parts:loadParts,meta:snapshot.meta}});
  const disabledBefore=await probe('summary');
  for(const name of ['transport_auto_state','transport_auto_config','transport_auto_run']) {
    const response=await rpc('domain.call',{methodId:'executeDevConsoleCommand',args:[{name,args:name==='transport_auto_config'?[JSON.stringify({enabled:false})]:[]}]});
    assert.equal(response.result.ok,false);assert.match(JSON.stringify(response.result),/disabled in this session/);
  }
  assert.equal((await probe('summary')).automationRecord,disabledBefore.automationRecord);passed('disabled launch preserves an enabled saved campaign while rejecting every Auto command');
  await rpc('clock.advanceDays',{days:1});const disabledDay=await probe('summary');
  assert.equal(disabledDay.automationRecord,disabledBefore.automationRecord);assert.equal(disabledDay.projectCount,disabledBefore.projectCount);passed('disabled daily hook leaves saved automation untouched and keeps existing construction');
  await worker.terminate();startWorker();
  await rpc('session.boot',{playerKey:country,transportAutopilotEnabled:true,savePayload:{formatVersion:3,parts:loadParts,meta:snapshot.meta}});
  assert.deepEqual(configOf(await state()),config);passed('reenabling on a later launch restores access to unchanged campaign settings');
}
main().then(()=>{evidence.ok=true;}).catch(error=>{evidence.ok=false;evidence.error=error.stack;process.exitCode=1;console.error(error.stack);}).finally(async()=>{
  for(const p of pending.values())clearTimeout(p.timer);
  pending.clear();
  if(worker)await worker.terminate();
  evidence.finished=new Date().toISOString();
  if(output){fs.mkdirSync(path.dirname(path.resolve(output)),{recursive:true});fs.writeFileSync(output,JSON.stringify(evidence,null,2)+'\n');console.log(`Evidence: ${path.resolve(output)}`);}
  else console.log(JSON.stringify(evidence,null,2));
  fs.rmSync(temp,{recursive:true,force:true});
});

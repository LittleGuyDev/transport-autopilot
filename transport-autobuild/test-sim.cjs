"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const context=vm.createContext({});
vm.runInContext(fs.readFileSync(__dirname+"/sim.js","utf8"),context);
const runtime=context.__geoTransportRuntime;
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-7,`${a} != ${b}`);
function engine(count=1) {
  const country={key:"840",provs:[]},rows=[],assets=[],provByKey=new Map;
  for(let i=0;i<count;i++) {
    const key="p"+i,p={provinceKey:key,roadKm:100,motorwayKm:0,railKm:0,roadCondition:1,railCondition:1,roadCeilingKm:10000,motorwayCeilingKm:1000,railCeilingKm:1000};
    rows.push(p);const province={__provKey:key,__owner:"840"};country.provs.push(province);provByKey.set(key,province);
    assets.push({id:"surface:"+key,provinceKey:key,actorId:"840",assetClass:"surface_province",modes:["land"],active:true,condition:1,capacityScaleByMode:{land:1},capacityByMode:{land:100}});
  }
  const sim={playerKey:"840",player:country,countries:new Map([["840",country]]),world:{provByKey},day:1,construction:[],finance:{sovereigns:{"840":{cashAccountId:"cash"}},accounts:{cash:{balanceBn:1000}}},transport:{network:{assets,calibration:{status:"calibrated",surfaceCapacityScale:.1}},surface:{byProvince:Object.fromEntries(rows.map(p=>[p.provinceKey,{...p}]))},lastSolveDay:1,lastSolve:{assetLoads:assets.map(a=>({assetId:a.id,mode:"land",load:90})),resultConstraints:[]}},attempts:0,quotes:0};
  sim.transportSnapshot=()=>({surface:{provinces:rows.filter(p=>provByKey.get(p.provinceKey)?.__owner==="840")},deliveryRate:1});
  sim.transportProjectQuote=spec=>{
    sim.quotes++;
    const p=rows.find(p=>p.provinceKey===spec.provinceKey),province=provByKey.get(spec.provinceKey);
    if(!p||province?.__owner!=="840"||province.__occupier&&province.__occupier!=="840") return {ok:false,reason:"province_not_owned"};
    if(sim.construction.some(p=>p.transportPlan?.surfaceProgram?.provinceKey===spec.provinceKey&&p.transportPlan.surfaceProgram.surfaceKind===spec.surfaceKind)) return {ok:false,reason:"project_exists"};
    const km=Math.round(Math.max(1,Math.min(5000,spec.lengthKm))*10)/10,months=Math.ceil(4+km/({road:80,motorway:24,rail:18}[spec.surfaceKind]));
    return {ok:true,costBn:km*({road:.0014,motorway:.011,rail:.0085}[spec.surfaceKind]),months,plan:{surfaceProgram:{provinceKey:spec.provinceKey,surfaceKind:spec.surfaceKind,lengthKm:km}}};
  };
  sim.startTransportProject=spec=>{sim.attempts++;if(sim.rejectStarts)return {ok:false,reason:"financing_unavailable"};const q=sim.transportProjectQuote(spec);if(!q.ok)return q;const project={id:sim.construction.length+1,kind:"transport",projectType:"surface_program",owner:"840",totalCost:q.costBn,monthsTotal:q.months,monthsLeft:q.months,paid:0,phase:"construction",transportPlan:q.plan};sim.construction.push(project);return {ok:true,project,quote:q};};
  sim.rows=rows;return sim;
}
function config(sim,extra={}) {const result=runtime.configure(sim,{enabled:true,budget:10,reserve:0,target:.8,kinds:["road"],...extra});assert.equal(result.ok,true,result.message);}
function manual(sim,kind,km,cost=1,months=10,paid=0,key="p0") {sim.construction.push({id:"m"+sim.construction.length,kind:"transport",projectType:"surface_program",owner:"840",totalCost:cost,monthsTotal:months,monthsLeft:months,paid,phase:"construction",transportPlan:{surfaceProgram:{provinceKey:key,surfaceKind:kind,lengthKm:km}}});}
function run(sim) {const result=runtime.run(sim);assert.equal(result.ok,true,result.message);return result.state;}
const tests=[];function test(name,body) {tests.push([name,body]);}
// The real adapter owns engine-specific physical sizes. This fixture models its
// public diagnosis and sized specs without duplicating the native solver.
context.__geoTransportAdapter={inspect:sim=>typeof sim.nativeView==="function"?sim.nativeView():sim.nativeView||{}};
function underfulfilled(sim,kind="gas_pipeline",scope="owned") {
  sim.transport.network.assets[0].capacityByMode.land=10.1164;
  sim.transport.lastSolve.assetLoads[0].load=6.33057;
  const originalSnapshot=sim.transportSnapshot;
  sim.transportSnapshot=()=>({...originalSnapshot(),deliveryRate:.958703,requestedLoad:17.4593,deliveredLoad:16.7382841});
  const candidate={kind,targetId:"pipe:one",label:"Owned gas pipeline",blockerId:"pipeline:bundle",spec:{projectType:"capacity_upgrade",targetId:"pipe:one",fixtureUnits:10},minimumSpec:{projectType:"capacity_upgrade",targetId:"pipe:one",fixtureUnits:1},specForScale:fraction=>({projectType:"capacity_upgrade",targetId:"pipe:one",fixtureUnits:Math.max(1,Math.ceil(fraction*10))})};
  sim.nativeView=()=>({delivery:{rate:.958703,requestedLoad:17.4593,deliveredLoad:16.7382841,unmetLoad:.7210159,fulfilled:false,blockers:[{id:"pipeline:bundle",label:"Gas pipeline bottleneck",mode:kind,ownerKey:scope==="owned"?"840":"124",ownerName:scope==="owned"?"Player":"Foreign country",scope,unmetLoad:.7210159,actionable:scope==="owned",reason:scope==="owned"?"Owned gas pipeline capacity limits deliveries.":"Foreign pipeline capacity limits deliveries."}]},candidates:scope==="owned"?[candidate]:[]});
  const base=sim.transportProjectQuote;
  sim.transportProjectQuote=spec=>spec.projectType==="surface_program"?base(spec):{ok:true,costBn:spec.fixtureUnits,months:10,projectType:spec.projectType,targetId:spec.targetId,plan:{targetId:spec.targetId}};
  sim.startTransportProject=spec=>{
    sim.attempts++;if(sim.rejectStarts) return {ok:false,reason:"financing_unavailable"};
    const q=sim.transportProjectQuote(spec);if(!q.ok) return q;
    const project={id:sim.construction.length+1,kind:"transport",projectType:spec.projectType,owner:"840",targetId:spec.targetId,totalCost:q.costBn,monthsTotal:q.months,monthsLeft:q.months,paid:0,phase:"construction",transportPlan:q.plan};sim.construction.push(project);return {ok:true,project};
  };
  return candidate;
}
test("defaults off; daily tick does not purchase",()=>{const s=engine();runtime.tick(s);assert.equal(s.construction.length,0);assert.equal(runtime.state(s).state.config.enabled,false);});
test("opposition pauses purchases without losing settings; government control resumes",()=>{const s=engine();let controls=false;s.playerControlsGovernment=key=>{assert.equal(key,"840");return controls;};config(s,{budget:.001,reserve:2});let result=run(s);assert.equal(result.status.code,"authority");assert.equal(s.quotes,0);assert.equal(s.attempts,0);assert.equal(result.config.enabled,true);near(result.config.budget,.001);near(result.config.reserve,2);controls=true;result=run(s);assert.equal(s.construction.length,1);assert.equal(result.status.code,"building");assert.ok(result.budget.monthlyCommitted<=.001+1e-12);assert.ok(result.budget.outstanding<=result.budget.cash-result.budget.reserve+1e-12);});
test("native quote cost and actual queued capacity prevent repeated builds",()=>{const s=engine();config(s);let result=run(s);assert.equal(s.construction.length,1);near(s.construction[0].transportPlan.surfaceProgram.lengthKm,12.5);near(result.demand.pendingCapacity,12.5);near(result.budget.monthlyCommitted,s.construction[0].totalCost/s.construction[0].monthsTotal);run(s);s.day++;runtime.tick(s);assert.equal(s.construction.length,1);});
test("repeated national constraints use MAX demand, never sum",()=>{const s=engine();s.transport.lastSolve.resultConstraints=[100,120,120].map(requestedLoad=>({causeId:"capacity",bottleneck:{id:"national:840:land",mode:"land",requestedLoad}}));config(s);const r=run(s);near(r.demand.requested,120);near(r.demand.targetCapacity,150);near(r.demand.pendingCapacity,50);});
test("monthly cap includes existing manual commitments; lowering cap never cancels",()=>{const s=engine(2);manual(s,"rail",1,10,10);config(s,{budget:.5});const r=run(s);assert.equal(s.construction.length,1);near(r.budget.monthlyCommitted,1);assert.equal(r.status.code,"budget");config(s,{budget:0});run(s);assert.equal(s.construction.length,1);});
test("quote shrinks to strict monthly and upfront cash caps",()=>{const s=engine();s.finance.accounts.cash.balanceBn=1.01;config(s,{budget:.001,reserve:1});const r=run(s);assert.equal(s.construction.length,1);assert.ok(r.budget.monthlyCommitted<=.001+1e-8);assert.ok(r.budget.outstanding<=.01+1e-8);assert.ok(r.demand.pendingCapacity<=12.5+1e-8);});
test("cash reserve covers all outstanding manual construction first",()=>{const s=engine(2);s.finance.accounts.cash.balanceBn=2;manual(s,"rail",1,1.5,10,.5);config(s,{reserve:1});const r=run(s);assert.equal(s.construction.length,1);near(r.budget.outstanding,1);near(r.budget.cashAvailable,0);assert.equal(r.status.code,"reserve");});
test("pending motorway gains four capacity and shares road ceiling",()=>{const s=engine();manual(s,"motorway",2,0,10);config(s);let r=runtime.state(s).state;near(r.demand.pendingCapacity,8);s.rows[0].roadCeilingKm=102;run(s);assert.equal(s.construction.length,1);});
test("mixed pending motorway and road are not double counted",()=>{const s=engine();manual(s,"motorway",2,0,10);manual(s,"road",3,0,10);config(s);near(runtime.state(s).state.demand.pendingCapacity,11);});
test("uncalibrated or zero demand never purchases",()=>{const s=engine();config(s);s.transport.network.calibration.status="pending";assert.equal(run(s).status.code,"calibrating");assert.equal(s.construction.length,0);s.transport.network.calibration.status="calibrated";s.transport.lastSolve.assetLoads=[];run(s);assert.equal(s.construction.length,0);});
test("lost ownership, occupation, destroyed, blockaded and inactive provinces do not receive spending",()=>{for(const mutate of [s=>s.world.provByKey.get("p0").__owner="124",s=>s.world.provByKey.get("p0").__occupier="124",s=>s.transport.network.assets[0].condition=0,s=>s.transport.network.assets[0].blockadeFactorByMode={land:.5},s=>s.transport.network.assets[0].active=false,s=>s.transport.network.assets[0].modes=[]]) {const s=engine();config(s);mutate(s);run(s);assert.equal(s.construction.length,0);}});
test("asset-specific scale wins over global, explicit zero stays zero",()=>{const s=engine();config(s);s.transport.network.assets[0].capacityScaleByMode.land=2;const r=run(s);near(r.demand.pendingCapacity,12.4);const z=engine();config(z);z.transport.network.assets[0].capacityScaleByMode.land=0;run(z);assert.equal(z.construction.length,0);});
test("eight starts per game day including repeated manual run; active limit64",()=>{const s=engine(80);s.transport.lastSolve.resultConstraints=[{causeId:"capacity",bottleneck:{id:"national:840:land",mode:"land",requestedLoad:80000}}];config(s);run(s);assert.equal(s.construction.length,8);run(s);assert.equal(s.construction.length,8);s.day++;run(s);assert.equal(s.construction.length,16);const t=engine(80);for(let i=0;i<64;i++)manual(t,"road",1,0,10,0,"p"+i);config(t);assert.equal(run(t).projects.active,64);assert.equal(t.construction.length,64);});
test("individual native project maximum5000 is respected",()=>{const s=engine();s.transport.lastSolve.resultConstraints=[{causeId:"capacity",bottleneck:{id:"national:840:land",mode:"land",requestedLoad:20000}}];config(s,{budget:100});run(s);assert.equal(s.construction.length,1);assert.equal(s.construction[0].transportPlan.surfaceProgram.lengthKm,5000);});
test("rejected start produces no history, tags, budget ledger or fake success",()=>{const s=engine();s.rejectStarts=true;config(s);const r=run(s);assert.equal(s.construction.length,0);assert.equal(r.history.length,0);near(r.budget.monthlyCommitted,0);assert.equal(r.projects.started,0);assert.equal(r.status.code,"rejected");});
test("settings reject nonfinite, unknown, out-of-range and oversized input atomically",()=>{const s=engine();const before=JSON.stringify(runtime.state(s).state.config);for(const patch of [{budget:NaN},{reserve:Infinity},{target:.1},{enabled:"yes"},{kinds:["boat"]},{other:2}," ".repeat(257)]){assert.equal(runtime.configure(s,patch).ok,false);assert.equal(JSON.stringify(runtime.state(s).state.config),before);}assert.equal(runtime.configure(s,"on 2 3 .85 road,rail").ok,true);near(runtime.state(s).state.config.target,.85);});
test("quantization does not buy a kilometer when less than one is needed",()=>{const s=engine();s.transport.lastSolve.assetLoads[0].load=80.01;config(s);run(s);assert.equal(s.construction.length,0);});
test("native cost rounding cannot exceed an exact cash or monthly limit",()=>{const s=engine();const base=s.transportProjectQuote;s.transportProjectQuote=spec=>{const q=base(spec);return {...q,costBn:.001400001};};s.finance.accounts.cash.balanceBn=.001400005;config(s,{budget:1});run(s);assert.equal(s.construction.length,0);const t=engine();t.finance.accounts.cash.currencyCode="EUR";assert.equal(runtime.state(t).state.budget.currency,"EUR");});
test("200 low-development expensive modes cannot starve affordable roads",()=>{const s=engine(200);for(const row of s.rows){row.roadKm=800;row.roadCeilingKm=1000;s.transport.surface.byProvince[row.provinceKey].roadKm=800;}for(const asset of s.transport.network.assets)asset.capacityByMode.land=800;for(const load of s.transport.lastSolve.assetLoads)load.load=720;config(s,{budget:.0004,kinds:["road","motorway","rail"]});const result=run(s);assert.equal(s.construction.length,1);assert.equal(s.construction[0].transportPlan.surfaceProgram.surfaceKind,"road");assert.ok(result.budget.monthlyCommitted<=.0004+1e-12);});
test("bounded quote windows rotate past provinces with native refusals",()=>{const s=engine(220);const base=s.transportProjectQuote,accepted="p99";s.transportProjectQuote=spec=>spec.provinceKey===accepted?base(spec):{ok:false,reason:"fixture_refusal"};config(s,{kinds:["road"]});run(s);assert.equal(s.construction.length,0);s.day++;run(s);assert.equal(s.construction.length,1);assert.equal(s.construction[0].transportPlan.surfaceProgram.provinceKey,accepted);});
test("hook errors stay contained; persisted history stays bounded and plain",()=>{const s=engine();config(s);s.transportSnapshot=()=>{throw Error("fixture");};assert.doesNotThrow(()=>runtime.tick(s));assert.equal(s.player._transportAutobuild.status.code,"error");const t=engine();config(t);t.player._transportAutobuild.history=Array.from({length:100},(_,id)=>({id,day:1}));assert.equal(runtime.state(t).state.history.length,32);assert.doesNotThrow(()=>JSON.stringify(t.player._transportAutobuild));});
test("95.8703 percent fulfilled with spare land capacity reports foreign shortage without irrelevant roads",()=>{const s=engine();underfulfilled(s,"gas_pipeline","foreign");config(s);const r=run(s);near(r.demand.targetCapacity,7.9132125);near(r.delivery.rate,.958703);assert.equal(r.delivery.fulfilled,false);assert.equal(r.status.code,"external");assert.match(r.status.message,/Foreign pipeline capacity/);assert.equal(s.construction.length,0);assert.equal(s.attempts,0);});
test("new transport types require explicit opt-in and existing selections stay unchanged",()=>{const s=engine();underfulfilled(s);assert.deepEqual(Array.from(runtime.state(s).state.config.kinds),["road","motorway","rail"]);config(s);let r=run(s);assert.equal(s.construction.length,0);assert.equal(r.status.code,"partial");assert.match(r.status.message,/Enable gas pipelines/);assert.deepEqual(Array.from(r.config.kinds),["road"]);config(s,{kinds:["road","gas_pipeline"]});r=run(s);assert.equal(s.construction.length,1);assert.equal(r.projects.items[0].kind,"gas_pipeline");assert.equal(r.status.code,"building");assert.equal(r.delivery.fulfilled,false);assert.match(r.status.message,/95\.8703/);});
test("owned gas oil and port remedies use native specs before land headroom and never duplicate queued targets",()=>{for(const kind of ["gas_pipeline","oil_pipeline","port"]){const s=engine();const c=underfulfilled(s,kind);if(kind==="port"){c.spec.projectType="new_asset";c.minimumSpec.projectType="new_asset";c.specForScale=f=>({...c.spec,fixtureUnits:Math.max(1,Math.ceil(f*10))});}config(s,{kinds:["road",kind]});run(s);assert.equal(s.construction.length,1);assert.equal(s.construction[0].transportAutobuildKind,kind);run(s);s.day++;runtime.tick(s);assert.equal(s.construction.length,1);assert.equal(runtime.state(s).state.delivery.fulfilled,false);assert.match(runtime.state(s).state.status.message,/queued; delivery has not yet improved/);}});
test("all own transport projects count toward monthly cash and active limits",()=>{const s=engine();underfulfilled(s);s.construction.push({id:"manual-port",kind:"transport",projectType:"new_asset",owner:"840",targetId:"port:manual",totalCost:5,paid:1,monthsTotal:10,monthsLeft:10});config(s,{budget:.5,kinds:["gas_pipeline"]});let r=run(s);assert.equal(r.status.code,"budget");near(r.budget.monthlyCommitted,.5);near(r.budget.outstanding,4);assert.equal(s.construction.length,1);s.finance.accounts.cash.balanceBn=5;config(s,{budget:10,reserve:1,kinds:["gas_pipeline"]});assert.equal(run(s).status.code,"reserve");for(let i=1;i<64;i++)s.construction.push({id:i,kind:"transport",projectType:"capacity_upgrade",owner:"840",totalCost:0,paid:0,monthsTotal:10});s.finance.accounts.cash.balanceBn=1000;config(s,{budget:10,reserve:0,kinds:["gas_pipeline"]});assert.equal(run(s).status.code,"batch");assert.equal(s.attempts,0);});
test("native remedy sizing honors discrete adapter tiers and strict budget reserve limits",()=>{const s=engine();underfulfilled(s);s.finance.accounts.cash.balanceBn=5;config(s,{budget:.25,reserve:3,kinds:["gas_pipeline"]});const r=run(s);assert.equal(s.construction.length,1);near(s.construction[0].totalCost,2);assert.ok(r.budget.monthlyCommitted<=.25);assert.ok(r.budget.outstanding<=2);const t=engine();underfulfilled(t);config(t,{budget:.05,kinds:["gas_pipeline"]});assert.equal(run(t).status.code,"budget");assert.equal(t.construction.length,0);});
test("foreign delivery shortages do not block maintaining the owned land utilization target",()=>{const s=engine();underfulfilled(s,"gas_pipeline","foreign");s.transport.network.assets[0].capacityByMode.land=100;s.transport.lastSolve.assetLoads[0].load=85;config(s);const r=run(s);assert.equal(s.construction.length,1);assert.equal(s.construction[0].transportAutobuildKind,"road");assert.ok(r.demand.pendingCapacity>0);assert.equal(r.delivery.fulfilled,false);});
test("owned delivery remedies precede land headroom construction at 85 percent land utilization",()=>{const s=engine();underfulfilled(s);s.transport.network.assets[0].capacityByMode.land=100;s.transport.lastSolve.assetLoads[0].load=85;const view=s.nativeView;s.nativeView=()=>{const v=view();v.delivery.blockers.push({...v.delivery.blockers[0],id:"foreign:sea",label:"Port capacity — Foreign country",scope:"foreign",actionable:false,reason:"foreign_controlled"});return v;};config(s,{kinds:["road","gas_pipeline"]});const r=run(s);assert.deepEqual(s.construction.map(p=>p.transportAutobuildKind),["gas_pipeline","road"]);assert.ok(r.budget.monthlyCommitted<=10);assert.equal(r.delivery.fulfilled,false);});
test("actual owned land shortages still build while demand is unfulfilled",()=>{const s=engine();underfulfilled(s,"gas_pipeline","foreign");s.transport.lastSolve.resultConstraints=[{causeId:"capacity",bottleneck:{id:"national:840:land",mode:"land",requestedLoad:12}}];s.startTransportProject=spec=>{s.attempts++;const q=s.transportProjectQuote(spec);const p={id:1,kind:"transport",projectType:"surface_program",owner:"840",totalCost:q.costBn,monthsTotal:q.months,paid:0,transportPlan:q.plan};s.construction.push(p);return {ok:true,project:p};};config(s);assert.equal(run(s).status.code,"building");assert.equal(s.construction.length,1);assert.equal(s.construction[0].transportAutobuildKind,"road");});
test("fulfillment threshold accepts tiny residual load or 99.999 percent delivery",()=>{for(const delivery of [{rate:.99999,requestedLoad:100,deliveredLoad:99.999,unmetLoad:.001},{rate:.9,requestedLoad:1e-6,deliveredLoad:9e-7,unmetLoad:1e-7}]){const s=engine();s.transport.lastSolve.assetLoads[0].load=1;s.nativeView={delivery,candidates:[]};config(s);const r=run(s);assert.equal(r.delivery.fulfilled,true);assert.equal(r.status.code,"sufficient");}});
test("native remedies share government guard and rejected starts create no fake history",()=>{const s=engine();underfulfilled(s);s.playerControlsGovernment=()=>false;config(s,{kinds:["gas_pipeline"]});assert.equal(run(s).status.code,"authority");assert.equal(s.attempts,0);s.playerControlsGovernment=()=>true;s.rejectStarts=true;const r=run(s);assert.equal(r.status.code,"rejected");assert.equal(r.history.length,0);assert.equal(s.construction.length,0);});
test("reading old sufficient status immediately diagnoses unmet delivery without a review or purchase",()=>{const s=engine();underfulfilled(s);s.player._transportAutobuild={config:{enabled:true,budget:10,reserve:0,target:.8,kinds:["road","rail"]},status:{code:"sufficient",message:"Old land-only result",day:1},history:[{id:"retained",kind:"road"}]};const r=runtime.state(s).state;assert.equal(r.status.code,"sufficient");assert.equal(r.currentStatus.code,"partial");assert.match(r.currentStatus.message,/95\.8703/);assert.match(r.currentStatus.message,/Owned gas pipeline/);assert.equal(r.review.stale,true);assert.deepEqual(Array.from(r.config.kinds),["road","rail"]);assert.equal(r.history[0].id,"retained");assert.equal(s.attempts,0);assert.equal(s.construction.length,0);assert.equal(s.player._transportAutobuild.status.code,"sufficient");});
test("native remedies share the eight-start daily limit across repeated reviews",()=>{const s=engine();const c=underfulfilled(s),view=s.nativeView;s.nativeView=()=>({...view(),candidates:Array.from({length:10},(_,i)=>({...c,targetId:"pipe:"+i,spec:{...c.spec,targetId:"pipe:"+i},minimumSpec:{...c.minimumSpec,targetId:"pipe:"+i},specForScale:undefined}))});config(s,{budget:100,kinds:["gas_pipeline"]});run(s);assert.equal(s.construction.length,8);run(s);assert.equal(s.construction.length,8);s.day++;run(s);assert.equal(s.construction.length,10);});
test("delivery status uses bottleneck labels and friendly explanations without native reason codes",()=>{const s=engine();underfulfilled(s);const view=s.nativeView;s.nativeView=()=>{const v=view();v.delivery.blockers[0].reason="expandable";v.delivery.blockers.push({...v.delivery.blockers[0],label:"Port capacity — Foreign country",scope:"foreign",reason:"foreign_controlled"});return v;};config(s);const r=run(s);assert.match(r.status.message,/Gas pipeline bottleneck: An owned expansion can improve deliveries/);assert.match(r.status.message,/Port capacity — Foreign country: This capacity is controlled by another country/);assert.doesNotMatch(r.status.message,/expandable|foreign_controlled/);});
test("missing or uncalibrated reports stay not ready instead of showing 100 percent success",()=>{
  for(const mutate of [s=>delete s.transport.lastSolve,s=>s.transport.network.calibration.status="pending",s=>s.nativeView={delivery:{ready:false,rate:null,fulfilled:false,blockers:[]}}]) {
    const s=engine();config(s);mutate(s);const r=run(s);
    assert.equal(r.delivery.ready,false);assert.equal(r.delivery.rate,null);assert.equal(r.delivery.fulfilled,false);
    assert.equal(r.status.code,"calibrating");assert.equal(r.currentStatus.code,"calibrating");assert.equal(r.lastRun.started,0);assert.equal(s.attempts,0);
  }
});
test("run results count only this review while the daily count stays cumulative",()=>{
  const s=engine();s.world.provByKey.get("p0").name="Named province";config(s);
  const first=run(s);assert.equal(first.lastRun.started,1);assert.equal(first.projects.started,1);
  near(first.lastRun.cost,s.construction[0].totalCost);near(first.lastRun.monthlyCost,s.construction[0].totalCost/s.construction[0].monthsTotal);
  assert.equal(first.projects.items[0].provinceName,"Named province");
  const next=run(s);assert.equal(next.lastRun.started,0);assert.equal(next.lastRun.cost,0);assert.equal(next.lastRun.monthlyCost,0);assert.equal(next.projects.started,1);
  assert.equal(next.review.day,1);assert.equal(next.review.stale,false);s.day=2;
  const stale=runtime.state(s).state;assert.equal(stale.review.day,1);assert.equal(stale.review.stale,true);assert.equal(stale.projects.started,0);assert.equal(stale.lastRun.day,1);
});
test("failed review preserves actual starts and marks the immediate result failed",()=>{
  const s=engine();config(s);const snapshot=s.transportSnapshot;s.transportSnapshot=()=>{if(s.construction.length)throw Error("after construction");return snapshot();};
  const r=runtime.run(s);assert.equal(r.ok,false);assert.equal(r.state,undefined);assert.equal(r.lastRun.started,1);assert.equal(r.lastRun.ok,false); // snapshot is still unavailable
  assert.equal(s.player._transportAutobuild.lastRun.ok,false);assert.equal(s.player._transportAutobuild.lastRun.started,1);
  near(s.player._transportAutobuild.lastRun.cost,s.construction[0].totalCost);
});
test("recent history declares the cap and distinguishes observed truncation from legacy unknown",()=>{
  const s=engine();let r=runtime.state(s).state;assert.equal(r.historyInfo.limit,32);assert.equal(r.historyInfo.retained,0);assert.equal(r.historyInfo.truncated,false);
  s.player._transportAutobuild.history=Array.from({length:33},(_,id)=>({id:String(id),day:id}));
  r=runtime.state(s).state;assert.equal(r.historyInfo.truncated,true);assert.equal(r.historyInfo.retained,32);assert.equal(r.history[0].id,"1");
  const legacy=engine();legacy.player._transportAutobuild={config:{enabled:false},history:Array.from({length:32},(_,id)=>({id}))};
  assert.equal(runtime.state(legacy).state.historyInfo.truncated,null);
});
test("settings edits preserve the last review decision separately from current action status",()=>{
 const s=engine();config(s);const first=run(s);assert.equal(first.review.status.code,"building");
 config(s,{enabled:false});const r=runtime.state(s).state;assert.equal(r.status.code,"disabled");assert.equal(r.currentStatus.code,"disabled");assert.equal(r.review.status.code,"building");assert.equal(r.review.day,1);assert.equal(r.review.stale,true);
});
test("same-day review freshness changes with report, treasury, project, or settings inputs",()=>{
 for(const change of [s=>s.finance.accounts.cash.balanceBn--,s=>s.transport.network.epoch=5,s=>s.transport.lastSolve.assetLoads[0].load=95,s=>s.construction[0].monthsLeft--,s=>{s.player._transportAutobuild.config.budget=9;}]) {
  const s=engine();config(s);run(s);assert.equal(runtime.state(s).state.review.stale,false);change(s);assert.equal(runtime.state(s).state.review.stale,true);
 }
});
test("current explanation reports limiting funds and preserves valid native refusals",()=>{
 const budget=engine();config(budget,{budget:0});assert.equal(run(budget).currentStatus.code,"budget");
 const cash=engine();config(cash,{reserve:1000});assert.equal(run(cash).currentStatus.code,"reserve");
 const unaffordable=engine();underfulfilled(unaffordable);config(unaffordable,{budget:.05,kinds:["gas_pipeline"]});const r=run(unaffordable);assert.equal(r.currentStatus.code,"budget");assert.match(r.currentStatus.message,/exceeds/);
 const rejected=engine();config(rejected);rejected.rejectStarts=true;assert.equal(run(rejected).currentStatus.code,"rejected");
 const sufficient=engine();sufficient.transport.lastSolve.assetLoads[0].load=1;config(sufficient,{budget:0});assert.equal(run(sufficient).currentStatus.code,"sufficient");
});
function spareLand(count=1) {
 const s=engine(count);s.finance.accounts.cash.balanceBn=3000;
 for(const load of s.transport.lastSolve.assetLoads)load.load=1;
 return s;
}
test("legacy saves default to demand policy; explicit invalid policies fail atomically",()=>{
 const s=spareLand();s.player._transportAutobuild={config:{enabled:true,budget:25,reserve:1250,target:.8,kinds:["road","motorway","rail"]},history:[]};
 assert.equal(runtime.state(s).state.config.landPolicy,"demand");assert.equal(run(s).projects.active,0);
 config(s,{landPolicy:"expand"});const before=JSON.stringify(s.player._transportAutobuild.config);
 for(const landPolicy of ["always",null,0,{},false]){assert.equal(runtime.configure(s,{landPolicy}).ok,false);assert.equal(JSON.stringify(s.player._transportAutobuild.config),before);}
 assert.equal(runtime.configure(s,{budget:24}).state.config.landPolicy,"expand");
 s.player._transportAutobuild.config.landPolicy="invalid-old-data";assert.equal(runtime.state(s).state.config.landPolicy,"demand");
});
test("keep expanding queues all selected land kinds with spare capacity and truthful demand",()=>{
 const s=spareLand(6);config(s,{budget:25,reserve:1250,landPolicy:"expand",kinds:["road","motorway","rail","gas_pipeline","oil_pipeline","port"]});
 const before=runtime.state(s).state;assert.equal(before.currentStatus.code,"expanding");assert.equal(before.expansion.enabled,true);assert.deepEqual(Array.from(before.expansion.eligibleKinds),["road","motorway","rail"]);
 const r=run(s);assert.deepEqual(s.construction.slice(0,3).map(p=>p.transportAutobuildKind),["road","motorway","rail"]);assert.equal(r.projects.started,8);
 near(r.demand.requested,before.demand.requested);near(r.demand.capacity,before.demand.capacity);near(r.demand.targetCapacity,before.demand.targetCapacity);near(r.demand.additionalCapacity,0);
 assert.ok(r.demand.pendingCapacity>0);assert.ok(r.budget.monthlyCommitted<=25);assert.ok(r.budget.outstanding<=r.budget.cash-1250);
 assert.ok(s.construction.every(p=>p.transportPlan.surfaceProgram.lengthKm<=5000));assert.doesNotMatch(JSON.stringify(r),/Infinity|NaN/);
});
test("expansion rotation skips unaffordable modes without starving affordable ones",()=>{
 const s=spareLand(8),base=s.transportProjectQuote;
 s.transportProjectQuote=spec=>{const q=base(spec);return spec.surfaceKind==="motorway"&&q.ok?{...q,costBn:1e6}:q;};
 config(s,{budget:25,reserve:1250,landPolicy:"expand",kinds:["road","motorway","rail"]});
 const r=run(s);assert.deepEqual(s.construction.map(p=>p.transportAutobuildKind),["road","rail","road","rail","road","rail","road","rail"]);assert.ok(r.budget.monthlyCommitted<=25);
});
test("expansion selection and inspection survive save reload without advancing cursors",()=>{
 const s=spareLand(10);config(s,{landPolicy:"expand",budget:25,kinds:["road","motorway","rail"]});run(s);
 assert.equal(s.player._transportAutobuild.landKindCursor,2);const saved=JSON.stringify(s.player._transportAutobuild);
 runtime.state(s);runtime.state(s);assert.equal(JSON.stringify(s.player._transportAutobuild),saved);
 s.player._transportAutobuild=JSON.parse(saved);s.day++;const prior=s.construction.length;run(s);
 assert.equal(s.construction[prior].transportAutobuildKind,"rail");assert.equal(s.player._transportAutobuild.config.landPolicy,"expand");
 assert.equal(runtime.configure(s,{landPolicy:"demand"}).ok,true);const count=s.construction.length;s.day++;run(s);assert.equal(s.construction.length,count);
});
test("expansion preserves shared road ceiling and independent rail ceiling",()=>{
 const s=spareLand();s.rows[0].roadCeilingKm=110;s.rows[0].motorwayCeilingKm=20;s.rows[0].railCeilingKm=7;
 config(s,{landPolicy:"expand",kinds:["road","motorway","rail"]});s.player._transportAutobuild.landKindCursor=1;run(s);
 const lengths=Object.fromEntries(s.construction.map(p=>[p.transportAutobuildKind,p.transportPlan.surfaceProgram.lengthKm]));assert.deepEqual(lengths,{motorway:10,rail:7});
 const r=runtime.state(s).state;assert.equal(r.expansion.eligibleCandidates,0);assert.equal(r.currentStatus.code,"waiting");assert.doesNotMatch(r.currentStatus.message,/fulfilled|completed|maximum/);
});
test("expansion honors monthly cash and active commitment limits",()=>{
 const s=spareLand(2);s.finance.accounts.cash.balanceBn=1250.01;config(s,{budget:.001,reserve:1250,landPolicy:"expand",kinds:["road"]});const r=run(s);
 assert.ok(r.projects.active>0);assert.ok(r.budget.monthlyCommitted<=.001+1e-12);assert.ok(r.budget.outstanding<=.01+1e-12);
 const committed=spareLand();manual(committed,"rail",1,10,10);config(committed,{budget:.5,landPolicy:"expand"});assert.equal(run(committed).status.code,"budget");assert.equal(committed.construction.length,1);
 const reserved=spareLand();config(reserved,{reserve:3000,landPolicy:"expand"});assert.equal(run(reserved).status.code,"reserve");assert.equal(reserved.construction.length,0);
 const active=spareLand(80);for(let i=0;i<64;i++)manual(active,"road",1,0,10,0,"p"+i);config(active,{landPolicy:"expand"});assert.equal(run(active).status.code,"batch");assert.equal(active.construction.length,64);
});
test("expansion shares daily caps across repeated manual reviews and automatic checks",()=>{
 const s=spareLand(20);config(s,{budget:25,landPolicy:"expand",kinds:["road","motorway","rail"]});run(s);run(s);runtime.tick(s);assert.equal(s.construction.length,8);
 assert.equal(runtime.state(s).state.currentStatus.code,"batch");s.day++;runtime.tick(s);assert.equal(s.construction.length,16);
});
test("expansion respects disabled government report occupation condition and zero-scale guards",()=>{
 for(const change of [s=>config(s,{enabled:false,landPolicy:"expand"}),s=>s.playerControlsGovernment=()=>false,s=>s.transport.network.calibration.status="pending",s=>s.world.provByKey.get("p0").__occupier="124",s=>s.transport.network.assets[0].condition=.99,s=>s.transport.network.assets[0].capacityScaleByMode.land=0]) {
  const s=spareLand();config(s,{landPolicy:"expand"});change(s);run(s);runtime.tick(s);assert.equal(s.construction.length,0);
 }
});
test("expansion never opts unselected kinds in or claims no land selection is complete",()=>{
 const s=spareLand(3);config(s,{landPolicy:"expand",kinds:["rail"]});run(s);assert.ok(s.construction.length>0);assert.ok(s.construction.every(p=>p.transportAutobuildKind==="rail"));
 const n=spareLand();config(n,{landPolicy:"expand",kinds:["gas_pipeline"]});const r=run(n);assert.equal(r.expansion.enabled,true);assert.equal(r.expansion.eligibleCandidates,0);assert.equal(r.status.code,"waiting");assert.match(r.status.message,/No selected land expansion is eligible/);
});
test("real native bottleneck remedies remain ahead of elective land expansion",()=>{
 const s=spareLand(2);const c=underfulfilled(s);s.transport.network.assets[0].capacityByMode.land=100;s.transport.lastSolve.assetLoads[0].load=1;
 config(s,{budget:25,landPolicy:"expand",kinds:["road","motorway","rail","gas_pipeline"]});const r=run(s);
 assert.equal(s.construction[0].transportAutobuildKind,"gas_pipeline");assert.ok(s.construction.some(p=>p.transportAutobuildKind==="road"));assert.equal(r.demand.additionalCapacity,0);assert.equal(r.delivery.fulfilled,false);
 assert.equal(c.kind,"gas_pipeline");
});
let passed=0;
for(const [name,body] of tests) {try{body();passed++;process.stdout.write(`PASS ${name}\n`);}catch(error){process.stderr.write(`FAIL ${name}\n${error.stack}\n`);process.exitCode=1;}}
process.stdout.write(`${passed}/${tests.length} transport simulation tests passed.\n`);

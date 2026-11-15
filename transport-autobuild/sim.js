/* Transport Autopilot: native construction only; persisted data stays on the player country. */
var __geoTransportRuntime = (() => {
  "use strict";
  const SURFACE_KINDS = ["road", "motorway", "rail"], KINDS = [...SURFACE_KINDS,"gas_pipeline","oil_pipeline","port"], WEIGHT = {road:1,motorway:4,rail:5};
  const MAX_STARTS = 8, MAX_ACTIVE = 64, HISTORY_LIMIT = 32, EPS = 1e-8;
  const finite = (v, fallback=0) => Number.isFinite(Number(v)) ? Number(v) : fallback;
  const positive = v => Math.max(0,finite(v));
  const clamp = (v,a,b) => Math.max(a,Math.min(b,v));
  const defaults = () => ({enabled:false,budget:1,reserve:1,target:.8,kinds:SURFACE_KINDS.slice(),landPolicy:"demand"});
  const player = sim => sim?.countries?.get?.(String(sim.playerKey)) || sim?.player;
  function record(sim) {
    const country=player(sim);
    if (!country) throw Error("Player country is unavailable.");
    let r=country._transportAutobuild;
    if (!r || typeof r!=="object" || Array.isArray(r)) r=country._transportAutobuild={config:defaults(),history:[],historyTruncated:false};
    r.config={...defaults(),...r.config};
    if(!["demand","expand"].includes(r.config.landPolicy)) r.config.landPolicy="demand";
    if (!Array.isArray(r.config.kinds)) r.config.kinds=SURFACE_KINDS.slice();
    if (!Array.isArray(r.history)) r.history=[];
    if(r.history.length>HISTORY_LIMIT) r.historyTruncated=true;
    r.history=r.history.slice(-HISTORY_LIMIT);
    return r;
  }
  function status(sim,r,code,message,error) {
    r.status={code,message,day:positive(sim.day)};
    if(error) r.status.error=String(error).slice(0,240);
  }
  const stockIndex = p => Math.max(0,positive(p.roadKm)-positive(p.motorwayKm))*positive(p.roadCondition)+positive(p.motorwayKm)*positive(p.roadCondition)*4+positive(p.railKm)*positive(p.railCondition)*5;
  function assetFactor(asset) {
    if(!asset || asset.active===false || !(asset.modes||[]).includes("land")) return 0;
    const quantized=v=>Math.round(clamp(finite(v,1),0,1)/.05)*.05;
    return clamp(finite(asset.condition,1),0,1)*quantized(asset.capacityFactorByMode?.land)*quantized(asset.blockadeFactorByMode?.land);
  }
  function inspect(sim,r) {
    const country=player(sim), key=String(sim.playerKey), tr=sim.transport||{}, network=tr.network||{};
    const snapshot=sim.transportSnapshot?.(key), surface=snapshot?.surface;
    const rows=(surface?.provinces||[]).map(p=>({...p,pending:{road:0,motorway:0,rail:0}}));
    const rowByKey=new Map(rows.map(p=>[String(p.provinceKey),p]));
    const assets=new Map((network.assets||[]).filter(a=>a.assetClass==="surface_province"&&String(a.actorId)===key).map(a=>[String(a.provinceKey),a]));
    for(const p of rows) {
      const raw=tr.surface?.byProvince?.[String(p.provinceKey)];
      if(raw) for(const field of ["roadKm","motorwayKm","railKm","roadCondition","railCondition"]) if(Number.isFinite(raw[field])) p[field]=raw[field];
      p.asset=assets.get(String(p.provinceKey)); p.factor=assetFactor(p.asset);
      p.index=stockIndex(p);
      const explicitScale=p.asset?.capacityScaleByMode?.land??network.calibration?.surfaceCapacityScale;
      p.scale=explicitScale!=null?positive(explicitScale):(p.index>0?positive(p.asset?.capacityByMode?.land)/p.index:0);
      p.capacity=positive(p.asset?.capacityByMode?.land)*p.factor;
    }
    const active=(sim.construction||[]).filter(p=>p?.kind==="transport"&&String(p.owner)===key);
    let monthly=0,outstanding=0,pendingCapacity=0;
    for(const project of active) {
      const plan=project.transportPlan?.surfaceProgram, row=rowByKey.get(String(plan?.provinceKey));
      monthly+=positive(project.totalCost)/Math.max(1,positive(project.monthsTotal));
      outstanding+=Math.max(0,positive(project.totalCost)-positive(project.paid));
      if(project.projectType==="surface_program"&&row&&SURFACE_KINDS.includes(plan?.surfaceKind)) {
        const km=positive(plan.lengthKm),kind=plan.surfaceKind;
        row.pending[kind]+=km;
        pendingCapacity+=km*WEIGHT[kind]*positive(kind==="rail"?row.railCondition:row.roadCondition)*row.scale*row.factor;
      }
    }
    const cashId=sim.finance?.sovereigns?.[key]?.cashAccountId;
    const cashAccount=sim.finance?.accounts?.[cashId];
    const cash=positive(cashAccount?.balanceBn),currency=String(cashAccount?.currencyCode||country.ccyCode||"USD");
    const capacity=rows.reduce((sum,p)=>sum+p.capacity,0);
    const ids=new Set([...assets.values()].map(a=>String(a.id)));
    const load=(tr.lastSolve?.assetLoads||[]).filter(l=>l.mode==="land"&&ids.has(String(l.assetId))).reduce((sum,l)=>sum+positive(l.load),0);
    let requested=load;
    for(const constraint of tr.lastSolve?.resultConstraints||[]) {
      const b=constraint?.bottleneck;
      if(constraint.causeId==="capacity"&&b?.mode==="land"&&String(b.id)===`national:${key}:land`) requested=Math.max(requested,positive(b.requestedLoad));
    }
    const native=globalThis.__geoTransportAdapter?.inspect?.(sim,{target:r.config.target})||{};
    requested=Math.max(requested,positive(native.landRequested));
    const requestedLoad=positive(snapshot?.requestedLoad),deliveredLoad=positive(snapshot?.deliveredLoad);
    const rate=finite(snapshot?.deliveryRate,requestedLoad>0?deliveredLoad/requestedLoad:1);
    const delivery={rate,requestedLoad,deliveredLoad,unmetLoad:Math.max(0,requestedLoad-deliveredLoad),blockers:[],...native.delivery};
    delivery.ready=network.calibration?.status==="calibrated"&&!!tr.lastSolve&&native.delivery?.ready!==false;
    delivery.fulfilled=delivery.ready&&(delivery.rate>=.99999||(delivery.requestedLoad>0&&delivery.unmetLoad<=1e-6));
    if(!delivery.ready) delivery.rate=null;
    if(!Array.isArray(delivery.blockers)) delivery.blockers=[];
    const calibrated=delivery.ready;
    const targetCapacity=requested/r.config.target, additionalCapacity=Math.max(0,targetCapacity-capacity-pendingCapacity);
    const nativeCandidates=(native.candidates||[]).filter(c=>c&&KINDS.includes(c.kind)&&!SURFACE_KINDS.includes(c.kind)&&c.spec&&r.config.kinds.includes(c.kind)&&!active.some(p=>c.targetId&&String(p.targetId||p.transportPlan?.targetId||"")===String(c.targetId)));
    const unselectedKinds=[...new Set((native.candidates||[]).filter(c=>c&&KINDS.includes(c.kind)&&!r.config.kinds.includes(c.kind)).map(c=>c.kind))];
    const view={country,key,rows,active,snapshot,delivery,nativeCandidates,unselectedKinds,demand:{calibrated,solveDay:positive(tr.lastSolveDay),load,requested,capacity,pendingCapacity,targetCapacity,additionalCapacity,utilization:capacity>0?load/capacity:0,deliveryRate:delivery.rate},budget:{currency,monthlyCap:r.config.budget,monthlyCommitted:monthly,monthlyAvailable:Math.max(0,r.config.budget-monthly),cash,reserve:r.config.reserve,outstanding,cashAvailable:Math.max(0,cash-r.config.reserve-outstanding)}};
    // Eligibility is a pure read; only a construction review advances quote windows.
    view.landCandidates=surfaceCandidates(sim,r,view);
    const enabled=r.config.landPolicy==="expand",selected=r.config.kinds.filter(kind=>SURFACE_KINDS.includes(kind));
    view.expansion={enabled,eligibleKinds:enabled?selected.filter(kind=>view.landCandidates.some(c=>c.kind===kind)):[],eligibleCandidates:enabled?view.landCandidates.length:0};
    return view;
  }
  function reviewStateKey(sim,r,v) {
    // Compare review inputs, including same-day edits and construction changes.
    // Do not use the record/status itself: summary reads must not invalidate it.
    return JSON.stringify({day:positive(sim.day),config:r.config,epoch:sim.transport?.network?.epoch??null,
      authority:typeof sim.playerControlsGovernment!=="function"||!!sim.playerControlsGovernment(v.key),
      demand:v.demand,delivery:v.delivery,budget:v.budget,expansion:v.expansion,
      landCandidates:v.landCandidates.map(c=>[c.kind,c.p.provinceKey,c.gain,c.km]),
      projects:v.active.map(p=>[p.id,p.totalCost,p.paid,p.monthsLeft,p.phase,p.transportPlan]),
      candidates:v.nativeCandidates.map(c=>[c.kind,c.targetId,c.spec]),unselectedKinds:v.unselectedKinds});
  }
  function currentStatusValue(sim,r,v,stale) {
    if(!r.config.enabled)return {code:"disabled",message:"Autopilot is off."};
    if(typeof sim.playerControlsGovernment==="function"&&!sim.playerControlsGovernment(v.key))return {code:"authority",message:"Automatic construction is paused while you do not control the government."};
    if(!v.delivery.ready)return {code:"calibrating",message:"Waiting for the first consolidated transport demand report."};
    if(!v.expansion.enabled&&v.delivery.fulfilled&&v.demand.additionalCapacity<=EPS&&!v.nativeCandidates.length)return {code:"sufficient",message:"Transport demand is fulfilled. Current land capacity plus any queued capacity covers the utilization target."};
    if(v.budget.monthlyAvailable<=EPS)return {code:"budget",message:"The monthly construction limit is committed. Existing projects continue."};
    if(v.budget.cashAvailable<=EPS)return {code:"reserve",message:"Cash is reserved for the treasury minimum and existing construction."};
    if(v.active.length>=MAX_ACTIVE||(r.startedDay===sim.day&&r.startedToday>=MAX_STARTS))return {code:"batch",message:"The construction batch limit is reached. Autopilot will review again tomorrow."};
    if(!stale&&["budget","reserve","rejected","error","waiting"].includes(r.reviewStatus?.code))return {...r.reviewStatus};
    if(v.expansion.enabled&&v.expansion.eligibleCandidates)return {code:"expanding",message:"Keep expanding is enabled. Eligible roads and railways can continue growing within your construction limits."};
    if(v.expansion.enabled&&v.delivery.fulfilled&&v.demand.additionalCapacity<=EPS&&!v.nativeCandidates.length)return {code:"waiting",message:"No selected land expansion is eligible right now. Existing projects continue."};
    return !v.delivery.fulfilled?deliveryStatusValue(v):{code:"ready",message:"Current land demand exceeds the utilization target."};
  }
  function summary(sim,r,view) {
    const v=view||inspect(sim,r),day=positive(sim.day);
    const reviewDay=Number.isFinite(r.reviewDay)?r.reviewDay:null;
    const stale=reviewDay==null||r.reviewStateKey!==reviewStateKey(sim,r,v);
    const currentStatus=currentStatusValue(sim,r,v,stale);
    return {
      config:{...r.config,kinds:r.config.kinds.slice()},
      status:r.status||{code:"disabled",message:"Autopilot is off.",day},
      currentStatus:{...currentStatus,day},
      review:{day:reviewDay,status:r.reviewStatus?{...r.reviewStatus}:null,stale},
      lastRun:r.lastRun?{...r.lastRun}:null,
      demand:v.demand,delivery:v.delivery,budget:v.budget,expansion:v.expansion,
      projects:{active:v.active.length,automatic:v.active.filter(p=>p.transportAutobuild===true).length,started:r.startedDay===sim.day?positive(r.startedToday):0,limit:MAX_ACTIVE,items:v.active.slice(0,MAX_ACTIVE).map(p=>{
        const provinceKey=String(p.transportPlan?.surfaceProgram?.provinceKey||p.transportPlan?.asset?.provinceKey||"");
        const province=sim.world?.provByKey?.get?.(provinceKey);
        return {id:String(p.id),kind:p.transportAutobuildKind||p.transportPlan?.surfaceProgram?.surfaceKind||p.projectType||"transport",label:p.transportAutobuildLabel||"",provinceKey,provinceName:typeof province?.name==="string"?province.name:"",km:positive(p.transportPlan?.surfaceProgram?.lengthKm),cost:positive(p.totalCost),monthly:positive(p.totalCost)/Math.max(1,positive(p.monthsTotal)),monthsLeft:positive(p.monthsLeft),phase:String(p.phase||"construction"),automatic:p.transportAutobuild===true};
      })},
      history:r.history.slice(-HISTORY_LIMIT).map(x=>({...x})),
      historyInfo:{limit:HISTORY_LIMIT,retained:r.history.length,truncated:typeof r.historyTruncated==="boolean"?r.historyTruncated:null}
    };
  }
  function state(sim) {
    try {const r=record(sim);return {ok:true,state:summary(sim,r)};} catch(error) {return {ok:false,reason:"unavailable",message:String(error.message||error).slice(0,240)};}
  }
  function configure(sim,arg) {
    try {
      const r=record(sim);let patch=arg;
      if(typeof arg==="string") {
        if(arg.length>256) throw Error("Settings must be at most 256 characters.");
        const text=arg.trim();
        if(text.startsWith("{")) patch=JSON.parse(text);
        else {
          const parts=text.split(/\s+/);if(!["on","off"].includes(parts[0])||parts.length>5) throw Error("Use on/off, monthly budget, cash reserve, utilization target, and network types.");
          patch={enabled:parts[0]==="on"};
          ["budget","reserve","target"].forEach((key,i)=>{if(parts[i+1]!==undefined) patch[key]=Number(parts[i+1]);});
          if(parts[4]!==undefined) patch.kinds=parts[4].split(",");
        }
      }
      if(!patch||typeof patch!=="object"||Array.isArray(patch)||Object.keys(patch).some(k=>!["enabled","budget","reserve","target","kinds","landPolicy"].includes(k))) throw Error("Invalid autopilot settings.");
      const next={...r.config,...patch};
      if(typeof next.enabled!=="boolean") throw Error("Enabled must be true or false.");
      if(!["demand","expand"].includes(next.landPolicy)) throw Error("Choose build when needed or keep expanding for roads and railways.");
      for(const [key,min,max] of [["budget",0,1e6],["reserve",0,1e9],["target",.5,.95]]) if(typeof next[key]!=="number"||!Number.isFinite(next[key])||next[key]<min||next[key]>max) throw Error(`Invalid ${key} setting.`);
      if(!Array.isArray(next.kinds)||next.kinds.length>KINDS.length||next.kinds.some(k=>!KINDS.includes(k))) throw Error("Choose roads, freeways, railways, gas pipelines, oil pipelines, or ports.");
      next.kinds=KINDS.filter(k=>next.kinds.includes(k));r.config=next;
      status(sim,r,next.enabled?"ready":"disabled",next.enabled?"Autopilot is ready to review transport demand.":"Autopilot is off. Existing construction continues.");
      return {ok:true,state:summary(sim,r)};
    } catch(error) {return {ok:false,reason:"settings",message:String(error.message||error).slice(0,240)};}
  }
  function owned(sim,key,provinceKey) {
    const province=sim.world?.provByKey?.get?.(String(provinceKey));
    // The engine's authoritative quote also checks ownership and occupation. Do not infer ownership from geometry fields.
    if(province?.__occupier&&String(province.__occupier)!==key) return false;
    return !!province;
  }
  function surfaceCandidates(sim,r,v) {
    const result=[];
    for(const p of v.rows) {
      if(!owned(sim,v.key,p.provinceKey)||p.factor<1-EPS||!(p.scale>0)) continue;
      const roadRoom=Math.max(0,positive(p.roadCeilingKm)-positive(p.roadKm)-p.pending.road-p.pending.motorway);
      for(const kind of r.config.kinds.filter(k=>SURFACE_KINDS.includes(k))) {
        if(p.pending[kind]>EPS) continue;
        const room=Math.max(0,Math.min(positive(p[`${kind}CeilingKm`])-positive(p[`${kind}Km`])-p.pending[kind],kind==="road"||kind==="motorway"?roadRoom:Infinity));
        const gain=WEIGHT[kind]*positive(kind==="rail"?p.railCondition:p.roadCondition)*p.scale*p.factor;
        if(room<1||!(gain>0)) continue;
        const km=Math.min(5000,room,r.config.landPolicy==="expand"?room:v.demand.additionalCapacity/gain);
        if(km<1-EPS) continue;
        const development=(positive(p[`${kind}Km`])+p.pending[kind])/Math.max(1,positive(p[`${kind}CeilingKm`]));
        result.push({p,kind,gain,km:Math.floor((km+EPS)*10)/10,development,efficiency:gain/({road:.0014,motorway:.011,rail:.0085}[kind])});
      }
    }
    return result;
  }
  function candidates(sim,r,v) {
    const result=v.landCandidates.slice();
    // Bound quote work without allowing a cheaper mode to be crowded out by
    // hundreds of less-developed provinces in another mode. Rotate each mode's
    // window so native refusals near the front cannot starve later provinces.
    result.sort((a,b)=>Math.floor(a.development*10)-Math.floor(b.development*10)||b.efficiency-a.efficiency||a.development-b.development||String(a.p.provinceKey).localeCompare(String(b.p.provinceKey))||KINDS.indexOf(a.kind)-KINDS.indexOf(b.kind));
    if(!r.candidateCursors||typeof r.candidateCursors!=="object"||Array.isArray(r.candidateCursors)) r.candidateCursors={};
    // An unavailable or unaffordable kind must not block later kinds. Move the
    // starting kind only after a successful land purchase, and persist it.
    const cursor=Math.floor(positive(r.landKindCursor))%SURFACE_KINDS.length;
    const kinds=r.config.landPolicy==="expand"?SURFACE_KINDS.slice(cursor).concat(SURFACE_KINDS.slice(0,cursor)):r.config.kinds;
    const groups=kinds.map(kind=>({kind,items:result.filter(c=>c.kind===kind)})).filter(group=>group.items.length);
    const perKind=Math.floor(192/Math.max(1,groups.length)),windows=groups.map(({kind,items})=>{
      const start=Math.floor(positive(r.candidateCursors[kind]))%items.length,count=Math.min(perKind,items.length);
      r.candidateCursors[kind]=(start+count)%items.length;
      return Array.from({length:count},(_,i)=>items[(start+i)%items.length]);
    });
    const selected=[];
    for(let i=0;i<perKind;i++) for(const window of windows) if(window[i]) selected.push(window[i]);
    return selected;
  }
  function findQuote(sim,c,v) {
    const month=v.budget.monthlyAvailable,cash=v.budget.cashAvailable;
    const quote=km=>sim.transportProjectQuote({projectType:"surface_program",terrainId:"plain",provinceKey:String(c.p.provinceKey),surfaceKind:c.kind,lengthKm:km});
    // Native procurement rounds totalCost upward to 1e-8 billion currency units.
    const committedCost=q=>Math.ceil(q.costBn*1e8)/1e8;
    const fits=q=>q?.ok&&Number.isFinite(q.costBn)&&q.costBn>0&&Number.isFinite(q.months)&&q.months>0&&committedCost(q)/q.months<=month+1e-12&&committedCost(q)<=cash+1e-12&&q.plan?.surfaceProgram?.surfaceKind===c.kind&&String(q.plan.surfaceProgram.provinceKey)===String(c.p.provinceKey)&&Number.isFinite(q.plan.surfaceProgram.lengthKm)&&q.plan.surfaceProgram.lengthKm>=1&&q.plan.surfaceProgram.lengthKm<=5000&&(v.expansion.enabled||q.plan.surfaceProgram.lengthKm*c.gain<=v.demand.additionalCapacity+EPS);
    let high=c.km,q=quote(high);if(fits(q)) return q;
    // Native financing, ceilings, and material prices can all change the quote. Search only valid small projects.
    let low=1,best=null;q=quote(low);if(!fits(q)) return null;best=q;
    for(let i=0;i<16&&high-low>=.1;i++) {const mid=Math.floor((low+high)*5)/10;if(mid<=low) break;q=quote(mid);if(fits(q)){best=q;low=mid;}else high=mid;}
    return best;
  }
  function findNativeQuote(sim,c,v) {
    let unaffordable=null;
    const quote=spec=>{
      const q=sim.transportProjectQuote(spec);
      if(!q?.ok||!Number.isFinite(q.costBn)||q.costBn<=0||!Number.isFinite(q.months)||q.months<=0) return null;
      const cost=Math.ceil(q.costBn*1e8)/1e8;
      if(cost>v.budget.cashAvailable+1e-12) {unaffordable="reserve";return null;}
      if(cost/q.months>v.budget.monthlyAvailable+1e-12) {unaffordable="budget";return null;}
      return {spec,quote:q};
    };
    let best=quote(c.spec);
    if(best) return {best};
    if(!c.minimumSpec) return {best:null,unaffordable};
    best=quote(c.minimumSpec);
    if(!best||typeof c.specForScale!=="function") return {best,unaffordable};
    let low=0,high=1;
    for(let i=0;i<16;i++) {
      const fraction=(low+high)/2,spec=c.specForScale(fraction);
      if(!spec) {high=fraction;continue;}
      const next=quote(spec);
      if(next) {best=next;low=fraction;} else high=fraction;
    }
    return {best,unaffordable};
  }
  function deliveryStatusValue(v) {
    const reasons={target_capacity_sufficient:"The reported capacity already covers the utilization target; no expansion is needed for this slot.",expandable:"An owned expansion can improve deliveries.",expand_surface:"More land capacity can improve deliveries.",foreign_controlled:"This capacity is controlled by another country.",foreign_serial_capacity:"A foreign section limits this route; expanding your section alone cannot improve delivery.",serial_upgrade_required:"Several sections limit this route and need coordinated upgrades; automation will not buy an ineffective partial upgrade.",demand_covered_by_queue:"Queued construction covers the current capacity shortfall.",queued:"Construction is already queued for this bottleneck.",queued_shared_route:"Queued construction uses this route's remaining headroom; waiting for completion and a new routing report.",no_route:"No usable transport route is available.",missing_terminal:"A required terminal is missing.",unavailable_route:"No usable transport route is available.",selected_route_unavailable:"Waiting for a reliable route forecast before authorizing construction.",unsupported_capacity_slot:"No supported construction remedy is available for this bottleneck.",unsupported_mode:"This transport mode has no supported expansion."};
    const blockers=v.delivery.blockers,detail=blockers.slice(0,3).map(b=>{
      const reason=String(b.reason||""),explanation=(reason==="queued_shared_route"&&b.queueEvidence==="unmapped"?"A queued project cannot be matched to the current routing report; waiting for completion or a new report.":reasons[reason])||(reason.startsWith("native_rejected:")?"The game cannot approve an expansion at this site.":reason.includes(" ")&&!reason.includes("_")?reason:"No eligible expansion is available.");
      const label=String(b.label||"");
      return `${label.includes(" ")?label:"Transport bottleneck"}: ${explanation}`;
    }).join(" ");
    const queued=v.active.length?` ${v.active.length} transport project${v.active.length===1?" is":"s are"} queued; delivery has not yet improved.`:"";
    const selection=v.unselectedKinds.length?` Enable ${v.unselectedKinds.map(k=>({gas_pipeline:"gas pipelines",oil_pipeline:"oil pipelines",port:"ports"}[k]||k)).join(", ")} to allow the available owned remedies.`:"";
    const code=blockers.length&&blockers.every(b=>b.scope==="foreign"||b.scope==="external")?"external":"partial";
    return {code,message:`${(v.delivery.rate*100).toFixed(4)}% of transport demand is fulfilled.${detail?" "+detail:" Remaining demand has no eligible selected construction remedy in this review."}${selection}${queued}`};
  }
  function deliveryStatus(sim,r,v) {
    const value=deliveryStatusValue(v);status(sim,r,value.code,value.message);
  }
  function perform(sim,r) {
    let v=inspect(sim,r);
    if(!r.config.enabled) {status(sim,r,"disabled","Autopilot is off. Existing construction continues.");return v;}
    if(typeof sim.playerControlsGovernment==="function"&&!sim.playerControlsGovernment(v.key)) {status(sim,r,"authority","Automatic construction is paused while you do not control the government.");return v;}
    if(!v.demand.calibrated) {status(sim,r,"calibrating","Waiting for the first consolidated transport demand report.");return v;}
    if(r.startedDay!==sim.day) {r.startedDay=sim.day;r.startedToday=0;}
    let started=0,rejected=false,unaffordable=null;
    while(r.startedToday<MAX_STARTS&&v.active.length<MAX_ACTIVE&&(v.nativeCandidates.length||v.landCandidates.length)&&v.budget.monthlyAvailable>EPS&&v.budget.cashAvailable>EPS&&r.config.kinds.length) {
      let added=false;
      const landCandidates=candidates(sim,r,v);
      for(const c of [...v.nativeCandidates,...landCandidates]) {
        let spec;
        if(c.spec) {const native=findNativeQuote(sim,c,v);unaffordable=native.unaffordable||unaffordable;if(!native.best) continue;spec=native.best.spec;}
        else {const q=findQuote(sim,c,v);if(!q) continue;spec={projectType:"surface_program",terrainId:"plain",provinceKey:String(c.p.provinceKey),surfaceKind:c.kind,lengthKm:q.plan.surfaceProgram.lengthKm};}
        const result=sim.startTransportProject(spec);
        if(!result?.ok) {rejected=true;continue;}
        const project=result.project;
        if(!project||!(sim.construction||[]).includes(project)) throw Error("Construction started without a discoverable project record.");
        project.transportAutobuild=true;
        project.transportAutobuildKind=c.kind;
        if(v.expansion.enabled&&SURFACE_KINDS.includes(c.kind)) r.landKindCursor=(SURFACE_KINDS.indexOf(c.kind)+1)%SURFACE_KINDS.length;
        if(c.label) project.transportAutobuildLabel=String(c.label);
        const plan=project.transportPlan?.surfaceProgram;
        r.history.push({day:positive(sim.day),id:String(project.id),kind:String(plan?.surfaceKind||c.kind),label:String(c.label||""),targetId:String(c.targetId||project.targetId||""),provinceKey:String(plan?.provinceKey||c.p?.provinceKey||""),km:positive(plan?.lengthKm),cost:positive(project.totalCost),monthly:positive(project.totalCost)/Math.max(1,positive(project.monthsTotal))});
        if(r.history.length>HISTORY_LIMIT) r.historyTruncated=true;
        r.history=r.history.slice(-HISTORY_LIMIT);
        r.lastRun.started++;r.lastRun.cost+=positive(project.totalCost);r.lastRun.monthlyCost+=positive(project.totalCost)/Math.max(1,positive(project.monthsTotal));
        r.startedToday++;started++;added=true;v=inspect(sim,r);break;
      }
      if(!added) break;
    }
    if(started) status(sim,r,"building",`Started ${started} transport project${started===1?"":"s"}. Current demand fulfillment is ${(v.delivery.rate*100).toFixed(4)}%; queued capacity is included in future reviews.`);
    else if(!v.expansion.enabled&&v.delivery.fulfilled&&v.demand.additionalCapacity<=EPS&&!v.nativeCandidates.length) status(sim,r,"sufficient","Transport demand is fulfilled. Current land capacity plus any queued capacity covers the utilization target.");
    else if(v.budget.monthlyAvailable<=EPS) status(sim,r,"budget","The monthly construction limit is committed. Existing projects continue.");
    else if(v.budget.cashAvailable<=EPS) status(sim,r,"reserve","Cash is reserved for the treasury minimum and existing construction.");
    else if(v.active.length>=MAX_ACTIVE||r.startedToday>=MAX_STARTS) status(sim,r,"batch","The construction batch limit is reached. Autopilot will review again tomorrow.");
    else if(unaffordable) status(sim,r,unaffordable,unaffordable==="budget"?"An eligible transport remedy exceeds the remaining monthly construction budget.":"An eligible transport remedy exceeds cash available after the treasury reserve and existing commitments.");
    else if(!v.delivery.fulfilled&&!rejected) deliveryStatus(sim,r,v);
    else if(v.expansion.enabled&&!v.landCandidates.length&&!rejected) status(sim,r,"waiting","No selected land expansion is eligible right now. Existing projects continue.");
    else status(sim,r,rejected?"rejected":"waiting",rejected?"The game declined new construction. Existing projects continue.":"No eligible project fits the remaining demand, budget, and cash limits.");
    return v;
  }
  function run(sim) {
    let r;
    try {
      r=record(sim);r.reviewDay=positive(sim.day);r.lastRun={day:positive(sim.day),started:0,cost:0,monthlyCost:0,ok:true};
      const view=perform(sim,r);r.reviewStatus={...r.status};r.reviewStateKey=reviewStateKey(sim,r,view);
      return {ok:true,state:summary(sim,r,view)};
    }
    catch(error) {if(r?.lastRun) r.lastRun.ok=false;if(r) {status(sim,r,"error","Autopilot paused this review after a construction error.",error.message||error);r.reviewStatus={...r.status};}let current;try {if(r) {const view=inspect(sim,r);r.reviewStateKey=reviewStateKey(sim,r,view);current=summary(sim,r,view);}}catch(_){}return {ok:false,reason:"runtime",message:String(error.message||error).slice(0,240),...(r?.lastRun?{lastRun:{...r.lastRun}}:{}),...(current?{state:current}:{})};}
  }
  function tick(sim) {
    // Never allow optional automation to fault the host simulation day.
    try {const r=record(sim);if(r.lastReviewDay===sim.day) return;r.lastReviewDay=sim.day;if(r.config.enabled) run(sim);} catch(error) {try {status(sim,record(sim),"error","Autopilot paused this review after a construction error.",error.message||error);} catch(_) {}}
  }
  return {state,configure,run,tick};
})();
globalThis.__geoTransportRuntime = __geoTransportRuntime;

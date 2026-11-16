/* Injected inside the native simulation closure. Quotes and forecasts never change the world. */
var __geoTransportAdapter = (() => {
  "use strict";
  const EPS=1e-8, cache=new WeakMap();
  const num=(x,fallback=0)=>Number.isFinite(Number(x))?Number(x):fallback;
  const pos=x=>Math.max(0,num(x));
  const keyOf=x=>x==null?null:String(typeof x==="object"?(x.key??x.id??x.actorId):x);
  const actorName=(sim,key)=>key==null?"Shared route":String(sim.countries?.get?.(String(key))?.name||key);
  const portZone=a=>a?.maritimeZoneId?String(a.maritimeZoneId):xce(AG(a?.provinceKey,[num(a?.lon),num(a?.lat)]),num(a?.lon),num(a?.lat));
  const transportProjects=(sim,key)=>(sim.construction||[]).filter(p=>p?.kind==="transport"&&String(p.owner)===key&&p.deliveryApplied!==true);
  const capacity=(kind,item,mode)=>{const m=kind==="corridor"?(item.modes||[]).find(m=>(m.id||m)===mode):null;return pos(Rs(kind,item,mode,m));};
  const corridorFactor=(c,mode)=>{const m=(c.modes||[]).find(m=>(m.id||m)===mode);return pos(m?.capacity)>0?capacity("corridor",c,mode)/pos(m.capacity):0;};
  // Native Bie also considers other segments of the same pipeline. Keep an additional
  // direct ownership/hosting guard so a foreign sibling cannot become an automatic target.
  const controlledCorridor=(c,key)=>String(c.actorId)===key||(c.hostActorIds||[]).map(String).includes(key);
  const sameSite=(a,b)=>!!a&&!!b&&String(a.provinceKey||"")===String(b.provinceKey||"")&&Math.abs(num(a.lon)-num(b.lon))<1e-5&&Math.abs(num(a.lat)-num(b.lat))<1e-5;
  const surfaceCache=(sim,view,demands,solve,key)=>{
    const prior=cache.get(sim),epoch=sim.transport?.network?.epoch;
    if(prior?.demands===demands&&prior?.solve===solve&&prior?.epoch===epoch&&prior?.key===key)return prior.requested;
    const slot=view.actors.get(key)?.slots?.land;
    if(slot==null)return undefined;
    let requested=0;const access=vve(),vectors=solve.resultVectors;
    for(let i=0;i<demands.length;i++) {
      const load=pos(vectors?.requestedLoad?.[i]);if(!(load>0))continue;
      const plan=Vwe(sim,view,demands[i],access);
      if(plan?.available&&plan.slots.includes(slot))requested+=load;
    }
    cache.set(sim,{demands,solve,epoch,key,requested});return requested;
  };
  function inspect(sim,options={}) {
    const key=String(sim.playerKey),tr=sim.transport||{},network=tr.network;
    const empty={delivery:{ready:false,rate:null,requestedLoad:0,deliveredLoad:0,unmetLoad:0,fulfilled:false,blockers:[]},candidates:[]};
    if(!network||!tr.lastSolve)return empty;
    const reportedDemands=tr.lastDemands,reportedVectors=tr.lastSolve.resultVectors;
    if(!Array.isArray(reportedDemands)||!reportedVectors)return empty;
    const validVector=values=>{
      if(values==null||typeof values!=="object"||!Number.isInteger(values.length)||values.length<reportedDemands.length)return false;
      // An indexed loop checks sparse array holes as well as typed-array entries.
      for(let i=0;i<reportedDemands.length;i++)if(typeof values[i]!=="number"||!Number.isFinite(values[i])||values[i]<0)return false;
      return true;
    };
    if(!validVector(reportedVectors.requestedLoad)||!validVector(reportedVectors.deliveredLoad))return empty;
    const target=Math.max(.5,Math.min(.95,num(options.target,.8))),solve=tr.lastSolve,demands=tr.lastDemands||[],vectors=solve.resultVectors||{};
    const view=sE(sim),slots=view.slots,slotById=new Map(slots.map(s=>[`${s.kind}|${s.id}|${s.mode}`,s]));
    const assets=new Map((network.assets||[]).map(a=>[String(a.id),a])),corridors=new Map((network.corridors||[]).map(c=>[String(c.id),c]));
    const projects=transportProjects(sim,key),groups=new Map();
    let requested=0,delivered=0;
    const involvement=d=>Number(keyOf(d?.exporterActorId??d?.actorId)===key)+Number(keyOf(d?.importerActorId)===key);
    // Actor statistics deliberately count both endpoints for domestic requests, as
    // does the game's delivery display. Capacity-slot requests below count each once.
    for(let i=0;i<demands.length;i++) {const weight=involvement(demands[i]);if(!weight)continue;requested+=pos(vectors.requestedLoad?.[i])*weight;delivered+=pos(vectors.deliveredLoad?.[i])*weight;}
    const stats=tr.statsByActor?.[key];
    if(stats&&typeof stats.requestedLoad==="number"&&Number.isFinite(stats.requestedLoad)&&stats.requestedLoad>=0&&typeof stats.deliveredLoad==="number"&&Number.isFinite(stats.deliveredLoad)&&stats.deliveredLoad>=0) {requested=pos(stats.requestedLoad);delivered=Math.min(requested,pos(stats.deliveredLoad));}
    for(const constraint of solve.resultConstraints||[]) {
      const i=constraint.index,d=demands[i],weight=involvement(d);if(!weight)continue;
      const lost=Math.max(0,pos(vectors.requestedLoad?.[i])-pos(vectors.deliveredLoad?.[i]))*weight;if(!(lost>0))continue;
      const b=constraint.bottleneck||{},id=String(b.id||`${constraint.causeId}:${d?.goodId||"route"}`),mode=String(b.mode||d?.mode||"unknown"),groupKey=`${constraint.causeId}|${b.kind||""}|${id}|${mode}`;
      let g=groups.get(groupKey);
      if(!g) {g={id,mode,kind:String(b.kind||""),cause:String(constraint.causeId||"no_route"),ownerKey:keyOf(b.actorId),unmetLoad:0,capacity:pos(b.capacity),requestedLoad:pos(b.requestedLoad),load:pos(b.load),indices:[],scope:"route",actionable:false,reason:"unavailable_route",label:id,slot:slotById.get(`${b.kind}|${id}|${mode}`)};groups.set(groupKey,g);}
      g.indices.push(i);g.unmetLoad+=lost;g.requestedLoad=Math.max(g.requestedLoad,pos(b.requestedLoad));g.capacity=Math.max(g.capacity,pos(b.capacity));
    }
    const pendingCorridor=new Map();
    for(const p of projects) if(["extension","upgrade"].includes(p.projectType)) {const plan=p.transportPlan||{},id=`${plan.targetId||p.targetId}|${plan.modeId}`;pendingCorridor.set(id,pos(pendingCorridor.get(id))+pos(plan.capacityAdd));}
    // Keep project evidence tied to the same slots used by the capacity forecast.
    const projectsForSlot=slot=>{
      if(!slot)return [];
      if(slot.kind==="corridor") {
        const ids=new Set((slot.members?slot.members.flatMap(m=>m.slots).map(i=>slots[i]):[slot]).map(s=>String(s.id)));
        return projects.filter(p=>["extension","upgrade"].includes(p.projectType)&&ids.has(String(p.transportPlan?.targetId||p.targetId))&&p.transportPlan?.modeId===slot.mode&&pos(p.transportPlan?.capacityAdd)>0);
      }
      if(slot.kind!=="asset"||String(slot.ownerActorId)!==key)return [];
      const ids=new Set((slot.sourceAssetIds||[]).map(String));
      return projects.filter(p=>{
        const plan=p.transportPlan||{};
        if(p.projectType==="new_asset"&&plan.asset?.assetClass==="port"&&String(plan.asset.actorId||p.owner)===key&&portZone(plan.asset)===String(slot.id).split(":").at(-1))return slot.mode==="sea"||slot.mode.endsWith("_terminal")&&!String(slot.id).includes(":gas_export:");
        return p.projectType==="terminal_upgrade"&&ids.has(String(plan.targetId||p.targetId))&&slot.mode===`${plan.fluid}_terminal`;
      });
    };
    const projectIds=items=>[...new Set(items.filter(p=>p.id!=null&&String(p.id)!=="").map(p=>String(p.id)))];
    const projectedSlot=(slot)=>{
      if(!slot)return 0;
      if(slot.members)return slot.members.reduce((sum,m)=>sum+Math.min(...m.slots.map(index=>{const s=slots[index],c=corridors.get(String(s.id));return pos(s.capacity)+pos(pendingCorridor.get(`${s.id}|${slot.mode}`))*corridorFactor(c||{},slot.mode);})),0);
      if(slot.kind==="corridor") {const c=corridors.get(String(slot.id));return pos(slot.capacity)+pos(pendingCorridor.get(`${slot.id}|${slot.mode}`))*corridorFactor(c||{},slot.mode);}
      let forecast=pos(slot.capacity);
      const sourceIds=new Set(slot.sourceAssetIds||[]);
      for(const p of projects) {
        const plan=p.transportPlan||{};
        if(p.projectType==="new_asset"&&slot.kind==="asset"&&String(slot.ownerActorId)===key&&plan.asset?.assetClass==="port"&&String(plan.asset.actorId||p.owner)===key&&portZone(plan.asset)===String(slot.id).split(":").at(-1)) {
          const sea=pos(plan.asset.capacityByMode?.sea);
          if(slot.mode==="sea")forecast+=sea;
          else if(slot.mode.endsWith("_terminal")&&!String(slot.id).includes(":gas_export:"))forecast+=sea*.8;
        }
        if(p.projectType==="terminal_upgrade"&&sourceIds.has(String(plan.targetId||p.targetId))&&slot.mode===`${plan.fluid}_terminal`) {
          const asset=assets.get(String(plan.targetId||p.targetId));if(!asset)continue;
          const copy=rp(asset);Fwe(sim,copy,plan.fluid,plan.toLevel,{source:"constructed"});
          forecast+=Math.max(0,capacity("asset",copy,`${plan.fluid}_pipeline`)-capacity("asset",asset,`${plan.fluid}_pipeline`));
        }
      }
      return forecast;
    };
    const routeAccess=vve(),routesByGroup=new Map();
    const slotUsed=slot=>{
      if(slot.kind==="corridor") {
        if(slot.members) {
          const loads=slot.members.map(m=>m.slots.map(i=>slotUsed(slots[i])));
          return loads.some(a=>a.some(n=>n==null))?null:loads.reduce((sum,a)=>sum+Math.min(...a),0);
        }
        if(!Array.isArray(solve.corridorLoads))return null;
        return solve.corridorLoads.filter(r=>String(r.corridorId)===String(slot.id)&&r.mode===slot.mode).reduce((sum,r)=>sum+pos(r.load),0);
      }
      if(slot.kind==="asset") {
        if(!Array.isArray(solve.assetLoads))return null;
        const ids=new Set(slot.sourceAssetIds||[]);
        // Terminal receipts can combine transfer/export claims for the same
        // asset and mode. Their sum is a conservative upper bound on slot use.
        return solve.assetLoads.filter(r=>ids.has(String(r.assetId))&&r.mode===slot.mode).reduce((sum,r)=>sum+pos(r.load),0);
      }
      if(slot.kind==="passage") {
        if(!Array.isArray(solve.passageLoads))return null;
        return solve.passageLoads.filter(r=>String(r.passageId)===String(slot.id)&&r.mode===slot.mode).reduce((sum,r)=>sum+pos(r.load),0);
      }
      return null;
    };
    let pendingClaims;
    const sharedPendingClaims=()=>{
      if(pendingClaims)return pendingClaims;
      pendingClaims={claims:[],unknown:false,unknownProjectIds:[]};
      const queuedBySlot=new Map(),queued=new Set();
      const tagSlot=(index,tag)=>{const tags=queuedBySlot.get(index)||new Set();tags.add(tag);queuedBySlot.set(index,tags);queued.add(tag);};
      for(let index=0;index<slots.length;index++) {
        const source=slots[index];
        if(source.kind==="corridor") {
          const members=source.members?source.members.flatMap(m=>m.slots).map(j=>slots[j]):[source];
          for(const raw of members) {const tag=`${raw.id}|${raw.mode}`;if(pendingCorridor.has(tag))tagSlot(index,tag);}
        }
        if(source.kind!=="asset"||String(source.ownerActorId)!==key)continue;
        for(const project of projects) {
          const plan=project.transportPlan||{};
          if(project.projectType==="new_asset"&&plan.asset?.assetClass==="port"&&portZone(plan.asset)===String(source.id).split(":").at(-1)&&(source.mode==="sea"||source.mode.endsWith("_terminal")&&!String(source.id).includes(":gas_export:")))tagSlot(index,`port:${project.id||project.targetId}`);
          if(project.projectType==="terminal_upgrade"&&source.mode===`${plan.fluid}_terminal`&&(source.sourceAssetIds||[]).includes(String(plan.targetId||project.targetId)))tagSlot(index,`terminal:${project.id||project.targetId}:${plan.fluid}`);
        }
      }
      for(const tag of pendingCorridor.keys())queued.add(tag);
      for(const project of projects) {
        if(project.projectType==="new_asset"&&project.transportPlan?.asset?.assetClass==="port")queued.add(`port:${project.id||project.targetId}`);
        if(project.projectType==="terminal_upgrade")queued.add(`terminal:${project.id||project.targetId}:${project.transportPlan?.fluid}`);
      }
      if(!queued.size)return pendingClaims;
      const matched=new Set();
      for(let i=0;i<demands.length;i++) {
        if(!involvement(demands[i])||!(pos(vectors.requestedLoad?.[i])>0))continue;
        const plan=Vwe(sim,view,demands[i],routeAccess);if(!plan?.available)continue;
        for(const index of plan.slots||[]) {
          const targets=queuedBySlot.get(index);if(!targets?.size)continue;
          targets.forEach(id=>matched.add(id));
          pendingClaims.claims.push({source:index,pools:new Set(plan.slots.filter(j=>j!==index)),projectIds:projectIds(projectsForSlot(slots[index]))});
        }
      }
      // A queued route absent from the receipt cannot be forecast confidently.
      // Wait for completion/a new solve instead of spending against unknown use.
      pendingClaims.unknown=[...queued].some(id=>!matched.has(id));
      if(pendingClaims.unknown) {
        const matchedIds=new Set(pendingClaims.claims.flatMap(claim=>claim.projectIds));
        pendingClaims.unknownProjectIds=projectIds(projects.filter(p=>["extension","upgrade","terminal_upgrade"].includes(p.projectType)||p.projectType==="new_asset"&&p.transportPlan?.asset?.assetClass==="port")).filter(id=>!matchedIds.has(id));
      }
      return pendingClaims;
    };
    const routeHeadroom=(g,slot)=>{
      if(routesByGroup.has(g))return routesByGroup.get(g);
      const index=slots.indexOf(slot),pendingRoutes=sharedPendingClaims();let best=0,known=false;
      if(pendingRoutes.unknown) {g.sharedQueue=true;g.queueEvidence="unmapped";g.sharedProjectIds=new Set(pendingRoutes.unknownProjectIds);}
      for(const i of g.indices) {
        const plan=Vwe(sim,view,demands[i],routeAccess);
        if(!plan?.available||!plan.slots?.includes(index))continue;
        let room=Infinity,valid=true;
        for(const otherIndex of plan.slots) {
          if(otherIndex===index)continue;
          const other=slots[otherIndex],used=other&&slotUsed(other);
          if(used==null) {valid=false;continue;}
          for(const claim of pendingRoutes.claims.filter(claim=>claim.source!==index&&claim.pools.has(otherIndex))) {
            g.sharedQueue=true;g.queueEvidence=g.queueEvidence||"shared";
            if(!g.sharedProjectIds)g.sharedProjectIds=new Set();claim.projectIds.forEach(id=>g.sharedProjectIds.add(id));
          }
          const slack=Math.max(0,projectedSlot(other)-used);room=Math.min(room,slack);
          if(slack<=EPS) {
            const owned=other.kind==="corridor"?controlledCorridor(corridors.get(String(other.id))||{},key):String(other.ownerActorId)===key;
            if(owned)g.ownSerial=true;
            else {g.foreignSerial=true;g.scope="shared";}
          }
        }
        if(valid) {known=true;best=Math.max(best,room);}
      }
      if(!known)g.routeUnknown=true;
      if(g.sharedQueue)best=0;
      routesByGroup.set(g,best);return best;
    };
    const candidates=[],seenTargets=new Set();
    for(const g of groups.values()) {
      const slot=g.slot;
      g.projectIds=projectIds(projectsForSlot(slot));
      if(g.cause!=="capacity"||!slot) {g.reason=g.cause==="capacity"?"unsupported_capacity_slot":g.cause;continue;}
      const sourceCorridorSlots=slot.kind==="corridor"?(slot.members?slot.members.flatMap(m=>m.slots):[slots.indexOf(slot)]):[];
      const sourceCorridors=[...new Set(sourceCorridorSlots)].map(i=>corridors.get(String(slots[i]?.id))).filter(Boolean);
      const sourceAssets=(slot.sourceAssetIds||[]).map(id=>assets.get(String(id))).filter(Boolean);
      const owners=[...new Set((sourceCorridors.length?sourceCorridors:sourceAssets).map(x=>keyOf(x.actorId)).filter(Boolean))];
      const ownCorridors=sourceCorridors.filter(c=>controlledCorridor(c,key)),ownAssets=sourceAssets.filter(a=>String(a.actorId)===key);
      const hasOwn=ownCorridors.length||ownAssets.length||g.ownerKey===key;
      const allOwn=sourceCorridors.length?ownCorridors.length===sourceCorridors.length:sourceAssets.length?ownAssets.length===sourceAssets.length:g.ownerKey===key;
      g.scope=hasOwn?(allOwn?"owned":"shared"):"foreign";
      if(g.ownerKey==null&&allOwn)g.ownerKey=key;
      g.ownerName=g.ownerKey==null?owners.map(k=>actorName(sim,k)).join(" / ")||"Shared route":actorName(sim,g.ownerKey);
      const modeLabel={land:"Land transport",sea:"Port capacity",gas_pipeline:"Gas pipeline",oil_pipeline:"Oil pipeline",gas_terminal:"Gas terminal",oil_terminal:"Oil terminal"}[g.mode]||g.mode;
      g.label=`${modeLabel} — ${g.ownerName}`;
      const supportedCapacity=slot.kind==="corridor"&&["gas_pipeline","oil_pipeline"].includes(g.mode)||slot.kind==="asset"&&["sea","gas_terminal","oil_terminal"].includes(g.mode);
      const headroom=supportedCapacity?routeHeadroom(g,slot):Infinity;
      g.pendingCapacity=Math.min(Math.max(0,projectedSlot(slot)-pos(slot.capacity)),headroom);
      const need=Math.max(0,g.requestedLoad/target-pos(slot.capacity)-g.pendingCapacity);g.need=need;
      if(!hasOwn) {g.reason="foreign_controlled";continue;}
      if(need<=EPS) {g.reason=g.requestedLoad/target-pos(slot.capacity)>EPS&&g.pendingCapacity>EPS&&projectsForSlot(slot).length?"demand_covered_by_queue":"target_capacity_sufficient";if(g.reason==="demand_covered_by_queue")g.queueEvidence="direct";continue;}
      if(g.mode==="land"&&slot.kind==="asset"&&g.ownerKey===key) {g.actionable=true;g.reason="expand_surface";continue;}
      const before=candidates.length;
      if(["gas_pipeline","oil_pipeline"].includes(g.mode)&&slot.kind==="corridor") {
        for(const c of ownCorridors) {
          const targetKey=`${c.id}|${g.mode}`,pending=pos(pendingCorridor.get(targetKey));
          if(seenTargets.has(targetKey)||pending>EPS)continue;
          const factor=corridorFactor(c,g.mode);if(!(factor>0))continue;
          const tiers=vG[J0(g.mode)]||[];if(!tiers.length)continue;
          // A bundled path is limited by its lowest serial segment. Buying an
          // owned segment cannot improve delivery while an unchanged foreign
          // (or another unqueued owned) segment remains at the same minimum.
          const members=slot.members||[{slots:[slots.indexOf(slot)]}];
          let attainableGain=0,foreignSerial=false,ownSerial=false;
          for(const member of members) {
            const memberSlots=member.slots.map(i=>slots[i]);
            if(!memberSlots.some(s=>String(s.id)===String(c.id)))continue;
            const projected=s=>{const raw=corridors.get(String(s.id));return pos(s.capacity)+pos(pendingCorridor.get(`${s.id}|${g.mode}`))*corridorFactor(raw||{},g.mode);};
            const current=projected(memberSlots.find(s=>String(s.id)===String(c.id)));
            const others=memberSlots.filter(s=>String(s.id)!==String(c.id));
            const ceiling=others.length?Math.min(...others.map(projected)):Infinity;
            attainableGain=Math.max(attainableGain,Math.max(0,Math.min(need,ceiling-current)));
            if(ceiling<=current+EPS)for(const other of others.filter(s=>projected(s)<=current+EPS)) {
              if(controlledCorridor(corridors.get(String(other.id))||{},key))ownSerial=true;else foreignSerial=true;
            }
          }
          attainableGain=Math.min(attainableGain,Math.max(0,headroom-g.pendingCapacity));
          if(!(attainableGain>EPS)) {g.foreignSerial=g.foreignSerial||foreignSerial;g.ownSerial=g.ownSerial||ownSerial;continue;}
          const fullPhysical=mnt(g.mode,Sce(sim,g.mode,attainableGain/factor));
          const specForPhysical=physicalCapacityAdd=>({projectType:"extension",terrainId:"plain",targetId:String(c.id),modeId:g.mode,physicalCapacityAdd});
          const spec=specForPhysical(fullPhysical),q=kS(sim,key,spec);
          if(!q?.ok) {g.nativeReason=String(q?.reason||"unavailable");continue;}
          const rawGain=pos(q.plan?.capacityAdd);if(!(rawGain>0))continue;
          seenTargets.add(targetKey);
          const specForScale=fraction=>specForPhysical(mnt(g.mode,Math.max(tiers[0],fullPhysical*Math.max(0,Math.min(1,num(fraction))))));
          candidates.push({kind:g.mode,spec,targetId:String(c.id),label:`${modeLabel} — ${String(c.name||c.id)}`,blockerId:g.id,need,gain:rawGain*factor,slotGain:Math.min(rawGain*factor,attainableGain),pending,minimumSpec:specForPhysical(tiers[0]),specForScale});
        }
      } else if(slot.kind==="asset"&&["sea","gas_terminal","oil_terminal"].includes(g.mode)) {
        for(const port of ownAssets.filter(a=>a.assetClass==="port"&&a.active!==false)) {
          const remainingHeadroom=Math.max(0,headroom-g.pendingCapacity);if(!(remainingHeadroom>EPS))continue;
          if(g.mode!=="sea") {
            const fluid=g.mode.slice(0,-9),targetKey=`${port.id}|${fluid}_terminal`;
            if(seenTargets.has(targetKey)||projects.some(p=>p.projectType==="terminal_upgrade"&&String(p.targetId)===String(port.id)&&p.transportPlan?.fluid===fluid))continue;
            const spec={projectType:"terminal_upgrade",terrainId:"plain",targetId:String(port.id),fluid},q=kS(sim,key,spec);
            if(!q?.ok) {g.nativeReason=String(q?.reason||"unavailable");continue;}
            const copy=rp(port);Fwe(sim,copy,fluid,q.plan.toLevel,{source:"constructed"});
            const gain=Math.max(0,capacity("asset",copy,`${fluid}_pipeline`)-capacity("asset",port,`${fluid}_pipeline`));
            if(!(gain>EPS))continue;seenTargets.add(targetKey);
            candidates.push({kind:"port",spec,targetId:String(port.id),sourcePortId:String(port.id),label:`${fluid==="gas"?"Gas":"Oil"} terminal — ${String(port.name||port.id)}`,blockerId:g.id,need,gain,slotGain:Math.min(gain,remainingHeadroom),pending:0});
            continue;
          }
          const targetKey=`port-site|${port.provinceKey}|${port.lon}|${port.lat}`;
          if(seenTargets.has(targetKey)||projects.some(p=>p.projectType==="new_asset"&&p.transportPlan?.asset?.assetClass==="port"&&sameSite(port,p.transportPlan.asset)))continue;
          const country=sim.countries.get(key),site=M$(sim,country,num(port.lat),num(port.lon),{maxDistanceDeg:1e-5});
          if(!site?.ok||String(site.provKey)!==String(port.provinceKey)) {g.nativeReason=String(site?.reason||"site_not_coastal");continue;}
          const portSize=Math.max(1,Math.min(F7.port.standardPorts.length,F7.port.standardPorts.findIndex(n=>n*iN(network)>=Math.min(need,remainingHeadroom))+1||F7.port.standardPorts.length));
          const id=jJr(network,key,`autobuild:${port.id}`,projects);
          const specForSize=size=>({projectType:"new_asset",terrainId:"plain",portSize:size,asset:{id,assetClass:"port",lat:num(port.lat),lon:num(port.lon),provinceKey:String(port.provinceKey),maritimeZoneId:portZone(port),maritimeClassification:port.maritimeClassification,modes:["sea","land"],capabilities:["seaport","bulk_terminal","chemical_terminal","vehicle_terminal","heavy_lift","dry_storage"]}});
          const spec=specForSize(portSize),q=kS(sim,key,spec);
          if(!q?.ok) {g.nativeReason=String(q?.reason||"unavailable");continue;}
          const gain=pos(q.plan?.designCapacity);if(!(gain>EPS))continue;
          seenTargets.add(targetKey);
          candidates.push({kind:"port",spec,targetId:id,sourcePortId:String(port.id),label:`Port-site expansion — ${String(port.name||port.id)}`,blockerId:g.id,need,gain,slotGain:Math.min(gain,remainingHeadroom),pending:0,minimumSpec:specForSize(1),specForScale:fraction=>specForSize(Math.max(1,Math.ceil(portSize*Math.max(0,Math.min(1,num(fraction))))))});
        }
      }
      const queued=projectsForSlot(slot).length>0;
      g.actionable=candidates.length>before;
      g.reason=g.actionable?"expandable":g.foreignSerial?"foreign_serial_capacity":g.ownSerial?"serial_upgrade_required":g.sharedQueue?"queued_shared_route":g.routeUnknown?"selected_route_unavailable":headroom<=g.pendingCapacity+EPS&&g.pendingCapacity>EPS?"queued":queued?"queued":g.nativeReason?`native_rejected:${g.nativeReason}`:"unsupported_mode";
    }
    const allBlockers=[...groups.values()].sort((a,b)=>b.unmetLoad-a.unmetLoad||a.id.localeCompare(b.id)).map(g=>({id:g.id,label:g.label,mode:g.mode,ownerKey:g.ownerKey,ownerName:g.ownerName||actorName(sim,g.ownerKey),scope:g.scope,unmetLoad:g.unmetLoad,actionable:g.actionable,reason:g.reason,requestedLoad:g.requestedLoad,capacity:g.capacity,pendingCapacity:pos(g.pendingCapacity),projectIds:g.projectIds||[],sharedProjectIds:[...(g.sharedProjectIds||[])],queueEvidence:g.queueEvidence||(["queued","demand_covered_by_queue"].includes(g.reason)?"direct":null)}));
    const blockers=allBlockers.filter(b=>b.unmetLoad>EPS);
    const unmetLoad=Math.max(0,requested-delivered),delivery={ready:true,rate:requested>0?delivered/requested:1,requestedLoad:requested,deliveredLoad:delivered,unmetLoad,fulfilled:unmetLoad<=Math.max(EPS,requested*1e-9),blockers};
    const shortfallById=new Map(allBlockers.map(b=>[b.id,b.unmetLoad]));
    candidates.sort((a,b)=>pos(shortfallById.get(b.blockerId))-pos(shortfallById.get(a.blockerId))||a.targetId.localeCompare(b.targetId));
    return {delivery,landRequested:surfaceCache(sim,view,demands,solve,key),candidates};
  }
  return {inspect};
})();
globalThis.__geoTransportAdapter=__geoTransportAdapter;

'use strict';
const D=JSON.parse(document.getElementById('playback-data').textContent);
const $=id=>document.getElementById(id), H=D.humans, U=D.usage, UP=D.updates;
const A=D.accounting, requests=A.requests;
const turnById=new Map(A.turns.map(turn=>[turn.id,turn]));
const messages=[...H,...UP].sort((a,b)=>a.t-b.t||a.order-b.order);
const fmtTime=new Intl.DateTimeFormat('en-US',{timeZone:D.meta.timezone,hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
const fmtDate=new Intl.DateTimeFormat('en-US',{timeZone:D.meta.timezone,weekday:'long',month:'long',day:'numeric',year:'numeric'});
const fmtStamp=new Intl.DateTimeFormat('en-US',{timeZone:D.meta.timezone,month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
const fmtHour=new Intl.DateTimeFormat('en-US',{timeZone:D.meta.timezone,hour:'2-digit',minute:'2-digit',hour12:false});
const fmtZone=new Intl.DateTimeFormat('en-US',{timeZone:D.meta.timezone,timeZoneName:'short'});
const zone=t=>fmtZone.formatToParts(t).find(p=>p.type==='timeZoneName').value;
const stamp=t=>fmtStamp.format(t)+' '+zone(t);
let now=D.meta.start,lo=now,hi=D.meta.end,scopeLo=lo,scopeHi=hi,playing=false,lastFrame=0;
let humans=[],usage=[],marked=[],points=[],viewMessages=[],allMessages=[],selected=null,current=null,focusedId=null;
let timelineDrag=null,suppressTimelineClick=false,syncedScrollLeft=0;
const svgNS='http://www.w3.org/2000/svg',W=1100,X0=150,X1=1080;
function text(id,value){$(id).textContent=value;}
function make(tag,attrs={},value){const el=document.createElementNS(svgNS,tag);for(const [k,v]of Object.entries(attrs))el.setAttribute(k,String(v));if(value!==undefined)el.textContent=value;return el;}
function preview(value,n=120){const v=value.replace(/<\/?pasted_content[^>]*>/g,'').replace(/\s+/g,' ').trim();return v.length>n?v.slice(0,n)+'…':v;}
function lastAt(rows,t){let a=0,b=rows.length;while(a<b){const m=(a+b)>>1;if(rows[m].t<=t)a=m+1;else b=m;}return a-1;}
function harnessMatch(row){return $('harness').value==='all'||row.tool===$('harness').value;}
function scoped(row){return row&&row.t>=lo&&row.t<=hi&&harnessMatch(row);}
function scopeMatch(row){return row.t>=scopeLo&&row.t<=scopeHi&&harnessMatch(row);}
function sameConversation(a,b){return a.tool===b.tool&&a.session===b.session;}
function pause(){playing=false;text('play','▶ Play');$('play').setAttribute('aria-label','Play timeline');text('play-state','PAUSED');$('play-state').classList.remove('playing');}
function rebuild(reset=true){
  if(D.meta.start===null)return;
  const day=D.days.find(d=>d.date===$('day').value);
  scopeLo=day?day.start:D.meta.start;scopeHi=day?day.end:D.meta.end;
  if(reset){lo=scopeLo;hi=scopeHi;}
  humans=H.filter(scoped);usage=U.filter(scoped);marked=humans.filter(h=>h.moment);
  viewMessages=messages.filter(scoped);allMessages=messages.filter(scopeMatch);
  points=[...new Set([...humans,...usage,...viewMessages].map(r=>r.t))].sort((a,b)=>a-b);
  now=Math.max(lo,Math.min(hi,now));
  text('range-start',stamp(lo));text('range-end',stamp(hi));
  text('marker-count',marked.length+' marked inputs');
  text('zoom-status',`${(Math.max(1,scopeHi-scopeLo)/Math.max(1,hi-lo)).toFixed(1)}× · ${stamp(lo)} – ${stamp(hi)}`);
  $('zoom-in').disabled=hi-lo<=1000;
  $('zoom-out').disabled=$('zoom-reset').disabled=lo===scopeLo&&hi===scopeHi;
  $('pan-prev').disabled=lo<=scopeLo;$('pan-next').disabled=hi>=scopeHi;
  buildMoments();buildClusters();render();drawTimeline();renderEvents();syncTimelineScrollbar();renderUsage();
}
function jump(t){pause();focusedId=null;now=Math.max(lo,Math.min(hi,t));text('gap-status','');render();}
function jumpMessage(row){
  if(row.t===null){$('undated-select').value=row.id;showUndated();return;}
  pause();
  if(!scopeMatch(row)){$('day').value='all';$('harness').value='all';now=row.t;rebuild();}
  if(row.t<lo||row.t>hi){const width=hi-lo;lo=Math.max(scopeLo,Math.min(scopeHi-width,row.t-width/2));hi=Math.min(scopeHi,lo+width);}
  now=row.t;focusedId=row.id;rebuild(false);
}
function stepInput(direction){const candidate=direction>0?H.filter(scopeMatch).find(h=>h.t>now):H.filter(scopeMatch).reverse().find(h=>h.t<now);if(candidate)jumpMessage(candidate);}
function stepText(direction){
  if(current?.t===null){const index=D.undated.findIndex(r=>r.id===current.id);const row=D.undated[index+direction];if(row)jumpMessage(row);return;}
  const index=current?allMessages.findIndex(r=>r.id===current.id):-1;
  const candidate=allMessages[index+direction];if(candidate)jumpMessage(candidate);
}
function nextIdea(){const rows=H.filter(scopeMatch).filter(h=>h.moment);const h=rows.find(h=>h.t>now)||rows[0];if(h)jumpMessage(h);}
function play(){
  if(playing){pause();return;}if(now>=hi){now=lo;focusedId=null;}
  playing=true;text('play','Ⅱ Pause');$('play').setAttribute('aria-label','Pause timeline');text('play-state','PLAYING');$('play-state').classList.add('playing');
  lastFrame=performance.now();requestAnimationFrame(tick);
}
function tick(frame){
  if(!playing)return;const elapsed=Math.min(1000,frame-lastFrame);lastFrame=frame;
  let target=now+elapsed*Number($('speed').value)*60;
  if($('skip').checked){const next=points.find(t=>t>now);if(next!==undefined&&next-now>30*60000){target=next;text('gap-status','Skipped an unobserved gap');}else text('gap-status','');}
  focusedId=null;now=Math.min(hi,target);render();if(now>=hi)pause();else requestAnimationFrame(tick);
}
function zoom(factor){
  pause();const width=Math.min(scopeHi-scopeLo,Math.max(1000,(hi-lo)*factor));
  lo=Math.max(scopeLo,Math.min(scopeHi-width,now-width/2));hi=lo+width;rebuild(false);
}
function pan(direction){
  pause();const width=hi-lo;
  lo=Math.max(scopeLo,Math.min(scopeHi-width,lo+direction*width/2));hi=lo+width;
  focusedId=null;now=Math.max(lo,Math.min(hi,now));rebuild(false);
}
function syncTimelineScrollbar(){
  const bar=$('timeline-scroll'),track=$('timeline-scroll-track');
  if(D.meta.start===null){bar.setAttribute('aria-disabled','true');return;}
  const available=Math.max(0,scopeHi-scopeLo-(hi-lo));
  const ratio=Math.max(1,(scopeHi-scopeLo)/Math.max(1,hi-lo));
  track.style.width=Math.max(bar.clientWidth+(available>0?1:0),Math.min(10000000,bar.clientWidth*ratio))+'px';
  bar.setAttribute('aria-disabled',String(available===0));
  bar.scrollLeft=available?(lo-scopeLo)/available*(bar.scrollWidth-bar.clientWidth):0;
  syncedScrollLeft=bar.scrollLeft;
}
function scrollTimeline(){
  const bar=$('timeline-scroll'),maxScroll=bar.scrollWidth-bar.clientWidth;
  if(maxScroll<=0||Math.abs(bar.scrollLeft-syncedScrollLeft)<.5)return;
  pause();const width=hi-lo;
  lo=scopeLo+(bar.scrollLeft/maxScroll)*(scopeHi-scopeLo-width);hi=lo+width;
  focusedId=null;now=Math.max(lo,Math.min(hi,now));rebuild(false);
}
function timelineX(event){
  const box=$('timeline').getBoundingClientRect();
  return Math.max(X0,Math.min(X1,(event.clientX-box.left)/box.width*W));
}
function beginTimelineDrag(event){
  if(event.button!==0||event.isPrimary===false)return;
  const box=$('timeline').getBoundingClientRect(),x=(event.clientX-box.left)/box.width*W;
  if(x<X0||x>X1)return;
  pause();suppressTimelineClick=false;
  timelineDrag={id:event.pointerId,start:x,end:x,clientX:event.clientX,lo,hi,active:false};
}
function moveTimelineDrag(event){
  const drag=timelineDrag;if(!drag||drag.id!==event.pointerId)return;
  drag.end=timelineX(event);
  if(!drag.active&&Math.abs(event.clientX-drag.clientX)>=5){
    drag.active=true;$('timeline').setPointerCapture(event.pointerId);
  }
  if(!drag.active)return;
  event.preventDefault();
  let selection=$('zoom-selection');
  if(!selection){selection=make('rect',{id:'zoom-selection',class:'zoom-selection',y:22});$('timeline').append(selection);}
  selection.setAttribute('x',Math.min(drag.start,drag.end));
  selection.setAttribute('width',Math.abs(drag.end-drag.start));
  selection.setAttribute('height',Math.max(1,$('timeline').viewBox.baseVal.height-71));
  const time=x=>drag.lo+(x-X0)/(X1-X0)*(drag.hi-drag.lo);
  text('zoom-status',`Release to zoom · ${fmtTime.format(time(Math.min(drag.start,drag.end)))} – ${fmtTime.format(time(Math.max(drag.start,drag.end)))}`);
}
function finishTimelineDrag(event,cancelled=false){
  const drag=timelineDrag;if(!drag||drag.id!==event.pointerId)return;
  timelineDrag=null;
  if($('timeline').hasPointerCapture(event.pointerId))$('timeline').releasePointerCapture(event.pointerId);
  if(!drag.active)return;
  suppressTimelineClick=true;
  if(!cancelled){
    drag.end=timelineX(event);
    const time=x=>drag.lo+(x-X0)/(X1-X0)*(drag.hi-drag.lo);
    const start=time(Math.min(drag.start,drag.end)),end=time(Math.max(drag.start,drag.end));
    const width=Math.min(scopeHi-scopeLo,Math.max(1000,end-start));
    lo=Math.max(scopeLo,Math.min(scopeHi-width,(start+end-width)/2));hi=lo+width;
    focusedId=null;now=Math.max(lo,Math.min(hi,now));
  }
  rebuild(false);
}
function render(){
  text('clock',fmtTime.format(now)+' '+zone(now));text('date',fmtDate.format(now));
  text('human-count',(lastAt(humans,now)+1).toLocaleString());text('agent-count',(lastAt(usage,now)+1).toLocaleString());
  $('scrub').value=String(Math.round((now-lo)/Math.max(1,hi-lo)*100000));$('scrub').setAttribute('aria-valuetext',stamp(now));
  const old=current?.id;
  current=viewMessages.find(r=>r.id===focusedId&&r.t===now)||viewMessages[lastAt(viewMessages,now)]||null;
  selected=current?H.filter(h=>sameConversation(h,current)&&h.t<=now&&(h.t!==current.t||h.order<=current.order)).slice(-1)[0]||null:null;
  renderTurnUsage();
  if(old!==current?.id){renderText();renderEvents();drawTimeline();}
  const candidates=current?UP.filter(r=>sameConversation(r,current)&&r.t<=now&&(r.t!==current.t||r.order<=current.order)):[];
  const update=candidates.slice(-1)[0];
  text('update-title',update?(selected&&update.t<selected.t?'Earlier agent context':'Latest visible agent response'):'Agent response not yet visible');
  text('agent-text',update?.text||'No following agent text has been reached for this input.');
  text('update-time',update?stamp(update.t):'');text('update-source',update?`${update.id} · ${update.source}`:'');
  const call=usage[lastAt(usage,now)];
  text('last-agent-event',call?`${stamp(call.t)} · ${call.tool} · ${call.model} · ${call.output.toLocaleString()} output tokens`:'No metered model event reached in this view.');
  const index=current?allMessages.findIndex(r=>r.id===current.id):-1;
  text('message-position',current?`Message ${index+1} of ${allMessages.length} · ${current.id} · ${current.role==='user'?'Human':'Agent'}`:'No message reached');
  $('prev-text').disabled=index<=0;$('next-text').disabled=index>=allMessages.length-1||!allMessages.length;
  $('prev').disabled=!H.filter(scopeMatch).some(h=>h.t<now);$('next').disabled=!H.filter(scopeMatch).some(h=>h.t>now);
  $('next-idea').disabled=!H.filter(scopeMatch).some(h=>h.moment);
  const line=$('playhead');if(line){line.setAttribute('x1',xAt(now));line.setAttribute('x2',xAt(now));}
}
function renderText(){
  if(!selected){text('input-title','No human input reached in this conversation');text('human-text','Advance the clock or choose a message.');text('input-meta','');text('input-source','');text('input-position','');$('moment-banner').hidden=true;return;}
  const h=selected;
  text('input-title',h.moment?.title||preview(h.text,110));text('human-text',h.text);
  text('input-meta',`${stamp(h.t)} · ${h.tool} · ${h.category}`);text('input-source',`${h.source} · conversation ${h.session}`);
  text('input-position',h.id);$('moment-banner').hidden=!h.moment;
  if(h.moment)text('moment-banner',`◆ ${h.moment.kind} — ${h.moment.note}`);
}
function summarizeUsage(rows){
  const priced=rows.filter(r=>r.estimated_usd!==null);
  return {calls:rows.length,input:rows.reduce((n,r)=>n+r.input,0),output:rows.reduce((n,r)=>n+r.output,0),
    estimated_usd:priced.length?priced.reduce((n,r)=>n+r.estimated_usd,0):null,
    priced_calls:priced.length,unpriced_calls:rows.length-priced.length,
    models:[...new Set(rows.map(r=>r.model))].sort()};
}
function spendLabel(total){
  if(!total.calls)return 'No recorded usage';
  if(total.estimated_usd===null)return 'Unpriced';
  return '$'+total.estimated_usd.toLocaleString('en-US',{minimumFractionDigits:4,maximumFractionDigits:6})+(total.unpriced_calls?' + unpriced calls':'');
}
function tokenLabel(total){
  return total.calls?`${total.input.toLocaleString()} in · ${total.output.toLocaleString()} out · ${total.calls.toLocaleString()} calls`:'Tokens unavailable · no metered calls recorded';
}
function coverageLabel(total){
  return `${total.priced_calls} / ${total.calls} recorded calls priced`+(total.unpriced_calls?' · priced subtotal only':'');
}
function renderTurnUsage(){
  const turn=turnById.get(selected?.id);
  $('prompt-usage').hidden=!turn;
  $('prompt-usage').title=turn?`Complete recorded turn, not clipped to zoom. ${coverageLabel(turn)}.`:'';
  text('turn-usage-title',turn?'Turn estimate':'');
  text('turn-spend',turn?spendLabel(turn):'');
  text('turn-tokens',turn?tokenLabel(turn):'');
  text('turn-models',turn?turn.models.join(', ')||'Model unknown':'');
  text('turn-attribution',turn?coverageLabel(turn)+'. '+Object.keys(turn.attribution).join(' '):'');
}
function appendUsageRow(body,label,scope,total){
  const row=document.createElement('tr');
  for(const value of [label,scope,total.calls,total.calls?total.input.toLocaleString():'—',total.calls?total.output.toLocaleString():'—',spendLabel(total)]){
    const cell=document.createElement('td');cell.textContent=value;row.append(cell);
  }
  body.append(row);
}
function renderUsage(){
  const visible=D.meta.start===null?[]:requests.filter(r=>r.t!==null&&scoped(r));
  const windowTotal=summarizeUsage(visible);
  const filtered=D.meta.start!==null&&(lo!==D.meta.start||hi!==D.meta.end||$('harness').value!=='all'||requests.some(r=>r.t===null));
  $('window-summary').hidden=!filtered;
  text('session-spend',spendLabel(A.total));text('session-tokens',tokenLabel(A.total));
  const models=A.total.models.length>1?`${A.total.models.length} models`:`Models: ${A.total.models[0]||'unknown (no usage)'}`;
  text('session-models',models+(A.total.unpriced_calls?`. ${coverageLabel(A.total)}.`:''));
  text('window-spend',spendLabel(windowTotal));text('window-tokens',tokenLabel(windowTotal));
  text('window-coverage',`${coverageLabel(windowTotal)} · ${$('harness').value}${D.meta.start===null?' · timing unavailable':''}`);
  text('model-scope',filtered?'Models · visible range':'Models · entire session');
  const rows=filtered?visible:requests;
  const body=$('model-usage');body.replaceChildren();
  for(const model of [...new Set(rows.map(r=>r.model))].sort()){
    appendUsageRow(body,model,filtered?'Visible range':'Entire session',summarizeUsage(rows.filter(r=>r.model===model)));
  }
}
function initializeUsage(){
  const pricing=A.pricing;
  text('pricing-note',pricing.verified_on?`Frozen rate card dated ${pricing.verified_on} (not freshly verified). Unknown models remain unpriced; partial estimates are subtotals.`:'No rate card supplied. Spend is unknown, not zero.');
  text('pricing-method',pricing.basis+' '+pricing.method+' '+pricing.verified_on_by_model_note);
  for(const model of A.total.models){
    const rate=pricing.rates[model],row=document.createElement('tr');
    const rule=rate?.long_context_threshold?`> ${rate.long_context_threshold.toLocaleString()} in: ${rate.long_input_multiplier||1}× input, ${rate.long_output_multiplier||1}× output.`:'No supplied long-context premium.';
    const values=rate?[`${model} · ${rate.verified_on||pricing.verified_on}`,rate.input,rate.cache_read,rate.cache_write,rate.output,`${rule} Write TTL: ${rate.write_ttl_seconds??'unspecified'} seconds.`]:[model,'Unpriced','—','—','—','No recorded rate'];
    for(const value of values){const td=document.createElement('td');td.textContent=value;row.append(td);}
    $('pricing-rates').append(row);
  }
  for(const [name,url]of Object.entries(pricing.sources)){const li=document.createElement('li');li.textContent=`${name}: ${url}`;$('pricing-sources').append(li);}
  for(const assumption of pricing.assumptions){const li=document.createElement('li');li.textContent=assumption;$('pricing-assumptions').append(li);}
  const body=$('turn-usage-rows');
  for(const turn of A.turns){
    const row=document.createElement('tr'),cell=document.createElement('td'),button=document.createElement('button');
    row.dataset.turn=turn.id;button.textContent=turn.id;button.title=preview(turn.text,200);
    button.addEventListener('click',()=>jumpMessage([...H,...D.undated].find(r=>r.id===turn.id)));
    cell.append(button,document.createTextNode(' '+preview(turn.text,75)));
    const detail=document.createElement('details'),summary=document.createElement('summary');
    summary.textContent='Attribution';detail.append(summary,document.createTextNode(Object.entries(turn.attribution).map(([method,count])=>`${count} calls: ${method}`).join(' ')||'No recorded usage.'));
    cell.append(detail);row.append(cell);
    for(const value of [turn.models.join(', ')||'Unknown',turn.calls,turn.calls?turn.input.toLocaleString():'—',turn.calls?turn.output.toLocaleString():'—',spendLabel(turn)]){
      const td=document.createElement('td');td.textContent=value;row.append(td);
    }
    body.append(row);
  }
  if(A.unattributed.calls)appendUsageRow(body,'Unattributed',A.unattributed.models.join(', '),A.unattributed);
  renderUsage();
}
function xAt(t){return X0+(t-lo)/Math.max(1,hi-lo)*(X1-X0);}
function drawTimeline(){
  const svg=$('timeline');svg.replaceChildren();
  const tools=[...new Set([...H,...U,...UP].map(r=>r.tool))];
  const lanes=[['Directions',46],['Human inputs',86],...tools.map((tool,i)=>[tool,122+i*36])];
  const bottom=Math.max(150,122+tools.length*36),axis=bottom+24;
  svg.setAttribute('viewBox',`0 0 1100 ${axis+25}`);
  svg.append(make('rect',{x:X0,y:22,width:X1-X0,height:bottom-22,fill:'#f6f8fa',rx:4}));
  for(const c of D.clusters){
    const members=c.inputs.map(id=>H.find(h=>h.id===id)).filter(scoped);if(members.length<2)continue;
    const x=xAt(Math.max(c.start,lo)),end=xAt(Math.min(c.end,hi));
    svg.append(make('rect',{x,y:23,width:Math.max(3,end-x),height:bottom-23,fill:'#f8dda1','fill-opacity':'.3'}));
  }
  for(const [label,y]of lanes){svg.append(make('text',{x:5,y:y+4,'font-size':12,fill:'#526d80'},label));svg.append(make('line',{x1:X0,x2:X1,y1:y,y2:y,stroke:'#e0e7ec'}));}
  for(let i=0;i<7;i++){
    const t=lo+(hi-lo)*i/6,x=xAt(t);
    const label=hi-lo>=86400000?fmtStamp.format(t):hi-lo<300000?fmtTime.format(t):fmtHour.format(t);
    svg.append(make('line',{x1:x,x2:x,y1:24,y2:bottom,stroke:'#d6e0e7','stroke-dasharray':'3 4'}));
    svg.append(make('text',{x,y:axis,class:'axis-label','font-size':12,fill:'#526d80','text-anchor':i===0?'start':i===6?'end':'middle'},label));
  }
  for(const [tool,y]of lanes.slice(2)){
    const bins=new Map(),bin=Math.min(900000,Math.max(1000,(hi-lo)/80));
    for(const e of usage.filter(e=>e.tool===tool)){const key=Math.floor(e.t/bin);bins.set(key,(bins.get(key)||0)+1);}
    for(const [key,n]of bins){
      const start=Math.max(lo,key*bin),end=Math.min(hi,(key+1)*bin);
      const rect=make('rect',{x:xAt(start),y:y-6,width:Math.max(1.4,xAt(end)-xAt(start)),height:12,fill:'#56819e','fill-opacity':Math.min(.95,.45+Math.log2(n+1)*.1)});
      rect.append(make('title',{},`${tool}: ${n} observed model events`));svg.append(rect);
    }
  }
  for(const h of humans){
    const dot=make('circle',{cx:xAt(h.t),cy:86,r:h.id===selected?.id?4.7:2.8,fill:h.id===selected?.id?'#173f50':'#2c8582',tabindex:'0',role:'button','aria-label':`${h.id} ${stamp(h.t)} ${preview(h.text,65)}`});
    dot.append(make('title',{},`${h.id} · ${stamp(h.t)}\n${preview(h.text,200)}`));
    dot.addEventListener('click',e=>{e.stopPropagation();jumpMessage(h);});dot.addEventListener('keydown',e=>{if(e.key==='Enter'){e.stopPropagation();jumpMessage(h);}});svg.append(dot);
  }
  for(const h of marked){
    const x=xAt(h.t),dot=make('polygon',{points:`${x},38 ${x+5},46 ${x},54 ${x-5},46`,fill:'#c58c2f',tabindex:'0',role:'button','aria-label':h.moment.title});
    dot.append(make('title',{},`${stamp(h.t)} · ${h.moment.title}`));
    dot.addEventListener('click',e=>{e.stopPropagation();jumpMessage(h);});dot.addEventListener('keydown',e=>{if(e.key==='Enter'){e.stopPropagation();jumpMessage(h);}});svg.append(dot);
  }
  svg.append(make('line',{id:'playhead',x1:xAt(now),x2:xAt(now),y1:18,y2:bottom+4,stroke:'#df7556','stroke-width':2,'pointer-events':'none'}));
}
function buildMoments(){
  const list=$('moments');list.replaceChildren();
  for(const h of marked){const button=document.createElement('button');button.className='moment-item';button.textContent=`◆ ${h.moment.title} · ${stamp(h.t)} · ${h.id}`;button.addEventListener('click',()=>jumpMessage(h));list.append(button);}
  if(!marked.length)list.textContent='No reviewed direction markers in this view.';
}
function buildClusters(){
  const list=$('clusters');list.replaceChildren();
  for(const c of D.clusters){const members=c.inputs.map(id=>H.find(h=>h.id===id)).filter(scoped);if(members.length<2)continue;const button=document.createElement('button');button.className='cluster-item';button.textContent=`${members.length} directions · ${stamp(c.start)} – ${stamp(c.end)}`;button.addEventListener('click',()=>jumpMessage(members[0]));list.append(button);}
  if(!list.children.length)list.textContent='No multi-direction groups in this view.';
}
function renderEvents(){
  const query=$('search').value.toLowerCase().trim(),list=$('event-list');list.replaceChildren();
  const rows=[...allMessages,...D.undated.filter(harnessMatch)].filter(r=>(r.id+' '+r.text+' '+(r.moment?.title||'')).toLowerCase().includes(query));
  for(const row of rows){
    const button=document.createElement('button');button.className='event-row'+(row.id===current?.id?' active':'');button.dataset.id=row.id;
    for(const [cls,value]of [['stamp',row.t===null?'Undated':stamp(row.t)],['badge',`${row.id} · ${row.role==='user'?'Human':'Agent'} · ${row.tool}`],['preview',preview(row.text,180)]]){const span=document.createElement('span');span.className=cls;span.textContent=value;button.append(span);}
    const turn=row.role==='user'?turnById.get(row.id):null;
    if(turn){
      const span=document.createElement('span');span.className='event-usage';
      span.textContent=`${turn.models.join(', ')||'Model unknown'} · Est. ${spendLabel(turn)} · ${tokenLabel(turn)}`;
      span.title=`Complete recorded turn; ${coverageLabel(turn)}. ${Object.keys(turn.attribution).join(' ')}`;
      button.append(span);
    }
    button.addEventListener('click',()=>jumpMessage(row));list.append(button);
  }
  if(!rows.length)list.textContent='No matching messages in this scope.';
}
function showUndated(){
  const row=D.undated.find(r=>r.id===$('undated-select').value);
  if(row){
    pause();current=row;
    const previous=D.undated.filter(r=>sameConversation(r,row)&&r.order<=row.order);
    selected=previous.filter(r=>r.role==='user').slice(-1)[0]||null;
    renderTurnUsage();
    const response=previous.filter(r=>r.role==='assistant').slice(-1)[0];
    text('human-text',selected?.text||'No human input in the available earlier source record.');
    text('input-title','Undated conversation');text('input-meta',`${row.tool} · Timestamp unavailable`);
    text('input-source',selected?.source||'');text('input-position',selected?.id||'');
    text('agent-text',response?.text||'No earlier assistant text in the available source record.');
    text('update-title','Agent response (source order only)');text('update-time','Timestamp unavailable');
    text('update-source',response?`${response.id} · ${response.source}`:'');$('moment-banner').hidden=true;
    const index=D.undated.findIndex(r=>r.id===row.id);
    text('message-position',`Undated message ${index+1} of ${D.undated.length} · ${row.id}`);
    $('prev-text').disabled=index<=0;$('next-text').disabled=index>=D.undated.length-1;
    text('undated-text',row.text);text('undated-source',`${row.tool} · ${row.role} · ${row.id} · ${row.source} · Timestamp unavailable`);
    document.querySelector('.undated').open=true;renderEvents();
  }
}
for(const day of D.days){const option=document.createElement('option');option.value=day.date;option.textContent=day.label;$('day').append(option);}
for(const tool of [...new Set([...H,...UP,...requests,...D.undated].map(r=>r.tool))]){const option=document.createElement('option');option.textContent=tool;$('harness').append(option);}
for(const row of D.undated){const option=document.createElement('option');option.value=row.id;option.textContent=`${row.id} · ${row.role} · ${preview(row.text,80)}`;$('undated-select').append(option);}
if(D.undated.length){$('undated-select').value=D.undated[0].id;showUndated();}
text('method',D.meta.method);
initializeUsage();
$('undated-select').addEventListener('change',showUndated);$('search').addEventListener('input',renderEvents);
$('prev-text').addEventListener('click',()=>stepText(-1));$('next-text').addEventListener('click',()=>stepText(1));
if(D.meta.start!==null){
  $('play').addEventListener('click',play);$('start').addEventListener('click',()=>jump(lo));$('end').addEventListener('click',()=>jump(hi));
  $('prev').addEventListener('click',()=>stepInput(-1));$('next').addEventListener('click',()=>stepInput(1));
  $('next-idea').addEventListener('click',nextIdea);$('zoom-in').addEventListener('click',()=>zoom(.5));$('zoom-out').addEventListener('click',()=>zoom(2));
  $('zoom-reset').addEventListener('click',()=>{pause();rebuild();});
  $('pan-prev').addEventListener('click',()=>pan(-1));$('pan-next').addEventListener('click',()=>pan(1));
  for(const id of ['day','harness'])$(id).addEventListener('change',()=>{pause();focusedId=null;rebuild();});
  $('scrub').addEventListener('input',()=>jump(lo+Number($('scrub').value)/100000*(hi-lo)));
  $('timeline').addEventListener('pointerdown',beginTimelineDrag);
  $('timeline').addEventListener('pointermove',moveTimelineDrag);
  $('timeline').addEventListener('pointerup',event=>finishTimelineDrag(event));
  $('timeline').addEventListener('pointercancel',event=>finishTimelineDrag(event,true));
  $('timeline').addEventListener('lostpointercapture',event=>finishTimelineDrag(event,true));
  $('timeline').addEventListener('click',event=>{if(suppressTimelineClick){suppressTimelineClick=false;event.preventDefault();event.stopImmediatePropagation();}},true);
  $('timeline-scroll').addEventListener('scroll',scrollTimeline);
  window.addEventListener('resize',syncTimelineScrollbar);
  $('timeline').addEventListener('click',event=>{const box=$('timeline').getBoundingClientRect(),x=(event.clientX-box.left)/box.width*W;if(x>=X0&&x<=X1)jump(lo+(x-X0)/(X1-X0)*(hi-lo));});
  document.addEventListener('keydown',e=>{if(e.target.closest('input,select,textarea,button,summary,#timeline-scroll'))return;if(e.code==='Space'){e.preventDefault();play();}else if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();jump(now+(e.key==='ArrowRight'?1:-1)*5*60000);}else if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();stepInput(e.key==='ArrowDown'?1:-1);}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();});
  rebuild();
}else{
  text('clock','Timing unavailable');text('date','Undated messages are preserved below; no dates inferred.');
  text('zoom-status','No timed records');text('input-title','Untimed conversation');
  $('timeline').append(make('text',{x:150,y:110,'font-size':24,fill:'#637689'},'No reliable timestamps; see the undated conversation below.'));
  for(const el of document.querySelectorAll('.transport button,.transport select,.transport input,.zoom-controls button,.text-controls button,.filters button,#scrub,#day,#harness'))el.disabled=true;
  if(D.undated.length)showUndated();else renderEvents();
  syncTimelineScrollbar();
}
window.vennPlayback={
  state:()=>({now,lo,hi,scopeLo,scopeHi,playing,selected:selected?.id||null,message:current?.id||null,visibleHumanInputs:humans.length,visibleModelEvents:usage.length}),
  jumpToInput:id=>{const row=H.find(r=>r.id===id);if(row)jumpMessage(row);},
  jumpToMessage:id=>{const row=[...messages,...D.undated].find(r=>r.id===id);if(row)jumpMessage(row);},pause
};
const initial=[...messages,...D.undated].find(r=>r.id===decodeURIComponent(location.hash.slice(1)));if(initial)jumpMessage(initial);

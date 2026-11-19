'use strict';
document.documentElement.classList.toggle('embedded',window.self!==window.top);
document.documentElement.classList.toggle('log-only',new URLSearchParams(location.search).get('view')==='log');
const D=JSON.parse(document.getElementById('playback-data').textContent);
const $=id=>document.getElementById(id), H=D.humans, U=D.usage, UP=D.updates;
const A=D.accounting, requests=A.requests;
const turnById=new Map(A.turns.map(turn=>[turn.id,turn]));
const messages=[...H,...UP].sort((a,b)=>a.t-b.t||a.order-b.order);
const messageById=new Map([...messages,...D.undated].map(row=>[row.id,row]));
const responsesByInput=new Map(),inputByResponse=new Map();
for(const turn of A.turns){
  const rows=(turn.response_ids||[]).map(id=>messageById.get(id)).filter(Boolean);
  responsesByInput.set(turn.id,rows);
  for(const row of rows)inputByResponse.set(row.id,messageById.get(turn.id));
}
const fmtTime=new Intl.DateTimeFormat('en-US',{timeZone:D.meta.timezone,hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
const fmtDate=new Intl.DateTimeFormat('en-US',{timeZone:D.meta.timezone,weekday:'long',month:'long',day:'numeric',year:'numeric'});
const fmtStamp=new Intl.DateTimeFormat('en-US',{timeZone:D.meta.timezone,month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
const fmtSessionDate=new Intl.DateTimeFormat('en-US',{timeZone:D.meta.timezone,year:'numeric',month:'short',day:'2-digit'});
const fmtHour=new Intl.DateTimeFormat('en-US',{timeZone:D.meta.timezone,hour:'2-digit',minute:'2-digit',hour12:false});
const fmtZone=new Intl.DateTimeFormat('en-US',{timeZone:D.meta.timezone,timeZoneName:'short'});
const zone=t=>fmtZone.formatToParts(t).find(p=>p.type==='timeZoneName').value;
const stamp=t=>fmtStamp.format(t)+' '+zone(t);
let now=D.meta.start,lo=now,hi=D.meta.end,scopeLo=lo,scopeHi=hi;
let humans=[],usage=[],marked=[],viewMessages=[],allMessages=[],selected=null,current=null,focusedId=null;
let timelineDrag=null,suppressTimelineClick=false,syncedScrollLeft=0;
let elidedDays=[],elidedGroups=[];
const eventButtons=new Map(),humanDots=new Map();
const eventGeometry=new Map();
const responseNodes=new Map();
let responseScopeKey=null,activeResponse=null,responseCurrentId=null;
let eventViewportHeight=0;
let eventScopeKey=null,activeEventButton=null,selectedDot=null;
let selectionFromParent=false,lastSentSelection=null;
const svgNS='http://www.w3.org/2000/svg',X0=150;
let W=1100,X1=1080,timelineViewportWidth=0;
function text(id,value){$(id).textContent=value;}
function make(tag,attrs={},value){const el=document.createElementNS(svgNS,tag);for(const [k,v]of Object.entries(attrs))el.setAttribute(k,String(v));if(value!==undefined)el.textContent=value;return el;}
function preview(value,n=120){const v=value.replace(/<\/?pasted_content[^>]*>/g,'').replace(/\s+/g,' ').trim();return v.length>n?v.slice(0,n)+'…':v;}
function lastAt(rows,t){let a=0,b=rows.length;while(a<b){const m=(a+b)>>1;if(rows[m].t<=t)a=m+1;else b=m;}return a-1;}
function filterMatch(row){return ($('harness').value==='all'||row.tool===$('harness').value)&&($('session').value==='all'||JSON.stringify([row.tool,row.session])===$('session').value);}
function scoped(row){return row&&row.t>=lo&&row.t<=hi&&filterMatch(row);}
function scopeMatch(row){return row.t>=scopeLo&&row.t<=scopeHi&&filterMatch(row);}
function refreshUndated(){
  const select=$('undated-select'),selectedId=select.value,rows=D.undated.filter(filterMatch);
  select.replaceChildren();
  for(const row of rows){const option=document.createElement('option');option.value=row.id;option.textContent=`${row.id} · ${row.role} · ${preview(row.text,80)}`;select.append(option);}
  if(rows.some(row=>row.id===selectedId))select.value=selectedId;
  document.querySelector('.undated').hidden=!rows.length;
  document.querySelector('.undated summary strong').textContent=`Outside the clock: ${rows.length} undated messages`;
}
function rebuild(reset=true){
  refreshUndated();
  if(D.meta.start===null){
    current=null;selected=null;renderText();renderTurnUsage();renderEvents();renderUsage();
    renderResponses();renderPosition([]);
    $('prev-text').disabled=$('next-text').disabled=true;
    if($('undated-select').options.length)showUndated();
    return;
  }
  const day=D.days.find(d=>d.date===$('day').value);
  const session=(D.sessions||[]).find(s=>JSON.stringify([s.tool,s.session])===$('session').value);
  scopeLo=day?day.start:D.meta.start;scopeHi=day?day.end:D.meta.end;
  if(!day&&session?.lo!==null&&session?.lo!==undefined){scopeLo=session.lo;scopeHi=session.hi;}
  updateElidedDays();
  if(reset){lo=scopeLo;hi=scopeHi;}
  humans=H.filter(scoped);usage=U.filter(scoped);marked=humans.filter(h=>h.moment);
  viewMessages=messages.filter(scoped);allMessages=messages.filter(scopeMatch);
  now=Math.max(lo,Math.min(hi,now));
  text('range-start',stamp(lo));text('range-end',stamp(hi));
  text('marker-count',marked.length+' marked inputs');
  $('next-idea').hidden=!H.some(h=>scopeMatch(h)&&h.moment);
  text('zoom-status',`${(Math.max(1,scopeHi-scopeLo)/Math.max(1,hi-lo)).toFixed(1)}× · ${stamp(lo)} – ${stamp(hi)}`);
  $('zoom-in').disabled=hi-lo<=1000;
  $('zoom-out').disabled=$('zoom-reset').disabled=lo===scopeLo&&hi===scopeHi;
  $('pan-prev').disabled=lo<=scopeLo;$('pan-next').disabled=hi>=scopeHi;
  buildMoments();buildClusters();render();drawTimeline();renderEvents();syncTimelineScrollbar();renderUsage();
  if(!allMessages.length&&$('undated-select').options.length)showUndated();
}
function jump(t){focusedId=null;now=Math.max(lo,Math.min(hi,t));render();}
function jumpMessage(row){
  if(row.t===null){if(!filterMatch(row)){$('harness').value=$('session').value='all';refreshUndated();}$('undated-select').value=row.id;showUndated();return;}
  if(!scopeMatch(row)){$('day').value='all';$('harness').value='all';$('session').value='all';now=row.t;rebuild();}
  const outside=row.t<lo||row.t>hi;
  if(outside){const width=hi-lo;lo=Math.max(scopeLo,Math.min(scopeHi-width,row.t-width/2));hi=Math.min(scopeHi,lo+width);}
  now=row.t;focusedId=row.id;
  if(outside)rebuild(false);else render();
}
function stepInput(direction){const candidate=direction>0?H.filter(scopeMatch).find(h=>h.t>now):H.filter(scopeMatch).reverse().find(h=>h.t<now);if(candidate)jumpMessage(candidate);}
function adjacentPrompt(direction){
  const rows=current?.t===null?D.undated.filter(filterMatch):allMessages;
  let index=rows.findIndex(r=>r.id===selected?.id);
  if(index<0)index=rows.findIndex(r=>r.id===current?.id);
  if(index<0)return direction>0?rows.find(r=>r.role==='user'):null;
  return (direction>0?rows.slice(index+1):rows.slice(0,index).reverse()).find(r=>r.role==='user');
}
function stepPrompt(direction){const candidate=adjacentPrompt(direction);if(candidate)jumpMessage(candidate);}
function nextIdea(){const rows=H.filter(scopeMatch).filter(h=>h.moment);const h=rows.find(h=>h.t>now)||rows[0];if(h)jumpMessage(h);}
function zoom(factor){
  const scaleLo=scaleTime(scopeLo),scaleHi=scaleTime(scopeHi),currentWidth=scaleTime(hi)-scaleTime(lo);
  const width=Math.min(scaleHi-scaleLo,Math.max(1000,currentWidth*factor));
  const start=Math.max(scaleLo,Math.min(scaleHi-width,scaleTime(now)-width/2));
  lo=timeAtScale(start,scopeLo,scopeHi);hi=timeAtScale(start+width,scopeLo,scopeHi);rebuild(false);
}
function pan(direction){
  const scaleLo=scaleTime(scopeLo),scaleHi=scaleTime(scopeHi),width=scaleTime(hi)-scaleTime(lo);
  const start=Math.max(scaleLo,Math.min(scaleHi-width,scaleTime(lo)+direction*width/2));
  lo=timeAtScale(start,scopeLo,scopeHi);hi=timeAtScale(start+width,scopeLo,scopeHi);
  focusedId=null;now=Math.max(lo,Math.min(hi,now));rebuild(false);
}
function syncTimelineScrollbar(){
  const bar=$('timeline-scroll'),track=$('timeline-scroll-track');
  if(D.meta.start===null){bar.setAttribute('aria-disabled','true');return;}
  const scaleLo=scaleTime(scopeLo),scaleHi=scaleTime(scopeHi),width=scaleTime(hi)-scaleTime(lo);
  const available=Math.max(0,scaleHi-scaleLo-width);
  bar.hidden=available===0;
  const ratio=Math.max(1,(scaleHi-scaleLo)/Math.max(1,width));
  track.style.width=Math.max(bar.clientWidth+(available>0?1:0),Math.min(10000000,bar.clientWidth*ratio))+'px';
  bar.setAttribute('aria-disabled',String(available===0));
  bar.scrollLeft=available?(scaleTime(lo)-scaleLo)/available*(bar.scrollWidth-bar.clientWidth):0;
  syncedScrollLeft=bar.scrollLeft;
}
function scrollTimeline(){
  const bar=$('timeline-scroll'),maxScroll=bar.scrollWidth-bar.clientWidth;
  if(maxScroll<=0||Math.abs(bar.scrollLeft-syncedScrollLeft)<.5)return;
  const scaleLo=scaleTime(scopeLo),scaleHi=scaleTime(scopeHi),width=scaleTime(hi)-scaleTime(lo);
  const start=scaleLo+(bar.scrollLeft/maxScroll)*(scaleHi-scaleLo-width);
  lo=timeAtScale(start,scopeLo,scopeHi);hi=timeAtScale(start+width,scopeLo,scopeHi);
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
  suppressTimelineClick=false;
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
  const time=x=>timeAtFraction((x-X0)/(X1-X0),drag.lo,drag.hi);
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
    const time=x=>timeAtFraction((x-X0)/(X1-X0),drag.lo,drag.hi);
    const start=time(Math.min(drag.start,drag.end)),end=time(Math.max(drag.start,drag.end));
    const width=Math.min(scopeHi-scopeLo,Math.max(1000,end-start));
    lo=Math.max(scopeLo,Math.min(scopeHi-width,(start+end-width)/2));hi=lo+width;
    focusedId=null;now=Math.max(lo,Math.min(hi,now));
  }
  rebuild(false);
}
function render(){
  text('clock',fmtTime.format(now)+' '+zone(now));text('date',fmtDate.format(now));
  $('scrub').value=String(Math.round((scaleTime(now)-scaleTime(lo))/Math.max(1,scaleTime(hi)-scaleTime(lo))*100000));$('scrub').setAttribute('aria-valuetext',stamp(now));
  const old=current?.id;
  current=viewMessages.find(r=>r.id===focusedId&&r.t===now)||viewMessages[lastAt(viewMessages,now)]||null;
  selected=current?.role==='user'?current:inputByResponse.get(current?.id)||null;
  renderTurnUsage();
  if(old!==current?.id){renderText();updateEventSelection();}
  updateSelectedDot();
  renderResponses();renderPosition(allMessages);
  $('prev').disabled=!H.filter(scopeMatch).some(h=>h.t<now);$('next').disabled=!H.filter(scopeMatch).some(h=>h.t>now);
  $('next-idea').disabled=!H.filter(scopeMatch).some(h=>h.moment);
  syncTimelineCursor();
}
function syncTimelineCursor(){
  const line=$('playhead');if(!line)return;
  const x=xAt(now);line.setAttribute('x1',x);line.setAttribute('x2',x);
  const viewport=document.querySelector('.timeline-viewport');
  if(timelineViewportWidth>0&&(x<viewport.scrollLeft+12||x>viewport.scrollLeft+timelineViewportWidth-12)){
    viewport.scrollLeft=Math.max(0,x-timelineViewportWidth/2);
  }
}
function renderText(){
  if(!selected){$('input-title').hidden=true;text('human-text',current?'No user input in the available earlier source record.':'Choose a message.');text('input-meta','');text('input-source','');$('moment-banner').hidden=true;return;}
  const h=selected;
  $('input-title').hidden=!h.moment;text('input-title',h.moment?.title||'');text('human-text',h.text);
  text('input-meta',`${h.t===null?'Timestamp unavailable':stamp(h.t)} · ${h.tool} · ${h.category}`);text('input-source',`${h.source} · conversation ${h.session}`);
  $('moment-banner').hidden=!h.moment;
  if(h.moment)text('moment-banner',`◆ ${h.moment.kind} — ${h.moment.note}`);
}
function renderPosition(rows){
  const prompts=rows.filter(row=>row.role==='user'),index=prompts.findIndex(row=>row.id===selected?.id);
  const number=index>=0?`${index+1} of ${prompts.length}`:'No matching user prompt';
  const details=selected?`· ${selected.id}${current?.role==='assistant'?` · Response ${current.id}`:''}`:current?`· ${current.id} · Agent`:'';
  text('message-number',number);text('message-details',details);
  const position=`${current?.t===null?'Undated user prompt':'User prompt'} ${number} ${details}`;
  $('message-position').setAttribute('aria-label',position);$('message-position').title=position;
  $('prev-text').disabled=!adjacentPrompt(-1);$('next-text').disabled=!adjacentPrompt(1);
}
function renderResponses(){
  const list=$('agent-text'),pane=document.querySelector('.turn-content'),key=selected?.id||(current?.role==='assistant'?current.id:'');
  const changed=key!==responseScopeKey;
  if(changed){
    responseScopeKey=key;responseNodes.clear();activeResponse=null;
    const rows=selected?(responsesByInput.get(selected.id)||[]):current?.role==='assistant'?[current]:[];
    text('update-title',`Agent responses (${rows.length})`);
    const fragment=document.createDocumentFragment();
    for(const row of rows){
      const article=document.createElement('article');article.className='agent-response';article.dataset.id=row.id;
      const meta=document.createElement('p');meta.className='meta response-meta';
      meta.textContent=`${row.id} · ${row.t===null?'Timestamp unavailable':stamp(row.t)} · ${row.source}`;
      const body=document.createElement('div');body.className='response-text';body.textContent=row.text;
      article.append(meta,body);fragment.append(article);responseNodes.set(row.id,article);
    }
    list.replaceChildren(fragment);
    if(!rows.length)list.textContent=current?'No recorded response for this input.':'Choose a message.';
  }
  const active=current?.role==='assistant'?responseNodes.get(current.id)||null:null;
  if(activeResponse!==active)activeResponse?.classList.remove('active');
  active?.classList.add('active');activeResponse=active;
  if(changed||responseCurrentId!==current?.id){
    if(active){
      const top=active.getBoundingClientRect().top-pane.getBoundingClientRect().top+pane.scrollTop-pane.clientTop,bottom=top+active.offsetHeight;
      if(top<pane.scrollTop||bottom>pane.scrollTop+pane.clientHeight)pane.scrollTop=Math.max(0,Math.min(top,bottom-pane.clientHeight));
    }else pane.scrollTop=0;
  }
  responseCurrentId=current?.id;
}
function summarizeUsage(rows){
  const priced=rows.filter(r=>(r.estimated_usd_min??r.estimated_usd)!==null);
  const components=priced.length&&priced.every(r=>r.cost_components)?Object.fromEntries(['fresh_input','cache_read','cache_write','output'].map(key=>[key,{tokens:priced.reduce((n,r)=>n+r.cost_components[key].tokens,0),estimated_usd:priced.reduce((n,r)=>n+r.cost_components[key].estimated_usd,0)}])):null;
  return {calls:rows.length,input:rows.reduce((n,r)=>n+r.input,0),output:rows.reduce((n,r)=>n+r.output,0),
    cost_components:components,
    input_complete:rows.every(r=>r.input_complete!==false),
    estimated_usd:priced.length&&priced.every(r=>r.estimated_usd!==null)?priced.reduce((n,r)=>n+r.estimated_usd,0):null,
    estimated_usd_min:priced.length?priced.reduce((n,r)=>n+(r.estimated_usd_min??r.estimated_usd),0):null,
    estimated_usd_max:priced.length?priced.reduce((n,r)=>n+(r.estimated_usd_max??r.estimated_usd),0):null,
    priced_calls:priced.length,unpriced_calls:rows.length-priced.length,
    models:[...new Set(rows.map(r=>r.model))].sort()};
}
function spendLabel(total){
  if(!total.calls)return 'No recorded usage';
  const lo=total.estimated_usd_min??total.estimated_usd,hi=total.estimated_usd_max??total.estimated_usd;
  if(lo===null)return 'Unpriced';
  const usd=value=>'$'+value.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  let amount=total.estimated_usd===null?'':usd(total.estimated_usd);
  if(lo!==hi)amount=amount?`~${amount} (range ${usd(lo)} - ${usd(hi)})`:`Range ${usd(lo)} - ${usd(hi)}`;
  return amount+(total.unpriced_calls?' + unpriced calls':'');
}
function inputLabel(total){return total.input.toLocaleString()+(total.input_complete===false?'+ (incomplete)':'');}
function tokenLabel(total){
  return total.calls?`${inputLabel(total)} in · ${total.output.toLocaleString()} out · ${total.calls.toLocaleString()} calls`:'Tokens unavailable · no metered calls recorded';
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
function appendModelUsageRow(body,label,scope,total){
  const row=document.createElement('tr');
  const component=key=>{const part=total.cost_components?.[key];return part?`${part.tokens.toLocaleString()} / $${part.estimated_usd.toFixed(2)}`:'Unknown';};
  for(const value of [label,scope,total.calls,...['fresh_input','cache_read','cache_write','output'].map(component),spendLabel(total)]){
    const cell=document.createElement('td');cell.textContent=value;row.append(cell);
  }
  body.append(row);
}
function renderUsage(){
  const visible=D.meta.start===null?[]:requests.filter(r=>r.t!==null&&scoped(r));
  const windowTotal=summarizeUsage(visible);
  const filtered=D.meta.start!==null&&(lo!==D.meta.start||hi!==D.meta.end||$('harness').value!=='all'||$('session').value!=='all'||requests.some(r=>r.t===null));
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
    appendModelUsageRow(body,model,filtered?'Visible range':'Entire session',summarizeUsage(rows.filter(r=>r.model===model)));
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
    for(const value of [turn.models.join(', ')||'Unknown',turn.calls,turn.calls?inputLabel(turn):'—',turn.calls?turn.output.toLocaleString():'—',spendLabel(turn)]){
      const td=document.createElement('td');td.textContent=value;row.append(td);
    }
    body.append(row);
  }
  if(A.unattributed.calls){
    const total=A.unattributed,row=document.createElement('tr');
    for(const value of ['Unattributed',total.models.join(', '),total.calls,inputLabel(total),total.output.toLocaleString(),spendLabel(total)]){
      const cell=document.createElement('td');cell.textContent=value;row.append(cell);
    }
    body.append(row);
  }
  renderUsage();
}
function updateElidedDays(){
  if(!$('elide-inactive').checked){elidedDays=[];elidedGroups=[];return;}
  const active=D.days.map(day=>[...H,...U,...UP].some(row=>row.t>=day.start&&row.t<=day.end&&filterMatch(row)));
  elidedDays=D.days.filter((day,index)=>!active[index]);
  elidedGroups=[];
  for(let i=0;i<D.days.length;){
    if(active[i]){i++;continue;}
    const first=i;
    while(i+1<D.days.length&&!active[i+1])i++;
    const last=i++;
    elidedGroups.push({start:D.days[first].start,end:D.days[last].end,label:first===last?D.days[first].label:`${D.days[first].label} – ${D.days[last].label}`});
  }
}
function scaleTime(t){
  let removed=0;
  for(const day of elidedDays){
    const end=day.end+1;
    if(t<=day.start)break;
    if(t<end)return day.start-removed;
    removed+=end-day.start;
  }
  return t-removed;
}
function timeAtScale(value,min,max){
  const scaleSpan=scaleTime(max)-scaleTime(min);
  const tolerance=scaleSpan/Math.max(1,X1-X0);
  const boundary=elidedGroups.find(day=>Math.abs(value-scaleTime(day.end+1))<=tolerance);
  if(boundary)return Math.min(max,boundary.end+1);
  let left=min,right=max;
  for(let i=0;i<48;i++){
    const middle=(left+right)/2;
    if(scaleTime(middle)<value)left=middle;else right=middle;
  }
  let result=Math.max(min,Math.min(max,right));
  const gap=elidedGroups.find(day=>result>=day.start&&result<day.end+1);
  if(gap)result=Math.min(max,gap.end+1);
  return result;
}
function timeAtFraction(fraction,min=lo,max=hi){
  const start=scaleTime(min),end=scaleTime(max);
  if(end<=start)return min+(max-min)*fraction;
  return timeAtScale(start+Math.max(0,Math.min(1,fraction))*(end-start),min,max);
}
function xAt(t){return X0+(scaleTime(t)-scaleTime(lo))/Math.max(1,scaleTime(hi)-scaleTime(lo))*(X1-X0);}
function drawTimeline(){
  const svg=$('timeline');svg.replaceChildren();
  W=Math.max(600,svg.clientWidth);X1=W-20;
  timelineViewportWidth=document.querySelector('.timeline-viewport').clientWidth;
  humanDots.clear();selectedDot=null;
  const tools=[...new Set([...H,...U,...UP].map(r=>r.tool))];
  const lanes=[['Directions',46],['User inputs',86],...tools.map((tool,i)=>[tool,122+i*36])];
  const bottom=Math.max(150,122+tools.length*36),axis=bottom+24;
  svg.setAttribute('viewBox',`0 0 ${W} ${axis+25}`);
  svg.append(make('rect',{x:X0,y:22,width:X1-X0,height:bottom-22,fill:'var(--cp-surface-soft)',rx:4}));
  for(const period of D.day_night||[]){
    const start=Math.max(lo,period.start),end=Math.min(hi,period.end);if(end<=start)continue;
    const xStart=xAt(start),xEnd=xAt(end);if(xEnd<=xStart)continue;
    const band=make('rect',{x:xStart,y:22,width:xEnd-xStart,height:bottom-22,
      class:`time-band ${period.kind==='day'?'timeline-day':'timeline-night'}`,'data-start':start,'data-end':end});
    band.append(make('title',{},`${period.kind==='day'?'Daytime':'Nighttime'} · ${stamp(start)} – ${stamp(end)}`));svg.append(band);
  }
  for(const day of elidedGroups){
    if(day.end<lo||day.start>hi)continue;
    const x=xAt(Math.max(lo,Math.min(hi,day.start)));
    const marker=make('line',{x1:x,x2:x,y1:22,y2:bottom,stroke:'var(--cp-border-strong)','stroke-width':1.5,'stroke-dasharray':'3 4',class:'elided-day'});
    marker.append(make('title',{},`Inactive day${day.label.includes(' – ')?'s':''} elided · ${day.label}`));svg.append(marker);
  }
  for(const c of D.clusters){
    const members=c.inputs.map(id=>H.find(h=>h.id===id)).filter(scoped);if(members.length<2)continue;
    const x=xAt(Math.max(c.start,lo)),end=xAt(Math.min(c.end,hi));
    svg.append(make('rect',{x,y:23,width:Math.max(3,end-x),height:bottom-23,fill:'var(--cp-warning)','fill-opacity':'.15'}));
  }
  for(const [label,y]of lanes){svg.append(make('text',{x:5,y:y+4,'font-size':12,fill:'var(--cp-text-muted)'},label));svg.append(make('line',{x1:X0,x2:X1,y1:y,y2:y,stroke:'var(--cp-border)'}));}
  for(let i=0;i<7;i++){
    const t=timeAtFraction(i/6),x=xAt(t);
    const label=hi-lo>=86400000?fmtStamp.format(t):hi-lo<300000?fmtTime.format(t):fmtHour.format(t);
    svg.append(make('line',{x1:x,x2:x,y1:24,y2:bottom,stroke:'var(--cp-border)','stroke-dasharray':'3 4'}));
    svg.append(make('text',{x,y:axis,class:'axis-label','font-size':12,fill:'var(--cp-text-muted)','text-anchor':i===0?'start':i===6?'end':'middle'},label));
  }
  for(const [tool,y]of lanes.slice(2)){
    const bins=new Map(),bin=Math.min(900000,Math.max(1000,(hi-lo)/80));
    for(const e of usage.filter(e=>e.tool===tool)){const key=Math.floor(e.t/bin);bins.set(key,(bins.get(key)||0)+1);}
    for(const [key,n]of bins){
      const start=Math.max(lo,key*bin),end=Math.min(hi,(key+1)*bin);
      const rect=make('rect',{x:xAt(start),y:y-6,width:Math.max(1.4,xAt(end)-xAt(start)),height:12,fill:'var(--cp-border-strong)','fill-opacity':Math.min(.95,.45+Math.log2(n+1)*.1)});
      rect.append(make('title',{},`${tool}: ${n} observed model events`));svg.append(rect);
    }
  }
  for(const h of humans){
    const dot=make('circle',{cx:xAt(h.t),cy:86,r:h.id===selected?.id?4.7:2.8,fill:h.id===selected?.id?'color-mix(in srgb,var(--cp-success) 80%,var(--cp-text))':'var(--cp-success)',tabindex:'0',role:'button','aria-label':`${h.id} ${stamp(h.t)} ${preview(h.text,65)}`});
    dot.append(make('title',{},`${h.id} · ${stamp(h.t)}\n${preview(h.text,200)}`));
    dot.addEventListener('click',e=>{e.stopPropagation();jumpMessage(h);});dot.addEventListener('keydown',e=>{if(e.key==='Enter'){e.stopPropagation();jumpMessage(h);}});svg.append(dot);humanDots.set(h.id,dot);
  }
  for(const h of marked){
    const x=xAt(h.t),dot=make('polygon',{points:`${x},38 ${x+5},46 ${x},54 ${x-5},46`,fill:'var(--cp-warning)',tabindex:'0',role:'button','aria-label':h.moment.title});
    dot.append(make('title',{},`${stamp(h.t)} · ${h.moment.title}`));
    dot.addEventListener('click',e=>{e.stopPropagation();jumpMessage(h);});dot.addEventListener('keydown',e=>{if(e.key==='Enter'){e.stopPropagation();jumpMessage(h);}});svg.append(dot);
  }
  svg.append(make('line',{id:'playhead',x1:xAt(now),x2:xAt(now),y1:18,y2:bottom+4,stroke:'var(--cp-danger)','stroke-width':2,'pointer-events':'none'}));
  syncTimelineCursor();
  updateSelectedDot();
}
function updateSelectedDot(){
  const dot=humanDots.get(selected?.id)||null;
  if(dot===selectedDot)return;
  if(selectedDot){selectedDot.setAttribute('r','2.8');selectedDot.setAttribute('fill','var(--cp-success)');}
  selectedDot=dot;
  if(dot){dot.setAttribute('r','4.7');dot.setAttribute('fill','color-mix(in srgb,var(--cp-success) 80%,var(--cp-text))');}
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
  const query=$('search').value.toLowerCase().trim(),list=$('event-list');
  const key=JSON.stringify([query,$('harness').value,$('session').value,scopeLo,scopeHi,allMessages.length]);
  if(key===eventScopeKey){updateEventSelection();return;}
  eventScopeKey=key;list.replaceChildren();eventButtons.clear();eventGeometry.clear();activeEventButton=null;
  const rows=[...allMessages,...D.undated.filter(filterMatch)].filter(r=>(r.id+' '+r.text+' '+(r.moment?.title||'')).toLowerCase().includes(query));
  for(const row of rows){
    const button=document.createElement('button');button.className='event-row'+(row.id===current?.id?' active':'');button.dataset.id=row.id;button.dataset.role=row.role;
    for(const [cls,value]of [['stamp',row.t===null?'Undated':stamp(row.t)],['badge',`${row.id} · ${row.role==='user'?'User':'Agent'} · ${row.tool}`],['preview',preview(row.text,180)]]){const span=document.createElement('span');span.className=cls;span.textContent=value;button.append(span);}
    const turn=row.role==='user'?turnById.get(row.id):null;
    if(turn){
      const span=document.createElement('span');span.className='event-usage';
      span.textContent=`${turn.models.join(', ')||'Model unknown'} · Est. ${spendLabel(turn)} · ${tokenLabel(turn)}`;
      span.title=`Complete recorded turn; ${coverageLabel(turn)}. ${Object.keys(turn.attribution).join(' ')}`;
      button.append(span);
    }
    button.addEventListener('click',()=>{const top=list.scrollTop;jumpMessage(row);list.scrollTop=top;});
    list.append(button);eventButtons.set(row.id,button);
  }
  if(!rows.length)list.textContent='No matching messages in this scope.';
  eventViewportHeight=list.clientHeight;
  for(const [id,button]of eventButtons)eventGeometry.set(id,[button.offsetTop,button.offsetHeight]);
  updateEventSelection();
}
function updateEventSelection(){
  const button=eventButtons.get(current?.id)||null;
  if(activeEventButton!==button)activeEventButton?.classList.remove('active');
  activeEventButton=button;button?.classList.add('active');
  const geometry=eventGeometry.get(current?.id);
  if(geometry){
    const list=$('event-list'),[top,height]=geometry;
    if(top<list.scrollTop)list.scrollTop=top;
    else if(top+height>list.scrollTop+eventViewportHeight)list.scrollTop=top+height-eventViewportHeight;
  }
  if(current?.id&&current.id!==lastSentSelection){
    lastSentSelection=current.id;
    if(window.self!==window.top&&!selectionFromParent)window.parent.postMessage({type:'session-message-selected',id:current.id},'*');
  }
}
function showUndated(){
  const row=D.undated.find(r=>r.id===$('undated-select').value);
  if(row){
    current=row;
    selected=row.role==='user'?row:inputByResponse.get(row.id)||null;
    renderTurnUsage();
    renderText();renderResponses();renderPosition(D.undated.filter(filterMatch));
    text('undated-text',row.text);text('undated-source',`${row.tool} · ${row.role} · ${row.id} · ${row.source} · Timestamp unavailable`);
    document.querySelector('.undated').open=true;renderEvents();
  }
}
for(const day of D.days){const option=document.createElement('option');option.value=day.date;option.textContent=day.label;$('day').append(option);}
for(const session of D.sessions||[]){
  const option=document.createElement('option');option.value=JSON.stringify([session.tool,session.session]);
  option.textContent=`${session.start===null?'Undated':fmtSessionDate.format(session.start)} · ${session.tool} · ${preview(session.preview||session.session,45)}`;
  option.title=session.session;$('session').append(option);
}
for(const tool of [...new Set([...H,...UP,...requests,...D.undated].map(r=>r.tool))]){const option=document.createElement('option');option.textContent=tool;$('harness').append(option);}
refreshUndated();
if(D.undated.length){$('undated-select').value=D.undated[0].id;showUndated();}
text('method',D.meta.method);
initializeUsage();
$('undated-select').addEventListener('change',showUndated);$('search').addEventListener('input',renderEvents);
$('prev-text').addEventListener('click',()=>stepPrompt(-1));$('next-text').addEventListener('click',()=>stepPrompt(1));
for(const id of ['day','harness'])$(id).addEventListener('change',()=>{if(id==='harness')$('session').value='all';focusedId=null;rebuild();});
$('elide-inactive').addEventListener('change',()=>rebuild());
$('session').addEventListener('change',()=>{$('day').value='all';focusedId=null;const session=(D.sessions||[]).find(s=>JSON.stringify([s.tool,s.session])===$('session').value);$('harness').value=session?.tool||'all';if(session?.lo!==null&&session?.lo!==undefined)now=session.lo;rebuild();});
if(D.meta.start!==null){
  $('start').addEventListener('click',()=>jump(lo));$('end').addEventListener('click',()=>jump(hi));
  $('prev').addEventListener('click',()=>stepInput(-1));$('next').addEventListener('click',()=>stepInput(1));
  $('next-idea').addEventListener('click',nextIdea);$('zoom-in').addEventListener('click',()=>zoom(.5));$('zoom-out').addEventListener('click',()=>zoom(2));
  $('zoom-reset').addEventListener('click',()=>rebuild());
  $('pan-prev').addEventListener('click',()=>pan(-1));$('pan-next').addEventListener('click',()=>pan(1));
  $('scrub').addEventListener('input',()=>jump(timeAtFraction(Number($('scrub').value)/100000)));
  $('timeline').addEventListener('pointerdown',beginTimelineDrag);
  $('timeline').addEventListener('pointermove',moveTimelineDrag);
  $('timeline').addEventListener('pointerup',event=>finishTimelineDrag(event));
  $('timeline').addEventListener('pointercancel',event=>finishTimelineDrag(event,true));
  $('timeline').addEventListener('lostpointercapture',event=>finishTimelineDrag(event,true));
  $('timeline').addEventListener('click',event=>{if(suppressTimelineClick){suppressTimelineClick=false;event.preventDefault();event.stopImmediatePropagation();}},true);
  $('timeline-scroll').addEventListener('scroll',scrollTimeline);
  window.addEventListener('resize',()=>{drawTimeline();syncTimelineScrollbar();eventScopeKey=null;renderEvents();});
  $('timeline').addEventListener('click',event=>{const box=$('timeline').getBoundingClientRect(),x=(event.clientX-box.left)/box.width*W;if(x>=X0&&x<=X1)jump(timeAtFraction((x-X0)/(X1-X0)));});
  document.addEventListener('keydown',e=>{if(e.target.closest('input,select,textarea,button,summary,.turn-content,#timeline-scroll,#event-list,#moments,#clusters'))return;if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();jump(now+(e.key==='ArrowRight'?1:-1)*5*60000);}else if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();stepInput(e.key==='ArrowDown'?1:-1);}});
  rebuild();
}else{
  text('clock','Timing unavailable');text('date','Undated messages are preserved below; no dates inferred.');
  text('zoom-status','No timed records');text('input-title','Untimed conversation');
  $('timeline').append(make('text',{x:150,y:110,'font-size':24,fill:'var(--cp-text-muted)'},'No reliable timestamps; see the undated conversation below.'));
  for(const el of document.querySelectorAll('.transport button,.transport select,.transport input,.zoom-controls button,.text-controls button,.filters button,#scrub,#day'))el.disabled=true;
  $('next-idea').hidden=true;
  if(D.undated.length)showUndated();else renderEvents();
  syncTimelineScrollbar();
}
window.vennPlayback={
  state:()=>({now,lo,hi,scopeLo,scopeHi,selected:selected?.id||null,message:current?.id||null,visibleHumanInputs:humans.length,visibleModelEvents:usage.length,elidedDays:elidedDays.map(day=>day.date)}),
  jumpToInput:id=>{const row=H.find(r=>r.id===id);if(row)jumpMessage(row);},
  jumpToMessage:id=>{const row=[...messages,...D.undated].find(r=>r.id===id);if(row)jumpMessage(row);}
};
window.addEventListener('message',event=>{
  if(event.source!==window.parent)return;
  if(event.data?.type==='session-log-search'&&typeof event.data.query==='string'){
    $('search').value=event.data.query;renderEvents();return;
  }
  if(event.data?.type!=='session-message-selected')return;
  const row=messageById.get(event.data.id);if(!row)return;
  selectionFromParent=true;
  try{jumpMessage(row);}finally{selectionFromParent=false;}
});
const initial=[...messages,...D.undated].find(r=>r.id===decodeURIComponent(location.hash.slice(1)));if(initial)jumpMessage(initial);
if(window.self!==window.top&&!document.documentElement.classList.contains('log-only')){
  const reportHeight=()=>{
    window.parent.postMessage({type:'session-replay-height',height:Math.ceil(document.querySelector('main').getBoundingClientRect().height)},'*');
  };
  new ResizeObserver(reportHeight).observe(document.querySelector('main'));
  window.addEventListener('resize',reportHeight);
  reportHeight();
}

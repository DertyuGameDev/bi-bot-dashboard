// Native Plotly interactions; persistence never replaces a plot with an SVG preview.
const plotlyClone=value=>JSON.parse(JSON.stringify(value));
const plotlySame=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
function plotlyInterpolate(v,xs,ys){
  if(xs.length===1)return ys[0]+v-xs[0];
  let i=v<=xs[0]?0:v>=xs.at(-1)?xs.length-2:Math.max(0,xs.findIndex(x=>x>=v)-1);
  return ys[i]+(v-xs[i])*(ys[i+1]-ys[i])/(xs[i+1]-xs[i]);
}
function plotlyPeriod(value){const m=String(value).match(/^(\d{4})(?:-(\d{2})|Q([1-4]))?$/);return m?Number(m[1])*12+(m[2]?Number(m[2])-1:m[3]?(Number(m[3])-1)*3:0):NaN;}
export function linkPlotlyZoom(source,range){
  const group=source.root.closest('.message-plots, [data-plotly-group]');
  if(!group || group.dataset.syncZoom!=='true' || !source.chart.plotly.meta.periods.length)return;
  const periods=source.chart.plotly.meta.periods.map(plotlyPeriod),positions=periods.map((_,i)=>i);
  for(const mount of group.querySelectorAll('.chart-mount')){
    const target=mount._plotlyState;if(!target||target===source||!target.ready||!target.chart.plotly.meta.periods.length)continue;
    const dates=target.chart.plotly.meta.periods.map(plotlyPeriod),pos=dates.map((_,i)=>i);
    const next=range?range.map(x=>plotlyInterpolate(plotlyInterpolate(x,positions,periods),dates,pos)):target.chart.plotly.meta.full_ranges[0];
    target.linkedX=next;
    applyPlotlyRanges(target,{xaxis:next});
  }
}
function applyPlotlyRanges(state,ranges){
  if(!state.ready)return Promise.resolve();
  state.applying++;
  const patch={};for(const [axis,range]of Object.entries(ranges)){patch[axis+'.range']=range;patch[axis+'.autorange']=false;}
  return Plotly.relayout(state.plot,patch).finally(()=>state.applying--);
}
function plotlyRanges(state,zoom){
  const meta=state.chart.plotly.meta;
  if(!zoom)return {xaxis:meta.full_ranges[0],yaxis:meta.full_ranges[1]};
  const [dx,dy]=meta.offset,x=zoom.slice(0,2).map(v=>v-dx),y=zoom.slice(2).map(v=>v-dy);
  if(['horizontal_bar','heatmap'].includes(meta.kind))y.reverse();
  return {xaxis:x,yaxis:y};
}
function plotlyQueue(state,changes,deferred=true){
  state.pending={changes:{...(state.pending?.changes||{}),...changes},seq:++state.seq};
  renderPlotlyTools(state);clearTimeout(state.timer);
  state.first ||= Date.now();
  state.timer=setTimeout(()=>flushPlotly(state),deferred?Math.max(0,Math.min(240,500-(Date.now()-state.first))):0);
}
async function flushPlotly(state){
  clearTimeout(state.timer);state.first=0;
  if(state.flight||!state.pending||!state.send)return;
  const pending=state.pending;
  state.flight=true;
  try{await state.send(pending.changes);}finally{
    state.flight=false;
    if(state.pending?.seq===pending.seq){state.pending=null;renderPlotlyTools(state);renderPlotlyFigure(state);}
    else if(state.pending)flushPlotly(state);
  }
}
function plotlyViewportEvent(state,event){
  if(state.applying||!state.send||!state.ready||!state.chart.plotly.meta.cartesian)return;
  if(!Object.keys(event).some(k=>/^([xy]axis)\.(range|autorange)/.test(k)))return;
  state.gestureUntil=0;
  state.linkedX=null;
  if(event['xaxis.autorange']===true||event['yaxis.autorange']===true){
    applyPlotlyRanges(state,plotlyRanges(state,null));plotlyQueue(state,{zoom:null});linkPlotlyZoom(state,null);return;
  }
  const x=state.plot._fullLayout.xaxis.range,y=state.plot._fullLayout.yaxis.range,[dx,dy]=state.chart.plotly.meta.offset;
  if(![...x,...y].every(Number.isFinite))return;
  const zoom=[...x.map(v=>v+dx).sort((a,b)=>a-b),...y.map(v=>v+dy).sort((a,b)=>a-b)];
  plotlyQueue(state,{zoom});linkPlotlyZoom(state,x);
}
function bindPlotlyEvents(state){
  const plot=state.plot;
  plot.on('plotly_relayout',event=>plotlyViewportEvent(state,event));
  plot.on('plotly_relayouting',event=>plotlyViewportEvent(state,event));
  plot.on('plotly_hover',event=>{
    const p=event.points?.[0];if(!p)return;
    state.tip.textContent=String(p.customdata||p.hovertext||p.text||'').replace(/<br\s*\/?\s*>/gi,' · ').replace(/<[^>]*>/g,'');
  });
  plot.on('plotly_unhover',()=>{state.tip.textContent=state.selectedText||'';});
  plot.on('plotly_click',event=>{
    if(state.chart.plotly.meta.cartesian)return;
    const point=event.points?.[0];if(!point)return;
    selectPlotlySector(state,state.piePoint===point.pointNumber?null:point.pointNumber);
  });
  plot.addEventListener('wheel',e=>{if(e.target.closest('.draglayer'))state.gestureUntil=performance.now()+200;},{capture:true,passive:true});
  plot.tabIndex=0;plot.dataset.focusKey='plot';plot.setAttribute('role','group');
  plot.addEventListener('keydown',e=>{
    if(e.ctrlKey||e.metaKey||e.altKey)return;
    if(['+','=','-','−','0'].includes(e.key)&&state.chart.plotly.meta.cartesian&&state.send){e.preventDefault();plotlyZoomAction(state,e.key==='0'?null:['+','='].includes(e.key) ? .7 : 1/.7);}
  });
}
function selectPlotlySector(state,index){
  const trace=state.chart.plotly.data[0],labels=trace.labels,total=trace.values.reduce((a,b)=>a+(b||0),0);
  state.piePoint=index;
  const colors=trace.marker.colors.map((color,i)=>{
    if(index===null||i===index)return color;
    const hex=String(color).match(/^#([0-9a-f]{6})$/i);
    if(!hex)return color;
    const rgb=[0,2,4].map(n=>parseInt(hex[1].slice(n,n+2),16));
    return `rgba(${rgb.join(',')},0.35)`;
  });
  Plotly.restyle(state.plot,{pull:[labels.map(()=>0)],'marker.colors':[colors]},[0]);
  const share=index===null?100:100*trace.values[index]/total,percent=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(share)+'%';
  state.applying++;Plotly.relayout(state.plot,{'annotations[0].text':percent}).finally(()=>{state.applying--;fitPlotlyPieLabel(state);});
  const select=state.tools.querySelector('.pie-category');if(select)select.value=index===null?'':String(index);
  state.selectedText=index===null?'':labels[index]+' · '+trace.customdata[index]+' · '+percent;state.tip.textContent=state.selectedText;
}
function fitPlotlyPieLabel(state){
  if(!state.ready||state.chart.plotly.meta.cartesian||state.fittingLabel||!state.root.isConnected)return;
  const paths=[...state.plot.querySelectorAll('.pielayer .slice path')],text=state.plot.querySelector('.annotation-text');
  if(!paths.length||!text)return;
  const boxes=paths.map(n=>n.getBoundingClientRect()),label=text.getBoundingClientRect();
  const diameter=Math.min(Math.max(...boxes.map(b=>b.right))-Math.min(...boxes.map(b=>b.left)),Math.max(...boxes.map(b=>b.bottom))-Math.min(...boxes.map(b=>b.top)));
  if(diameter<=0||!label.width)return;
  const current=parseFloat(getComputedStyle(text).fontSize)||20,space=diameter*state.plot.data[0].hole*.82;
  const size=Math.max(8,Math.min(20,Math.floor(current*space/Math.max(label.width,label.height))));
  if(size===current)return;
  state.fittingLabel=true;state.applying++;
  Plotly.relayout(state.plot,{'annotations[0].font.size':size}).finally(()=>{state.applying--;state.fittingLabel=false;});
}
function plotlyZoomAction(state,factor){
  if(!state.ready)return;
  if(factor===null){applyPlotlyRanges(state,plotlyRanges(state,null));plotlyQueue(state,{zoom:null},false);linkPlotlyZoom(state,null);return;}
  const ranges={};
  for(const name of ['xaxis','yaxis']){const [a,b]=state.plot._fullLayout[name].range,mid=(a+b)/2,delta=(b-a)*factor/2;ranges[name]=[mid-delta,mid+delta];}
  Plotly.relayout(state.plot,{'xaxis.range':ranges.xaxis,'yaxis.range':ranges.yaxis,'xaxis.autorange':false,'yaxis.autorange':false});
}
function renderPlotlyTools(state){
  const tools=state.tools,hidden=state.pending?.changes.hidden||state.spec.hidden;
  for(const series of state.chart.metadata.series){
    let button=tools.querySelector('[data-series-key="'+series.id+'"]');
    if(!button){button=document.createElement('button');button.type='button';button.className='legend-button';button.dataset.seriesKey=series.id;button.dataset.focusKey='series:'+series.id;tools.insertBefore(button,tools.querySelector('.zoom-controls'));}
    button.textContent=series.label;button.style.setProperty('--series',series.color);
    button.setAttribute('aria-pressed',String(!hidden.includes(series.id)));
    button.disabled=!state.send||(!hidden.includes(series.id)&&state.chart.metadata.series.filter(s=>!hidden.includes(s.id)).length<=1);
    button.title=button.disabled?'Хотя бы одна серия должна быть видимой':'Показать или скрыть серию';
    button.onclick=()=>{
      const next=hidden.includes(series.id)?hidden.filter(k=>k!==series.id):[...hidden,series.id];
      Plotly.restyle(state.plot,{visible:state.chart.plotly.data.map(trace=>next.includes(trace.meta.key)?'legendonly':true)});
      plotlyQueue(state,{hidden:next},false);
    };
  }
  for(const button of tools.querySelectorAll('[data-series-key]'))if(!state.chart.metadata.series.some(s=>s.id===button.dataset.seriesKey))button.remove();
  const cartesian=state.chart.plotly.meta.cartesian;
  let zoom=tools.querySelector('.zoom-controls');
  if(!zoom){zoom=document.createElement('span');zoom.className='zoom-controls';tools.append(zoom);}
  zoom.hidden=!cartesian;
  if(cartesian&&!zoom.childElementCount){
    for(const[key,text,label,factor]of[['zoom-in','+','Увеличить масштаб',.7],['zoom-out','−','Уменьшить масштаб',1/.7],['reset-zoom','Сброс','Сброс масштаба',null]]){
      const b=document.createElement('button');b.type='button';b.textContent=text;b.className='zoom-button';b.dataset.focusKey=key;b.setAttribute('aria-label',label);b.title=label;b.onclick=()=>plotlyZoomAction(state,factor);zoom.append(b);
    }
    const pan=document.createElement('button');pan.type='button';pan.className='plotly-pan';pan.dataset.focusKey='pan';pan.textContent='Перемещение';
    pan.onclick=()=>{const enabled=state.plot._fullLayout.dragmode!=='pan';pan.setAttribute('aria-pressed',String(enabled));state.applying++;Plotly.relayout(state.plot,{dragmode:enabled?'pan':'zoom'}).finally(()=>state.applying--);};pan.setAttribute('aria-pressed','false');zoom.append(pan);
  }
  for(const b of zoom.querySelectorAll('button'))b.disabled=!state.send;
  zoom.querySelector('.plotly-pan')?.setAttribute('aria-pressed',String(state.plot._fullLayout?.dragmode==='pan'));
  let category=tools.querySelector('.pie-category');
  if(!cartesian){
    if(!category){category=document.createElement('select');category.className='pie-category';category.dataset.focusKey='pie-sector';category.setAttribute('aria-label','Выделить сектор');tools.append(category);}
    const labels=state.chart.plotly.data[0].labels;
    if(category.dataset.labels!==JSON.stringify(labels)){
      category.replaceChildren(new Option('Все секторы',''),...labels.map((label,i)=>new Option(label.replace(/&quot;/g,'"').replace(/&amp;/g,'&'),String(i))));category.dataset.labels=JSON.stringify(labels);
    }
    category.onchange=()=>selectPlotlySector(state,category.value===''?null:Number(category.value));
    category.value=state.piePoint===null||state.piePoint===undefined?'':String(state.piePoint);
  }else category?.remove();
}
async function renderPlotlyFigure(state){
  state.queued=true;if(state.drawing)return;
  state.drawing=true;
  try{while(state.queued){
    state.queued=false;
    const f=plotlyClone(state.chart.plotly),effective={...state.spec,...state.pending?.changes};
    const base=plotlyClone(f);delete base.layout.xaxis.range;delete base.layout.yaxis.range;
    for(const trace of base.data)delete trace.visible;
    const signature=JSON.stringify([base, state.context,!!state.send]);
    const ranges=plotlyRanges(state,effective.zoom),view=state.plot;
    if(state.linkedX)ranges.xaxis=state.linkedX;
    state.applying++;
    try{
      if(signature!==state.signature){
        if(!f.meta.cartesian){state.piePoint=null;state.selectedText='';state.tip.textContent='';}
        f.layout.height=state.height;f.layout.uirevision=JSON.stringify([state.context,state.spec.id,state.spec.type,state.spec.x,state.spec.y]);
        if(f.meta.cartesian){f.layout.xaxis.range=ranges.xaxis;f.layout.yaxis.range=ranges.yaxis;}
        for(const trace of f.data)trace.visible=effective.hidden.includes(trace.meta.key)?'legendonly':true;
        await Plotly.react(view,f.data,f.layout,{scrollZoom:true,responsive:true,displayModeBar:false,displaylogo:false,locale:'ru',doubleClick:'autosize',staticPlot:!state.send});
        if(!state.ready){state.ready=true;bindPlotlyEvents(state);}
        state.signature=signature;
      }else if(f.meta.cartesian&&!state.pending){
        const actual={xaxis:view._fullLayout.xaxis.range,yaxis:view._fullLayout.yaxis.range};
        if(!plotlySame(actual,ranges))await Plotly.relayout(view,{'xaxis.range':ranges.xaxis,'yaxis.range':ranges.yaxis,'xaxis.autorange':false,'yaxis.autorange':false});
      }
    }finally{state.applying--;}
    fitPlotlyPieLabel(state);
    view.setAttribute('aria-label','График «'+state.spec.title+'». '+(f.meta.cartesian?'Колесо — масштаб, выделение — приблизить область; плюс, минус, 0 — сброс.':'Наведите на сектор или выберите его в списке для точных значений.'));
    renderPlotlyTools(state);
  }}catch(e){state.tip.textContent='Не удалось отрисовать график: '+e.message;}finally{state.drawing=false;}
}
export function chartWidget(root,chart,spec,onChange,options={}){
  if(!chart.plotly||typeof Plotly==='undefined'){
    root._plotlyDispose?.();root.replaceChildren();
    const fallback=document.createElement('div');fallback.className='chart-metric';
    if(chart.svg){fallback.innerHTML=chart.svg;fallback.setAttribute('aria-label','Обновляю интерактивный график');}
    else{for(const item of chart.values||[]){const row=document.createElement('p');row.textContent=item.label+': '+item.formatted;fallback.append(row);}const note=document.createElement('p');note.textContent=chart.notice||'Нет данных';fallback.append(note);}
    root.append(fallback);return;
  }
  let state=root._plotlyState;
  if(!state){
    const plot=document.createElement('div'),tools=document.createElement('div'),tip=document.createElement('output');plot.className='chart-plotly';plot.dataset.uiScroll='plot';tools.className='chart-tools';tip.className='plotly-status';tip.setAttribute('aria-live','polite');
    root.replaceChildren(plot,tools,tip);
    state=root._plotlyState={root,plot,tools,tip,seq:0,pending:null,flight:false,applying:0,ready:false};
    root._zoomSession={get pending(){return state.pending||(performance.now()<(state.gestureUntil||0)?{gesture:true}:null);}};
    const observer=new ResizeObserver(()=>{if(state.ready&&root.isConnected&&root.clientWidth>0)Promise.resolve(Plotly.Plots.resize(plot)).then(()=>fitPlotlyPieLabel(state)).catch(()=>{});});observer.observe(root);
    root._plotlyDispose=()=>{clearTimeout(state.timer);observer.disconnect();Plotly.purge(plot);delete root._plotlyState;delete root._zoomSession;};
  }
  const signature=JSON.stringify([options.context,spec.type,spec.source,spec.x,spec.y,spec.group]);
  if(state.context&&state.context!==signature){clearTimeout(state.timer);state.pending=null;state.linkedX=null;state.seq++;}
  state.context=signature;state.chart=chart;state.spec=spec;state.send=onChange;
  state.height=Math.max(options.compact?180:240,Math.min(420,spec.height));state.plot.style.height=state.height+'px';
  renderPlotlyFigure(state);
}

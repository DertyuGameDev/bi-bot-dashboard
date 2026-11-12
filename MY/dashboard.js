import { chartWidget } from '/assets/chart-widget.js';
import { outletPicker } from '/assets/outlet-picker.js';
import { filterPicker } from '/assets/filter-picker.js';

const number = (value, compact = false) => value == null ? '—' : Number(value).toLocaleString('ru-RU', {
  maximumFractionDigits: compact ? 1 : 2, ...(compact ? {notation:'compact'} : {}),
});
export const METRICS=[['amount','Объём продаж'],['discount','Сумма скидок'],['deals','Количество сделок'],['completion','Выполнение плана']];
export const VIEW_MODES=[['absolute','Абсолютные значения'],['percent','Доли, %'],['dynamics','Динамика']];
export const metricMeta=metric=>({amount:{label:'Выручка',unit:' ₽',kind:'money'},discount:{label:'Скидки',unit:' ₽',kind:'money'},deals:{label:'Сделки',unit:'',kind:'number'},completion:{label:'Выполнение',unit:' %',kind:'percent'},quantity:{label:'Количество',unit:'',kind:'number'}}[metric]);
const signed=value=>value==null ? '—' : (value>0 ? '+' : '')+number(value);
function previousLabel(period){
  if(!period)return 'Нет базы сравнения';
  const [sy,sm,sd]=period.start.split('-'),[ey,em,ed]=period.end.split('-');
  if(sy===ey && sm==='01' && sd==='01' && em==='12' && ed==='31')return 'к '+sy+' году';
  const date=value=>new Date(value+'T00:00:00').toLocaleDateString('ru-RU');
  return 'к '+date(period.start)+' — '+date(period.end);
}
export function dashboardSummaryLines(data){
  const k=data.kpis;if(!k)return [];
  if(k.rows===0)return ['Нет данных для выбранных фильтров. Измените период или выбор объектов.'];
  const f=data.filters,totals=k.totals || k,metric=totals.metric || f.metric,meta=metricMeta(metric);
  const label={discount:'Сумма скидок',deals:'Количество сделок',quantity:'Количество товара',completion:'Выполнение плана'}[metric] || meta.label;
  const known=v=>typeof v==='number' && Number.isFinite(v),amount=v=>number(v)+meta.unit;
  const date=v=>v ? v.slice(0,10).split('-').reverse().join('.') : '';
  const start=date(f.start || data.period?.[0]),end=date(f.end || data.period?.[1]);
  const period=start && end ? ' за '+start+' — '+end : '';
  const scope=f.regions?.length ? ' ('+(f.regions.length<=2 ? f.regions.join(', ') : 'регионов: '+f.regions.length)+')' : '';
  let main=known(totals.fact) ? label+period+scope+' — '+amount(totals.fact) : label+period+scope+': значение не определено';
  main+=known(totals.plan) ? ' при плане '+amount(totals.plan)+'.' : '. План для этой метрики не задан.';
  const details=[];
  if(known(k.completion))details.push('Выполнение плана — '+number(k.completion)+'%.');
  else if(known(totals.plan))details.push('Выполнение плана не определено.');
  if(known(totals.deviation)){
    const delta=Math.abs(totals.deviation)<0.005 ? 0 : totals.deviation;
    details.push(delta===0 ? 'Факт соответствует плану.' : 'Отклонение от плана — '+signed(delta)+meta.unit+(known(totals.deviation_pct) ? ' ('+signed(totals.deviation_pct)+'%)' : '')+'.');
  }
  const context=[];
  if(known(k.growth))context.push((f.metric==='completion' ? 'Изменение выполнения ' : 'Изменение ')+(k.comparison_period ? previousLabel(k.comparison_period) : 'к предыдущему периоду')+': '+signed(k.growth)+(k.growth_unit==='points' ? ' п.п.' : '%.'));
  else context.push('Сравнение с предыдущим периодом недоступно.');
  const activity=[['точек',k.active_outlets],['клиентов',k.active_clients],['продуктов',k.active_products]].filter(([,v])=>known(v)).map(([label,v])=>label+': '+number(v));
  if(activity.length)context.push('Активных '+activity.join(', ')+'.');
  return [main,details.join(' '),context.join(' ')].filter(Boolean);
}
function dashboardSummary(data,previous=null){
  const root=previous || document.createElement('section');root.className='dashboard-summary';root.setAttribute('aria-label','Краткий итог дашборда');
  const lines=dashboardSummaryLines(data);root.hidden=!lines.length;
  const heading=document.createElement('h4');heading.textContent='Краткий итог';
  root.replaceChildren(heading,...lines.map(text=>{const p=document.createElement('p');p.textContent=text;return p;}));
  return root;
}
export function kpiCards(k,metric,previous=null){
  const root=previous || document.createElement('dl');root.className='plot-metrics kpi-cards';root.dataset.uiKey='kpis';root.setAttribute('aria-label','Ключевые показатели');
  const totals=k.totals || k,meta=metricMeta(totals.metric || metric),unit=meta.unit;
  const value=(v,suffix=unit)=>v==null ? '—' : number(v,true)+suffix;
  const exact=(v,suffix=unit)=>v==null ? 'Нет данных' : number(v)+suffix;
  const direction=v=>v==null || v===0 ? '' : v>0 ? 'positive' : 'negative';
  const growthUnit=k.growth_unit==='points' ? ' п.п.' : ' %';
  const growthCaption=k.growth==null ? (k.growth_notice?.includes('нулевой') ? 'Нулевая база сравнения' : 'Нет базы сравнения') : previousLabel(k.comparison_period);
  const entries=[
    ['plan',!k.totals && metric==='completion' ? 'Цель' : 'Общий план',value(totals.plan),totals.plan==null ? 'План не задан' : 'Выбранный период',exact(totals.plan),''],
    ['fact',!k.totals && metric==='completion' ? 'Выполнение' : 'Общий факт',value(totals.fact),meta.label,exact(totals.fact),''],
    ['completion','Выполнение плана',value(k.completion,' %'),k.completion==null ? 'План не задан' : 'Факт ÷ план',exact(k.completion,' %'),direction(k.completion==null ? null : k.completion-100)],
    ['deviation','Отклонение',totals.deviation==null ? '—' : (totals.deviation>0 ? '+' : '')+value(totals.deviation),totals.deviation_pct==null ? 'План не задан' : signed(totals.deviation_pct)+' % к плану',exact(totals.deviation)+'; '+exact(totals.deviation_pct,' %'),direction(totals.deviation)],
    ['clients','Активные клиенты',value(k.active_clients,''),k.active_clients==null ? 'Нет детализации' : 'С фактическими продажами',exact(k.active_clients,'')+'; '+(k.activity_notice || ''),''],
    ['outlets','Активные точки',value(k.active_outlets,''),'С фактическими продажами',exact(k.active_outlets,''),''],
    ['products','Активные продукты',value(k.active_products,''),'С фактическими продажами',exact(k.active_products,''),''],
    ['growth',metric==='completion' ? 'Динамика выполнения' : 'Динамика факта',k.growth==null ? '—' : signed(k.growth)+growthUnit,growthCaption,k.growth==null ? k.growth_notice || 'Нет базы сравнения' : signed(k.growth)+growthUnit+'; '+previousLabel(k.comparison_period),metric==='discount' ? '' : direction(k.growth)],
  ];
  for(const [key,label,display,caption,title,tone] of entries){
    let card=[...root.children].find(n=>n.dataset.kpiKey===key);
    if(!card){card=document.createElement('div');card.dataset.kpiKey=key;const dt=document.createElement('dt'),dd=document.createElement('dd'),small=document.createElement('small');dd.tabIndex=0;dd.dataset.focusKey='kpi:'+key;card.append(dt,dd,small);root.append(card);}
    card.className='kpi-card '+(['clients','outlets','products','growth'].includes(key) ? 'kpi-secondary' : 'kpi-primary')+(tone ? ' kpi-'+tone : '');card.title=label+': '+title;
    card.querySelector('dt').textContent=key==='growth' ? 'Динамика' : label;card.querySelector('dd').textContent=display;card.querySelector('dd').setAttribute('aria-label',label+': '+title);card.querySelector('small').textContent=caption;
  }
  return root;
}
function selectField(fields,label,key,values,value,onChange) {
  return filterPicker(fields,{key,label,values,current:[String(value)],multiple:false,all:'Выберите значение',hint:key==='dimension' ? 'Сравнение и тепловая карта — по выбранным объектам. Динамика — по времени.' : undefined,onChange:onChange ? selected=>onChange(selected[0]) : null});
}
const resetFilters=data=>({regions:[],outlets:[],products:[],clients:[],start:data.period?.[0],end:data.period?.[1],min_amount:null,max_amount:null,metric:'amount',value_mode:'absolute'});
function toolbar(root,data,options) {
  let bar=root.querySelector(':scope > .plot-settings');
  if (!bar) {
    bar=document.createElement('div');bar.className='plot-settings';bar.dataset.uiKey='settings';
    bar.setAttribute('role','group');
    const fields=document.createElement('div');fields.className='plot-filter-fields';bar.append(fields);root.append(bar);
  }
  const fields=bar.querySelector('.plot-filter-fields'), f=data.filters, change=options.onFilters;
  bar.setAttribute('aria-label',`Общие фильтры всех графиков: ${data.charts.length}`);
  bar.title='Фильтры применяются ко всем графикам этого дашборда одновременно';
  let mobileToggle=root.querySelector(':scope > .mobile-filter-toggle');
  if(!mobileToggle){
    mobileToggle=document.createElement('button');mobileToggle.type='button';mobileToggle.className='mobile-filter-toggle';mobileToggle.dataset.focusKey='mobile-filters';
    bar.id='dashboard-filters-'+crypto.randomUUID();mobileToggle.setAttribute('aria-controls',bar.id);root.insertBefore(mobileToggle,bar);
    root.dataset.filtersOpen='false';
  }
  const showFilters=()=>{
    mobileToggle.setAttribute('aria-expanded',root.dataset.filtersOpen==='true'?'true':'false');
    const label=(root.dataset.filtersOpen==='true'?'Свернуть фильтры':'Фильтры и настройки')+' · '+(f.regions.length?f.regions.join(', '):'Все регионы');
    const period=document.createElement('small');period.textContent=[f.start,f.end].filter(Boolean).map(v=>v.split('-').reverse().join('.')).join(' — ')+(f.outlets.length?' · Точек выбрано: '+f.outlets.length:'');
    mobileToggle.replaceChildren(document.createTextNode(label),period);
  };
  showFilters();mobileToggle.onclick=()=>{root.dataset.filtersOpen=root.dataset.filtersOpen==='true'?'false':'true';showFilters();};
  const regions=options.regions || f.regions, outlets=options.outlets || f.outlets;
  filterPicker(fields,{key:'regions',label:'Регион',values:regions,current:f.regions,all:'Все регионы',onChange:change ? values=>change({regions:values,outlets:[]}) : null});
  outletPicker(fields,outlets,f.outlets,change ? values=>change({outlets:values}) : null,{
    pending:options.cascadePending,
    hint:(f.regions.length ? 'Только точки выбранных регионов. ' : 'Точки всех регионов. ')+`Доступно: ${outlets.length}. Фильтры общие для всех графиков.`,
  });
  let dates=fields.querySelector('.plot-date-range');
  if (!dates) {
    dates=document.createElement('div');dates.className='plot-date-range';
    for (const [key,label] of [['start','С'],['end','По']]) {
      const field=document.createElement('label');field.className='plot-field';
      const name=document.createElement('span');name.textContent=label;
      const input=document.createElement('input');input.type='date';input.dataset.dateKey=key;input.dataset.focusKey='filter:'+key;
      input.setAttribute('aria-label',key==='start' ? 'Начало периода дашборда' : 'Конец периода дашборда');
      field.append(name,input);dates.append(field);
    }
    fields.append(dates);
  }
  for (const input of dates.querySelectorAll('input')) {
    input.value=f[input.dataset.dateKey] || '';input.disabled=!change;
    if(data.period){input.min=input.dataset.dateKey==='end' ? f.start || data.period[0] : data.period[0];input.max=input.dataset.dateKey==='start' ? f.end || data.period[1] : data.period[1];}
    input.onchange=change ? ()=>{if(input.reportValidity())change({[input.dataset.dateKey]:input.value || null});} : null;
  }
  selectField(fields,'Разрез','dimension',[['region','Регионы'],['outlet','Точки'],['product','Продукты'],...(f.dimension==='client' ? [['client','Клиенты']] : [])],f.dimension,change ? value=>change({dimension:value}) : null);
  selectField(fields,'Метрика','metric',[...METRICS,...(f.metric==='quantity' ? [['quantity','Количество товара']] : [])],f.metric,change ? value=>change({metric:value}) : null);
  let secondary=bar.querySelector('.plot-secondary-row');
  if(!secondary){secondary=document.createElement('div');secondary.className='plot-secondary-row';bar.append(secondary);}
  let advanced=secondary.querySelector('.plot-advanced');
  if(!advanced){
    advanced=document.createElement('details');advanced.className='plot-advanced';advanced.dataset.uiKey='advanced';
    const summary=document.createElement('summary');summary.dataset.focusKey='advanced';
    const label=document.createElement('span'),current=document.createElement('span');label.textContent='Настройки графиков';current.className='plot-settings-summary';summary.append(label,current);
    const extras=document.createElement('div');extras.className='plot-extra-fields';advanced.append(summary,extras);secondary.append(advanced);
  }
  advanced.querySelector('.plot-settings-summary').textContent=[{month:'Месяцы',quarter:'Кварталы',year:'Годы'}[f.granularity],{absolute:'Значения',percent:'Доли, %',dynamics:'Динамика'}[f.value_mode],'Топ-'+f.top_n].join(' · ');
  const extras=advanced.querySelector('.plot-extra-fields');
  selectField(extras,'Группировка по времени','granularity',[['month','Месяцы'],['quarter','Кварталы'],['year','Годы']],f.granularity,change ? value=>change({granularity:value}) : null);
  selectField(extras,'Отображение значений','value_mode',VIEW_MODES,f.value_mode,change ? value=>change({value_mode:value}) : null);
  const limits=[...new Set([5,10,20,30,50,f.top_n])].sort((a,b)=>a-b);
  selectField(extras,'Количество в рейтинге','top_n',limits.map(n=>[n,`Топ-${n}`]),f.top_n,change ? value=>change({top_n:Number(value)}) : null);
  let linked=secondary.querySelector('.sync-zoom-control');
  if(!linked){linked=document.createElement('label');linked.className='sync-zoom-control';const input=document.createElement('input');input.type='checkbox';input.dataset.focusKey='sync-zoom';linked.append(input,document.createTextNode('Связать масштаб по времени'));secondary.append(linked);}
  const canLink=data.charts.filter(c=>c.spec.x==='period' && ['line','bar','grouped_bar','stacked_bar','heatmap'].includes(c.spec.type)).length>=2;
  linked.querySelector('input').checked=!!data.sync_zoom;linked.querySelector('input').disabled=!options.onSync || !canLink;
  linked.title=canLink ? 'Временные графики меняют общую горизонтальную область. Вертикальные оси и итоговые KPI сохраняются.' : 'Нужны хотя бы два графика с осью времени';
  linked.querySelector('input').onchange=e=>options.onSync?.(e.target.checked);
  let reset=bar.querySelector('.plot-reset');
  if(!reset){reset=document.createElement('button');reset.type='button';reset.className='plot-reset';reset.dataset.focusKey='reset-filters';reset.textContent='Сбросить';secondary.append(reset);}
  reset.hidden=!change;reset.onclick=()=>change?.(resetFilters(data));
  let chips=root.querySelector(':scope > .plot-active-filters');
  if(!chips){chips=document.createElement('div');chips.className='plot-active-filters';root.append(chips);}
  chips.replaceChildren();
  function chip(text,title,patch){
    const node=document.createElement(change ? 'button' : 'span');node.textContent=text+(change ? ' ×' : '');node.title=title || text;
    if(change){node.type='button';node.setAttribute('aria-label','Сбросить фильтр: '+text);node.onclick=()=>change(patch);}chips.append(node);
  }
  for(const [key,label] of [['products','Продукты'],['clients','Клиенты']])if(f[key]?.length)chip(`${label}: ${f[key].length}`,f[key].join(', '),{[key]:[]});
  if(f.min_amount!=null || f.max_amount!=null)chip(`Сумма строки: ${number(f.min_amount)} — ${number(f.max_amount)}`,'',{min_amount:null,max_amount:null});
  chips.hidden=!chips.childElementCount;
}

// Reconcile by id so filters, open values and scroll survive updates.
export function messageCharts(data,options={},previous=null) {
  const root=previous || document.createElement('section');root.className='message-plots';root.dataset.editable=String(!!options.onFilters);root.dataset.uiKey='dashboard';
  root.setAttribute('role','group');root.setAttribute('aria-label','Дашборд ответа');
  let heading=root.querySelector(':scope > .plot-heading');
  if(!heading){heading=document.createElement('div');heading.className='plot-heading';const title=document.createElement('h3'),status=document.createElement('span');status.className='plot-status';status.setAttribute('role','status');heading.append(title,status);root.prepend(heading);}
  heading.querySelector('h3').textContent=data.title;
  root.dataset.statusText=options.onFilters ? (data.saved ? 'Автосохранено' : 'Изменения не сохранены') : data.saved_id ? 'Сохранён' : 'Снимок ответа';
  heading.querySelector('.plot-status').textContent=root.getAttribute('aria-busy')==='true' ? 'Обновляю…' : root.dataset.statusText;
  let actions=heading.querySelector('.plot-actions');
  if(!actions){actions=document.createElement('div');actions.className='plot-actions';heading.append(actions);}
  let resume=actions.querySelector('[data-resume-dashboard]');
  if(options.onContinue){
    if(!resume){resume=document.createElement('button');resume.type='button';resume.dataset.resumeDashboard='true';resume.dataset.focusKey='continue-dashboard';actions.prepend(resume);}
    resume.textContent=options.active ? 'Сейчас в работе' : 'Продолжить работу';resume.disabled=options.active;resume.onclick=options.onContinue;
    resume.title=options.active ? 'Новые сообщения относятся к этому дашборду' : 'Продолжить анализ этого дашборда в чате';
  }
  root.classList.toggle('is-working',!!options.active);
  let save=actions.querySelector('[data-save-dashboard]');
  if(options.onSave){
    if(!save){save=document.createElement('button');save.type='button';save.dataset.saveDashboard='true';save.dataset.focusKey='save-dashboard';save.textContent='Название и копия';actions.append(save);}
    save.title='Изменения сохраняются автоматически. Изменить название или создать копию';save.onclick=options.onSave;
  }else save?.remove();
  let html=actions.querySelector('[data-download-html]');
  if(options.onDownload){
    if(!html){html=document.createElement('button');html.type='button';html.dataset.downloadHtml='true';html.dataset.focusKey='download-html';html.textContent='Скачать HTML';actions.append(html);}
    html.title='Скачать дашборд: графики, KPI и краткий итог в одном HTML-файле';
    html.onclick=async()=>{
      if(html.disabled)return;html.disabled=true;html.dataset.downloading='true';html.setAttribute('aria-busy','true');
      try{await options.onDownload('html');}finally{html.disabled=false;delete html.dataset.downloading;html.removeAttribute('aria-busy');}
    };
  }else html?.remove();
  let more=actions.querySelector('[data-card-actions]');
  if(options.onActions){if(!more){more=document.createElement('button');more.type='button';more.dataset.cardActions='true';more.dataset.focusKey='card-actions';more.textContent='Действия';actions.append(more);}more.onclick=options.onActions;more.title='Скачать, открыть историю версий или поделиться';}
  let open=actions.querySelector('[data-open-saved]');
  if(!options.onSave && data.saved_id && options.onOpen){
    if(!open){open=document.createElement('button');open.type='button';open.dataset.openSaved='true';open.dataset.focusKey='open-dashboard';open.textContent='Открыть';actions.append(open);}
    open.title='Вернуться к сохранённому дашборду';open.onclick=()=>options.onOpen(data.saved_id);
  }else open?.remove();
  actions.hidden=!actions.childElementCount;
  toolbar(root,data,options);
  let notice=root.querySelector(':scope > .plot-notice');
  if(!notice){notice=document.createElement('p');notice.className='plot-notice';notice.setAttribute('role','status');root.append(notice);}
  notice.textContent=[data.edit_notice,data.filter_notice,data.coverage_notice,data.view_notice].filter(Boolean).join(' ');notice.hidden=!notice.textContent;
  if(data.kpis){const metrics=kpiCards(data.kpis,data.filters.metric,root.querySelector(':scope > .plot-metrics'));root.insertBefore(metrics,root.querySelector('.plot-settings'));}
  let grid=root.querySelector(':scope > .message-chart-grid');if(!grid){grid=document.createElement('div');root.append(grid);}
  grid.className='message-chart-grid'+(data.charts.length===1 ? ' one-chart' : '')+(data.layout==='one-column' ? ' single-column' : '');
  root.dataset.syncZoom=String(!!data.sync_zoom);
  const alive=new Set();
  data.charts.slice(0,4).forEach((chart,index)=>{
    alive.add(chart.id);let card=[...grid.children].find(node=>node.dataset.chartId===chart.id);
    if(!card){
      card=document.createElement('figure');card.className='message-chart-card';card.dataset.chartId=chart.id;
      const caption=document.createElement('figcaption'),mount=document.createElement('div'),footer=document.createElement('div');
      mount.className='chart-mount';footer.className='chart-footer';card.append(caption,mount,footer);
    }
    card.querySelector('figcaption').textContent=chart.spec.title;
    let note=card.querySelector('.chart-note');
    if(chart.notice && chart.svg){if(!note){note=document.createElement('p');note.className='chart-note';card.querySelector('figcaption').after(note);}note.textContent=chart.notice;}else note?.remove();
    const mount=card.querySelector('.chart-mount'),footer=card.querySelector('.chart-footer'),oldPlot=mount.querySelector('.chart-svg');
    const left=oldPlot?.scrollLeft || 0,top=oldPlot?.scrollTop || 0;
    if(!mount._plotlyState)mount.replaceChildren();
    chartWidget(mount,chart,chart.spec,options.onChange ? changes=>options.onChange(chart.index,changes) : null,{compact:true,context:JSON.stringify(data.filters)});
    const plot=mount.querySelector('.chart-svg');if(plot){plot.scrollLeft=left;plot.scrollTop=top;}
    const tools=mount._plotlyState?.tools||mount.querySelector('.chart-tools'),previousTools=footer.querySelector('.chart-tools');
    if(previousTools!==tools){previousTools?.remove();if(tools)footer.prepend(tools);}
    let details=card.querySelector('.chart-values');
    if(chart.svg && Object.keys(chart.metadata.tips).length){
      if(!details){
        details=document.createElement('details');details.className='chart-values';details.dataset.uiKey='values:'+chart.id;
        const summary=document.createElement('summary');summary.textContent='Значения';summary.setAttribute('aria-label','Точные значения: '+chart.spec.title);
        const scroll=document.createElement('div');scroll.className='table-scroll';scroll.dataset.uiScroll='values';details.append(summary,scroll);footer.append(details);
      }
      details.hidden=false;const scroll=details.querySelector('.table-scroll'),scrollTop=scroll.scrollTop;
      const table=document.createElement('table'),head=table.createTHead().insertRow();
      for(const label of ['Точка / период','Показатель','Значение']){const th=document.createElement('th');th.textContent=label;th.scope='col';head.append(th);}
      const tbody=table.createTBody();
      for(const point of Object.values(chart.metadata.tips)){
        const row=tbody.insertRow(),series=chart.metadata.series.find(s=>s.id===point.series)?.label || point.series;
        for(const value of [point.label,series,point.value===null ? 'Нет данных' : number(point.value)])row.insertCell().textContent=value;
      }
      scroll.replaceChildren(table);scroll.scrollTop=scrollTop;
    }else if(details)details.hidden=true;
    if(grid.children[index]!==card)grid.insertBefore(card,grid.children[index] || null);
  });
  for(const card of [...grid.children])if(!alive.has(card.dataset.chartId)){card.querySelector('.chart-mount')._plotlyDispose?.();card.remove();}
  grid.after(dashboardSummary(data,root.querySelector(':scope > .dashboard-summary')));
  return root;
}

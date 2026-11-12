// Same Plotly widget as MY; this standalone snapshot saves changes only in memory.
const data=JSON.parse(document.querySelector('#snapshot').textContent);
document.querySelector('#kpis').append(kpiCards(data.kpis,data.filters.metric));
document.querySelector('#scope').textContent=[data.filters.start||data.period[0],data.filters.end||data.period[1]].join(' — ')+(data.filters.regions.length?' · '+data.filters.regions.join(', '):' · Все регионы');
for(const text of dashboardSummaryLines(data)){const p=document.createElement('p');p.textContent=text;document.querySelector('#summary').append(p);}
const sync=document.querySelector('#sync'),group=document.querySelector('main');
group.dataset.plotlyGroup='true';sync.checked=data.sync_zoom;group.dataset.syncZoom=String(sync.checked);
sync.onchange=()=>{group.dataset.syncZoom=String(sync.checked);};
for(const chart of data.charts){
 const figure=document.createElement('figure'),title=document.createElement('figcaption'),mount=document.createElement('div');
 title.textContent=chart.spec.title;mount.className='chart-mount';figure.append(title,mount);group.append(figure);
 chartWidget(mount,chart,chart.spec,changes=>{Object.assign(chart.spec,changes);return true;});
 const details=document.createElement('details'),summary=document.createElement('summary'),table=document.createElement('table'),scroll=document.createElement('div');
 summary.textContent='Точные значения';scroll.className='values';table.innerHTML='<thead><tr><th>Объект / период</th><th>Показатель</th><th>Значение</th></tr></thead>';
 const body=table.createTBody();for(const v of Object.values(chart.metadata.tips)){const row=body.insertRow();for(const text of [v.label,chart.metadata.series.find(s=>s.id===v.series)?.label||v.series,v.value==null?'Нет данных':number(v.value)])row.insertCell().textContent=text;}
 scroll.append(table);details.append(summary,scroll);figure.append(details);
}
sync.disabled=data.charts.filter(c=>c.plotly?.meta.periods.length).length<2;

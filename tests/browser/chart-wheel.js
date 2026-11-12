// Real native Plotly gestures across every supported chart type and offline HTML.
async(page)=>{
 const dir='/Users/sviat/Downloads/RPA/.local/verification/';
 const context=await page.context().browser().newContext({storageState:dir+'library-cookie.json',acceptDownloads:true,viewport:{width:1440,height:1000}});
 const checks=[],errors=[];let step='boot';
 const ok=(name,pass,data)=>{checks.push({name,pass:!!pass,...(data?{data}:{})});if(!pass)throw Error(name);};
 try{
  const p=await context.newPage();p.setDefaultTimeout(30000);p.on('pageerror',e=>errors.push(e.message));p.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  const root=()=>p.locator('#dashboard-mount .message-plots'),card=(i=0)=>root().locator('.message-chart-card').nth(i),mount=(i=0)=>card(i).locator('.chart-mount'),plot=(i=0)=>mount(i).locator('.chart-plotly');
  const ready=()=>p.waitForFunction(()=>[...document.querySelectorAll('#dashboard-mount .chart-mount')].length===4&&[...document.querySelectorAll('#dashboard-mount .chart-mount')].every(n=>n._plotlyState?.ready&&!n._plotlyState.drawing));
  const idle=()=>p.waitForFunction(()=>[...document.querySelectorAll('#dashboard-mount .chart-mount')].every(n=>n._plotlyState?.ready&&!n._plotlyState.drawing&&!n._zoomSession?.pending)&&!document.querySelector('#dashboard-mount .message-plots[aria-busy="true"]'));
  const ranges=i=>plot(i).evaluate(n=>({x:[...n._fullLayout.xaxis.range],y:[...n._fullLayout.yaxis.range]}));
  const response=()=>p.waitForResponse(r=>r.url().endsWith('/edit')&&r.request().method()==='POST');
  const save=async action=>{const[r]=await Promise.all([response(),action()]);ok('native gesture save HTTP 200',r.ok());const value=await r.json();await idle();return value;};
  const wheel=async(i=0,delta=-120)=>{await plot(i).scrollIntoViewIfNeeded();const b=await plot(i).locator('.nsewdrag').boundingBox();await p.mouse.move(b.x+b.width*.61,b.y+b.height*.48);await p.mouse.wheel(0,delta);};
  const reset=i=>save(()=>card(i).locator('[data-focus-key="reset-zoom"]').click());
  await p.goto('http://127.0.0.1:8081/?chat=main');await p.waitForFunction(()=>document.querySelector('#connection-status')?.textContent==='Готов к работе');await ready();
  const id=await p.locator('#dashboard-select').inputValue();
  const configure=async(changes,operation='chart')=>{
    await idle();
    const value=await p.evaluate(async({id,changes,operation})=>{const before=await(await fetch('/api/cards/'+id)).json();return(await fetch('/api/cards/'+id+'/edit',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'puzzle-rpa'},body:JSON.stringify({settings_revision:before.settings_revision,operation,arguments:operation==='chart'?{index:1,changes}:changes})})).json();},{id,changes,operation});
    ok('configuration API accepted',!!value.message);
    const[r]=await Promise.all([p.waitForResponse(r=>/\/api\/cards\/[^/]+$/.test(r.url())),p.locator('.message-dashboard-link').first().click()]);await r.json();await ready();return value;
  };
  await configure({regions:[]},'filters');await reset(0);
  const state=await p.evaluate(async()=>await(await fetch('/api/state')).json());const kpis=JSON.stringify(state.kpis);
  step='first wheel';const native=await plot().evaluateHandle(n=>n),before=await ranges(0);
  await p.route('**/api/cards/*/edit',async route=>{await new Promise(r=>setTimeout(r,700));await route.continue();});
  let returned=false;const completed=response().then(r=>{returned=true;return r;});await wheel(0);
  await p.waitForFunction(old=>{const a=document.querySelector('#dashboard-mount .chart-plotly')._fullLayout.xaxis.range;return a[1]-a[0]<old;},before.x[1]-before.x[0]);
  ok('native wheel updates axes before server response',!returned);
  for(let i=0;i<5;i++){await p.mouse.wheel(0,-35);await p.waitForTimeout(20);}
  await completed;await idle();
  const after=await ranges(0),saved=await p.evaluate(async id=>(await(await fetch('/api/cards/'+id)).json()).message.dashboard,id);
  ok('last wheel range persisted without rollback',saved.charts[0].spec.zoom.slice(0,2).every((v,i)=>Math.abs(v-after.x[i])<1e-7));
  ok('Plotly DOM survives asynchronous saves',await plot().evaluate((n,old)=>n===old,native));
  ok('zoom preserves KPI and other chart settings',JSON.stringify(saved.kpis)===kpis&&saved.charts[3].spec.zoom===null);
  await p.unroute('**/api/cards/*/edit');await reset(0);
  step='base types';for(const i of[2,3]){const a=await ranges(i);await save(()=>wheel(i));const b=await ranges(i);ok('wheel works on '+(i===2?'horizontal bars':'line'),Math.abs(b.x[1]-b.x[0])<Math.abs(a.x[1]-a.x[0]));await reset(i);}
  step='pan';await card().locator('[data-focus-key="pan"]').click();const panBefore=await ranges(0);
  await save(async()=>{await plot().scrollIntoViewIfNeeded();const b=await plot().locator('.nsewdrag').boundingBox();await p.mouse.move(b.x+b.width*.35,b.y+b.height*.5);await p.mouse.down();await p.mouse.move(b.x+b.width*.6,b.y+b.height*.5,{steps:8});await p.mouse.up();});
  const panAfter=await ranges(0);ok('native pan shifts range without changing width',Math.abs(panAfter.x[0]-panBefore.x[0])>.1&&Math.abs((panAfter.x[1]-panAfter.x[0])-(panBefore.x[1]-panBefore.x[0]))<1e-7);
  await card().locator('[data-focus-key="pan"]').click();await reset(0);
  step='box zoom';await save(async()=>{const b=await plot().locator('.nsewdrag').boundingBox();await p.mouse.move(b.x+b.width*.2,b.y+b.height*.2);await p.mouse.down();await p.mouse.move(b.x+b.width*.8,b.y+b.height*.8,{steps:8});await p.mouse.up();});
  ok('native box selection zooms',!!(await p.evaluate(async id=>(await(await fetch('/api/cards/'+id)).json()).message.dashboard.charts[0].spec.zoom,id)));await reset(0);
  step='series and tooltip';await save(()=>card().locator('[data-series-key="plan"]').click());
  ok('series hides immediately and last visible is protected',await plot().evaluate(n=>n.data[0].visible==='legendonly')&&await card().locator('[data-series-key="fact"]').isDisabled());await save(()=>card().locator('[data-series-key="plan"]').click());
  await plot().scrollIntoViewIfNeeded();const mark=await plot().locator('.barlayer .point path').evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect()).find(b=>b.width>3&&b.height>3&&b.y>0&&b.bottom<innerHeight));await p.mouse.move(mark.x+mark.width/2,mark.y+mark.height/2);await plot().locator('.hovertext').first().waitFor();ok('native tooltip contains Russian metric and exact values',/План|Факт/.test(await plot().locator('.hovertext').first().textContent()));
  step='all type variants';
  for(const type of['bar','stacked_bar','heatmap','scatter','histogram']){
    await configure({type,source:'period',x:type==='scatter'?'plan':'period',group:type==='heatmap'?'region':'',y:type==='heatmap'||type==='histogram'?['fact']:['plan','fact'],zoom:null});
    const a=await ranges(0);const changed=await save(()=>wheel());const b=await ranges(0);
    ok('native wheel works on '+type,Math.abs(b.x[1]-b.x[0])<Math.abs(a.x[1]-a.x[0]));
    if(type==='heatmap')ok('heatmap inverted axis and legacy offset retained',b.y[0]>b.y[1]&&Math.abs(changed.message.dashboard.charts[0].spec.zoom[0]-b.x[0]-.5)<1e-7);
    await reset(0);
  }
  step='linked heatmap and line';await configure({type:'heatmap',source:'period',x:'period',group:'region',y:['fact'],zoom:null});
  await save(()=>root().locator('.sync-zoom-control input').check());const lineY=(await ranges(3)).y;
  await save(()=>wheel());const heat=await ranges(0),line=await ranges(3);
  ok('linked native heatmap and line share the same calendar viewport',heat.x.every((v,i)=>Math.abs(v-line.x[i])<1e-7));
  ok('linked zoom preserves independent Y units',lineY.every((v,i)=>Math.abs(v-line.y[i])<1e-7));await reset(0);await save(()=>root().locator('.sync-zoom-control input').uncheck());
  step='pie sector';await plot(1).scrollIntoViewIfNeeded();await card(1).locator('.pie-category').selectOption('1');await p.waitForTimeout(100);
  ok('pie supports keyboard sector focus and exact share',await plot(1).evaluate(n=>n.parentElement._plotlyState.piePoint===1&&n.data[0].pull.every(v=>v===0))&&(await mount(1).locator('.plotly-status').textContent()).includes('%'));
  await card(1).locator('.pie-category').selectOption('');
  const pie=await plot(1).locator('.slice path').first().boundingBox();await p.mouse.move(pie.x+pie.width*.7,pie.y+pie.height*.5);await p.mouse.click(pie.x+pie.width*.7,pie.y+pie.height*.5);await p.waitForTimeout(100);ok('pie click highlights a sector',await plot(1).evaluate(n=>n.parentElement._plotlyState.piePoint!==null&&n.data[0].pull.every(v=>v===0)));
  step='reload';await save(()=>wheel(0));const expected=await ranges(0);await p.reload();await p.waitForFunction(()=>document.querySelector('#connection-status')?.textContent==='Готов к работе');await ready();ok('saved Plotly viewport restores after reload',(await ranges(0)).x.every((v,i)=>Math.abs(v-expected.x[i])<1e-7));
  await p.screenshot({path:dir+'plotly-desktop.png'});
  step='offline export';const[d]=await Promise.all([p.waitForEvent('download'),root().locator('[data-download-html]').click()]);const file=dir+'plotly-dashboard.html';await d.saveAs(file);
  const offline=await cNew();async function cNew(){return context.browser().newContext({offline:true,viewport:{width:1366,height:1000}});}
  const f=await offline.newPage();f.on('pageerror',e=>errors.push(e.message));let network=0;f.on('request',r=>{if(/^https?:/.test(r.url()))network++;});await f.goto('file://'+file);await f.waitForFunction(()=>[...document.querySelectorAll('.chart-mount')].every(n=>n._plotlyState?.ready));
  ok('self-contained offline file uses Plotly for all four charts',await f.locator('.js-plotly-plot').count()===4&&network===0);
  const fp=f.locator('.chart-plotly').first();const ox=await fp.evaluate(n=>[...n._fullLayout.xaxis.range]);const fb=await fp.locator('.nsewdrag').boundingBox();await f.mouse.move(fb.x+fb.width*.5,fb.y+fb.height*.5);await f.mouse.wheel(0,-120);await f.waitForTimeout(400);ok('offline wheel natively redraws axes',await fp.evaluate((n,old)=>n._fullLayout.xaxis.range[1]-n._fullLayout.xaxis.range[0]<old,ox[1]-ox[0]));await f.screenshot({path:dir+'plotly-offline.png'});await offline.close();
  step='responsive';for(const width of[390,320,1440]){await p.setViewportSize({width,height:900});if(width<1100)await p.locator('#dashboard-view-tab').click();await p.waitForTimeout(350);ok('native responsive layout fits '+width,await p.evaluate(()=>document.documentElement.scrollWidth===innerWidth));await p.screenshot({path:dir+'plotly-'+width+'.png'});}
  ok('no browser or CSP errors',errors.length===0,{errors});return{passed:checks.length,checks,errors};
 }catch(e){return{step,error:e.message,passed:checks.filter(v=>v.pass).length,checks,errors};}finally{await context.close();}
}

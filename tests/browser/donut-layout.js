// Measure rendered SVG geometry, not just the annotation configuration.
async(page)=>{
 const dir='/Users/sviat/Downloads/RPA/.local/verification/';
 const c=await page.context().browser().newContext({storageState:dir+'library-cookie.json',acceptDownloads:true,viewport:{width:1440,height:1000}});
 const checks=[],errors=[];let step='boot';
 const ok=(name,pass,data)=>{checks.push({name,pass:!!pass,...(data?{data}:{})});if(!pass)throw Error(name);};
 const geometry=plot=>plot.evaluate(n=>{
  const b=[...n.querySelectorAll('.pielayer .slice path')].map(p=>p.getBoundingClientRect());
  const left=Math.min(...b.map(v=>v.left)),right=Math.max(...b.map(v=>v.right)),top=Math.min(...b.map(v=>v.top)),bottom=Math.max(...b.map(v=>v.bottom));
  const t=n.querySelector('.annotation-text').getBoundingClientRect();
  return {x:(left+right)/2,y:(top+bottom)/2,width:right-left,height:bottom-top,tx:t.x+t.width/2,ty:t.y+t.height/2,textWidth:t.width,textHeight:t.height,hole:n.data[0].hole,percent:n.querySelector('.annotation-text').textContent,pull:n.data[0].pull||[],values:[...n.data[0].values],colors:[...n.data[0].marker.colors]};
 });
 const checkGeometry=(g,label)=>{
  ok('centered number '+label,Math.abs(g.tx-g.x)<1.5&&Math.abs(g.ty-g.y)<1.5,{dx:g.tx-g.x,dy:g.ty-g.y});
  ok('circular ring '+label,Math.abs(g.width-g.height)<.1&&g.pull.every(v=>v===0));
  ok('number fits inside hole '+label,g.textWidth<g.width*g.hole&&g.textHeight<g.height*g.hole);
 };
 try{
  const p=await c.newPage();p.setDefaultTimeout(30000);p.on('pageerror',e=>errors.push(e.message));
  await p.goto('http://127.0.0.1:8081/?chat=main');
  const card=()=>p.locator('#dashboard-mount .message-chart-card').nth(1),plot=()=>card().locator('.chart-plotly');
  await p.waitForFunction(()=>document.querySelectorAll('#dashboard-mount .chart-mount')[1]?._plotlyState?.ready);await p.locator('[data-download-html]').waitFor();await p.waitForTimeout(350);
  const baseline=await geometry(plot());checkGeometry(baseline,'all categories');
  step='selection';
  for(const value of ['0','1','5','']){
   await card().locator('.pie-category').selectOption(value);await p.waitForTimeout(100);
   const g=await geometry(plot());checkGeometry(g,'sector '+(value||'all'));
   ok('selection preserves size and position '+value,Math.abs(g.width-baseline.width)<1&&Math.abs(g.x-baseline.x)<1&&Math.abs(g.y-baseline.y)<1,{width:g.width-baseline.width,dx:g.x-baseline.x,dy:g.y-baseline.y});
   ok('selection preserves values '+value,JSON.stringify(g.values)===JSON.stringify(baseline.values));
   if(value){const expected=100*baseline.values[Number(value)]/baseline.values.reduce((a,b)=>a+b,0);ok('selected percentage is accurate '+value,g.percent===new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(expected)+'%');ok('selected sector emphasized without moving '+value,g.colors[Number(value)]===baseline.colors[Number(value)]&&g.colors.some(s=>s.startsWith('rgba(')));}
   else ok('clearing restores palette',JSON.stringify(g.colors)===JSON.stringify(baseline.colors));
  }
  await card().locator('.pie-category').selectOption('0');await p.waitForTimeout(100);await p.screenshot({path:dir+'donut-fixed.png'});
  step='resize';for(const width of [1024,390,320,1440]){
   await p.setViewportSize({width,height:1000});if(width<1100)await p.locator('#dashboard-view-tab').click();await card().scrollIntoViewIfNeeded();await p.waitForTimeout(350);
   checkGeometry(await geometry(plot()),String(width));ok('page fits viewport '+width,await p.evaluate(()=>document.documentElement.scrollWidth===innerWidth));
   await p.screenshot({path:dir+'donut-'+width+'.png'});
  }
  step='wide card';await p.locator('#dashboard-mount .message-chart-grid').evaluate(n=>n.style.gridTemplateColumns='1fr');await p.waitForTimeout(300);await card().scrollIntoViewIfNeeded();checkGeometry(await geometry(plot()),'wide');
  step='native click';await card().locator('.pie-category').selectOption('');const b=await plot().locator('.slice path').first().boundingBox();await p.mouse.click(b.x+b.width*.7,b.y+b.height*.5);await p.waitForTimeout(100);checkGeometry(await geometry(plot()),'native click');ok('native click selects sector',await card().locator('.pie-category').inputValue()!=='');
  step='offline';const[d]=await Promise.all([p.waitForEvent('download'),p.locator('#dashboard-mount [data-download-html]').click()]);const file=dir+'donut-fixed.html';await d.saveAs(file);
  const oc=await c.browser().newContext({offline:true,viewport:{width:1200,height:1000}}),f=await oc.newPage();f.on('pageerror',e=>errors.push(e.message));await f.goto('file://'+file);await f.waitForFunction(()=>document.querySelectorAll('.chart-mount')[1]?._plotlyState?.ready);const offlineCard=f.locator('main figure').nth(1);await offlineCard.locator('.pie-category').selectOption('0');await f.waitForTimeout(100);checkGeometry(await geometry(offlineCard.locator('.chart-plotly')),'offline');await f.screenshot({path:dir+'donut-offline.png'});await oc.close();
  ok('no page errors',errors.length===0,{errors});return{passed:checks.length,checks,errors};
 }catch(e){return{step,error:e.message,passed:checks.filter(v=>v.pass).length,checks,errors};}finally{await c.close();}
}

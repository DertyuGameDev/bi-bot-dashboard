// Actual download from an old selected card, then a file:// viewer with networking disabled.
async(page)=>{
 const dir='/Users/sviat/Downloads/RPA/.local/verification/';
 const c=await page.context().browser().newContext({storageState:dir+'library-cookie.json',acceptDownloads:true,viewport:{width:1440,height:900}});
 const checks=[],errors=[];let step='boot';
 const ok=(name,pass,data)=>{checks.push({name,pass:!!pass,...(data?{data}:{})});if(!pass)throw Error(name);};
 try{
  const p=await c.newPage();p.setDefaultTimeout(30000);p.on('pageerror',e=>errors.push(e.message));
  const ready=()=>p.waitForFunction(()=>document.querySelector('#connection-status')?.textContent==='Готов к работе');
  const root=()=>p.locator('#dashboard-mount .message-plots'),button=()=>root().locator('[data-download-html]');
  const download=async(action,path)=>{const[d]=await Promise.all([p.waitForEvent('download'),action()]);await d.saveAs(path);ok('download is a real HTML file',d.suggestedFilename().endsWith('.html')&&!await d.failure());return path;};
  await p.goto('http://127.0.0.1:8081/?chat=main');await ready();await button().waitFor();
  const originalId=await p.locator('.message-dashboard-link').first().evaluate(n=>n.closest('[data-message-id]').dataset.messageId);
  ok('direct HTML button is visible without opening Actions',await button().isVisible()&&!await p.locator('#card-actions-dialog').isVisible());
  await p.locator('#chat-saved-button').click();await p.waitForFunction(()=>document.querySelectorAll('#saved-list [data-open]').length===2);
  const item=p.locator('#saved-list .saved-item').filter({has:p.locator('h3',{hasText:'Обзор 2024'})});
  await item.locator('[data-open]').click();await p.locator('#saved-dialog').waitFor({state:'hidden'});
  await p.locator('.message-dashboard-link').first().click();await button().waitFor();await p.waitForFunction(id=>document.querySelector('#dashboard-select').value===id,originalId);
  ok('old card is selected after a newer dashboard',await p.locator('.message-dashboard-link').count()>=2);
  const order=[];p.on('request',r=>{if(r.url().includes('/api/download/html'))order.push('download');});p.on('response',r=>{if(r.url().endsWith('/edit'))order.push('saved');});
  await p.route('**/api/cards/*/edit',async route=>{await new Promise(r=>setTimeout(r,600));await route.continue();});
  const region=root().locator('[data-value-key=regions]');await region.locator('summary').click();await region.locator('input[value="Уральский"]').check();
  step='pending filters';const firstPath=dir+'dashboard-selected.html';await download(()=>button().click(),firstPath);
  ok('export waits for pending filters to save',order.indexOf('saved')>=0&&order.indexOf('saved')<order.indexOf('download'),{order:[...order]});
  await p.keyboard.press('Escape');
  const [currentResponse]=await Promise.all([p.waitForResponse(r=>/\/api\/cards\/[^/]+$/.test(r.url())),p.locator('.message-dashboard-link').first().click()]);
  const current=(await currentResponse.json()).message.dashboard;
  step='pending wheel';order.length=0;
  const plot=root().locator('.chart-plotly').first();await plot.scrollIntoViewIfNeeded();
  const bounds=await plot.locator('.nsewdrag').boundingBox();const at={x:bounds.x+bounds.width*.6,y:bounds.y+bounds.height*.5};
  await p.mouse.move(at.x,at.y);await p.mouse.wheel(0,-120);await p.waitForFunction(()=>document.querySelector('#dashboard-mount .chart-mount')?._zoomSession?.pending);
  const finalPath=dir+'dashboard-selected-zoom.html';await download(()=>button().click(),finalPath);
  ok('export waits for a wheel gesture and saves first',order.indexOf('saved')>=0&&order.indexOf('saved')<order.indexOf('download'));
  await p.unroute('**/api/cards/*/edit');await p.screenshot({path:dir+'html-download-button.png'});
  const originalScope=await p.evaluate(async id=>(await(await fetch('/api/cards/'+id)).json()).message.dashboard,originalId);
  step='offline file';const offline=await c.browser().newContext({offline:true,viewport:{width:1366,height:900}}),f=await offline.newPage();
  f.on('pageerror',e=>errors.push(e.message));const network=[];f.on('request',r=>{if(/^https?:/.test(r.url()))network.push(r.url());});
  await f.goto('file://'+finalPath);await f.waitForFunction(()=>[...document.querySelectorAll('.chart-mount')].every(n=>n._plotlyState?.ready));const snapshot=await f.locator('#snapshot').textContent(),data=JSON.parse(snapshot);
  ok('file opens without a server or network',await f.locator('main figure').count()===4&&network.length===0);
  ok('selected old card filters and KPI are exported',JSON.stringify(data.filters.regions)===JSON.stringify(originalScope.filters.regions)&&JSON.stringify(data.kpis)===JSON.stringify(originalScope.kpis));
  ok('latest wheel viewport is present in downloaded HTML',JSON.stringify(data.charts[0].spec.zoom)===JSON.stringify(originalScope.charts[0].spec.zoom));
  ok('offline file contains KPI and summary',await f.locator('.kpi-card').count()===8&&await f.locator('#summary p').count()>0);
  ok('export excludes conversation and source snapshot',!snapshot.includes('Дашборд для проверки общей библиотеки.')&&!Object.hasOwn(data,'dataset')&&!Object.hasOwn(data,'messages'));
  const legends=f.locator('figure').first().locator('.legend-button');await legends.first().click();ok('offline series toggles remain interactive',await legends.first().getAttribute('aria-pressed')==='false');
  const svg=f.locator('main .chart-plotly').first(),beforeBox=await svg.evaluate(n=>n._fullLayout.xaxis.range[1]-n._fullLayout.xaxis.range[0]);await f.locator('main figure').first().getByRole('button',{name:'Увеличить масштаб',exact:true}).click();ok('offline zoom works',await svg.evaluate((n,old)=>n._fullLayout.xaxis.range[1]-n._fullLayout.xaxis.range[0]<old,beforeBox));
  const target=f.locator('main figure details summary').first();await target.focus();await f.keyboard.press('Enter');ok('offline exact values accessible by keyboard',await f.locator('main figure details').first().evaluate(n=>n.open&&n.querySelectorAll('tbody tr').length>0));
  await f.screenshot({path:dir+'html-downloaded-offline.png'});await offline.close();
  step='non-main chat';await p.locator('#conversations-button').click();await p.locator('#conversation-search').fill('Сравнение 2024');await p.locator('#conversations-list a').first().click();await ready();await button().waitFor();
  const [scopedResponse]=await Promise.all([p.waitForResponse(r=>r.url().includes('/api/download/html')),download(()=>button().click(),dir+'dashboard-second-chat.html')]);
  ok('download is scoped to the current non-main chat',scopedResponse.ok()&&scopedResponse.request().headers()['x-conversation-id']!=='main');
  step='recoverable failure';await p.route('**/api/download/html?*',r=>r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Повторите скачивание'})}));await button().click();await p.locator('#error').waitFor();
  ok('failed export allows retry and shows error',await button().isEnabled()&&(await p.locator('#error').textContent()).includes('Повторите скачивание'));await p.unroute('**/api/download/html?*');
  await download(()=>button().click(),dir+'dashboard-retried.html');ok('retry clears previous error',await p.locator('#error').isHidden());
  step='phone';await p.setViewportSize({width:390,height:844});await p.locator('#dashboard-view-tab').click();await button().scrollIntoViewIfNeeded();ok('HTML action fits phone viewport',await button().isVisible()&&await p.evaluate(()=>document.documentElement.scrollWidth===innerWidth));await p.screenshot({path:dir+'html-download-phone.png'});
  ok('no page errors',errors.length===0,{errors});return{passed:checks.length,checks,errors};
 }catch(e){return{step,error:e.message,passed:checks.filter(v=>v.pass).length,checks,errors};}finally{await c.close();}
}

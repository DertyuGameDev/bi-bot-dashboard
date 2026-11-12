// Real table workspace, independent old-card edits and responsive navigation.
async(page)=>{
 const dir='/Users/sviat/Downloads/RPA/.local/verification/';
 const c=await page.context().browser().newContext({storageState:dir+'library-cookie.json',viewport:{width:1440,height:1000}}),p=await c.newPage(),checks=[],errors=[];let step='boot';
 p.on('pageerror',e=>errors.push(e.message));p.setDefaultTimeout(30000);
 const ok=(name,pass)=>{checks.push({name,pass:!!pass});if(!pass)throw Error(name);};
 const root=()=>p.locator('#workspace-table-mount'),table=()=>root().locator('.message-table');
 const ready=()=>p.waitForFunction(()=>!!document.querySelector('#workspace-table-mount .message-table')&&document.querySelector('#workspace-table-mount').getAttribute('aria-busy')!=='true');
 const state=()=>p.evaluate(async()=>await(await fetch('/api/state',{headers:{'X-Conversation-ID':'main'}})).json());
 const save=async action=>{const[r]=await Promise.all([p.waitForResponse(r=>r.url().endsWith('/edit')&&r.request().method()==='POST'),action()]);ok('edit HTTP 200',r.ok());await ready();return r.json();};
 try{
  await p.goto('http://127.0.0.1:8081/?chat=main');await p.waitForFunction(()=>document.querySelector('#connection-status')?.textContent==='Готов к работе');
  const initial=await state(),cards=initial.messages.filter(m=>m.dashboard),a=cards[0].id,b=cards[1].id;
  await p.locator('#dashboard-select').selectOption(a);await p.locator('#panel-table').click();await ready();
  ok('table beside chat',await p.locator('#conversation-pane').isVisible()&&await p.locator('#workspace-table-panel').isVisible()&&await p.locator('#data-panel').isHidden());
  ok('correct old table and calculation',await root().locator('h3').first().textContent()==='Таблица · Общий анализ 2025'&&await root().locator('[data-calculation=gap]').count()===1);
  ok('all columns and first page',await table().evaluate(n=>n._table.rows.length===25&&n._table.columns.some(c=>c.name==='gap')));
  const before=await state(),node=await table().evaluateHandle(n=>n);
  step='pagination';await table().locator('[data-page=next]').click();await p.waitForFunction(()=>document.querySelector('#workspace-table-mount .message-table')._table.offset===25);
  ok('real pagination',await table().evaluate(n=>n._table.offset===25));
  await root().locator('.chat-table-scroll').evaluate(n=>{n.scrollLeft=300;n.scrollTop=60;});const left=await root().locator('.chat-table-scroll').evaluate(n=>n.scrollLeft);
  await p.locator('#workspace-charts-tab').click();await p.locator('#workspace-table-tab').click();await ready();
  ok('tab switch preserves page and table DOM',await table().evaluate((n,old)=>n===old&&n._table.offset===25,node));
  ok('tab switch preserves table scroll',await root().locator('.chat-table-scroll').evaluate((n,left)=>n.scrollLeft===left,left));
  step='add formula';await root().locator('[data-table-add]').click();await p.locator('#calc-name').fill('pane_gap');await p.locator('#calc-label').fill('Отклонение для проверки');await p.locator('#calc-expression').fill('fact-plan');await save(()=>p.locator('#calculation-form button[type=submit]').click());
  await p.waitForFunction(()=>document.querySelector('#workspace-table-mount .message-table')?._table.columns.some(c=>c.name==='pane_gap'));
  ok('new column has actual computed values',await table().evaluate(n=>n._table.rows.every(r=>Math.abs(r.pane_gap-(r.fact-r.plan))<.02)));
  ok('table opens after formula edit',await p.locator('#workspace-table-panel').isVisible());
  const after=await state();ok('old-card edit leaves newer dataset unchanged',JSON.stringify(after.dataset)===JSON.stringify(before.dataset)&&JSON.stringify(after.calculations)===JSON.stringify(before.calculations));
  step='edit formula';await root().locator('[data-table-edit=pane_gap]').click();ok('formula prefilled',await p.locator('#calc-expression').inputValue()==='fact-plan');await p.locator('#calc-expression').fill('fact-plan+10');await save(()=>p.locator('#calculation-form button[type=submit]').click());await p.waitForFunction(()=>document.querySelector('#workspace-table-mount .message-table')?._table.rows.every(r=>Math.abs(r.pane_gap-(r.fact-r.plan+10))<.02));
  ok('changed formula recalculates rows',true);
  await save(()=>root().locator('[data-table-delete=pane_gap]').click());await p.waitForFunction(()=>!document.querySelector('#workspace-table-mount .message-table')?._table.columns.some(c=>c.name==='pane_gap'));ok('delete removes column',true);
  await save(()=>root().locator('[data-table-undo]').click());await p.waitForFunction(()=>document.querySelector('#workspace-table-mount .message-table')?._table.columns.some(c=>c.name==='pane_gap'));ok('undo restores column',true);
  step='switch cards';await p.locator('#dashboard-select').selectOption(b);await ready();ok('switch selects own table',await table().evaluate(n=>!n._table.columns.some(c=>c.name==='pane_gap')&&n._table.rows.every(r=>r.date.startsWith('2024'))));await p.locator('#dashboard-select').selectOption(a);await ready();ok('return restores edited table',await table().evaluate(n=>n._table.columns.some(c=>c.name==='pane_gap')));
  await p.screenshot({path:dir+'table-pane-desktop.png'});
  step='reload';await p.reload();await p.waitForFunction(()=>document.querySelector('#connection-status')?.textContent==='Готов к работе');await ready();ok('reload restores table mode and selected dashboard',await p.locator('#workspace-table-panel').isVisible()&&await p.locator('#dashboard-select').inputValue()===a);
  step='responsive';for(const width of[1024,390,320]){
   await p.setViewportSize({width,height:900});await p.locator('#dashboard-view-tab').click();await ready();
   ok('table fits viewport '+width,await p.evaluate(()=>document.documentElement.scrollWidth===innerWidth)&&await root().locator('.chat-table-scroll').evaluate(n=>n.getBoundingClientRect().right<=innerWidth));
   ok('internal horizontal scroll '+width,await root().locator('.chat-table-scroll').evaluate(n=>n.scrollWidth>n.clientWidth));
   await p.screenshot({path:dir+'table-pane-'+width+'.png'});
  }
  step='keyboard';await p.locator('#workspace-table-tab').focus();await p.locator('#workspace-table-tab').press('Home');ok('keyboard switches to charts',await p.locator('#dashboard-scroll').isVisible());await p.locator('#workspace-charts-tab').press('End');await ready();ok('keyboard switches to table',await p.locator('#workspace-table-panel').isVisible());
  ok('no page errors',errors.length===0);return{passed:checks.length,checks,errors};
 }catch(e){return{step,error:e.message,passed:checks.filter(c=>c.pass).length,checks,errors};}finally{await c.close();}
}

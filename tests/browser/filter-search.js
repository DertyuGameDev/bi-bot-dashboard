// Run with browser_run_code_unsafe against the local QA server on 8081.
(async (page) => {
  const context=await page.context().browser().newContext({viewport:{width:900,height:800}});
  const qa=await context.newPage();
  let checks=0;
  function check(ok,message){if(!ok)throw Error(message);checks++;}
  try {
    await qa.goto('http://127.0.0.1:8081');
    await qa.waitForFunction(()=>document.querySelector('#connection-status').textContent==='Готов к работе');
    await qa.evaluate(async()=>{
      const {filterPicker}=await import('/assets/filter-picker.js');
      const root=document.createElement('div');root.id='filter-test';root.style='position:fixed;inset:20px;z-index:100;background:white;padding:20px;overflow:auto';
      document.body.append(root);
      const values=['Магазин «Ёлка», Москва, Ленина, 1','Магазин «Пятёрка», Тула, Мира, 2','<img src=x onerror="window.filterInjected=true">',...Array.from({length:650},(_,i)=>'Точка '+i)];
      const config={key:'cities',label:'Магазины',values,current:[],all:'Все магазины',onChange:current=>{
        config.current=current;root.dataset.selected=JSON.stringify(current);filterPicker(root,config);
      }};
      filterPicker(root,config);
      window.filterFixture={config,root,render:()=>filterPicker(root,config)};
    });
    const root=qa.locator('#filter-test'), picker=root.locator('[data-value-key="cities"]');
    await picker.locator('summary').click();
    const search=picker.locator('input[type=search]'), matches=picker.locator('.value-option:not([hidden])');
    await qa.waitForFunction(()=>document.activeElement?.getAttribute('aria-label')==='Поиск: Магазины');
    check(await search.evaluate(node=>document.activeElement===node),'Opening must focus search');
    await search.fill('МОСКВА елка');
    check(await matches.count()===1,'Search must ignore case and ё/е, and accept words in any order');
    await matches.locator('input').check();
    await search.fill('ТУЛА пятерка');await matches.locator('input').check();
    check(JSON.parse(await root.getAttribute('data-selected')).length===2,'Searching another value must preserve earlier selection');
    await search.fill('нет-такого-значения');
    check(await matches.count()===0 && await picker.locator('.value-none').isVisible(),'Empty search needs a clear state');
    check(await picker.locator('input:checked').count()===2,'Empty search must not clear selected values');
    await search.fill('Точка 649');
    check(await matches.count()===1,'Values beyond the old 500-item limit must be searchable');
    await matches.locator('input').check();
    await search.focus();
    await picker.evaluate(node=>node.dataset.marker='stable');
    await qa.evaluate(()=>{window.filterFixture.config.pending=true;window.filterFixture.render();});
    check(await picker.locator('.value-options').isHidden() && await picker.locator('.value-count').textContent()==='Обновляю список…','Pending cascade must not expose stale choices');
    check(await picker.locator('.value-actions button').isDisabled() && await search.inputValue()==='Точка 649','Pending cascade must preserve search and block edits');
    await qa.evaluate(()=>{window.filterFixture.config.pending=false;window.filterFixture.render();});
    check(await matches.count()===1 && !await matches.locator('input').isDisabled(),'Completed cascade must restore searchable choices');
    await qa.evaluate(()=>window.filterFixture.render());
    check(await search.inputValue()==='Точка 649' && await search.evaluate(node=>document.activeElement===node),'Render must preserve query and focus');
    check(await picker.getAttribute('data-marker')==='stable' && await picker.locator('details').evaluate(node=>node.open),'Render must preserve open picker identity');
    await search.press('Escape');
    check(await picker.locator('summary').evaluate(node=>document.activeElement===node) && !await picker.locator('details').evaluate(node=>node.open),'Escape must close and restore focus');
    await picker.locator('summary').press('Enter');
    await search.fill('МОСКВА');await search.press('ArrowDown');await qa.keyboard.press('Space');
    check(JSON.parse(await root.getAttribute('data-selected')).length===2,'Keyboard must toggle a matching value');
    await picker.locator('.value-actions button').click();
    check(JSON.parse(await root.getAttribute('data-selected')).length===0 && await search.inputValue()==='МОСКВА','Reset selection must preserve the search query');
    await search.fill('<img');
    check(await matches.count()===1 && await root.locator('img').count()===0 && !await qa.evaluate(()=>window.filterInjected),'Value labels must remain literal text');
    await qa.evaluate(()=>{window.filterFixture.config.onChange=null;window.filterFixture.render();});
    check(await matches.locator('input').isDisabled(),'Historical values must not become editable');
    await search.press('Escape');
    await qa.evaluate(async()=>{
      const {filterPicker}=await import('/assets/filter-picker.js');
      filterPicker(window.filterFixture.root,{key:'step',label:'По времени',values:[['month','Месяцы'],['quarter','Кварталы'],['year','Годы']],current:['month'],multiple:false,all:'Выберите',onChange:values=>window.filterFixture.root.dataset.step=values[0]});
    });
    const step=root.locator('[data-value-key="step"]');await step.locator('summary').click();
    const stepSearch=step.locator('input[type=search]');await stepSearch.fill('КВАР');await stepSearch.press('Enter');
    check(await root.getAttribute('data-step')==='quarter' && await step.locator('input[type=radio]:checked').inputValue()==='quarter','Enter must apply a matching single value');
    return {passed:checks};
  }finally{await context.close();}
})

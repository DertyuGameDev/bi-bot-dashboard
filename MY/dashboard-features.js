// Secondary dashboard actions, durable drafts and reusable query text.
export async function installFeatures({api,headers,getView,getQueue,setQueue,mutate,toast,error,beforeDownload}){
 const $=s=>document.querySelector(s),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const input=$('#message-input'),status=$('#draft-status');
 const context=()=>getView()?.workspace?.saved_id || 'conversation';
 const initial=await api('/api/draft?context='+encodeURIComponent(context()));
 const namespace=initial.storage_key,key=c=>'my-draft:'+namespace+':'+c,queueKey='my-queue:'+namespace;
 const read=k=>{try{return JSON.parse(localStorage.getItem(k)||'null');}catch{return null;}};
 const write=(k,v)=>{try{localStorage.setItem(k,JSON.stringify(v));return true;}catch{return false;}};
 let currentContext=null,timer,epoch=0,loaded=false,edits=0;
 function resize(){input.style.height='auto';input.style.height=Math.min(120,input.scrollHeight)+'px';}
 function persistQueue(){write(queueKey,getQueue());}
 const restored=read(queueKey),known=new Set((getView()?.messages||[]).map(m=>m.request_id));
 if(restored){const active=getQueue(),ids=new Set(active.outbox.map(j=>j.id));setQueue({outbox:[...active.outbox,...(restored.outbox||[]).filter(j=>j.id&&!ids.has(j.id)&&!known.has(j.id)&&j.kind==='text'&&typeof j.text==='string')],failed:[...active.failed,...(restored.failed||[]).filter(j=>j.id&&!known.has(j.id)&&typeof j.text==='string')]});}
 async function save(c,text,keepalive=false){
   if(!c)return;
   const stamp=Math.max(Date.now(),(read(key(c))?.at||0)+1);write(key(c),{text,at:stamp,dirty:true});
   if(c===currentContext)status.textContent=text?'Черновик сохранён на устройстве':'';
   try{
     const result=await api('/api/draft',{context:c,text,version:stamp},keepalive?{keepalive:true}:{});
     const latest=read(key(c));if(latest?.at===stamp&&result.text===text){write(key(c),{text,at:result.version,dirty:false});if(c===currentContext)status.textContent=text?'Черновик сохранён':'';}
   }catch{if(c===currentContext&&text)status.textContent='Черновик сохранён на устройстве';}
 }
 function mark(){edits++;if(!currentContext)return;write(key(currentContext),{text:input.value,at:Date.now(),dirty:true});status.textContent=input.value?'Черновик сохранён на устройстве':'';clearTimeout(timer);const c=currentContext;timer=setTimeout(()=>save(c,input.value),350);}
 async function loadContext(){
   const next=context();if(next===currentContext)return;
   clearTimeout(timer);if(currentContext!==null)save(currentContext,input.value);
   const typed=!loaded&&input.value;loaded=true;currentContext=next;const serial=++epoch,local=read(key(next));
   input.value=typed||local?.text||'';resize();if(typed){mark();return;}
   status.textContent=input.value?'Черновик восстановлен':'';
   const before=input.value,editAtStart=edits;
   try{
     const server=next===initial.context&&serial===1?initial:await api('/api/draft?context='+encodeURIComponent(next));
     if(serial!==epoch||edits!==editAtStart||input.value!==before)return;
     if(local?.dirty || (local?.at||0)>server.version){if(local?.dirty)save(next,local.text);return;}
     input.value=server.text;resize();write(key(next),{text:server.text,at:server.version,dirty:false});status.textContent=server.text?'Черновик восстановлен':'';
   }catch{ /* The local draft remains available during a connection interruption. */ }
 }
 input.addEventListener('input',mark);
 window.addEventListener('pagehide',()=>{clearTimeout(timer);save(currentContext,input.value,true);persistQueue();});
 function clearDraft(){edits++;clearTimeout(timer);input.value='';save(currentContext,'');resize();}
 async function setInput(text){input.value=text;resize();mark();input.focus({preventScroll:true});}
 await loadContext();persistQueue();

 let actionMessage=null,actionMeta=null,actionEpoch=0;
 function currentCard(){return getView()?.messages.find(m=>m.id===actionMessage)?.dashboard;}
 function setPublication(info){
   const published=info?.published,url=published?.url;
   $('#publication-link-row').hidden=!url;$('#publication-url').value=url||'';
   $('#publish-dashboard').disabled=!info?.available;
   $('#publish-dashboard').textContent=published?'Обновить публикацию':'Создать ссылку';$('#revoke-publication').hidden=!published;
   $('#publication-status').textContent=!info?.available?'Сервис внешних ссылок не запущен.':published?'Опубликован снимок от '+new Date(published.updated).toLocaleString('ru-RU'):'Ссылка ещё не создана.';
   if(info?.temporary)$('#publication-status').textContent+=' Временный адрес может измениться при переподключении сервиса.';
 }
 async function openActions(messageId){
   const seq=++actionEpoch;actionMessage=messageId;actionMeta=null;
   const card=currentCard();$('#card-actions-name').textContent=card?.saved_title||card?.title||'Дашборд';
   $('#open-versions').disabled=true;setPublication(null);
   if(!$('#card-actions-dialog').open)$('#card-actions-dialog').showModal();
   try{const meta=await api('/api/card-actions?message_id='+encodeURIComponent(messageId));if(seq!==actionEpoch)return;actionMeta=meta;$('#card-actions-name').textContent=meta.title;$('#open-versions').disabled=!meta.saved_id;setPublication(meta.publication);}catch(e){error(e.message);}
 }
 async function download(messageId,kind='html'){
   if(!messageId)return false;
   error('');
   try{
     await beforeDownload?.(messageId);
     const response=await fetch('/api/download/'+kind+'?message_id='+encodeURIComponent(messageId),{headers:headers?.(messageId)});
     if(!response.ok){const data=await response.json();throw Error(data.detail||'Не удалось скачать файл');}
     const blob=await response.blob(),a=document.createElement('a'),url=URL.createObjectURL(blob);
     a.href=url;a.download=(response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1])||'dashboard.'+kind;
     document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);
     if(kind==='html')toast('Скачивание HTML началось');return true;
   }catch(e){error(e.message);return false;}
 }
 for(const button of document.querySelectorAll('[data-download-kind]'))button.onclick=async()=>{
   const messageId=actionMessage;if(!messageId)return;button.disabled=true;button.setAttribute('aria-busy','true');
   try{await download(messageId,button.dataset.downloadKind);}finally{button.disabled=false;button.removeAttribute('aria-busy');}
 };
 $('#open-versions').onclick=async()=>{
   if(!actionMeta?.saved_id)return;$('#card-actions-dialog').close();$('#versions-name').textContent=actionMeta.title;$('#versions-list').textContent='Загружаю версии…';$('#versions-dialog').showModal();
   try{const result=await api('/api/dashboards/'+actionMeta.saved_id+'/versions');$('#versions-list').innerHTML=result.items.map((v,i)=>`<article class="version-item"><div><strong>Версия ${v.seq}</strong> · ${esc(v.label)}<small>${esc(new Date(v.created).toLocaleString('ru-RU'))}</small><small>${esc(v.details)} · Вычислений: ${v.calculations}</small></div><button type="button" data-restore-version="${v.seq}" ${i===0?'disabled':''}>${i===0?'Текущая':'Восстановить'}</button></article>`).join('');
     for(const b of document.querySelectorAll('[data-restore-version]'))b.onclick=async()=>{const id=actionMessage,seq=b.dataset.restoreVersion;if(await mutate('/api/dashboards/'+actionMeta.saved_id+'/versions/'+seq+'/restore',{},'Восстановлена версия '+seq,id)){$('#versions-dialog').close();toast('Версия восстановлена. Последующие версии остались в истории.');}};
   }catch(e){$('#versions-list').textContent=e.message;}
 };
 $('#publish-dashboard').onclick=async()=>{
   if(!actionMeta)return;const b=$('#publish-dashboard');b.disabled=true;
   try{const result=await api('/api/publications',{revision:getView().revision,arguments:{},message_id:actionMessage});actionMeta.publication=result;setPublication(result);toast('Ссылка на снимок создана');}catch(e){error(e.message);}finally{b.disabled=!actionMeta?.publication?.available;}
 };
 $('#revoke-publication').onclick=async()=>{if(!actionMeta)return;try{await api('/api/publications/'+actionMeta.saved_id,undefined,{method:'DELETE'});actionMeta.publication={...actionMeta.publication,published:null};setPublication(actionMeta.publication);toast('Ссылка отозвана');}catch(e){error(e.message);}};
 $('#copy-publication').onclick=async()=>{const field=$('#publication-url');try{await navigator.clipboard.writeText(field.value);toast('Ссылка скопирована');}catch{field.focus();field.select();toast('Ссылка выделена — скопируйте её');}};

 let editingTemplate=null;
 async function loadTemplates(){
   const {items}=await api('/api/templates');$('#templates-list').innerHTML=items.map(item=>`<article class="template-item"><h3>${esc(item.title)}</h3><p>${esc(item.prompt)}</p><div class="button-group"><button type="button" data-use-template="${esc(item.id)}">Подставить в чат</button>${item.builtin?'':`<button type="button" data-edit-template="${esc(item.id)}">Изменить</button><button type="button" data-delete-template="${esc(item.id)}">Удалить</button>`}</div></article>`).join('');
   for(const b of document.querySelectorAll('[data-use-template]'))b.onclick=()=>{const item=items.find(t=>t.id===b.dataset.useTemplate);$('#templates-dialog').close();setInput(item.prompt);};
   for(const b of document.querySelectorAll('[data-edit-template]'))b.onclick=()=>{const item=items.find(t=>t.id===b.dataset.editTemplate);editingTemplate=item.id;$('#template-title').value=item.title;$('#template-prompt').value=item.prompt;$('#template-editor').open=true;$('#template-title').focus();};
   for(const b of document.querySelectorAll('[data-delete-template]'))b.onclick=async()=>{try{await api('/api/templates/'+b.dataset.deleteTemplate,undefined,{method:'DELETE'});await loadTemplates();}catch(e){error(e.message);}};
 }
 $('#templates-button').onclick=async()=>{$('#templates-dialog').showModal();try{await loadTemplates();}catch(e){$('#templates-list').textContent=e.message;}};
 $('#template-from-input').onclick=()=>{$('#template-prompt').value=input.value;};
 $('#template-form').onsubmit=async e=>{e.preventDefault();const title=$('#template-title').value.trim(),prompt=$('#template-prompt').value.trim();if(!title||!prompt)return;try{await api('/api/templates'+(editingTemplate?'/'+editingTemplate:''),{title,prompt},editingTemplate?{method:'PATCH'}:{});editingTemplate=null;$('#template-form').reset();$('#template-editor').open=false;await loadTemplates();toast('Шаблон сохранён');}catch(e){error(e.message);}};
 return {openActions,download,onRender:loadContext,persistQueue,clearDraft};
}

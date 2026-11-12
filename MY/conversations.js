// Navigation creates a fresh document: in-flight replies cannot enter another chat.
const namespace=document.querySelector('meta[name="draft-key"]')?.content || 'my';
const preference='my-active-conversation:'+namespace;
const valid=id=>id==='main'||/^[a-f0-9]{32}$/.test(id||'');
let selected=new URLSearchParams(location.search).get('chat');
try{if(!selected)selected=localStorage.getItem(preference);}catch{}
if(!valid(selected))selected='main';
export const conversationID=()=>selected;
export const layoutKey=()=>`my-layout:${namespace}:${selected}`;
export async function installConversations({api,beforeNavigate,error}){
 const $=s=>document.querySelector(s);let items=[];
 async function navigate(id){
   if(id===selected){$('#conversations-dialog').close();return;}
   if(await beforeNavigate?.()===false)return;
   try{localStorage.setItem(preference,id);}catch{}
   location.assign('/?chat='+encodeURIComponent(id));
 }
 async function load(){
   items=(await api('/api/conversations')).items;
   if(!items.some(i=>i.id===selected))selected='main';
   try{localStorage.setItem(preference,selected);}catch{}
   const current=items.find(i=>i.id===selected);
   $('#conversation-title').textContent=current.title;$('#conversation-name').value=current.title;
   $('#conversations-button').setAttribute('aria-label','Переключить чат: '+current.title);
   $('#conversations-button').title='Открыть список чатов';
   document.title=current.title+' · MY';render();
 }
 function render(){
   const query=$('#conversation-search').value.toLocaleLowerCase('ru');
   const root=$('#conversations-list');root.replaceChildren();
   for(const item of items.filter(i=>(i.title+' '+i.preview).toLocaleLowerCase('ru').includes(query))){
     const li=document.createElement('li'),link=document.createElement('a');link.href='/?chat='+item.id;link.dataset.conversationId=item.id;
     if(item.id===selected)link.setAttribute('aria-current','page');
     const title=document.createElement('strong'),preview=document.createElement('span'),date=document.createElement('small');
     title.textContent=item.title;preview.textContent=item.preview||'Новый диалог с ассистентом';date.textContent=new Date(item.updated).toLocaleString('ru-RU')+(item.running?' · Идёт анализ':'');
     link.append(title,preview,date);link.onclick=e=>{if(e.button||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;e.preventDefault();navigate(item.id).catch(e=>error(e.message));};li.append(link);root.append(li);
   }
   if(!root.childElementCount){const li=document.createElement('li');li.textContent='Чатов не найдено';root.append(li);}
 }
 const create=async()=>{try{const next=await api('/api/conversations',{});await navigate(next.id);}catch(e){error(e.message);}};
 $('#new-conversation').onclick=create;$('#dialog-new-conversation').onclick=create;
 $('#conversations-button').onclick=async()=>{$('#conversations-dialog').showModal();$('#conversation-search').focus();try{await load();}catch(e){error(e.message);}};
 $('#conversation-search').oninput=render;
 $('#conversation-name-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/conversations/'+selected,{title:$('#conversation-name').value.trim()},{method:'PATCH'});await load();}catch(e){error(e.message);}};
 await load();
 return {refresh:load};
}

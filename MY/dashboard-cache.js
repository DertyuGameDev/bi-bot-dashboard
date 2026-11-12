// Private browser cache: one namespace per owner and conversation, bounded variants.
const stable=value=>Array.isArray(value)?value.map(stable):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(k=>[k,stable(value[k])])):value;
const signature=(data,filters=data.filters,resetZoom=false)=>JSON.stringify(stable([data.cache_engine,data.source_signature,data.id,data.table_id,data.table_version,data.saved_id,data.saved_title,data.render_profile,data.layout,filters,(data.source_specs||[]).map(c=>({...c,...(resetZoom?{zoom:null}:{})})),data.sync_zoom]));
export async function openDashboardCache(namespace){
 let db=null;const memory=new Map();
 try{db=await new Promise((resolve,reject)=>{const r=indexedDB.open('my-dashboard-cache',1);r.onupgradeneeded=()=>r.result.createObjectStore('entries',{keyPath:'key'});r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});}catch{}
 async function get(key){
  if(memory.has(key))return structuredClone(memory.get(key));
  if(!db)return null;
  try{return await new Promise(resolve=>{const r=db.transaction('entries').objectStore('entries').get(namespace+':'+key);r.onsuccess=()=>resolve(r.result?.data||null);r.onerror=()=>resolve(null);});}catch{return null;}
 }
 async function put(key,data){
  memory.set(key,structuredClone(data));while(memory.size>50)memory.delete(memory.keys().next().value);
  if(!db)return;
  try{await new Promise(resolve=>{const tx=db.transaction('entries','readwrite');tx.objectStore('entries').put({key:namespace+':'+key,namespace,updated:Date.now(),data});tx.oncomplete=tx.onerror=()=>resolve();});
   const tx=db.transaction('entries','readwrite'),store=tx.objectStore('entries'),r=store.getAll();r.onsuccess=()=>{const entries=r.result.filter(e=>e.namespace===namespace).sort((a,b)=>b.updated-a.updated);for(const item of entries.slice(50))store.delete(item.key);};
  }catch{}
 }
 return{readView:()=>get('conversation'),saveView:view=>put('conversation',view),remember:(id,data)=>put('card:'+id+':'+signature(data),data),find:(id,data,filters)=>get('card:'+id+':'+signature(data,filters,true))};
}

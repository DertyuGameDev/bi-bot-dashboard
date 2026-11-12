import { captureChatView, restoreChatView } from '/assets/chat-view.js';

const number=value=>Number(value).toLocaleString('ru-RU',{maximumFractionDigits:2});
const format=value=>value==null ? '—' : typeof value==='number' ? number(value) : typeof value==='boolean' ? (value ? 'Да' : 'Нет') : String(value);
const numeric=column=>/int|float|decimal/i.test(column.type);
function button(text,label,key) {
  const node=document.createElement('button');node.type='button';node.textContent=text;node.setAttribute('aria-label',label);node.dataset.focusKey=key;return node;
}
function showPage(root,data) {
  const scroll=root.querySelector('.chat-table-scroll'), left=scroll.scrollLeft;
  const table=scroll.querySelector('table');
  table.querySelector('caption').textContent=`${data.title}: строки ${data.total ? data.offset+1 : 0}–${data.offset+data.rows.length} из ${data.total}`;
  const thead=table.querySelector('thead'), row=document.createElement('tr');
  const index=document.createElement('th');index.scope='col';index.textContent='№';row.append(index);
  for(const column of data.columns){const th=document.createElement('th');th.scope='col';th.textContent=column.label;if(numeric(column))th.className='numeric';row.append(th);}
  thead.replaceChildren(row);
  const body=table.querySelector('tbody');body.replaceChildren();
  data.rows.forEach((record,index)=>{
    const tr=body.insertRow();tr.insertCell().textContent=String(data.offset+index+1);
    for(const column of data.columns){const cell=tr.insertCell();cell.textContent=format(record[column.name]);if(numeric(column))cell.className='numeric';cell.title=record[column.name]==null ? 'Нет значения' : String(record[column.name]);}
  });
  if(!data.rows.length){const tr=body.insertRow(),cell=tr.insertCell();cell.colSpan=data.columns.length+1;cell.textContent='По выбранным условиям данных нет.';}
  root.querySelector('h3').textContent=data.title;
  root.querySelector('.chat-table-count').textContent=`${data.total ? number(data.offset+1) : '0'}–${number(data.offset+data.rows.length)} из ${number(data.total)} строк`;
  const f=data.filters;
  root.querySelector('.chat-table-scope').textContent=[f.start && f.end ? `${f.start} — ${f.end}` : '',f.regions.length ? f.regions.join(', ') : 'Все регионы',f.outlets.length ? `Выбрано точек: ${f.outlets.length}` : 'Все точки',data.snapshot===false?'Актуальные данные выбранного дашборда':'Данные на момент ответа'].filter(Boolean).join(' · ');
  const pages=Math.max(1,Math.ceil(data.total/data.limit));
  const input=root.querySelector('.chat-table-page');input.value=Math.floor(data.offset/data.limit)+1;input.max=pages;input.disabled=pages===1;
  root.querySelector('.chat-table-pages').textContent='из '+number(pages);
  root.querySelector('.chat-table-limit').value=data.limit;
  root.querySelector('[data-page=prev]').disabled=data.offset===0;
  root.querySelector('[data-page=next]').disabled=data.offset+data.rows.length>=data.total;
  scroll.scrollLeft=left;scroll.scrollTop=0;root._table=data;
}
async function loadPage(root,offset,limit) {
  if(root.getAttribute('aria-busy')==='true')return;
  const data=root._table;
  const focused=root.contains(document.activeElement) ? document.activeElement : null;
  root.setAttribute('aria-busy','true');
  for(const control of root.querySelectorAll('.chat-table-pagination button, .chat-table-pagination input, .chat-table-pagination select'))control.disabled=true;
  root.querySelector('.chat-table-status').textContent='Загружаю строки…';
  root.querySelector('.chat-table-error').hidden=true;
  try {
    const next=await root._options.loadPage(data.id,offset,limit);
    if(!root.isConnected)return;
    const messages=document.querySelector('#messages'),scroll=document.querySelector('#chat-scroll');
    const view=captureChatView(messages,scroll);
    showPage(root,next);
    restoreChatView(messages,scroll,view);
  }catch(error){
    const message=root.querySelector('.chat-table-error');message.textContent=error.message || 'Не удалось загрузить страницу. Повторите действие.';message.hidden=false;
    root.querySelector('.chat-table-limit').value=data.limit;
    root.querySelector('.chat-table-page').value=Math.floor(data.offset/data.limit)+1;
  }finally{
    root.setAttribute('aria-busy','false');root.querySelector('.chat-table-status').textContent='';
    const current=root._table;
    root.querySelector('[data-page=prev]').disabled=current.offset===0;
    root.querySelector('[data-page=next]').disabled=current.offset+current.rows.length>=current.total;
    root.querySelector('.chat-table-page').disabled=current.total<=current.limit;
    root.querySelector('.chat-table-limit').disabled=false;
    if(focused && document.activeElement===document.body && !focused.disabled)focused.focus({preventScroll:true});
  }
}
export function messageTable(data,options,previous=null) {
  if(previous?._table?.id===data.id){previous._options=options;return previous;}
  const root=document.createElement('section');root.className='message-table';root.dataset.uiKey='table';root.setAttribute('aria-label','Таблица в ответе');root.setAttribute('aria-busy','false');root._options=options;
  const heading=document.createElement('div');heading.className='chat-table-heading';
  const title=document.createElement('h3'),count=document.createElement('span');count.className='chat-table-count';count.setAttribute('role','status');heading.append(title,count);
  const scope=document.createElement('p');scope.className='chat-table-scope';
  const scroll=document.createElement('div');scroll.className='table-scroll chat-table-scroll';scroll.dataset.uiScroll='chat-table';scroll.tabIndex=0;scroll.setAttribute('aria-label','Строки таблицы; прокрутка по горизонтали и вертикали');
  const table=document.createElement('table'),caption=table.createCaption();caption.className='sr-only';table.createTHead();table.createTBody();scroll.append(table);
  const pagination=document.createElement('div');pagination.className='chat-table-pagination';
  const prev=button('← Назад','Предыдущая страница таблицы','table-prev'),next=button('Далее →','Следующая страница таблицы','table-next');prev.dataset.page='prev';next.dataset.page='next';
  const pageLabel=document.createElement('label');pageLabel.className='chat-table-page-control';pageLabel.append('Страница ');
  const page=document.createElement('input');page.type='number';page.min=1;page.step=1;page.required=true;page.className='chat-table-page';page.setAttribute('aria-label','Номер страницы таблицы');page.dataset.focusKey='table-page';
  const pages=document.createElement('span');pages.className='chat-table-pages';pageLabel.append(page,pages);
  const limitLabel=document.createElement('label');limitLabel.className='chat-table-limit-control';limitLabel.append('Строк ');
  const limit=document.createElement('select');limit.className='chat-table-limit';limit.setAttribute('aria-label','Строк на странице таблицы');limit.dataset.focusKey='table-limit';
  for(const size of [25,50,100])limit.append(new Option(String(size),String(size)));limitLabel.append(limit);
  pagination.append(prev,pageLabel,next,limitLabel);
  const status=document.createElement('span');status.className='chat-table-status';status.setAttribute('role','status');
  const error=document.createElement('p');error.className='chat-table-error';error.setAttribute('role','alert');error.hidden=true;
  root.append(heading,scope,scroll,pagination,status,error);
  prev.onclick=()=>loadPage(root,Math.max(0,root._table.offset-root._table.limit),root._table.limit);
  next.onclick=()=>loadPage(root,root._table.offset+root._table.limit,root._table.limit);
  page.onchange=()=>{if(page.reportValidity())loadPage(root,(Number(page.value)-1)*root._table.limit,root._table.limit);};
  limit.onchange=()=>loadPage(root,0,Number(limit.value));
  showPage(root,data);
  return root;
}

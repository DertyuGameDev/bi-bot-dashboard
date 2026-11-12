import { messageTable } from '/assets/chat-table.js';

export function workspaceTable(value, options, previous=null) {
  const root=previous||document.createElement('section');root.className='workspace-table';
  const d=value.dataset;
  if(!d){root.textContent='Данные ещё не загружены. Задайте вопрос в чате.';return root;}
  const data={id:options.key,title:'Таблица · '+value.title,columns:d.columns,rows:d.preview,total:d.rows,offset:d.offset,limit:d.limit,filters:value.filters,snapshot:false};
  const previousTable=root.querySelector('.message-table');
  const table=messageTable(data,{loadPage:async(_,offset,limit)=>{
    const next=await options.loadPage(offset,limit),page=next.dataset;
    return {...data,rows:page.preview,total:page.rows,columns:page.columns,offset:page.offset,limit:page.limit,filters:next.filters};
  }},previousTable);
  if(previousTable&&previousTable!==table)previousTable.replaceWith(table);
  if(!table.isConnected)root.append(table);
  table.setAttribute('aria-label','Актуальная таблица выбранного дашборда');
  let formulas=root.querySelector('.workspace-calculations');
  if(!formulas){formulas=document.createElement('section');formulas.className='workspace-calculations';root.append(formulas);}
  const heading=document.createElement('div');heading.className='formulas-head';
  const title=document.createElement('h3');title.textContent='Вычисления';
  const actions=document.createElement('div');actions.className='button-group';
  const button=(text,fn)=>{const b=document.createElement('button');b.type='button';b.textContent=text;b.onclick=fn;b.disabled=!options.editable;return b;};
  const undo=button('Отменить последнее',()=>options.onEdit({action:'undo'}));undo.disabled||=!value.can_undo;undo.dataset.tableUndo='';
  const add=button('Добавить вычисление',options.onAdd);add.dataset.tableAdd='';actions.append(undo,add);heading.append(title,actions);
  const rows=value.calculations.map(c=>{
    const row=document.createElement('div');row.className='formula-row';row.dataset.calculation=c.name;
    const label=document.createElement('strong');label.textContent=c.label;
    const code=document.createElement('code');code.textContent=c.kind==='formula'?c.expression:c.kind+'('+c.column+')';
    const edit=button('Изменить',()=>options.onAdd(c));edit.dataset.tableEdit=c.name;
    const remove=button('Удалить',()=>options.onEdit({action:'delete',name:c.name}));remove.setAttribute('aria-label','Удалить вычисление '+c.label);remove.dataset.tableDelete=c.name;
    row.append(label,code,edit,remove);return row;
  });
  if(!rows.length){const p=document.createElement('p');p.className='workspace-table-hint';p.textContent='Попросите ассистента добавить колонку или задайте формулу кнопкой выше.';rows.push(p);}
  formulas.replaceChildren(heading,...rows);
  return root;
}

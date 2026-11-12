// Search changes the visible choices, never the selected values.
export const normalizeFilterValue = value => String(value).normalize('NFKC').toLocaleLowerCase('ru-RU').replaceAll('ё','е');
let sequence=0;

function position(picker) {
  if(!picker.open)return;
  const popup=picker.querySelector('.value-popup'), rect=picker.querySelector('summary').getBoundingClientRect();
  const parent=picker.closest('#chat-scroll, #data-panel')?.getBoundingClientRect();
  const top=Math.max(0,parent?.top || 0), bottom=Math.min(innerHeight,parent?.bottom || innerHeight);
  const below=bottom-rect.bottom, above=rect.top-top, up=below<220 && above>below;
  const height=Math.max(70,Math.min(360,(up ? above : below)-12));
  const width=Math.min(430,innerWidth-24);
  popup.style.width=width+'px';popup.style.maxHeight=height+'px';
  popup.style.left=Math.max(12-rect.left,Math.min(0,innerWidth-12-rect.left-width))+'px';
  picker.classList.toggle('opens-up',up);
  picker.querySelector('.value-options').style.maxHeight=Math.max(40,height-122)+'px';
}
function sync(picker) {
  const config=picker._config, pending=!!config.pending;
  picker.setAttribute('aria-busy',String(pending));
  picker.querySelector('.value-options').hidden=pending;
  picker.querySelector('.value-actions button').disabled=!config.onChange || pending;
  const terms=normalizeFilterValue(picker.querySelector('input[type=search]').value.trim()).split(/\s+/).filter(Boolean);
  let count=0;
  for(const row of picker.querySelector('.value-options').children){
    const input=row.querySelector('input');input.checked=picker._selected.has(input.value);input.disabled=!config.onChange || pending;
    row.hidden=!terms.every(term=>row._search.includes(term));if(!row.hidden)count++;
  }
  const values=[...picker._selected];
  const names=values.map(value=>picker._labels.get(value) || value);
  const label=names.length===0 ? config.all : names.length===1 ? names[0] : `Выбрано: ${names.length}`;
  picker.querySelector('.value-selection').textContent=label;
  picker.querySelector('summary').title=names.length ? names.join('\n') : config.all;
  picker.querySelector('summary').setAttribute('aria-label',config.label+': '+label);
  picker.querySelector('.value-count').textContent=pending ? 'Обновляю список…' : `Найдено: ${count}`+(config.multiple ? ` · Выбрано: ${values.length || 'все'}` : '');
  picker.querySelector('.value-hint').textContent=pending ? 'Список уточняется по выбранным фильтрам. Поиск можно вводить уже сейчас.' : config.hint || (config.multiple ? 'Можно выбрать несколько значений. Поиск не сбрасывает выбор.' : 'Найдите и выберите значение.');
  picker.querySelector('.value-none').hidden=pending || count>0;
  position(picker);
}
function select(picker,input){
  if(input.disabled || picker._config.pending)return;
  if(picker._config.multiple){input.checked ? picker._selected.add(input.value) : picker._selected.delete(input.value);}
  else picker._selected=new Set([input.value]);
  sync(picker);picker._config.onChange?.([...picker._selected]);
}

export function setFilterPending(field,pending){
  const picker=field?.querySelector('.value-picker');
  if(picker){picker._config.pending=pending;sync(picker);}
}

export function filterPicker(fields,config) {
  const {key,label,multiple=true,onChange}=config;
  let field=[...fields.children].find(node=>node.dataset.valueKey===key);
  if(!field){field=document.createElement('div');field.className='plot-field plot-field-'+key;field.dataset.valueKey=key;fields.append(field);}
  let picker=field.querySelector('.value-picker');
  if(!picker){
    const caption=document.createElement('span');caption.className='value-label';caption.textContent=label;
    picker=document.createElement('details');picker.className='value-picker';picker.dataset.uiKey='picker:'+key;
    const summary=document.createElement('summary');summary.dataset.focusKey='filter:'+key;
    const selection=document.createElement('span');selection.className='value-selection';summary.append(selection);
    const popup=document.createElement('div');popup.className='value-popup';popup.id='filter-popup-'+(++sequence);
    popup.setAttribute('role','group');popup.setAttribute('aria-label','Выбор: '+label);summary.setAttribute('aria-controls',popup.id);summary.setAttribute('aria-expanded','false');
    const search=document.createElement('input');search.type='search';search.placeholder='Найти значение…';search.autocomplete='off';
    search.setAttribute('aria-label','Поиск: '+label);search.dataset.focusKey='filter-search:'+key;
    const actions=document.createElement('div');actions.className='value-actions';
    const clear=document.createElement('button');clear.type='button';clear.dataset.focusKey='filter-clear:'+key;
    const count=document.createElement('span');count.className='value-count';count.setAttribute('role','status');actions.append(clear,count);
    const list=document.createElement('div');list.className='value-options';list.dataset.uiScroll='filter-options:'+key;
    const none=document.createElement('p');none.className='value-none';none.textContent='Значений не найдено. Измените поиск.';none.hidden=true;
    const hint=document.createElement('p');hint.className='value-hint';
    popup.append(search,actions,hint,list,none);picker.append(summary,popup);field.append(caption,picker);
    search.oninput=()=>{list.scrollTop=0;sync(picker);};
    clear.onclick=()=>{picker._selected.clear();sync(picker);picker._config.onChange?.([]);};
    list.onchange=event=>{if(event.target.matches('input'))select(picker,event.target);};
    picker.addEventListener('toggle',()=>{
      summary.setAttribute('aria-expanded',String(picker.open));
      if(picker.open){
        for(const other of document.querySelectorAll('.value-picker[open]'))if(other!==picker)other.open=false;
        position(picker);search.focus({preventScroll:true});
      }
    });
    picker.onkeydown=event=>{
      if(event.key==='Escape' && picker.open){event.preventDefault();event.stopPropagation();picker.open=false;summary.focus({preventScroll:true});return;}
      const choices=[...list.querySelectorAll('label:not([hidden]) input:not(:disabled)')];
      if((event.key==='ArrowDown' || event.key==='ArrowUp') && event.target!==summary){
        event.preventDefault();const index=choices.indexOf(event.target), next=event.key==='ArrowDown' ? choices[index+1] : choices[index-1];(next || search).focus();
      }else if(event.key==='Enter' && event.target!==summary){
        event.preventDefault();const target=choices.includes(event.target) ? event.target : !picker._config.multiple ? choices[0] : null;
        if(target){target.checked=picker._config.multiple ? !target.checked : true;select(picker,target);}
      }
    };
  }
  picker._config={...config,multiple};picker._selected=new Set(config.current);
  const values=config.values.map(value=>Array.isArray(value) ? value.map(String) : [String(value),String(value)]);
  picker._labels=new Map(values);
  picker.querySelector('input[type=search]').placeholder=config.placeholder || 'Найти значение…';
  const clear=picker.querySelector('.value-actions button');clear.textContent=config.all;clear.hidden=!multiple;clear.disabled=!onChange;
  const list=picker.querySelector('.value-options'), entries=new Map([...list.children].map(row=>[row.querySelector('input').value,row]));
  const alive=new Set(values.map(([value])=>value));
  for(const row of [...list.children])if(!alive.has(row.querySelector('input').value))row.remove();
  values.forEach(([value,name],index)=>{
    let row=entries.get(value);
    if(!row){
      row=document.createElement('label');row.className='value-option';const input=document.createElement('input'),text=document.createElement('span');
      input.type=multiple ? 'checkbox' : 'radio';input.name=picker.querySelector('.value-popup').id;input.value=value;input.dataset.focusKey='filter-option:'+key+':'+value;row.append(input,text);
    }
    row._search=normalizeFilterValue(name);row.querySelector('span').textContent=name;row.querySelector('input').disabled=!onChange;
    if(list.children[index]!==row)list.insertBefore(row,list.children[index] || null);
  });
  sync(picker);return field;
}

document.addEventListener('pointerdown',event=>{for(const picker of document.querySelectorAll('.value-picker[open]'))if(!picker.contains(event.target))picker.open=false;});
window.addEventListener('resize',()=>{for(const picker of document.querySelectorAll('.value-picker[open]'))position(picker);});
let layoutFrame;
document.addEventListener('scroll',()=>{
  cancelAnimationFrame(layoutFrame);
  layoutFrame=requestAnimationFrame(()=>{for(const picker of document.querySelectorAll('.value-picker[open]'))position(picker);});
},{capture:true,passive:true});

import { filterPicker } from '/assets/filter-picker.js';

export function outletPicker(fields,values,current,onChange,options={}){
  return filterPicker(fields,{
    key:'outlets',label:'Торговые точки',values,current,onChange,all:'Все точки',
    placeholder:'Название, город или адрес',hint:'Отметьте несколько точек для сравнения. Поиск не сбрасывает выбор.',
    ...options,
  });
}

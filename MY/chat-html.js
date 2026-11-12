// Safe Markdown HTML. Interactive dashboards are typed SVG objects, not model HTML.
export function htmlFragment(value) {
  const sourceDoc = new DOMParser().parseFromString(String(value), 'text/html');
  const fragment = document.createDocumentFragment();
  const aliases = {strong:'b',em:'i',ins:'u',strike:'s',del:'s'};
  const allowed = new Set(['b','i','u','s','a','code','pre','blockquote','p','br','hr','h1','h2','h3','h4','h5','h6','ul','ol','li','table','thead','tbody','tr','th','td','caption','details','summary','sub','sup','mark']);
  function append(source,parent) {
    if (source.nodeType === Node.TEXT_NODE) {parent.append(document.createTextNode(source.textContent));return;}
    if (source.nodeType !== Node.ELEMENT_NODE) return;
    const raw = source.tagName.toLowerCase();
    if (['script','style','iframe','object','embed','svg','canvas','img'].includes(raw)) return;
    if (!allowed.has(raw) && !Object.hasOwn(aliases,raw)) {for(const child of source.childNodes) append(child,parent);return;}
    const target = document.createElement(aliases[raw] || raw);
    if (raw === 'a') {
      const href = source.getAttribute('href') || '';
      if (/^https?:\/\//i.test(href)) {target.href=href;target.rel='noopener noreferrer';target.target='_blank';}
    }
    if (raw === 'details' && source.hasAttribute('open')) target.open = true;
    if (['th','td'].includes(raw)) for (const attr of ['colspan','rowspan']) {
      const number=Number(source.getAttribute(attr));if(number>0 && number<100)target.setAttribute(attr,String(number));
    }
    for (const child of source.childNodes) append(child,target);
    if(raw==='table') {const scroll=document.createElement('div');scroll.className='table-scroll';scroll.tabIndex=0;scroll.setAttribute('aria-label','Таблица ответа');scroll.append(target);parent.append(scroll);} else parent.append(target);
  }
  for (const node of sourceDoc.body.childNodes) append(node,fragment);
  return fragment;
}

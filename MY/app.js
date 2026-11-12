import { chartWidget } from "/assets/chart-widget.js";
import { htmlFragment } from "/assets/chat-html.js";
import { messageCharts, kpiCards, METRICS, VIEW_MODES } from "/assets/dashboard.js";
import { filterPicker, setFilterPending } from "/assets/filter-picker.js";
import { messageTable } from "/assets/chat-table.js";
import { workspaceTable } from "/assets/workspace-table.js";
import { captureChatView, restoreChatView } from "/assets/chat-view.js";
import { conversationID, layoutKey, installConversations } from "/assets/conversations.js";
const $ = (s) => document.querySelector(s),
  $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
let lastError = "",
  view = null,
  busy = false,
  activeTab = "chat",
  tableOffset = 0,
  savedItems = [],
  filterTimer,
  filterFlight,
  toastTimer;
const filterQueues = new Map();
const queueKey = id => id || "data";
const pendingFor = id => filterQueues.get(queueKey(id))?.desired || {};
let saveMessageId = null;
let working = false,
  outbox = [],
  failed = [],
  forceScroll = false;
let featureUI=null;
let independentCards=false,liveReady=false,boardCache=null,saveCacheTimer;
const cardTokens=new Map(),cardPreviews=new Map(),cardFlights=new Map();
let panelSelectionEpoch=0,analysisSelectionEpoch=0;
let conversationUI=null, selectedPanel=null, panelMessage=null, calculationMessageId=null;
const panelCache=new Map(),panelPositions=new Map();
let initialLayout=null;
try{initialLayout=JSON.parse(sessionStorage.getItem(layoutKey())||'null');selectedPanel=initialLayout?.dashboard||null;}catch{}
let workspaceMode=initialLayout?.workspaceView==='table'?'table':'charts',tableSequence=0,tableFlightKey='';
const workspaceTables=new Map();
let renderedLastMessage = null;
let stableChatView = null, resizing = false;
function rememberChatView() {
  if (!resizing && view) stableChatView=captureChatView($('#messages'),$('#chat-scroll'));
}
$('#chat-scroll').addEventListener('scroll',()=>requestAnimationFrame(rememberChatView),{passive:true});
const mediaCache = new Map();
const baseHeaders = (messageId = null) => {
  const content = $("#dashboard-scroll")?.clientWidth || Math.min(1320, innerWidth - 48);
  const columns = content >= 760 ? 2 : 1;
  const chartWidth = (content - (columns - 1) * 22) / columns;
  const plot = messageId ? [...$$(".message-plots")].find(n=>n.dataset.messageId===messageId) : $('.message-plots[data-current="true"]');
  const plotStyle = plot && getComputedStyle(plot);
  const plotContent = plot?.clientWidth ? plot.clientWidth - parseFloat(plotStyle.paddingLeft) - parseFloat(plotStyle.paddingRight) : content - (innerWidth <= 760 ? 22 : 30);
  const dashboardHeight=$("#dashboard-scroll")?.clientHeight || innerHeight - 120;
  return {"Content-Type":"application/json", "X-Requested-With":"puzzle-rpa", "X-Conversation-ID":conversationID(), "X-Chart-Width":String(Math.round(Math.min(700,Math.max(320,chartWidth)))), "X-Dashboard-Width":String(Math.round(plotContent)), "X-Dashboard-Height":String(Math.round(dashboardHeight))};
};
async function api(path, body, options = {}) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: baseHeaders(body?.message_id),
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    ...options,
  });
  if (!response.ok) {
    let message;
    try {
      const value = await response.json();
      message =
        typeof value.detail === "string"
          ? value.detail
          : "Проверьте параметры запроса.";
    } catch {
      message = "Сервер недоступен. Повторите сообщение.";
    }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.json();
}
function error(message) {
  lastError = message;
  $("#error").textContent = message;
  $("#error").hidden = !message;
  if (activeTab === "data" && view?.dataset) renderDebug();
}
function toast(text) {
  clearTimeout(toastTimer);
  $("#toast").textContent = text;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 3500);
}
function setBusy(value, text = "Обрабатываю запрос…") {
  busy = value;
  $("#busy").hidden = !value;
  $("#busy-text").textContent = text;
  $("#connection-status").textContent = value ? "Идёт анализ" : "Готов к работе";
  if (value) showProgress({label: text, phase: "analysis", steps: 0});
  $("#messages").setAttribute("aria-busy", String(value));
  for (const button of $$('#saved-list [data-open], #saved-list [data-copy], #saved-list [data-rename], #saved-list [data-delete], #saved-list .saved-rename button, #work-new, #work-copy')) button.disabled=value;
  for(const button of $$('[data-resume-dashboard]'))button.disabled=value || button.closest('.message-plots').classList.contains('is-working');
  for(const button of $$('.message-dashboard-link'))button.disabled=value&&!independentCards;
  if(view)renderPanel();
}
function tab(name) {
  activeTab = name;
  for (const x of ["data", "chat"]) {
    $("#" + x + "-panel").hidden = x !== name;
    $("#" + x + "-tab").setAttribute("aria-selected", String(x === name));
    $("#" + x + "-tab").tabIndex = x === name ? 0 : -1;
  }
  if (name === "data") {
    renderData();
    loadSavedCount();
  } else {
    renderMessages();
    renderPanel();
    $("#message-input").focus();
  }
}
$("#data-tab").onclick = () => tab("data");
$("#chat-tab").onclick = () => tab("chat");
$("#to-chat").onclick = () => tab("chat");
$(".tabs").onkeydown = (e) => {
  if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
    e.preventDefault();
    tab(activeTab === "chat" ? "data" : "chat");
    $("#" + activeTab + "-tab").focus();
  }
};
$("#message-input").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("#chat-form").requestSubmit();
  }
};
$("#message-input").oninput = (e) => {
  e.target.style.height = "auto";
  e.target.style.height = Math.min(120, e.target.scrollHeight) + "px";
};
$("#chat-form").onsubmit = (e) => {
  e.preventDefault();
  const text = $("#message-input").value.trim();
  if (text) send(text);
};
function send(text) {
  outbox.push({ id: crypto.randomUUID(), kind: "text", text });
  featureUI?.persistQueue();featureUI?.clearDraft();
  $("#message-input").value = "";
  $("#message-input").style.height = "auto";
  forceScroll = true;
  renderMessages();
  drain();
  $("#message-input").focus();
}
function action(ident, messageId) {
  outbox.push({ id: crypto.randomUUID(), kind: "action", action_id: ident, message_id: messageId });
  featureUI?.persistQueue();
  forceScroll = true;
  drain();
}
async function drain() {
  if (working || !outbox.length) return;
  if(!liveReady){setTimeout(drain,200);return;}
  if (busy) {setTimeout(drain,200);return;}
  working = true;
  analysisSelectionEpoch=panelSelectionEpoch;
  if (!view) {
    try {
      view = await api("/api/state");
    } catch (e) {
      working = false;
      error(e.message);
      return;
    }
  }
  while (filterQueues.size || filterFlight) {
    clearTimeout(filterTimer);
    await flushFilters();
  }
  setBusy(true);
  error("");
  const job = outbox[0];
  try {
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        if (job.kind === "text") {
          if(selectedPanel && view.messages.find(m=>m.id===selectedPanel)?.dashboard && view.workspace?.message_id!==selectedPanel){
            view=await api('/api/workspace/continue',{revision:view.revision,arguments:{},message_id:selectedPanel});
            render();
          }
          const started = await api("/api/chat/jobs", {message: job.text, revision: view.revision, request_id: job.id});
          view = await waitJob(started);
        } else view = await api("/api/chat/action", {action_id: job.action_id, message_id: job.message_id, revision: view.revision});
        break;
      } catch (e) {
        if (e.status === 409 && attempt < 2) {
          view = await api("/api/state");
          continue;
        }
        throw e;
      }
    }
    outbox.shift();
    tableOffset = 0;
    forceScroll = true;
    render();
    loadSavedCount();
    conversationUI?.refresh().catch(()=>{});
  } catch (e) {
    outbox.shift();
    if (job.kind === "text") failed.push({ ...job, error: e.message });
    error(e.message);
    renderMessages();
  } finally {
    working = false;
    setBusy(false);
    featureUI?.persistQueue();
    if (outbox.length) drain();
  }
}
function keyboard(rows) {
  const root = document.createElement("div");
  root.className = "native-keyboard";
  for (const row of rows || []) {
    const line = document.createElement("div");
    line.className = "native-button-row";
    for (const spec of row) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = spec.text;
      button.dataset.actionId = spec.action_id;
      button.onclick = () => action(spec.action_id);
      line.append(button);
    }
    root.append(line);
  }
  return root;
}
function timeLabel(message) {
  if (!message.created) return "";
  return new Date(message.created).toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  });
}
async function mediaURL(media) {
  if (!/^\/api\/chat\/media\/[a-f0-9]{32}$/.test(media.url))
    throw new Error("Неверный адрес вложения");
  if (!mediaCache.has(media.id)) {
    mediaCache.set(
      media.id,
      fetch(media.url, { headers: baseHeaders() })
        .then(async (response) => {
          if (!response.ok) throw new Error("Вложение недоступно");
          return URL.createObjectURL(await response.blob());
        })
        .catch((e) => {
          mediaCache.delete(media.id);
          throw e;
        }),
    );
  }
  return mediaCache.get(media.id);
}
function showProgress(progress) {
  $("#busy").hidden = false;
  $("#busy-text").textContent = progress.label || "Обрабатываю запрос…";
  const bar = $("#analysis-progress");
  if (progress.phase === "render" && progress.total) {
    bar.max = progress.total; bar.value = progress.completed;
  } else if (progress.phase === "done") {bar.max = 1; bar.value = 1;}
  else bar.removeAttribute("value");
  const determinate = bar.hasAttribute("value");
  $(".progress-track").classList.toggle("indeterminate", !determinate);
  $(".progress-fill").style.width = determinate ? Math.round(bar.value / bar.max * 100) + "%" : "30%";
  $("#progress-detail").textContent = [
    progress.steps ? `Выполнено шагов: ${progress.steps}` : "Подготовка ответа",
    progress.rows ? `${number(progress.rows,0)} строк получено` : "",
    progress.phase === "render" ? `Графики: ${progress.completed} из ${progress.total}` : "",
  ].filter(Boolean).join(" · ");
}
async function waitJob(job) {
  while (!["done","error"].includes(job.status)) {
    showProgress(job.progress);
    await new Promise(resolve => setTimeout(resolve, 550));
    job = await api("/api/chat/jobs/" + job.id);
  }
  if (job.status === "error") throw new Error(job.error);
  showProgress(job.progress);
  return mergeView(await api('/api/state'));
}

function mergeView(next){
  const known=new Map((view?.messages||[]).map(m=>[m.id,m]));
  next.messages=next.messages.map(m=>{const previous=known.get(m.id);return previous?.dashboard&&(previous.dashboard.settings_revision||0)>(m.dashboard?.settings_revision||0)?{...m,dashboard:previous.dashboard}:m;});
  next.revision=Math.max(next.revision,view?.revision||0);
  for(const m of next.messages)if(m.dashboard){cardTokens.set(m.id,m.dashboard.settings_revision||0);boardCache?.remember(m.id,m.dashboard);}
  return next;
}
function acceptCard(result){
  if(!view)return;
  const id=result.message.id,revision=result.settings_revision||0;
  if(revision<(cardTokens.get(id)||0))return;
  const index=view.messages.findIndex(m=>m.id===id);if(index<0)return;
  if(view.messages[index].dashboard?.source_signature!==result.message.dashboard.source_signature)cardPreviews.delete(id);
  view.messages[index]={...view.messages[index],...result.message};cardTokens.set(id,revision);
  view.revision=Math.max(view.revision,result.revision);
  if(result.current)Object.assign(view,result.current);
  boardCache?.remember(id,result.message.dashboard);
}
async function refreshCard(id){
  const result=await api('/api/cards/'+encodeURIComponent(id));acceptCard(result);render();return result;
}
function editIndependentCard(id,operation,arguments_){
  const previous=cardFlights.get(id)||Promise.resolve();
  const flight=previous.catch(()=>{}).then(async()=>{
    dashboardBusy(id,true);
    try{
      for(let attempt=0;attempt<3;attempt++){
        try{
          const result=await api('/api/cards/'+encodeURIComponent(id)+'/edit',{settings_revision:cardTokens.get(id)??view.messages.find(m=>m.id===id)?.dashboard?.settings_revision??0,operation,arguments:arguments_});
          acceptCard(result);return result;
        }catch(e){if(e.status!==409||attempt===2)throw e;await refreshCard(id);}
      }
    }finally{dashboardBusy(id,false);}
  });
  cardFlights.set(id,flight);
  return flight.finally(()=>{if(cardFlights.get(id)===flight)cardFlights.delete(id);});
}
function chartChanges(index, changes) {
  return mutate("/api/chart", {index, changes});
}
function drawMessageDashboard(message, previous = null) {
  const latest = [...view.messages].reverse().find(m => m.dashboard);
  const editable = message.dashboard.editable !== false;
  const pending = pendingFor(message.id);
  const display=cardPreviews.get(message.id)||message.dashboard;
  const data = {...display,filters:{...display.filters,...pending}};
  const result = messageCharts(data, {
    ...(display.options || {}),
    onChange: editable && liveReady && (!busy||independentCards) ? (index,changes)=>mutate('/api/chart',{index,changes},undefined,message.id) : null,
    onFilters: editable && liveReady && (!busy||independentCards) ? patch=>queueFilters(patch,message.id) : null,
    onSave: editable ? ()=>openSave(message.id) : null,
    onOpen: openSavedDashboard,
    onContinue: editable ? ()=>continueCard(message.id) : null,
    onActions: editable ? ()=>featureUI?.openActions(message.id) : null,
    onDownload: editable && liveReady && featureUI ? kind=>featureUI.download(message.id,kind) : null,
    onSync: editable ? enabled=>mutate('/api/sync-zoom',{enabled},undefined,message.id) : null,
    active:view.workspace?.message_id===message.id,
    cascadePending:editable && Object.hasOwn(pending,'regions') && !cardPreviews.has(message.id),
  }, previous);
  if(busy){const button=result.querySelector('[data-resume-dashboard]');if(button)button.disabled=!independentCards;}
  result.dataset.messageId=message.id;
  result.dataset.current=String((view.workspace ? view.workspace.message_id===message.id : latest?.id===message.id) && message.dashboard.table_id===view.dataset?.id);
  if(filterQueues.has(queueKey(message.id)))result.setAttribute('aria-busy','true');
  if(!liveReady)result.querySelector('.plot-status').textContent='Проверяю сохранённое состояние…';
  return result;
}
function drawMessageTable(message, previous = null) {
  const result=previous?.matches('.message-table-link')?previous:document.createElement('button');
  result.type='button';result.className='message-table-link';
  result.textContent='Таблица · '+number(message.table.total,0)+' строк';
  result.setAttribute('aria-label','Открыть таблицу в рабочей области');
  result.onclick=()=>continueCard(message.id,'table');
  if(previous && result!==previous)previous.replaceWith(result);
  return result;
}
function analysisContext(group,message) {
  if (!message.dashboard) return;
  let note=group.querySelector('.analysis-context');
  if (!note) {note=document.createElement('p');note.className='analysis-context';group.querySelector('.message-content')?.before(note);}
  note.hidden=!message.analysis_filters || JSON.stringify(message.analysis_filters)===JSON.stringify(message.dashboard.filters);
  note.textContent='Графики обновлены. Текст ответа относится к исходному запросу.';
}
function renderMessages() {
  const root = $("#messages"), scroll = $("#chat-scroll");
  const savedView = captureChatView(root, scroll);
  const requestedScroll = forceScroll;
  forceScroll = false;
  const current = view?.messages || [];
  const newest = current.at(-1);
  const newReply = newest?.id !== renderedLastMessage && newest?.role === "assistant";
  if(newReply && (newest.dashboard||newest.table) && panelSelectionEpoch===analysisSelectionEpoch && (renderedLastMessage||!initialLayout)){
    selectedPanel=newest.id;
    if(newest.workspace_view==='table'||newest.table){workspaceContent('table');if(renderedLastMessage)panelView('dashboard');}
    else if(renderedLastMessage)workspaceContent('charts');
  }
  renderedLastMessage = newest?.id;
  $("#welcome").hidden = !!(current.length || outbox.length || failed.length);
  const existing = new Map([...root.children].filter(node => node.dataset.messageId).map(node => [node.dataset.messageId,node]));
  const alive = new Set(current.map(message => message.id));
  for (const node of [...root.children]) if (!node.dataset.messageId || !alive.has(node.dataset.messageId)) node.remove();
  for (const [index,message] of current.entries()) {
    const reused = existing.get(message.id);
    const old = reused?._message;
    if (old && old.content === message.content && old.markup === message.markup && old.formatted_html === message.formatted_html && JSON.stringify(old.attachments) === JSON.stringify(message.attachments) && JSON.stringify(old.buttons) === JSON.stringify(message.buttons)) {
      if (message.dashboard) dashboardLink(reused,message);
      if (message.table) {
        const table=drawMessageTable(message,reused.querySelector('.message-table-link, .message-table'));
        if(!table.isConnected){const body=reused.querySelector('.native-message');body.insertBefore(table,body.querySelector('.message-content'));}
      }
      analysisContext(reused,message);
      reused._message=message;
      if (root.children[index] !== reused) root.insertBefore(reused,root.children[index] || null);
      continue;
    }
    const group = document.createElement("article");
    group.className = "chat-message " + (message.role === "user" ? "outgoing" : "incoming") + (message.dashboard ? " has-dashboard" : "");
    group.dataset.messageId = message.id;
    const bubble = document.createElement("div");
    bubble.className = "native-message text-message";
    const author = document.createElement("span");
    author.className = "message-author";
    author.textContent = message.role === "user" ? "Вы" : "Ассистент";
    bubble.append(author);
    const text = document.createElement("div");
    text.className = "message-content";
    if (message.role === "user") text.textContent = message.content;
    else text.append(htmlFragment(message.markup || message.formatted_html || message.html || esc(message.content)));
    if (message.table) bubble.append(drawMessageTable(message));
    if (message.content || message.formatted_html) bubble.append(text);
    const photos = (message.attachments || []).filter(a => a.kind === "photo");
    if (!message.dashboard && photos.length) {
      const gallery = document.createElement("div");
      gallery.className = "legacy-chart-grid";
      for (const photo of photos.slice(0,4)) {
        const img = document.createElement("img"); img.alt = photo.title;
        mediaURL(photo).then(url => img.src = url).catch(e => error(e.message));
        gallery.append(img);
      }
      const previous = document.createElement("details");
      previous.className = "legacy-attachments";
      const summary = document.createElement("summary");summary.textContent = "Вложения прежнего ответа";
      previous.append(summary,gallery);bubble.append(previous);
    }
    for (const media of (message.attachments || []).filter(a => a.kind === "document")) {
      const button = document.createElement("button");
      button.className = "native-file"; button.type = "button"; button.textContent = "↓ " + media.filename;
      button.onclick = async () => {
        try {const a = document.createElement("a");a.href = await mediaURL(media);a.download = media.filename;a.click();} catch(e) {error(e.message);}
      };
      bubble.append(button);
    }
    if (!message.dashboard && message.buttons?.length) bubble.append(keyboard(message.buttons));
    const time = document.createElement("time"); time.className = "message-time"; time.textContent = timeLabel(message);
    bubble.append(time);group.append(bubble);group._message=message;
    if(message.dashboard)dashboardLink(group,message);
    analysisContext(group,message);
    if (reused) reused.replaceWith(group);
    if (root.children[index] !== group) root.insertBefore(group,root.children[index] || null);
  }
  const known = new Set(current.map(m => m.request_id));
  for (const job of [...failed, ...outbox]) {
    if (job.kind !== "text" || known.has(job.id)) continue;
    const group = document.createElement("article");group.className = "chat-message outgoing queued-message";
    const bubble = document.createElement("div");bubble.className = "native-message text-message";
    const text = document.createElement("div");text.className = "message-content";text.textContent = job.text;
    const status = document.createElement("small");status.className = "message-time";status.textContent = job.error ? "Не отправлено" : "Ожидает ответа";
    bubble.append(text,status);group.append(bubble);
    if (job.error) {const button = document.createElement("button");button.textContent = "Повторить";button.type = "button";button.onclick = () => {failed = failed.filter(x => x.id !== job.id);outbox.push({...job,id:crypto.randomUUID(),error:null});featureUI?.persistQueue();renderMessages();drain();};group.append(button);}
    root.append(group);
  }
  const pending = outbox.some(job => job.kind === "text" && !known.has(job.id));
  if (newReply && !pending) {
    const reply=[...root.children].find(node=>node.dataset.messageId===newest.id);
    const question = [...current].reverse().find(message => message.role === "user");
    const anchor = reply?.querySelector('.message-plots, .message-table') || (question ? [...root.children].find(node => node.dataset.messageId === question.id) : root.lastElementChild);
    if (anchor) requestAnimationFrame(() => {
      scroll.scrollTop += anchor.getBoundingClientRect().top - scroll.getBoundingClientRect().top - 8;
    });
  } else if (requestedScroll) requestAnimationFrame(() => scroll.scrollTop = scroll.scrollHeight);
  else restoreChatView(root,scroll,savedView);
  requestAnimationFrame(rememberChatView);
}

function dashboardLink(group,message){
  let button=group.querySelector('.message-dashboard-link');
  if(!button){button=document.createElement('button');button.type='button';button.className='message-dashboard-link';group.querySelector('.native-message').insertBefore(button,group.querySelector('.message-content'));}
  const title=message.dashboard.saved_title||message.dashboard.title;
  button.textContent=title;button.setAttribute('aria-label','Открыть дашборд: '+title);
  button.classList.toggle('is-selected',selectedPanel===message.id);
  button.disabled=busy&&!independentCards;button.onclick=()=>continueCard(message.id,'charts');
}
function workspaceContent(name,focus=false){
  workspaceMode=name;
  for(const [key,panel]of [['charts','dashboard-scroll'],['table','workspace-table-panel']]){
    const button=$('#workspace-'+key+'-tab');button.setAttribute('aria-selected',String(key===name));button.tabIndex=key===name?0:-1;
    $('#'+panel).hidden=key!==name;if(focus&&key===name)button.focus({preventScroll:true});
  }
  if(name==='table')renderWorkspaceTable();
}
for(const name of ['charts','table'])$('#workspace-'+name+'-tab').onclick=()=>{workspaceContent(name);if(name==='charts')fitWorkspace().catch(e=>error(e.message));};
$('.workspace-view-tabs').onkeydown=e=>{
  if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
  e.preventDefault();const name=e.key==='Home'?'charts':e.key==='End'?'table':workspaceMode==='table'?'charts':'table';
  if(!$('#workspace-'+name+'-tab').disabled)workspaceContent(name,true);
};
function openCalculation(messageId,calculation=null){
  calculationMessageId=messageId;
  $('#calc-name').value=calculation?.name||'';$('#calc-label').value=calculation?.label||'';$('#calc-expression').value=calculation?.expression||'';
  $('#calculation-dialog').showModal();$('#calc-name').focus();
}
async function renderWorkspaceTable(){
  if(!view||!liveReady||workspaceMode!=='table')return;
  const message=view.messages.find(m=>m.id===selectedPanel),mount=$('#workspace-table-mount'),status=$('#workspace-table-status');
  if(!message){tableSequence++;mount.replaceChildren();status.textContent='Попросите показать таблицу или выберите дашборд из переписки.';return;}
  const key=message.id+':'+JSON.stringify(message.dashboard?[message.dashboard.source_signature,message.dashboard.filters]:message.table?.id);
  const cached=workspaceTables.get(message.id);
  if(cached?.key===key){if(mount.firstElementChild!==cached.node)mount.replaceChildren(cached.node);status.textContent='';return;}
  if(tableFlightKey===key)return;
  tableFlightKey=key;
  const sequence=++tableSequence;mount.replaceChildren();status.textContent='Загружаю таблицу…';mount.setAttribute('aria-busy','true');
  try{
    let node;
    if(message.dashboard){
      const load=(offset=0,limit=25)=>api('/api/cards/'+encodeURIComponent(message.id)+'/table?offset='+offset+'&limit='+limit);
      const value=await load();
      if(sequence!==tableSequence||selectedPanel!==message.id||workspaceMode!=='table')return;
      node=workspaceTable(value,{key,loadPage:load,editable:liveReady&&(!busy||independentCards),onAdd:c=>openCalculation(message.id,c),onEdit:args=>mutate('/api/calculation',args,'Таблица пересчитана',message.id)});
    }else{
      node=messageTable(message.table,{loadPage:(id,offset,limit)=>api(`/api/chat/tables/${id}?offset=${offset}&limit=${limit}`)});
    }
    if(sequence!==tableSequence||selectedPanel!==message.id||workspaceMode!=='table')return;
    workspaceTables.set(message.id,{key,node});mount.replaceChildren(node);status.textContent='';
  }catch(e){if(sequence===tableSequence){status.textContent=e.message;const retry=document.createElement('button');retry.type='button';retry.textContent='Повторить загрузку';retry.onclick=renderWorkspaceTable;mount.replaceChildren(retry);}}
  finally{if(tableFlightKey===key)tableFlightKey='';if(sequence===tableSequence)mount.setAttribute('aria-busy','false');}
}
function panelView(name,focus=false){
  $('#chat-panel').dataset.mobileView=name;
  for(const [id,panel] of [['conversation','chat'],['dashboard','dashboard']]){
    const button=$('#'+id+'-view-tab');button.setAttribute('aria-selected',String(panel===name));button.tabIndex=panel===name?0:-1;
    if(focus&&panel===name)button.focus({preventScroll:true});
  }
}
for(const [id,name] of [['conversation','chat'],['dashboard','dashboard']])$('#'+id+'-view-tab').onclick=()=>{panelView(name);fitWorkspace().catch(e=>error(e.message));};
$('.mobile-work-tabs').onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const fromChat=document.activeElement?.id==='conversation-view-tab';const next=e.key==='Home'?'chat':e.key==='End'?'dashboard':fromChat?'dashboard':'chat';panelView(next,true);fitWorkspace().catch(e=>error(e.message));}};
function renderPanel(){
  if(!view)return;
  const cards=view.messages.filter(m=>m.dashboard||m.table),workspace=view.workspace?.message_id;
  if(!cards.some(m=>m.id===selectedPanel))selectedPanel=workspace || (view.dataset?cards.at(-1)?.id:null);
  if(!view.dataset&&!view.workspace&&!(independentCards&&selectedPanel))selectedPanel=null;
  const message=cards.find(m=>m.id===selectedPanel),mount=$('#dashboard-mount'),scroll=$('#dashboard-scroll');
  const same=panelMessage===selectedPanel,saved=same?captureChatView(mount,scroll):null;
  if(!same&&panelMessage)panelPositions.set(panelMessage,scroll.scrollTop);
  const select=$('#dashboard-select');
  const options=cards.map(m=>({id:m.id,title:m.dashboard?.saved_title||m.dashboard?.title||m.table.title}));
  if(select.dataset.options!==JSON.stringify(options)){
    select.replaceChildren(...(options.length?options.map(m=>new Option(m.title,m.id)):[new Option('Пока нет дашбордов','')]));select.dataset.options=JSON.stringify(options);
  }
  select.value=selectedPanel||'';select.disabled=!cards.length||(busy&&!independentCards);
  $('#dashboard-empty').hidden=!!message?.dashboard;$('#panel-table').disabled=!message;$('#panel-calculation').disabled=!message?.dashboard||(busy&&!independentCards);$('#panel-new').disabled=busy;
  $('#workspace-charts-tab').disabled=!!message&&!message.dashboard?.charts?.length;
  if(message?.dashboard){
    const plot=drawMessageDashboard(message,panelCache.get(message.id));panelCache.set(message.id,plot);
    if(mount.firstElementChild!==plot)mount.replaceChildren(plot);
  }else mount.replaceChildren();
  panelMessage=selectedPanel;
  if(message&&!message.dashboard)workspaceContent('table');
  else workspaceContent(workspaceMode);
  if(saved)restoreChatView(mount,scroll,saved);else scroll.scrollTop=panelPositions.get(selectedPanel)||0;
  for(const b of $$('.message-dashboard-link'))b.classList.toggle('is-selected',b.closest('[data-message-id]')?.dataset.messageId===selectedPanel);
}
$('#dashboard-select').onchange=e=>continueCard(e.target.value);
$('#panel-table').onclick=()=>{workspaceContent('table');panelView('dashboard');};
$('#panel-calculation').onclick=()=>openCalculation(selectedPanel);
$('#panel-templates').onclick=()=>{panelView('chat');$('#templates-button').click();};
$('#panel-new').onclick=()=>$('#work-new').click();
window.addEventListener('pagehide',()=>{try{sessionStorage.setItem(layoutKey(),JSON.stringify({dashboard:selectedPanel,chatTop:$('#chat-scroll').scrollTop,dashboardTop:$('#dashboard-scroll').scrollTop,mobileView:$('#chat-panel').dataset.mobileView,workspaceView:workspaceMode}));}catch{}});
async function mutate(path, arguments_, message, messageId = null) {
  const operation={'/api/chart':'chart','/api/calculation':'calculation','/api/sync-zoom':'sync'}[path];
  if(independentCards&&messageId&&operation){
    if(working)panelSelectionEpoch++;
    try{await editIndependentCard(messageId,operation,arguments_);cardPreviews.delete(messageId);render();if(message)toast(message);return true;}catch(e){error(e.message);return false;}
  }
  if (busy || !view) return;
  while (filterFlight || filterQueues.size) {
    if (busy) return false;
    clearTimeout(filterTimer);
    await flushFilters();
  }
  if (busy) return false;
  setBusy(true, "Обновляю результат…");
  error("");
  try {
    dashboardBusy(messageId,true);
    view = await api(path, { revision: view.revision, arguments: arguments_, message_id: messageId });
    render();
    loadSavedCount();
    if (message) toast(message);
    return true;
  } catch (e) {
    error(e.message);
    if (e.status === 409) {
      view = await api("/api/state");
      render();
    }
    return false;
  } finally {
    setBusy(false);
    dashboardBusy(messageId,false);
  }
}
function render() {
  if (!view) return;
  const data = view.dataset;
  $("#tab-count").hidden = !data;
  $("#tab-count").textContent = data ? shortCount(data.rows) : "";
  $("#debug").hidden = !data;
  $("#refresh-button").disabled = !data;
  $("#csv-button").disabled = !data;
  renderWorkspace();
  featureUI?.onRender();featureUI?.persistQueue();
  renderMessages();
  renderPanel();
  if (activeTab === "data") {
    renderData();
  }
  if(liveReady&&boardCache){clearTimeout(saveCacheTimer);saveCacheTimer=setTimeout(()=>boardCache.saveView(view),150);}
}

function renderWorkspace(){
  const workspace=view.workspace,bar=$('#work-context');bar.hidden=!workspace;
  $('#data-title').textContent=workspace ? 'Таблица · '+workspace.title : 'Единая таблица';
  if(!workspace){$('#message-input').placeholder=view.dataset ? 'Сообщение…' : 'Какой дашборд построим?';$('#message-input').removeAttribute('aria-label');return;}
  $('#work-show').textContent=workspace.title;$('#work-show').title='Показать дашборд «'+workspace.title+'»';
  $('#work-context').setAttribute('aria-label','Выбран дашборд: '+workspace.title);
  $('#message-input').placeholder='Что изменить?';
  $('#message-input').setAttribute('aria-label','Сообщение о дашборде «'+workspace.title+'»');
}
function showWorkingCard(){
  selectedPanel=view?.workspace?.message_id || selectedPanel;renderPanel();panelView('dashboard');
}
async function fitWorkspace(){
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  if(independentCards&&selectedPanel){await refreshCard(selectedPanel);return;}
  const revision=view.revision,next=await api('/api/state');
  if(next.revision===revision){view=next;render();}
}
async function continueCard(messageId,mode=null){
  if(mode)workspaceContent(mode);
  if(independentCards){
    panelSelectionEpoch++;selectedPanel=messageId;renderMessages();renderPanel();panelView('dashboard');
    if(view.messages.find(m=>m.id===messageId)?.dashboard)refreshCard(messageId).catch(e=>error(e.message));return;
  }
  if(outbox.length||working){toast('Дождитесь завершения отправленных сообщений.');return;}
  const previous=selectedPanel;selectedPanel=messageId;
  const ok=await mutate('/api/workspace/continue',{},null,messageId);
  if(!ok){selectedPanel=previous;renderPanel();}
  if(ok){tab('chat');await fitWorkspace();showWorkingCard();$('#message-input').focus({preventScroll:true});}
}
$('#work-show').onclick=()=>{tab('chat');showWorkingCard();};
$('#work-table').onclick=()=>{tab('chat');workspaceContent('table');panelView('dashboard');};
$('#work-copy').onclick=()=>{openSave();$('#save-copy').checked=true;$('#save-title').value=(view.workspace.title+' — копия').slice(0,160);};
$('#work-new').onclick=async()=>{
  if(outbox.length||working){toast('Дождитесь завершения отправленных сообщений.');return;}
  if(await mutate('/api/workspace/new',{},'Начат новый анализ')){selectedPanel=null;tab('chat');panelView('chat');await fitWorkspace();$('#message-input').focus({preventScroll:true});}
};
function renderData() {
  const panel=$('#data-panel'), field=document.activeElement?.closest('.data-value-field, .data-setting-field');
  const top=field?.getBoundingClientRect().top;
  renderTable();renderDashboard();renderDebug();
  if(field?.isConnected)panel.scrollTop+=field.getBoundingClientRect().top-top;
}

function shortCount(n) {
  return n >= 1000
    ? (n / 1000).toLocaleString("ru-RU", { maximumFractionDigits: 1 }) + "k"
    : String(n);
}
function number(n, digits = 2) {
  return n === null || n === undefined
    ? "—"
    : Number(n).toLocaleString("ru-RU", { maximumFractionDigits: digits });
}
function multi(key, label) {
  return `<div class="data-value-field" data-value-key="${key}" data-value-label="${label}"></div>`;
}
function segmented(key, options) {
  return `<div class="segmented" aria-label="${key === "dimension" ? "Разрез" : "Период"}">${options.map(([v, label]) => `<button type="button" data-control="${key}" data-value="${v}" aria-pressed="${view.filters[key] === v}">${label}</button>`).join("")}</div>`;
}
function renderDashboard() {
  const root = $("#dashboard-root");
  for(const mount of root.querySelectorAll('.chart-mount'))mount._plotlyDispose?.();
  if (!view.dataset || !view.charts.length) {
    root.replaceChildren();
    return;
  }
  const oldOpen = [...root.querySelectorAll('.filter-dropdown[open]')].map(x=>x.dataset.filter);
  const pickers = new Map([...root.querySelectorAll('.data-value-field, .data-setting-field')].map(node=>[node.dataset.valueKey,node]));
  const oldKpis=root.querySelector('.kpi-cards');
  const active = document.activeElement;
  const panel = $('#data-panel'), scrollTop = panel.scrollTop;
  const f = view.filters,
    k = view.kpis,
    d = view.dataset;
  root.innerHTML = `<section class="dashboard" aria-label="Интерактивный дашборд"><div class="dashboard-head"><div><h2>${esc(view.dashboard.title)}</h2><div class="dashboard-description"><span class="live-dot"></span>${esc(d.source)}<span>·</span>${esc(f.start || d.query_start)} — ${esc(f.end || d.query_end)}<span>·</span>версия ${d.version}</div></div><div class="button-group"><button id="show-data" class="quiet-button">▤ Таблица</button><button id="export-html" class="quiet-button" aria-label="Скачать дашборд в HTML">↓ HTML</button><button id="save-dashboard" class="quiet-button">Название и копия</button></div></div><div id="data-kpis"></div><div class="filters">${multi("regions", "Регион")}${multi("outlets", "Торговая точка")}${multi("products", "Продукт")}${view.dataset.grain.includes("discount") ? multi("clients", "Клиент") : ""}<div class="date-controls"><label class="sr-only" for="date-start">Начальная дата</label><input type="date" id="date-start" value="${esc(f.start || "")}" min="${d.query_start}" max="${d.query_end}"><span>—</span><label class="sr-only" for="date-end">Конечная дата</label><input type="date" id="date-end" value="${esc(f.end || "")}" min="${d.query_start}" max="${d.query_end}"></div><details class="filter-dropdown filter-more" data-filter="more"><summary>Настройки</summary><div class="filter-popup"><label for="top-n">Рейтинг: ТОП-<output id="top-value">${f.top_n}</output></label><div class="range-control"><input type="range" id="top-n" min="1" max="50" value="${f.top_n}"></div><label for="min-amount">Факт в строке таблицы, ₽: от / до</label><div class="numeric-pair"><input id="min-amount" type="number" placeholder="От" value="${f.min_amount ?? ""}" aria-label="Минимальная сумма"><input id="max-amount" type="number" placeholder="До" value="${f.max_amount ?? ""}" aria-label="Максимальная сумма"></div><button class="text-button close-filter" type="button">Закрыть настройки</button></div></details><button id="reset-filters" class="filter-reset">Сбросить</button></div><div class="data-switchers"><div class="data-setting-field" data-value-key="metric"></div><div class="data-setting-field" data-value-key="value_mode"></div></div><p class="plot-notice data-mode-notice" ${view.view_notice ? "" : "hidden"}>${esc(view.view_notice || "")}</p><div class="dashboard-body"><div class="view-controls"><div class="button-group">${segmented(
    "dimension",
    [
      ["region", "Регионы"],
      ["outlet", "Точки"],
      ["product", "Продукты"],
    ],
  )}${segmented("granularity", [
    ["month", "Месяц"],
    ["quarter", "Квартал"],
    ["year", "Год"],
  ])}</div><span class="chart-hint">⌖ Выделите область для зума · нажмите на серию</span></div><div class="chart-grid ${view.dashboard.layout === "one-column" ? "single-column" : ""}">${view.charts.map((c, i) => `<article class="chart-card"><div class="chart-heading"><h3>${esc(c.spec.title)}</h3><span class="chart-index">${i + 1} / ${view.charts.length}</span></div><div id="chart-${i}" class="chart-mount"></div></article>`).join("")}</div><div class="dashboard-bottom"><span>${number(d.rows, 0)} строк · ${number(k.products, 0)} продуктов · единая таблица v${d.version}</span><button id="add-column" class="text-button">＋ Добавить вычисление</button></div>${view.debug.warnings.length ? `<div class="warning-note">${esc(view.debug.warnings[0])}${view.debug.warnings.length > 1 ? " Остальные ограничения — в панели «Как получен ответ»." : ""}</div>` : ""}</div></section>`;
  $('#data-kpis').replaceWith(kpiCards(view.kpis,f.metric,oldKpis));
  view.charts.forEach((chart, i) =>
    chartWidget($("#chart-" + i), chart, chart.spec, (changes) =>
      mutate("/api/chart", { index: i + 1, changes }),
    ),
  );
  $("#show-data").onclick = () => tab("data");
  $("#export-html").onclick = () => download("/api/export/html");
  $("#save-dashboard").onclick = openSave;
  $("#add-column").onclick = () => {calculationMessageId=null;$("#calculation-dialog").showModal();};
  const allLabels={regions:'Все регионы',outlets:'Все точки',products:'Все продукты',clients:'Все клиенты'};
  for(const placeholder of root.querySelectorAll('.data-value-field')){
    const {valueKey:key,valueLabel:label}=placeholder.dataset;
    if(pickers.has(key))placeholder.replaceWith(pickers.get(key));
    filterPicker(root.querySelector('.filters'),{
      key,label,values:view.options[key] || [],current:pendingFor(null)[key] || f[key],all:allLabels[key],
      pending:key==='outlets' && Object.hasOwn(pendingFor(null),'regions'),
      placeholder:key==='outlets' ? 'Название, город или адрес' : 'Найти значение…',
      onChange:values=>queueFilters({[key]:values,...(key==='regions' ? {outlets:[]} : {})}),
    });
  }
  for(const [key,label,values] of [['metric','Метрика',[...METRICS,...(f.metric==='quantity' ? [['quantity','Количество товара']] : [])]],['value_mode','Вид',VIEW_MODES]]){
    const placeholder=root.querySelector('.data-setting-field[data-value-key="'+key+'"]');
    if(pickers.has(key))placeholder.replaceWith(pickers.get(key));
    filterPicker(root.querySelector('.data-switchers'),{key,label,values,current:[f[key]],multiple:false,all:'Выберите значение',onChange:selected=>queueFilters({[key]:selected[0]})});
  }
  for (const detail of $$(".filter-dropdown")) {
    detail.open = oldOpen.includes(detail.dataset.filter);
    detail.addEventListener("toggle", () => {
      if (detail.open) {
        for (const other of $$(".filter-dropdown[open]"))
          if (other !== detail) other.open = false;
        detail.querySelector("input[type=search]")?.focus();
      }
    });
  }
  $("#date-start").onchange = (e) =>
    queueFilters({ start: e.target.value || null });
  $("#date-end").onchange = (e) =>
    queueFilters({ end: e.target.value || null });
  $("#top-n").oninput = (e) => {
    $("#top-value").textContent = e.target.value;
    queueFilters({ top_n: Number(e.target.value) });
  };
  for (const [id, key] of [
    ["min-amount", "min_amount"],
    ["max-amount", "max_amount"],
  ])
    $("#" + id).onchange = (e) =>
      queueFilters({
        [key]: e.target.value === "" ? null : Number(e.target.value),
      });
  for (const b of $$("[data-control]"))
    b.onclick = () => queueFilters({ [b.dataset.control]: b.dataset.value });
  $("#reset-filters").onclick = () =>
    queueFilters({
      regions: [],
      outlets: [],
      products: [],
      clients: [],
      start: d.query_start,
      end: d.query_end,
      min_amount: null,
      max_amount: null,
      top_n: 10,
      dimension: "region",
      metric: "amount",
      granularity: "month",
      value_mode: "absolute",
    });
  $(".close-filter").onclick = () => ($(".filter-more").open = false);
  if(active?.isConnected)active.focus({preventScroll:true});
  panel.scrollTop=scrollTop;
}
function queueFilters(patch,messageId=null) {
  if(independentCards&&messageId&&working)panelSelectionEpoch++;
  cardPreviews.delete(messageId);
  const key=queueKey(messageId), entry=filterQueues.get(key) || {id:messageId,pending:{},desired:{}};
  entry.pending={...entry.pending,...patch};entry.desired={...entry.desired,...patch};
  filterQueues.set(key,entry);dashboardBusy(messageId,true);
  if(independentCards&&messageId&&boardCache){
    const data=view.messages.find(m=>m.id===messageId)?.dashboard,sequence=entry.sequence=(entry.sequence||0)+1;
    if(data)boardCache.find(messageId,data,{...data.filters,...entry.desired}).then(cached=>{if(cached&&filterQueues.get(key)===entry&&entry.sequence===sequence){cardPreviews.set(messageId,cached);renderPanel();dashboardBusy(messageId,true);}});
  }
  clearTimeout(filterTimer);filterTimer=setTimeout(flushFilters,200);
}
async function waitForCardEdits(messageId){
  const deadline=Date.now()+30000;
  while(true){
    const zooming=[...(panelCache.get(messageId)?.querySelectorAll('.chart-mount')||[])].some(n=>n._zoomSession?.pending);
    if(!filterQueues.has(queueKey(messageId))&&!cardFlights.has(messageId)&&!zooming)return;
    if(Date.now()>deadline)throw Error('Не удалось дождаться сохранения настроек. Повторите скачивание.');
    if(filterQueues.has(queueKey(messageId))&&(!busy||independentCards)){clearTimeout(filterTimer);await flushFilters();}
    else if(cardFlights.has(messageId))await cardFlights.get(messageId);
    else await new Promise(resolve=>setTimeout(resolve,40));
  }
}
function dashboardBusy(messageId,value) {
  for(const root of $$('.message-plots'))if(root.dataset.messageId===messageId){
    root.setAttribute('aria-busy',String(value));
    root.querySelector('.plot-status').textContent=value ? 'Сохраняю…' : root.dataset.statusText;
    setFilterPending(root.querySelector('[data-value-key="outlets"]'),value && Object.hasOwn(pendingFor(messageId),'regions') && !cardPreviews.has(messageId));
  }
  if(!messageId){
    $('#dashboard-root').setAttribute('aria-busy',String(value));
    setFilterPending($('#dashboard-root [data-value-key="outlets"]'),value && Object.hasOwn(pendingFor(null),'regions'));
  }
}
async function flushFilters() {
  if(filterFlight)return filterFlight;
  if(busy&&!independentCards){filterTimer=setTimeout(flushFilters,200);return;}
  filterFlight=(async()=>{
    while(filterQueues.size && (!busy||independentCards)){
      const [key,entry]=filterQueues.entries().next().value;
      const changes=entry.pending;entry.pending={};error('');
      try{
        if(independentCards&&entry.id){await editIndependentCard(entry.id,'filters',changes);}
        else {
        for(let attempt=0;attempt<3;attempt++){
          try{
            view=await api('/api/filters',{revision:view.revision,arguments:changes,message_id:entry.id});break;
          }catch(e){if(e.status!==409 || attempt===2)throw e;view=await api('/api/state');}
        }
        }
        entry.desired={...entry.pending};
        if(!Object.keys(entry.pending).length)filterQueues.delete(key);
        if(!filterQueues.has(key))cardPreviews.delete(entry.id);
        render();
      }catch(e){filterQueues.delete(key);cardPreviews.delete(entry.id);error(e.message);render();}
      finally{
        dashboardBusy(entry.id,filterQueues.has(key));
        for(const other of filterQueues.values())dashboardBusy(other.id,true);
      }
    }
  })().finally(()=>{filterFlight=null;});
  return filterFlight;
}
function renderTable() {
  if (!view?.dataset) return;
  const d = view.dataset;
  const isNumeric = (t) => t.includes("float") || t.includes("int");
  $("#table-root").innerHTML =
    `<div class="table-meta"><div><strong>${esc(d.source)}</strong><small>${esc(d.grain)} · ${esc(d.query_start)} — ${esc(d.query_end)} · таблица v${d.version}</small></div><span class="badge">${number(d.rows, 0)} строк в фильтре / ${number(d.loaded_rows, 0)} загружено</span></div><div class="table-scroll"><table><thead><tr><th>№</th>${d.columns.map((c) => `<th class="${isNumeric(c.type) ? "numeric" : ""}">${esc(c.label)}<small>${esc(c.name)} · ${esc(c.type)}</small></th>`).join("")}</tr></thead><tbody>${d.preview.map((row, i) => `<tr><td>${tableOffset + i + 1}</td>${d.columns.map((c) => `<td class="${isNumeric(c.type) ? "numeric" : ""}" title="${esc(row[c.name] ?? "Нет данных")}">${row[c.name] === null ? "—" : isNumeric(c.type) ? esc(number(row[c.name])) : esc(row[c.name])}</td>`).join("")}</tr>`).join("") || `<tr><td colspan="${d.columns.length + 1}">По выбранным фильтрам данных нет.</td></tr>`}</tbody></table></div><div class="table-pagination"><span>${d.rows ? tableOffset + 1 : 0}–${Math.min(d.rows, tableOffset + d.preview.length)} из ${number(d.rows, 0)}</span><div class="pagination-buttons"><button id="prev-page" ${tableOffset === 0 ? "disabled" : ""} aria-label="Предыдущая страница">←</button><button id="next-page" ${tableOffset + d.preview.length >= d.rows ? "disabled" : ""} aria-label="Следующая страница">→</button></div></div><div class="formulas"><div class="formulas-head"><h3>Вычисляемые колонки <span class="count-pill">${view.calculations.length}</span></h3><div class="button-group"><button id="undo-calculation" class="text-button" ${view.calculations.length ? "" : "disabled"}>↶ Отменить последнее</button><button id="new-calculation" class="text-button">＋ Добавить</button></div></div>${view.calculations.map((c) => `<div class="formula-row"><strong>${esc(c.label)}</strong><code>${esc(c.kind === "formula" ? c.expression : c.kind + "(" + c.column + ")")}</code><span class="muted">${esc(c.name)} · ${esc(c.dependencies.join(", "))}</span><button data-delete-calc="${esc(c.name)}" class="text-button" aria-label="Удалить вычисление ${esc(c.label)}">Удалить</button></div>`).join("") || '<p class="muted">Попросите ассистента добавить отклонение, долю или процент выполнения.</p>'}</div><details class="schema-details"><summary>Типы колонок и качество данных</summary><pre>${esc(JSON.stringify({ columns: d.columns, quality: d.quality }, null, 2))}</pre></details>`;
  $("#prev-page").onclick = () => pageTable(-60);
  $("#next-page").onclick = () => pageTable(60);
  $("#new-calculation").onclick = () => {calculationMessageId=null;$("#calculation-dialog").showModal();};
  $("#undo-calculation").onclick = () =>
    mutate("/api/calculation", { action: "undo" }, "Вычисление отменено");
  for (const b of $$("[data-delete-calc]"))
    b.onclick = () =>
      mutate(
        "/api/calculation",
        { action: "delete", name: b.dataset.deleteCalc },
        "Колонка удалена",
      );
}
async function pageTable(delta) {
  const offset = Math.max(0, tableOffset + delta);
  try {
    const result = await api("/api/table?offset=" + offset + "&limit=60");
    if (result.revision !== view.revision) {
      view = await api("/api/state");
      tableOffset = 0;
      render();
      loadSavedCount();
      return;
    }
    view.dataset = result.dataset;
    tableOffset = offset;
    renderTable();
  } catch (e) {
    error(e.message);
  }
}
function renderDebug() {
  if (!view?.dataset) return;
  const d = view.debug;
  $("#debug-content").innerHTML =
    (d.dashboard_brief ? `<div class="debug-block"><h3>Спецификация этого чата</h3><pre>${esc(JSON.stringify(d.dashboard_brief, null, 2))}</pre></div>` : "") +
    (d.preflight ? `<div class="debug-block"><h3>Preflight · разбор запроса</h3><pre>${esc(JSON.stringify(d.preflight, null, 2))}</pre></div>` : "") +
    `<div class="debug-block"><h3>Шаги ReAct</h3><pre>${esc(JSON.stringify(d.agent_steps || [], null, 2))}</pre></div>` +
    `<div class="debug-block"><h3>Подключённые скиллы</h3><div class="skill-tags">${d.skills.map((s) => `<span>${esc(s)}</span>`).join("")}</div></div><div class="debug-block"><h3>Наблюдаемые действия</h3>${d.tools.map((t) => `<div class="trace-row"><span>✓</span><code>${esc(t.tool)}</code><small>${number(t.ms, 1)} мс</small></div>`).join("")}</div><div class="debug-block"><h3>Модель и время</h3><pre>${esc(JSON.stringify({ ...d.model, fetch_ms: d.fetch_ms, calculation_ms: d.calc_ms, render_ms: view.charts.reduce((sum, c) => sum + c.diagnostics.render_ms, 0), table_id: d.table_id, table_version: d.table_version, dashboard_version: view.dashboard.version }, null, 2))}</pre></div><div class="debug-block"><h3>Таблица: строки, колонки и типы</h3><pre>${esc(JSON.stringify({ rows: view.dataset.rows, loaded_rows: view.dataset.loaded_rows, columns: view.dataset.columns }, null, 2))}</pre></div><div class="debug-block"><h3>Запросы к БД: только исходные поля</h3><pre>${esc(JSON.stringify(d.queries, null, 2))}</pre></div><div class="debug-block"><h3>Формулы и спецификации графиков</h3><pre>${esc(JSON.stringify({ calculations: view.calculations, charts: d.chart_specs }, null, 2))}</pre></div><div class="debug-block"><h3>Предупреждения и ограничения</h3><pre>${esc(d.warnings.join("\n"))}</pre></div>${lastError ? `<div class="debug-block"><h3>Последняя ошибка</h3><pre>${esc(lastError)}</pre></div>` : ""}`;
}
function openSave(messageId = null) {
  saveMessageId=typeof messageId==='string' ? messageId : null;
  const card=view.messages.find(m=>m.id===saveMessageId)?.dashboard;
  $("#save-title").value = card?.saved_title || card?.title || view.dashboard.title;
  $("#save-copy").checked = false;
  $("#save-dialog").showModal();
  $("#save-title").focus();
  $("#save-title").select();
}
$("#save-form").onsubmit = async (e) => {
  e.preventDefault();
  const ok = await mutate(
    "/api/dashboards/save",
    { title: $("#save-title").value.trim(), as_new: $("#save-copy").checked },
    "Дашборд сохранён",
    saveMessageId,
  );
  if (ok) {
    $("#save-dialog").close();
    await loadSavedCount();
  }
};
$("#calculation-form").onsubmit = async (e) => {
  e.preventDefault();
  const name = $("#calc-name").value.trim();
  const exists = calculationMessageId&&independentCards ? (view.messages.find(m=>m.id===calculationMessageId)?.dashboard?.calculation_names||[]).includes(name) : view.calculations.some((x) => x.name === name);
  const ok = await mutate(
    "/api/calculation",
    {
      action: exists ? "change" : "add",
      calculation: {
        name,
        label: $("#calc-label").value.trim(),
        expression: $("#calc-expression").value.trim(),
        kind: "formula",
      },
    },
    "Таблица пересчитана",
    calculationMessageId,
  );
  if (ok) {
    $("#calculation-dialog").close();
    selectedPanel=calculationMessageId||view.workspace?.message_id||view.messages.filter(m=>m.dashboard?.table_id===view.dataset?.id).at(-1)?.id||selectedPanel;
    workspaceContent('table');panelView('dashboard');renderPanel();
  }
};
for (const b of $$("[data-close]"))
  b.onclick = () => $("#" + b.dataset.close).close();
for (const d of $$("dialog"))
  d.addEventListener("click", (e) => {
    if (e.target === d) {
      const r = d.getBoundingClientRect();
      if (
        e.clientX < r.x ||
        e.clientX > r.right ||
        e.clientY < r.y ||
        e.clientY > r.bottom
      )
        d.close();
    }
  });
function showSaved() {
  $("#saved-dialog").showModal();
  loadSaved();
  $("#saved-search").focus();
}
$("#saved-button").onclick = showSaved;
$("#chat-saved-button").onclick = showSaved;
let savedSearchTimer;
$("#saved-search").oninput = () => {
  clearTimeout(savedSearchTimer);
  savedSearchTimer = setTimeout(loadSaved, 200);
};
$("#saved-sort").onchange = loadSaved;
async function loadSavedCount() {
  try {
    const result = await api("/api/dashboards");
    $("#saved-count").textContent = result.items.length;
    $("#chat-saved-count").textContent = result.items.length;
    $("#chat-saved-button").setAttribute('aria-label',`Мои дашборды, сохранено ${result.items.length}`);
  } catch {
    /* Main request handles authentication. */
  }
}
let savedSequence = 0;
async function openSavedDashboard(ident,asNew=false) {
  if(busy){toast('Дождитесь завершения текущего анализа.');return false;}
  if(outbox.length||working){toast('Дождитесь завершения отправленных сообщений.');return false;}
  const selectionEpoch=panelSelectionEpoch;
  const ok=await mutate('/api/dashboards/'+ident+'/open',{as_new:asNew},asNew ? 'Копия открыта в этом чате' : 'Дашборд открыт в этом чате');
  if(ok){
    if($('#saved-dialog').open)$('#saved-dialog').close();
    tab('chat');await fitWorkspace();
    // Keep a card the user selected while the saved dashboard was opening.
    if(panelSelectionEpoch===selectionEpoch){showWorkingCard();$('#message-input').focus({preventScroll:true});}
  }
  return ok;
}
async function loadSaved() {
  const seq = ++savedSequence;
  $('#saved-list').setAttribute('aria-busy','true');
  try {
    const result = await api(
      "/api/dashboards?search=" +
        encodeURIComponent($("#saved-search").value) +
        "&sort=" +
        $("#saved-sort").value,
    );
    if (seq !== savedSequence) return;
    savedItems = result.items;
    $("#saved-list").innerHTML =
      savedItems
        .map(
          (item) =>
            `<article class="saved-item${view.workspace?.saved_id===item.id ? ' is-selected' : ''}"><h3>${esc(item.title)}${view.workspace?.saved_id===item.id ? '<span class="saved-active">Сейчас в работе</span>' : ''}</h3><p>${esc(item.query.slice(0, 180))}</p><small>Создан ${new Date(item.created).toLocaleString("ru-RU")} · изменён ${new Date(item.updated).toLocaleString("ru-RU")}</small><div class="button-group"><button class="primary-button" data-open="${esc(item.id)}">Открыть в этом чате</button><button class="text-button" data-copy="${esc(item.id)}">Создать копию</button><button class="text-button" data-rename="${esc(item.id)}">Переименовать</button><button class="text-button" data-delete="${esc(item.id)}">Удалить</button></div><form class="saved-rename" id="rename-${esc(item.id)}" hidden><label class="sr-only" for="rename-input-${esc(item.id)}">Новое название</label><input id="rename-input-${esc(item.id)}" value="${esc(item.title)}" maxlength="160" required><button type="submit">Сохранить</button></form></article>`,
        )
        .join("") ||
      `<div class="empty-state"><h2>${$('#saved-search').value ? 'Ничего не найдено' : 'Пока нет дашбордов'}</h2><p>${$('#saved-search').value ? 'Измените поисковый запрос.' : 'Постройте дашборд в чате — он сохранится автоматически.'}</p></div>`;
    for (const b of $$("[data-open]"))
      {b.disabled=busy;b.onclick=()=>openSavedDashboard(b.dataset.open);}
    for (const b of $$("[data-copy]")){b.disabled=busy;b.onclick=()=>openSavedDashboard(b.dataset.copy,true);}
    for (const b of $$("[data-rename]")) {
      b.disabled=busy;
      b.onclick = () => {
        $("#rename-" + b.dataset.rename).hidden = false;
        $("#rename-input-" + b.dataset.rename).focus();
      };
      $("#rename-" + b.dataset.rename).onsubmit = async (e) => {
        e.preventDefault();
        if(busy)return;
        setBusy(true, 'Сохраняю название…');
        try {
          await api("/api/dashboards/" + b.dataset.rename, undefined, {
            method: "PATCH",
            body: JSON.stringify({
              title: $("#rename-input-" + b.dataset.rename).value,
              as_new: false,
            }),
          });
          view=await api('/api/state');render();
          await loadSaved();
          toast("Название обновлено");
        } catch (e) {
          error(e.message);
        } finally {
          setBusy(false);
        }
      };
    }
    for (const b of $$("[data-delete]"))
      {b.disabled=busy;b.onclick = async () => {
        if(busy)return;
        setBusy(true, 'Удаляю дашборд…');
        try {
          await api("/api/dashboards/" + b.dataset.delete, undefined, {
            method: "DELETE",
          });
          view=await api('/api/state');render();
          await loadSaved();
          await loadSavedCount();
          toast("Дашборд удалён");
        } catch (e) {
          error(e.message);
        } finally {
          setBusy(false);
        }
      };}
  } catch (e) {
    error(e.message);
    if(seq===savedSequence)$('#saved-list').textContent=e.message;
  } finally {
    if(seq===savedSequence)$('#saved-list').setAttribute('aria-busy','false');
  }
}
async function download(path) {
  try {
    const r = await fetch(path, { headers: baseHeaders() });
    if (!r.ok) throw new Error("Выгрузка недоступна");
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = path.includes("csv")
      ? "puzzle-data.csv"
      : path.includes("svg")
        ? "puzzle-chart.svg"
        : "puzzle-dashboard.html";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch (e) {
    error(e.message);
  }
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-export]");
  if (a) {
    e.preventDefault();
    download(a.dataset.export);
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape")
    for (const d of $$(".filter-dropdown[open]")) d.open = false;
});
$("#csv-button").onclick = () => download("/api/export/csv");
$("#refresh-button").onclick = () =>
  mutate(
    "/api/refresh",
    {},
    "Данные загружены заново. Прежний снимок остаётся в сохранённых дашбордах.",
  );
async function boot() {
  try {
    conversationUI=await installConversations({api,error,beforeNavigate:async()=>{
      if(filterFlight || filterQueues.size){
        if(busy){toast('Дождитесь применения выбранных фильтров.');return false;}
        clearTimeout(filterTimer);await flushFilters();
      }
      featureUI?.persistQueue();return true;
    }});
    panelView(initialLayout?.mobileView || 'chat');
    const health = await api("/api/health");
    independentCards=!!health.independent_cards;
    if(independentCards){try{const module=await import('/assets/dashboard-cache.js');boardCache=await module.openDashboardCache(layoutKey());const cached=await boardCache.readView();if(cached){view=cached;render();$('#connection-status').textContent='Восстанавливаю сохранённые дашборды…';}}catch{}}
    $("#source-badge").textContent =
      health.source === "demo"
        ? "Демо · синтетические данные"
        : "PostgreSQL · данные кейса";
    $("#model-badge").textContent =
      health.model_adapter === "demo"
        ? "Демо · без ИИ"
        : health.model + " · medium";
    view = mergeView(await api("/api/state"));
    liveReady=true;
    $("#connection-status").textContent = "Готов к работе";
    render();
    if(view.workspace)await fitWorkspace();
    try{const module=await import('/assets/dashboard-features.js');featureUI=await module.installFeatures({api,headers:baseHeaders,beforeDownload:waitForCardEdits,getView:()=>view,getQueue:()=>({outbox,failed}),setQueue:q=>{outbox=q.outbox;failed=q.failed;},mutate,toast,error,render});render();}catch{ /* The main chat remains usable while optional features are loading. */ }
    if(initialLayout){const savedLayout=initialLayout;requestAnimationFrame(()=>{$('#chat-scroll').scrollTop=savedLayout.chatTop||0;$('#dashboard-scroll').scrollTop=savedLayout.dashboardTop||0;});initialLayout=null;}
    loadSavedCount();
    const active = (await api("/api/chat/jobs/active")).job;
    if (active) {
      working = true; setBusy(true);
      if(!outbox.some(j=>j.id===active.request_id))outbox.unshift({id:active.request_id, kind:"text", text:active.message});renderMessages();
      try {view = await waitJob(active);outbox=outbox.filter(j=>j.id!==active.request_id);render();} catch(e) {outbox=outbox.filter(j=>j.id!==active.request_id);failed.push({id:active.request_id,kind:'text',text:active.message,error:e.message});error(e.message);} finally {working=false;setBusy(false);featureUI?.persistQueue();}
    }
    drain();
  } catch (e) {
    error(e.message);
    $("#connection-status").textContent =
      "Нет соединения. Сообщение можно повторить.";
  }
}
boot();
let resizeTimer, resizeEpoch = 0;
function installPaneResize(){
  const root=$('#chat-panel'),handle=$('#workspace-splitter'),chat=$('#conversation-pane'),desktop=matchMedia('(min-width:1100px)');
  const key='my-pane-width:'+(document.querySelector('meta[name="draft-key"]')?.content||'my');
  let preferred=null,drag=null,fitTimer;
  try{const saved=Number(localStorage.getItem(key));if(Number.isFinite(saved)&&saved>0)preferred=saved;}catch{}
  const bounds=()=>({min:260,max:Math.min(800,Math.max(260,root.clientWidth-480))});
  const apply=()=>{
    if(!desktop.matches||!root.clientWidth)return;
    const {min,max}=bounds(),width=Math.round(Math.max(min,Math.min(max,preferred??Math.max(320,Math.min(400,root.clientWidth*.28)))));
    root.style.setProperty('--conversation-width',width+'px');
    handle.setAttribute('aria-valuemin',min);handle.setAttribute('aria-valuemax',max);handle.setAttribute('aria-valuenow',width);handle.setAttribute('aria-valuetext','Ширина чата '+width+' пикселей');
  };
  const persist=()=>{try{if(preferred===null)localStorage.removeItem(key);else localStorage.setItem(key,String(preferred));}catch{}};
  const settle=saved=>{
    clearTimeout(fitTimer);const last=renderedLastMessage;
    fitTimer=setTimeout(async()=>{
      try{if(view?.dataset&&activeTab==='chat'&&!busy&&desktop.matches)await fitWorkspace();}catch(e){error(e.message);}
      finally{if(!drag){resizing=false;if(saved&&last===renderedLastMessage)restoreChatView($('#messages'),$('#chat-scroll'),saved);rememberChatView();}}
    },150);
  };
  const end=cancel=>{
    if(!drag)return;
    const previous=drag;drag=null;
    if(cancel){preferred=previous.preferred;apply();}else persist();
    root.classList.remove('is-resizing');
    if(handle.hasPointerCapture(previous.id))handle.releasePointerCapture(previous.id);
    restoreChatView($('#messages'),$('#chat-scroll'),previous.chatView);settle(previous.chatView);
  };
  handle.onpointerdown=e=>{
    if(e.button!==0||!desktop.matches)return;
    e.preventDefault();clearTimeout(fitTimer);resizing=true;
    drag={id:e.pointerId,x:e.clientX,width:chat.getBoundingClientRect().width,preferred,chatView:captureChatView($('#messages'),$('#chat-scroll'))};
    handle.focus({preventScroll:true});handle.setPointerCapture(e.pointerId);root.classList.add('is-resizing');
  };
  handle.onpointermove=e=>{
    if(!drag||e.pointerId!==drag.id)return;
    const {min,max}=bounds();preferred=Math.max(min,Math.min(max,drag.width+e.clientX-drag.x));apply();
    restoreChatView($('#messages'),$('#chat-scroll'),drag.chatView);
  };
  handle.onpointerup=()=>end(false);handle.onpointercancel=()=>end(true);handle.onlostpointercapture=()=>end(false);
  handle.ondblclick=()=>{preferred=null;apply();persist();settle(captureChatView($('#messages'),$('#chat-scroll')));};
  handle.onkeydown=e=>{
    if(e.key==='Escape'&&drag){e.preventDefault();end(true);return;}
    if(!['ArrowLeft','ArrowRight','Home','End','Enter'].includes(e.key)||!desktop.matches)return;
    e.preventDefault();const saved=captureChatView($('#messages'),$('#chat-scroll')),{min,max}=bounds();
    preferred=e.key==='Enter'?null:e.key==='Home'?min:e.key==='End'?max:chat.getBoundingClientRect().width+(e.key==='ArrowLeft'?-1:1)*(e.shiftKey?48:16);
    if(preferred!==null)preferred=Math.max(min,Math.min(max,preferred));
    apply();persist();restoreChatView($('#messages'),$('#chat-scroll'),saved);settle(saved);
  };
  window.addEventListener('resize',()=>{if(drag&&!desktop.matches)end(true);apply();});
  new ResizeObserver(apply).observe(root);
  apply();
}
installPaneResize();
function viewportHeight() {
  if (window.visualViewport && innerWidth < 720)
    document.documentElement.style.setProperty(
      "--app-height",
      visualViewport.height + "px",
    );
  else document.documentElement.style.removeProperty("--app-height");
}
viewportHeight();
window.visualViewport?.addEventListener("resize", viewportHeight);
window.addEventListener("resize", () => {
  const epoch = ++resizeEpoch, saved = stableChatView;
  resizing = true;
  viewportHeight();
  if (saved) restoreChatView($('#messages'),$('#chat-scroll'),saved);
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(async () => {
    try {
      if (busy || !view?.dataset) return;
      const next = await api("/api/state");
      if (epoch === resizeEpoch && !busy && next.revision === view.revision) {
        view = next;
        if (activeTab === "data") renderDashboard();
        else {renderMessages();renderPanel();}
      }
    } catch {} finally {
      if (epoch === resizeEpoch) {
        if (saved && activeTab === 'chat') restoreChatView($('#messages'),$('#chat-scroll'),saved);
        resizing=false;rememberChatView();
      }
    }
  }, 350);
});

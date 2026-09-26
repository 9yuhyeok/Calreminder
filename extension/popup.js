const DEFAULT_BASE = 'https://calreminder.onrender.com';
let base = DEFAULT_BASE;
let token = '';
let calendar = null;
const $ = (selector) => document.querySelector(selector);
const show = (selector, visible) => $(selector).classList.toggle('hidden', !visible);
function error(message) {$('#error').textContent = message || ''; show('#error', Boolean(message));}
function cleanBase(value) {
  const url = new URL(value);
  if (!['https:', 'http:'].includes(url.protocol) ||
      (url.protocol === 'http:' && !['localhost','127.0.0.1'].includes(url.hostname)) ||
      url.pathname !== '/' || url.search || url.hash || url.username || url.password) throw new Error('HTTPS 웹 앱 주소를 입력해 주세요.');
  return url.origin;
}
function originPattern(value) {return `${new URL(value).origin}/*`;}
async function api(path, body) {
  const response = await fetch(`${base}/api/extension/${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers:{'Authorization':`Bearer ${token}`,'Content-Type':'application/json'},
    body:body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '요청에 실패했습니다.');
  return data;
}
function eventDate(event) {return event.all_day ? new Date(`${event.start}T00:00:00`) : new Date(event.start);}
function localKey(date) {return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;}
function render() {
  show('#settings', !base);
  show('#signed-out', Boolean(base) && !token);
  show('#calendar', Boolean(base) && Boolean(token));
  if (!calendar || !token) return;
  $('#source').textContent = calendar.source || '캘린더 연결 전';
  $('#today').textContent = new Intl.DateTimeFormat('ko-KR',{month:'long',day:'numeric',weekday:'long'}).format(new Date());
  const events = calendar.events.filter(event => !calendar.completed[event.id] && localKey(eventDate(event)) >= localKey(new Date())).sort((a,b) => eventDate(a)-eventDate(b));
  $('#pending-count').textContent = `남은 일정 ${events.length}개`;
  const items = $('#items'); items.replaceChildren();
  if (!events.length) {const empty=document.createElement('div');empty.className='empty';empty.textContent=calendar.configured?'예정된 일정이 없습니다.':'웹 앱에서 캘린더 링크를 연결해 주세요.';items.append(empty);return;}
  let lastDay='';
  for (const event of events.slice(0,30)) {
    const date = eventDate(event), day=localKey(date);
    if (day!==lastDay) {const h=document.createElement('div');h.className='day';h.textContent=new Intl.DateTimeFormat('ko-KR',{month:'long',day:'numeric',weekday:'long'}).format(date);items.append(h);lastDay=day;}
    const row=document.createElement('div');row.className='item';
    const check=document.createElement('button');check.className='check';check.type='button';check.setAttribute('aria-label',`${event.title} 완료 표시`);
    check.addEventListener('click',async()=>{try{calendar=await api('complete',{id:event.id,done:true});render();error('');}catch(e){error(e.message);}});
    const body=document.createElement('div'), title=document.createElement('strong'), time=document.createElement('small');
    title.textContent=event.title;time.textContent=event.all_day?'하루 종일':new Intl.DateTimeFormat('ko-KR',{hour:'numeric',minute:'2-digit'}).format(date);body.append(title,time);row.append(check,body);items.append(row);
  }
}
async function loadCalendar() {
  if (!token) return;
  try {calendar=await api('state');error('');}
  catch(e) {if (e.message==='로그인이 필요합니다.') {token='';await chrome.storage.local.remove('token');} error(e.message);}
  render();
}
$('#settings-toggle').addEventListener('click',()=>show('#settings',$('#settings').classList.contains('hidden')));
$('#server-form').addEventListener('submit',async event=>{
  event.preventDefault();
  try {
    const value=cleanBase($('#server-url').value.trim());
    const granted=await chrome.permissions.request({origins:[originPattern(value)]});
    if (!granted) throw new Error('서버 접근 권한이 필요합니다.');
    base=value;token='';calendar=null;
    await chrome.storage.local.set({base});await chrome.storage.local.remove('token');
    error('');render();
  } catch(e) {error(e.message);}
});
$('#login').addEventListener('click',async()=>{
  try {
    error('');
    const granted=await chrome.permissions.request({origins:[originPattern(base)]});
    if (!granted) throw new Error('서버 접근 권한이 필요합니다.');
    await chrome.tabs.create({url:chrome.runtime.getURL('login.html')});
  } catch(e) {error(e.message);}
});
$('#refresh').addEventListener('click',async()=>{try{calendar=await api('refresh',{});render();error('');}catch(e){error(e.message);}});
$('#open-web').addEventListener('click',()=>chrome.tabs.create({url:base}));
$('#logout').addEventListener('click',async()=>{try{await api('logout',{});}catch(e){}token='';calendar=null;await chrome.storage.local.remove('token');render();});
(async()=>{const saved=await chrome.storage.local.get(['base','token']);base=saved.base||DEFAULT_BASE;token=saved.token||'';$('#server-url').value=base;render();await loadCalendar();})();

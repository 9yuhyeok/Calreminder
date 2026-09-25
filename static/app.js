const state = {events: [], completed: {}, configured: false, source: '', last_sync: null};
let view = 'upcoming';
let busy = false;
const $ = (selector) => document.querySelector(selector);
const todayKey = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; };
const dateKey = (event) => event.start.slice(0, 10);
const isDone = (event) => Boolean(state.completed[event.id]);
const isUpcoming = (event) => dateKey(event) >= todayKey();
const dateFormatter = new Intl.DateTimeFormat('ko-KR', {month:'long',day:'numeric',weekday:'long'});
const timeFormatter = new Intl.DateTimeFormat('ko-KR', {hour:'numeric',minute:'2-digit'});
function localDate(event) {
  if (event.all_day) return new Date(`${event.start}T00:00:00`);
  return new Date(event.start);
}
function eventDateKey(event) {
  const d = localDate(event);
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}
function dateText(event) { return dateFormatter.format(localDate(event)); }
function timeText(event) {
  if (event.all_day) return '하루 종일';
  const start = timeFormatter.format(localDate(event));
  if (!event.end) return start;
  const end = new Date(event.end);
  return Number.isNaN(end.getTime()) ? start : `${start} – ${timeFormatter.format(end)}`;
}
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function showMessage(message) {
  const box = $('#message'); box.textContent = message || ''; box.classList.toggle('hidden', !message);
}
async function api(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '요청에 실패했습니다.');
  return data;
}
function applyState(data) { Object.assign(state, data); render(); }
function setBusy(value) {
  busy = value;
  $('#refresh-button').disabled = value;
  $('#save-settings').disabled = value;
  $('#refresh-button').innerHTML = value ? '동기화 중…' : '<span aria-hidden="true">↻</span> 동기화';
}
function renderCard(event) {
  const card = el('article', `event-card${isDone(event) ? ' done' : ''}`);
  const check = el('button','check',isDone(event) ? '✓' : '');
  check.type = 'button'; check.setAttribute('aria-label', isDone(event) ? '완료 취소' : '완료 표시');
  check.setAttribute('aria-pressed', String(isDone(event)));
  check.addEventListener('click', async () => {
    if (busy) return;
    try { applyState(await api('/api/complete', {id:event.id, done:!isDone(event)})); showMessage(''); }
    catch (error) { showMessage(error.message); }
  });
  const body = el('div','event-body');
  body.append(el('h3','event-title',event.title));
  const meta = el('div','event-meta');
  meta.append(el('span','',`◷ ${timeText(event)}`));
  if (event.location) meta.append(el('span','',`⌖ ${event.location}`));
  body.append(meta);
  if (event.description) body.append(el('p','event-description',event.description));
  if (event.url) {
    const link = el('a','event-link','일정 열기 ↗');
    link.href = event.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    body.append(link);
  }
  card.append(check, body, el('span','event-badge',isDone(event) ? '완료' : '예정'));
  return card;
}
function render() {
  const now = new Date();
  $('#today-label').textContent = new Intl.DateTimeFormat('ko-KR',{year:'numeric',month:'long',day:'numeric',weekday:'long'}).format(now).toUpperCase();
  $('#source-name').textContent = state.source || '연결 대기 중';
  $('#sync-label').textContent = state.last_sync ? `${new Intl.DateTimeFormat('ko-KR',{month:'numeric',day:'numeric',hour:'numeric',minute:'2-digit'}).format(new Date(state.last_sync))} 동기화` : '';
  const pending = state.events.filter(e => !isDone(e));
  const completed = state.events.filter(isDone);
  const today = state.events.filter(e => eventDateKey(e) === todayKey());
  $('#stat-pending').textContent = pending.length;
  $('#stat-today').textContent = today.length;
  const progress = state.events.length ? Math.round(completed.length / state.events.length * 100) : 0;
  $('#stat-progress').textContent = `${progress}%`;
  $('#progress-fill').style.width = `${progress}%`;
  $('#upcoming-count').textContent = pending.filter(e => eventDateKey(e) >= todayKey()).length;
  $('#completed-count').textContent = completed.length;
  const titles = {upcoming:['예정된 일정','다가오는 일정을 한눈에 확인하세요.','다가오는 일정','완료한 일정은 완료 탭에서 다시 볼 수 있습니다.'],today:['오늘의 일정','오늘 해야 할 일을 확인하세요.','오늘 일정','오늘 날짜의 모든 일정입니다.'],all:['전체 일정','캘린더에서 가져온 모든 일정입니다.','전체 일정','날짜순으로 정리했습니다.'],completed:['완료한 일정','끝낸 일들을 모아봤어요.','완료한 일정','체크 버튼을 다시 누르면 완료가 취소됩니다.']};
  const labels = titles[view];
  $('#page-title').textContent = labels[0]; $('#page-subtitle').textContent = labels[1];
  $('#list-title').textContent = labels[2]; $('#list-caption').textContent = labels[3];
  document.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === view));
  const query = $('#search').value.trim().toLocaleLowerCase();
  let events = state.events.filter(event => {
    if (view === 'upcoming' && (isDone(event) || eventDateKey(event) < todayKey())) return false;
    if (view === 'today' && eventDateKey(event) !== todayKey()) return false;
    if (view === 'completed' && !isDone(event)) return false;
    return !query || `${event.title} ${event.description} ${event.location}`.toLocaleLowerCase().includes(query);
  });
  events.sort((a,b) => localDate(a) - localDate(b));
  const container = $('#events'); container.replaceChildren();
  if (!events.length) {
    const empty = el('div','empty');
    empty.append(el('span','empty-icon',state.configured ? '✦' : '⌁'));
    empty.append(el('strong','',state.configured ? (query ? '검색 결과가 없습니다' : '표시할 일정이 없습니다') : '캘린더를 연결해 주세요'));
    empty.append(el('p','',state.configured ? '다른 보기나 검색어를 확인해 보세요.' : '왼쪽 아래 설정에서 iCalendar 링크를 입력할 수 있습니다.'));
    container.append(empty); return;
  }
  let lastDay = '';
  for (const event of events) {
    const day = eventDateKey(event);
    if (day !== lastDay) {container.append(el('div','day-heading',dateText(event))); lastDay = day;}
    container.append(renderCard(event));
  }
}
document.querySelectorAll('.nav-item').forEach(item => item.addEventListener('click', () => {view = item.dataset.view; render();}));
$('#search').addEventListener('input', render);
$('#settings-button').addEventListener('click', () => $('#settings-dialog').showModal());
$('#close-settings').addEventListener('click', () => $('#settings-dialog').close());
$('#cancel-settings').addEventListener('click', () => $('#settings-dialog').close());
$('#settings-form').addEventListener('submit', async event => {
  event.preventDefault(); if (busy) return;
  const url = $('#feed-url').value.trim(); setBusy(true);
  try { applyState(await api('/api/source',{url})); $('#feed-url').value = ''; $('#settings-dialog').close(); showMessage(''); }
  catch (error) { showMessage(error.message); $('#settings-dialog').close(); }
  finally {setBusy(false);}
});
$('#refresh-button').addEventListener('click', async () => {
  if (busy) return;
  setBusy(true); showMessage('');
  try { applyState(await api('/api/refresh',{})); }
  catch (error) { showMessage(error.message); }
  finally {setBusy(false);}
});
(async () => {
  try {
    applyState(await api('/api/state'));
    if (state.configured) {
      setBusy(true);
      try { applyState(await api('/api/refresh',{})); }
      catch (error) { showMessage(`최근 저장된 일정을 표시합니다. ${error.message}`); }
      finally { setBusy(false); }
    }
  } catch (error) { showMessage(error.message); }
})();

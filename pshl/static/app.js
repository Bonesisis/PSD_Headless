const $ = id => document.getElementById(id);
let token = '', extension = '.json', selected = '', current = null, imageURLs = [], imageRevision = 0;
const labels = {queued:'Wartet auf Vorschau', rendering:'Vorschau läuft', review:'Bitte prüfen', approved:'Freigegeben / wartet auf Termin', publishing:'Ausgabe läuft', finished:'Abgeschlossen', error:'Fehler', rejected:'Gestoppt'};
const headers = () => ({'Content-Type':'application/json', 'X-PSHL-Token':$('key').value || token});
const showError = error => { $('message').textContent = error.message; };
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers:headers()});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  return data;
}
function download(data, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)], {type:'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function refresh() {
  const rows = await api('/api/queue'); $('queue-list').replaceChildren();
  for (const row of rows) {
    const button = document.createElement('button'); button.className = 'secondary queue-item';
    button.textContent = `${row.id.slice(0,8)} · ${labels[row.status] || row.status} · ${row.done}/${row.total}${row.schedule?.at ? ' · ' + new Date(row.schedule.at).toLocaleString() : ''}`;
    button.onclick = () => { selected = row.id; current = null; $('series').checked = false; sessionStorage.setItem('pshlReview', selected); poll().catch(showError); };
    $('queue-list').append(button);
  }
  if (!rows.length) $('queue-list').textContent = 'Keine Aufträge.';
}
async function showImages() {
  const revision = ++imageRevision;
  imageURLs.forEach(url => URL.revokeObjectURL(url)); imageURLs = [];
  $('before').hidden = true; $('after').hidden = true;
  const index = Number($('preview-select').value), item = current?.results[index];
  if (!item) return;
  $('events').textContent = JSON.stringify(item, null, 2);
  if (!item.preview) return;
  for (const [side, id] of [['before','before'], ['preview','after']]) {
    const response = await fetch(`/api/queue/${selected}/images/${index}/${side}`, {headers:headers()});
    if (!response.ok) throw new Error('Vorschaubild konnte nicht geladen werden');
    const blob = await response.blob(); if (revision !== imageRevision) return;
    const url = URL.createObjectURL(blob); imageURLs.push(url); $(id).src = url; $(id).hidden = false;
  }
}
async function poll() {
  if (!selected) return;
  const id = selected, state = await api('/api/queue/' + id); if (id !== selected) return;
  const changed = !current || current.status !== state.status || current.done !== state.done;
  current = state; $('status').textContent = labels[state.status] || state.status;
  $('progress').max = state.total || 1; $('progress').value = state.done; $('count').textContent = `${state.done} / ${state.total}`;
  $('message').textContent = state.error || state.reason || (state.status === 'review' ? 'Bitte alle Ausgabeformate kontrollieren, dann freigeben oder stoppen.' : 'Der Worker arbeitet unabhängig vom geöffneten Browser.');
  $('approve').disabled = state.status !== 'review'; $('reject').disabled = !['review','approved','queued','error'].includes(state.status);
  $('retry').disabled = state.status !== 'error'; $('report').disabled = false;
  $('series').disabled = state.status !== 'review' || !state.schedule?.every_days;
  if (changed) {
    $('preview-select').replaceChildren();
    state.results.forEach((item, index) => {
      const option = document.createElement('option'); option.value = index;
      option.textContent = `${index + 1}: ${item.output || item.error} ${item.size ? '(' + item.size.join(' × ') + ')' : ''}`;
      $('preview-select').append(option);
    });
    $('preview-select').disabled = !state.results.length;
    if (!state.results.length) $('events').textContent = 'Vorschauen werden vorbereitet …';
    await showImages(); await refresh();
  }
}
function requestData() {
  const at = $('at').value, days = Number($('every').value);
  if (days && !at) throw new Error('Wiederholung benötigt einen Starttermin');
  return {content:$('jobs').value, extension, templates:$('templates').value, output:$('output').value, font:$('font').value || null,
    schedule:at ? {at:new Date(at).toISOString(), every_days:days, date_fields:$('date-fields').value.split(',').map(s => s.trim()).filter(Boolean)} : null};
}
$('form').onsubmit = async event => {
  event.preventDefault(); $('start').disabled = true;
  try { const state = await api('/api/queue', {method:'POST', body:JSON.stringify(requestData())}); selected = state.id; current = null; $('series').checked = false; sessionStorage.setItem('pshlReview', selected); await poll(); }
  catch (error) { showError(error); } finally { $('start').disabled = false; }
};
$('dry').onclick = async () => {
  if (!$('form').reportValidity()) return; $('dry').disabled = true;
  try {
    const batch = await api('/api/batches', {method:'POST', body:JSON.stringify({...requestData(), dry_run:true})}); let state;
    do { await new Promise(resolve => setTimeout(resolve, 500)); state = await api('/api/batches/' + batch.id); } while (state.status === 'running');
    if (state.error) throw new Error(state.error);
    const report = await api('/api/batches/' + batch.id + '/report'); $('message').textContent = `${report.checked} geprüft, ${report.errors} Fehler`; download(report, 'check-report.json');
  } catch (error) { showError(error); } finally { $('dry').disabled = false; }
};
for (const action of ['approve','reject','retry']) $(action).onclick = async () => {
  $(action).disabled = true;
  try { await api(`/api/queue/${selected}/${action}`, {method:'POST', body:JSON.stringify({reason:$('reason').value, series:$('series').checked})}); await poll(); }
  catch (error) { showError(error); $(action).disabled = false; }
};
$('report').onclick = () => current && download(current, `review-${selected}.json`);
$('refresh').onclick = () => refresh().catch(showError); $('preview-select').onchange = () => showImages().catch(showError);
$('file').onchange = async () => {
  const file = $('file').files[0]; if (!file) return;
  if (file.size > 5 * 1024 * 1024) return showError(new Error('Maximal 5 MiB; größere Dateien per CLI einreichen.'));
  extension = '.' + file.name.split('.').pop().toLowerCase(); $('jobs').value = await file.text(); $('format').textContent = extension.slice(1).toUpperCase();
};
fetch('/api/config').then(r => r.json()).then(async config => {
  token = config.token || ''; $('key-label').hidden = Boolean(token); $('templates').value = config.templates; $('output').value = config.output;
  $('templates').disabled = config.locked_roots; $('output').disabled = config.locked_roots; selected = sessionStorage.getItem('pshlReview') || '';
  if (token) { await refresh(); await poll(); }
}).catch(showError);
setInterval(() => { if (token || $('key').value) poll().catch(showError); }, 1500);

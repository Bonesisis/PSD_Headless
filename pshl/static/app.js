const $ = id => document.getElementById(id);
let token = '', extension = '.json', selected = '', current = null, imageURLs = [], imageRevision = 0;
let imported = null, modelEnabled = false, serverConfig = null, polling = false;
const labels = {queued:'Wartet auf Vorschau', rendering:'Vorschau läuft', review:'Bitte prüfen', approved:'Freigegeben / wartet auf Termin', publishing:'Ausgabe läuft', finished:'Abgeschlossen', error:'Fehler', rejected:'Gestoppt'};
const headers = () => ({'Content-Type':'application/json', 'X-PSHL-Token':$('key').value || token});
const showError = error => { $('message').textContent = error.message; };
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers:{...headers(), ...options.headers}});
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
  const index = Number($('preview-select').value), item = current?.results.find(r => r.index === index);
  const canReview = item?.status === 'success' && ['rendering','review','error'].includes(current?.status) && !current?.approved_bytes;
  $('item-approve').disabled = !canReview; $('item-reject').disabled = !canReview;
  const decision = current?.reviews?.[index]?.decision;
  $('item-status').textContent = decision === 'approved' ? 'Bild als gut bewertet' : decision === 'rejected' ? 'Bild abgelehnt' : 'Bild noch nicht bewertet';
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
  if (state.repackaged_to) { selected = state.repackaged_to; current = null; sessionStorage.setItem('pshlReview', selected); return poll(); }
  const changed = !current || current.status !== state.status || current.done !== state.done || JSON.stringify(current.reviews) !== JSON.stringify(state.reviews);
  current = state; $('status').textContent = labels[state.status] || state.status;
  $('progress').max = state.total || 1; $('progress').value = state.done; $('count').textContent = `${state.done} / ${state.total}`;
  $('message').textContent = state.error || state.reason || (state.status === 'review' ? 'Die Bilder liegen noch im internen Vorschauordner. Nach deiner Freigabe werden sie in die Ausgabeordner kopiert.' : state.status === 'finished' ? 'Die freigegebenen Bilder sind im Ausgabeordner gespeichert.' : 'Der Worker arbeitet unabhängig vom geöffneten Browser.');
  $('delivery-location').textContent = state.packages?.length ? `Ausgabe: ${state.output} → ${(state.packages.length > 4 ? `${state.packages.length} Wochenordner: ${state.packages[0]} … ${state.packages[state.packages.length - 1]}` : state.packages.join(', '))} · je Paket manifest.json und pruefbericht.json` : `Ausgabe: ${state.output}`;
  $('approve').disabled = state.status !== 'review'; $('reject').disabled = !['review','approved','queued','error'].includes(state.status);
  $('retry').disabled = state.status !== 'error'; $('report').disabled = false;
  $('publish-reviewed').disabled = state.status !== 'review'; $('rerun').disabled = false;
  $('active-render').textContent = state.active ? `${state.active.phase}: ${state.active.output} (${state.active.exports} Exporte)` : '';
  $('series').disabled = state.status !== 'review' || !state.schedule?.every_days;
  if (changed) {
    const previous = $('preview-select').value;
    $('preview-select').replaceChildren();
    $('result-list').replaceChildren();
    state.results.forEach(item => {
      const index = item.index;
      const option = document.createElement('option'); option.value = index;
      const delivery = state.jobs[index]?.delivery;
      option.textContent = `${index + 1}: ${delivery ? delivery.platform + ' / ' + delivery.order_id + ' / ' + (state.jobs[index].artboard_name || '') : (item.output || item.error)} ${item.size ? '(' + item.size.join(' × ') + ')' : ''} ${item.output?.split('.').pop().toUpperCase() || ''}`;
      $('preview-select').append(option);
      const chip = document.createElement('button'); chip.type = 'button'; chip.className = 'secondary';
      const decision = state.reviews?.[index]?.decision;
      chip.textContent = `${index + 1} ${item.status === 'error' ? 'Fehler' : decision === 'approved' ? '✓' : decision === 'rejected' ? '✕' : 'Prüfen'}`;
      chip.title = item.output || item.error;
      chip.onclick = () => { $('preview-select').value = index; showImages().catch(showError); };
      $('result-list').append(chip);
    });
    if (state.results.some(r => String(r.index) === previous)) $('preview-select').value = previous;
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
  try {
    const data = requestData();
    const path = imported ? `/api/controller/${imported.id}/submit` : '/api/queue';
    const payload = imported ? {plan:JSON.parse($('jobs').value), schedule:data.schedule} : data;
    const state = await api(path, {method:'POST', body:JSON.stringify(payload)});
    selected = state.id; current = null; $('series').checked = false; sessionStorage.setItem('pshlReview', selected); await poll();
  }
  catch (error) { showError(error); } finally { $('start').disabled = false; }
};
$('dry').onclick = async () => {
  if (!$('form').reportValidity()) return; $('dry').disabled = true;
  try {
    let data = requestData();
    if (imported) {
      const checked = await api(`/api/controller/${imported.id}/plan`, {method:'POST', body:JSON.stringify({plan:JSON.parse($('jobs').value)})});
      data = {...data, jobs:checked.jobs, templates:serverConfig.templates, output:serverConfig.output};
    }
    const batch = await api('/api/batches', {method:'POST', body:JSON.stringify({...data, dry_run:true})}); let state;
    do { await new Promise(resolve => setTimeout(resolve, 500)); state = await api('/api/batches/' + batch.id); } while (state.status === 'running');
    if (state.error) throw new Error(state.error);
    const report = await api('/api/batches/' + batch.id + '/report'); $('message').textContent = `${report.checked} geprüft, ${report.errors} Fehler`; download(report, 'check-report.json');
  } catch (error) { showError(error); } finally { $('dry').disabled = false; }
};
for (const action of ['approve','publish-reviewed','reject','retry']) $(action).onclick = async () => {
  $(action).disabled = true;
  try { await api(`/api/queue/${selected}/${action}`, {method:'POST', body:JSON.stringify({reason:$('reason').value, series:$('series').checked})}); await poll(); }
  catch (error) { showError(error); $(action).disabled = false; }
};
for (const [id, decision] of [['item-approve','approved'], ['item-reject','rejected']]) $(id).onclick = async () => {
  $(id).disabled = true;
  try {
    await api(`/api/queue/${selected}/items/${$('preview-select').value}/review`, {method:'POST', body:JSON.stringify({decision, reason:$('reason').value})});
    await poll();
  } catch (error) { showError(error); $(id).disabled = false; }
};
function manual() {
  imported = null; extension = '.json'; $('format').textContent = 'JSON';
  $('templates').disabled = serverConfig?.locked_roots; $('output').disabled = serverConfig?.locked_roots;
  $('plan-model').disabled = true; $('plan-export').disabled = true;
  $('font').disabled = false;
}
$('manual').onclick = () => { manual(); $('jobs').value = '{"jobs": []}'; $('import-status').textContent = 'Manueller Auftrag aktiv.'; };
$('rerun').onclick = () => {
  if (!current) return;
  manual(); $('jobs').value = JSON.stringify({jobs:current.original_jobs},null,2);
  $('templates').value = current.templates; $('output').value = current.output;
  $('import-status').textContent = 'Vorbereitet: Datum oder Inhalt ändern. Ein unveränderter Auftrag wird als Duplikat erkannt.';
  $('setup').scrollIntoView({behavior:'smooth'});
};
$('excel').onchange = async () => {
  const file = $('excel').files[0]; if (!file) return;
  if (file.size > 5 * 1024 * 1024) return showError(new Error('Excel darf maximal 5 MiB groß sein.'));
  $('start').disabled = true; $('import-status').textContent = 'Excel wird geprüft …';
  try {
    const data = await api('/api/controller/import', {method:'POST', body:file, headers:{'Content-Type':'application/octet-stream'}});
    imported = data; extension = '.json'; $('jobs').value = JSON.stringify(data.plan,null,2); $('format').textContent = 'EDITORIAL PLAN';
    $('templates').value = serverConfig.templates; $('output').value = serverConfig.output;
    $('templates').disabled = true; $('output').disabled = true;
    $('font').disabled = true;
    $('date-fields').value = 'date'; $('plan-model').disabled = !modelEnabled || Boolean(data.table.errors.length); $('plan-export').disabled = false;
    $('import-status').textContent = `${data.table.rows.length} Zeilen · ${data.export_count} geplante Exportdateien · ${data.errors.length} Fehler. ${data.errors.map(e => 'Zeile ' + e.row + ': ' + e.error).join(' ')} ${data.table.warnings.join(' ')}`;
    const fields = [...new Set(data.jobs.flatMap(job => Object.keys(job.fields)))];
    if (fields.length) $('import-status').textContent += ` Gerenderte Textfelder: ${fields.join(', ')}.`;
    const table = document.createElement('table'), head = document.createElement('tr');
    for (const title of ['Zeile','Plattform','Datum','Anzeigetitel','Auftragsordner']) { const th = document.createElement('th'); th.textContent = title; head.append(th); }
    table.append(head);
    for (const row of data.table.rows) {
      const tr = document.createElement('tr');
      const folder = data.delivery?.find(order => order.row === row.row)?.folder || 'Zuordnung prüfen';
      for (const value of [row.row,row.platform,row.date,row.title || '',folder]) { const td = document.createElement('td'); td.textContent = value; tr.append(td); }
      table.append(tr);
    }
    $('import-table').replaceChildren(table);
  } catch (error) { imported = null; $('import-status').textContent = error.message; showError(error); }
  finally { $('start').disabled = false; }
};
$('plan-export').onclick = () => imported && download(imported.planner_input, 'planner-input.json');
$('plan-model').onclick = async () => {
  if (!imported) return; $('plan-model').disabled = true;
  const id = imported.id; $('import-status').textContent = 'KI erstellt einen Vorschlag. Tabellenwerte und Profilbeschreibungen werden an den konfigurierten Anbieter gesendet.';
  try {
    const data = await api(`/api/controller/${id}/plan`, {method:'POST', body:JSON.stringify({mode:'model'})});
    if (imported?.id !== id) return;
    $('jobs').value = JSON.stringify(data.plan,null,2); $('import-status').textContent = 'KI-Vorschlag geprüft. Bitte Felder kontrollieren und Vorschauen starten.';
  } catch (error) { $('import-status').textContent = error.message; showError(error); }
  finally { $('plan-model').disabled = !modelEnabled || !imported; }
};
$('report').onclick = () => current && download(current, `review-${selected}.json`);
$('refresh').onclick = () => refresh().catch(showError); $('preview-select').onchange = () => showImages().catch(showError);
$('file').onchange = async () => {
  const file = $('file').files[0]; if (!file) return;
  if (file.size > 5 * 1024 * 1024) return showError(new Error('Maximal 5 MiB; größere Dateien per CLI einreichen.'));
  const content = await file.text();
  let isPlan = false;
  try { isPlan = Array.isArray(JSON.parse(content).assignments); } catch { /* raw YAML/CSV supported */ }
  if (isPlan && !imported) return showError(new Error('Zuerst die zugehörige Excel-Datei hochladen.'));
  if (!isPlan) manual();
  extension = '.' + file.name.split('.').pop().toLowerCase(); $('jobs').value = content; $('format').textContent = isPlan ? 'EDITORIAL PLAN' : extension.slice(1).toUpperCase();
};
fetch('/api/config').then(r => r.json()).then(async config => {
  serverConfig = config; modelEnabled = config.planner.enabled;
  $('planner-info').textContent = modelEnabled ? `Optionaler KI-Planer: ${config.planner.protocol} · ${config.planner.model}. Aufruf nur über den KI-Knopf; externe Anbieter können Kosten verursachen.` : 'Kein KI-Anbieter eingerichtet. Excel-Regeln und externe JSON-Pläne funktionieren ohne Modell.';
  token = config.token || ''; $('key-label').hidden = Boolean(token); $('templates').value = config.templates; $('output').value = config.output;
  $('templates').disabled = config.locked_roots; $('output').disabled = config.locked_roots; selected = sessionStorage.getItem('pshlReview') || '';
  if (token) { await refresh(); await poll(); }
}).catch(showError);
setInterval(async () => {
  if (polling || !(token || $('key').value)) return;
  polling = true;
  try { await poll(); if (!selected) await refresh(); } catch (error) { showError(error); }
  finally { polling = false; }
}, 1500);

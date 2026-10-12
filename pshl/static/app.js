const $ = id => document.getElementById(id);
let token = '', extension = '.json', batchId = '', busy = false;
const showError = error => { $('message').textContent = error.message; $('message').className = 'hint error'; };
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'Content-Type':'application/json', 'X-PSHL-Token': $('key').value || token}});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  return data;
}
function setBusy(value) { busy = value; $('start').disabled = value; $('dry').disabled = value; }
fetch('/api/config').then(r => r.json()).then(c => {
  $('templates').value = c.templates; $('output').value = c.output; token = c.token || '';
  $('key-label').hidden = Boolean(c.token);
  $('templates').disabled = c.locked_roots; $('output').disabled = c.locked_roots;
  const saved = sessionStorage.getItem('pshlBatch');
  if (saved && token) { batchId = saved; setBusy(true); poll(); }
}).catch(showError);
$('file').addEventListener('change', async () => {
  const file = $('file').files[0]; if (!file) return;
  if (file.size > 5 * 1024 * 1024) return showError(new Error('Maximal 5 MiB in der Oberfläche. Für größere Dateien die CLI verwenden.'));
  extension = '.' + file.name.split('.').pop().toLowerCase();
  $('jobs').value = await file.text(); $('format').textContent = extension.slice(1).toUpperCase();
});
async function start(dryRun) {
  if (busy || !$('form').reportValidity()) return;
  setBusy(true); $('report').disabled = true; $('message').className = 'hint';
  try {
    const result = await api('/api/batches', {method:'POST', body:JSON.stringify({
      content:$('jobs').value, extension, templates:$('templates').value, output:$('output').value,
      font:$('font').value || null, workers:Number($('workers').value), dry_run:dryRun
    })});
    batchId = result.id; sessionStorage.setItem('pshlBatch', batchId); poll();
  } catch (error) { showError(error); setBusy(false); }
}
async function poll() {
  try {
    const state = await api('/api/batches/' + batchId);
    $('status').textContent = {running:'Läuft', finished:'Abgeschlossen', error:'Fehler'}[state.status];
    $('progress').max = state.total || 1; $('progress').value = state.done;
    $('count').textContent = `${state.done} / ${state.total}`;
    $('events').textContent = state.events.map(e => `#${e.index+1} ${e.status}${e.error ? '\n' + e.error : ''}${e.warnings.length ? '\n' + e.warnings.join('\n') : ''}`).join('\n\n') || 'Vorlagen werden geprüft …';
    if (state.status === 'running') {
      $('message').textContent = 'Batch wird verarbeitet. Du kannst dieses Fenster geöffnet lassen.';
      setTimeout(poll, 750);
    } else {
      setBusy(false); sessionStorage.removeItem('pshlBatch');
      if (state.error) throw new Error(state.error);
      $('message').textContent = `${state.summary.success} gerendert · ${state.summary.checked} geprüft · ${state.summary.errors} Fehler · ${state.summary.warnings} Hinweise`;
      $('report').disabled = false;
    }
  } catch (error) { showError(error); setBusy(false); }
}
$('form').addEventListener('submit', event => { event.preventDefault(); start(false); });
$('dry').addEventListener('click', () => start(true));
$('report').addEventListener('click', async () => {
  try {
    const data = await api('/api/batches/' + batchId + '/report');
    const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
    const link = document.createElement('a'); link.href = url; link.download = `report-${batchId}.json`; link.click(); URL.revokeObjectURL(url);
  } catch (error) { showError(error); }
});

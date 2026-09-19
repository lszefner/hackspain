const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const eur = n => Number(n ?? 0).toLocaleString('es-ES', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' €';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function _api(r) {
  const j = await r.json().catch(() => { throw new Error('respuesta no JSON'); });
  if (!r.ok || j.ok === false) throw new Error(j.message || j.error || ('HTTP ' + r.status));
  return j;
}
const api = {
  get: (p) => fetch(p).then(_api),
  post: (p, b) => fetch(p, {method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(b)}).then(_api),
};

let BRIEF = null;

/* ---------------------------------------------------------------- toast */
let toastTimer;
function toast(msg, undo) {
  const t = $('#toast');
  t.innerHTML = `<span>${esc(msg)}</span>`;
  if (undo) {
    const b = document.createElement('button');
    b.textContent = 'Deshacer';
    b.onclick = () => { undo(); t.classList.remove('show'); };
    t.append(b);
  }
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), undo ? 9000 : 4200);
}

/* ---------------------------------------------------------------- brief */
async function loadBrief() {
  BRIEF = await api.get('/api/brief');
  const s = BRIEF.summary;
  $('#brief').innerHTML =
    `<b>${s.escalated}</b> necesitan tu criterio, agrupadas en <a data-ask="que necesita mi atencion">` +
    `${BRIEF.clusters} patrones</a> · <b>${s.queued}</b> en cola de remesa por <b>${eur(s.queued_total)}</b> · ` +
    `<a data-ask="duplicados">${s.duplicates_blocked} duplicados parados</a> = <b>${eur(s.duplicates_saved)}</b> ` +
    `que no se pagan dos veces · ${s.paid} pagadas` +
    (BRIEF.outbox ? ` · <span class="warn">${BRIEF.outbox} correo(s) retenidos</span>` : '');
  $('#n-inv').textContent = s.total;
  $('#n-pay').textContent = s.paid || '';
  $('#f-cost').textContent = (s.cost_today_usd ?? s.cost_usd).toFixed(4) + ' $';
  $('#f-per').textContent = s.cost_per_invoice.toFixed(5) + ' $';
  $('#f-rs').textContent = s.ruleset_version;
  $('#n-rules').textContent = s.ruleset_version.split('-')[0];
  $('#btn-reseed').hidden = !BRIEF.demo;
  const tags = [];
  if (BRIEF.demo) tags.push(['demo · sin red ni SMTP', '']);
  const ing = BRIEF.ingestion || {};
  if (!BRIEF.demo && ing.state === 'failed')
    tags.push([`ingesta fallida: ${ing.error_code || 'error'}`, 'bad']);
  if (!BRIEF.demo && ing.state === 'running') tags.push(['ingesta en curso…', 'warn']);
  if (!BRIEF.demo && ing.state === 'never_run') tags.push(['sin ingesta todavia', 'warn']);
  if (!BRIEF.demo && ing.state === 'finished' && (ing.failed || 0) > 0)
    tags.push([`${ing.failed} extracciones pendientes de revision; ver Actividad`, 'warn']);
  if (!BRIEF.demo && ing.state === 'failed' && ing.missing?.length)
    tags.push([`falta configuracion: ${ing.missing.join(', ')}`, 'bad']);
  if (BRIEF.mail?.error)
    tags.push([`email: error de configuracion (${BRIEF.mail.error})`, 'bad']);
  if (BRIEF.llm?.degraded)
    tags.push([`LLM ${BRIEF.llm.reason === 'disabled' ? 'desactivado' : 'degradado: ' + (BRIEF.llm.reason || '')} · respuestas deterministas`, 'warn']);
  if (BRIEF.mail?.transport === 'smtp-sandbox')
    tags.push(['email: sandbox · nada sale a internet', 'warn']);
  if (s.cost_unknown) tags.push([`${s.cost_unknown} evento(s) sin coste conocido`, 'warn']);
  $('#statusbar').innerHTML = tags.map(([t, c]) => `<span class="tag ${c}">${esc(t)}</span>`).join('');
}

/* ---------------------------------------------------------------- blocks */
function renderBlock(b) {
  if (b.type === 'clusters') return clustersBlock(b);
  if (b.type === 'invoices') return tableBlock(b);
  if (b.type === 'trace') return traceCard(b);
  if (b.type === 'proposal') return proposalBlock(b);
  if (b.type === 'report') return reportBlock(b);
  if (b.type === 'outbox') return outboxBlock(b);
  if (b.type === 'metrics') return metricsBlock(b.summary);
  if (b.type === 'payments') return `<div class="card"><h4>Remesa</h4><div class="sub">${b.paid} pagadas · ${b.queued} en cola por ${eur(b.queued_total)}</div>
    <div class="row-actions"><a class="btn" href="/api/remesa.xml">Descargar SEPA pain.001</a></div></div>`;
  if (b.type === 'rules') return `<div class="card"><h4>Ruleset vigente</h4><pre class="doc">${esc(JSON.stringify(b.ruleset, null, 2))}</pre></div>`;
  return '';
}

function clustersBlock(b) {
  if (!b.clusters.length) return `<div class="card"><h4>Nada pendiente</h4><div class="sub">No hay escalaciones abiertas.</div></div>`;
  return b.clusters.map(c => {
    const rec = c.recommendation;
    const ids = esc(c.file_ids.join(','));
    const acts = [];
    if (rec === 'PAGAR') acts.push(`<button class="btn primary" data-act="approve" data-ids="${ids}">Pagar ${c.count === 1 ? 'esta' : 'las ' + c.count} · ${eur(c.total)}</button>`);
    if (rec === 'NO_PAGAR') acts.push(`<button class="btn danger" data-act="reject" data-ids="${ids}">Rechazar ${c.count}</button>`);
    if (c.proposed_action?.startsWith('email:'))
      acts.push(`<button class="btn" data-act="email" data-tpl="${esc(c.proposed_action.split(':')[1])}" data-ids="${ids}">Escribir a ${esc(c.vendor_email)}</button>`);
    if (!rec)
      acts.push(`<button class="btn" data-ask="por que ${esc(c.file_ids[0])}">Revisar grupo</button>`,
                `<button class="btn" data-act="approve" data-ids="${ids}">Aprobar grupo</button>`);
    acts.push(`<button class="btn" data-ask="por que ${esc(c.file_ids[0])}">Ver expediente</button>`);
    if (c.rule === 'MISSING')
      acts.push(`<button class="btn" data-ask="${esc(c.vendor.split(' ')[0])} por debajo de 200 EUR sin pedido, paga">Convertirlo en regla</button>`);
    return `<div class="card">
      <h4>${c.count} × ${esc(c.label)} <span class="badge">${esc(c.vendor)}</span></h4>
      <div class="sub">${eur(c.total)} en total · ${c.file_ids.length} factura(s) con el mismo patron</div>
      ${c.finding ? `<div class="body">${esc(c.finding)}</div>` : ''}
      <details style="margin-top:8px"><summary class="sub" style="cursor:pointer">Ver las ${c.count} facturas</summary>
        <div style="margin-top:8px">${c.items.map(i => `<div class="flip"><a data-ask="por que ${esc(i.file_id)}">${esc(i.file_id)}</a><span class="num">${esc(i.number)} · ${esc(i.date)} · ${eur(i.total)}</span></div>`).join('')}</div>
      </details>
      <div class="row-actions">${acts.join('')}</div></div>`;
  }).join('');
}

function tableBlock(b) {
  return `<div class="card" style="padding:0;overflow:hidden">
    <div class="tbl-wrap" style="border:0;box-shadow:none;max-height:420px;overflow:auto">
    <table><thead><tr><th>Fichero</th><th>Proveedor</th><th>Numero</th><th>Fecha</th>
      <th style="text-align:right">Importe</th><th>Veredicto</th><th>Reglas</th></tr></thead>
    <tbody>${b.rows.map(r => `<tr data-file="${esc(r.file_id)}">
      <td class="mono">${esc(r.file_id)}</td><td>${esc(r.vendor)}</td><td class="mono">${esc(r.number)}</td>
      <td class="num">${esc(r.date)}</td><td class="num" style="text-align:right">${eur(r.total)}</td>
      <td class="${r.decision}"><b>${r.decision}</b></td>
      <td class="sub">${r.blocking.join(', ') || '—'}</td></tr>`).join('')}</tbody></table></div>
    ${b.truncated ? `<div class="sub" style="padding:8px 14px">+${b.truncated} mas</div>` : ''}</div>`;
}

function metricsBlock(s) {
  const item = (k, v) => `<div class="flip"><span>${k}</span><b class="num">${v}</b></div>`;
  return `<div class="card"><h4>Estado del sistema</h4><div class="body">
    ${item('Facturas procesadas', s.total)}
    ${item('Pagadas / en cola / escaladas / retenidas', `${s.paid} / ${s.queued} / ${s.escalated} / ${s.blocked}`)}
    ${item('Ahorro por duplicados', eur(s.duplicates_saved))}
    ${item('Coste total / por factura', `${s.cost_usd.toFixed(4)} $ / ${s.cost_per_invoice.toFixed(5)} $`)}
    ${item('Reintentos del ERP absorbidos', s.erp_retries)}
    ${item('Caidas de proveedor cubiertas', s.fallbacks)}
    ${item('Ruleset', s.ruleset_version)}</div></div>`;
}

function proposalBlock(b) {
  const p = b.proposal, bt = b.backtest;
  const flips = bt.flips.slice(0, 12).map(f =>
    `<div class="flip"><a data-ask="por que ${esc(f.file_id)}">${esc(f.file_id)} · ${esc(f.vendor)}</a>
     <span class="num"><span class="${f.from}">${f.from}</span> → <span class="${f.to}">${f.to}</span> · ${eur(f.total)}</span></div>`).join('');
  return `<div class="card">
    <h4>${esc(p.title)} <span class="badge">${p.new_rule ? 'regla nueva' : 'parametro existente'}</span></h4>
    <div class="sub">${esc(p.rationale)} · ${p.from_version} → ${p.to_version}</div>
    ${p.diff ? `<pre class="doc">${esc(p.diff)}</pre>` : `<pre class="doc">${esc(JSON.stringify(p.params, null, 2))}</pre>`}
    <div class="body"><b>Backtest sobre ${bt.evaluated} facturas ya decididas:</b>
      ${bt.flips.length} cambian de veredicto —
      <span class="PAGAR">${bt.loosened} se abren (${eur(bt.loosened_eur)})</span>,
      <span class="ESCALAR">${bt.tightened} se cierran (${eur(bt.tightened_eur)})</span>.
      ${bt.risky.length ? `<div class="sub" style="margin-top:6px">⚠ Vigila: ${bt.risky.map(r => esc(r.file_id)).join(', ')}</div>` : ''}
      <div style="margin-top:8px">${flips || '<span class="sub">Ninguna factura cambia.</span>'}</div></div>
    <div class="row-actions">
      <button class="btn primary" data-act="apply_rule" data-reprocess="1" data-prop='${esc(JSON.stringify(p))}'>Aplicar y reprocesar</button>
      <button class="btn" data-act="apply_rule" data-reprocess="0" data-prop='${esc(JSON.stringify(p))}'>Aplicar solo de hoy en adelante</button>
      <button class="btn" data-dismiss="1">Cancelar</button></div></div>`;
}

function reportBlock(b) {
  return `<div class="card"><h4>${esc(b.subject)}</h4>
    <pre class="doc">${esc(b.body)}</pre>
    <div class="row-actions">
      <button class="btn primary" data-act="send_report" data-to="${esc(b.to || '')}">Retener cierre para ${esc(b.to || 'destinatario sin configurar')}</button>
      <a class="btn" href="/api/remesa.xml">Adjuntar remesa SEPA</a></div></div>`;
}

function outboxBlock(b) {
  if (!b.emails.length) return `<div class="card"><h4>Bandeja vacia</h4><div class="sub">Nada retenido ahora mismo.</div></div>`;
  const label = m => m.delivery === 'failed' ? ` · <span class="NO_PAGAR">fallido: ${esc(m.error)}</span>`
    : m.delivery === 'uncertain' ? ` · <span class="ESCALAR">entrega incierta: comprobar a mano, no se reintenta solo</span>`
    : m.transport === 'smtp-sandbox' ? ' · sandbox (no sale a internet)'
    : '';
  return `<div class="card"><h4>Bandeja de salida · ${b.emails.length}</h4>
    <div class="sub">Se liberan solos al vencer la ventana de ${60}s. Hasta entonces son reversibles. Transporte real: ${esc((b.emails[0] || {}).transport || 'smtp-sandbox')}.</div>
    <div class="body">${b.emails.map(m => `<div class="flip" style="align-items:flex-start">
      <span><b>${esc(m.subject)}</b><br><span class="sub">${esc(m.to || 'sin destinatario')} · ${esc(m.file_id || '')} · sale ${esc((m.release_at || '').slice(11, 19))}${label(m)}</span>
      <details><summary class="sub">Ver texto</summary><pre class="doc">${esc(m.body)}</pre></details></span>
      <button class="btn" data-act="cancel_email" data-seq="${esc(m.seq)}">Cancelar</button></div>`).join('')}</div>
    <div class="row-actions"><button class="btn" data-act="flush_outbox">Liberar los que ya cumplieron la ventana</button></div></div>`;
}

/* ---------------------------------------------------------------- trace */
const HOT = {VERDICT: 'hot', PAYMENT_REGISTERED: 'good', PAYMENT_BLOCKED: 'bad',
             PROVIDER_FAILED: 'bad', INVESTIGATED: 'hot', HUMAN_APPROVED: 'good',
             HUMAN_REJECTED: 'bad', EMAIL_SENT: 'good'};

function timelineHTML(b) {
  return `<ul class="tl">${b.timeline.map(n => `<li class="${HOT[n.kind] || ''}">
    <div class="t">${esc(n.title)}</div>
    <div class="d">${esc(n.detail || '')}</div>
    <div class="m">${esc(n.actor)} · ${esc((n.ts || '').slice(11, 19))}
      · ${Number(n.cost_usd ?? 0).toFixed(5)} $ · ${n.ms ?? 0} ms
      ${n.ruleset_version ? ' · ' + esc(n.ruleset_version) : ''}</div>
    <details><summary>evento ${n.seq}</summary><pre>${esc(JSON.stringify(n.payload, null, 2))}</pre></details>
  </li>`).join('')}</ul>`;
}

function traceCard(b) {
  const r = b.row, i = r.invoice;
  const checks = r.checks.map(c => `<div class="flip"><span><b class="${c.effect === 'PAGAR' ? 'PAGAR' : c.effect === 'NO_PAGAR' ? 'NO_PAGAR' : 'ESCALAR'}">${c.canonical}</b>
    <span class="sub"> ${esc(c.reason)}</span></span><span class="badge">${c.verdict}${c.effect && c.effect !== (c.verdict === 'PASS' ? 'PAGAR' : 'ESCALAR') ? ' → ' + c.effect : ''}</span></div>
    ${c.detail ? `<details><summary class="sub" style="cursor:pointer">valores comparados</summary><pre class="doc">${esc(JSON.stringify(c.detail, null, 2))}</pre></details>` : ''}`).join('');
  return `<div class="card">
    <h4>${esc(b.file_id)} · <span class="${r.decision}">${r.decision}</span></h4>
    <div class="sub">${esc(i.vendor)} · ${esc(i.invoice_number)} · ${eur(i.total)} · pedido ${esc(i.pedido || 'n/d')} · ${r.ruleset_version}</div>
    <div class="body"><b>Por que.</b> ${esc(b.why)}</div>
    <div class="body sub">${esc(b.how)}</div>
    <div class="body"><b>Reglas evaluadas</b>${checks}</div>
    <details open style="margin-top:10px"><summary class="sub" style="cursor:pointer">Traza completa (${b.timeline.length} eventos)</summary>${timelineHTML(b)}</details>
    <div class="row-actions">
      ${r.status === 'escalated' ? `<button class="btn primary" data-act="approve" data-ids="${esc(b.file_id)}">Aprobar y pagar</button>
        <button class="btn danger" data-act="reject" data-ids="${esc(b.file_id)}">Rechazar</button>` : ''}
      ${r.status === 'queued' ? `<button class="btn primary" data-act="approve" data-ids="${esc(b.file_id)}">Aprobar y pagar</button>` : ''}
      ${r.payment ? `<button class="btn" data-act="approve" data-ids="${esc(b.file_id)}">Intentar pagar otra vez (probar idempotencia)</button>` : ''}
    </div></div>`;
}

/* ---------------------------------------------------------------- chat */
function bubble(who, html, cls = '') {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.innerHTML = html;
  $('#thread').append(d);
  $('#thread').scrollTop = 1e9;
  return d;
}

async function ask(text) {
  if (!text.trim()) return;
  $('.hero')?.remove();
  bubble('user', `<span>${esc(text)}</span>`, 'user');
  const pending = bubble('agent', `<div class="who">Agente</div><div class="say sub">Pensando…</div>`);
  try {
    const res = await api.post('/api/chat', {text});
    pending.innerHTML = `<div class="who">Agente</div><div class="say">${esc(res.text)}</div>` +
                        res.blocks.map(renderBlock).join('');
  } catch (err) {
    pending.innerHTML = `<div class="who">Agente</div><div class="say sub">Ha fallado la llamada (${esc(err.message || err)}). El servidor sigue ahi; intentalo otra vez.</div>`;
  }
  $('#thread').scrollTop = 1e9;
  loadBrief();
}

async function doAction(payload, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  let res;
  try {
    res = await api.post('/api/action', payload);
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = 'Reintentar'; }
    toast('Ha fallado la accion: ' + (err.message || err));
    return null;
  }
  toast(res.message || 'Hecho',
        payload.action === 'email' && res.emails?.length
          ? () => res.emails.forEach(m => api.post('/api/action', {action: 'cancel_email', seq: m.seq}).then(loadBrief))
          : null);
  await loadBrief();
  if (payload.action === 'apply_rule') await ask('que necesita mi atencion');
  else if (btn) btn.closest('.card')?.classList.add('done'), btn.replaceWith(Object.assign(document.createElement('span'), {className: 'sub', textContent: '✓ hecho'}));
  refreshView();
  return res;
}

/* ---------------------------------------------------------------- views */
let VIEW = 'agent';
async function refreshView() {
  if (VIEW === 'invoices') return loadInvoices();
  if (VIEW === 'payments') return loadPayments();
  if (VIEW === 'rules') return loadRules();
  if (VIEW === 'activity') return loadActivity();
}

async function loadInvoices() {
  const q = $('#inv-q').value, f = $('#inv-filters').dataset.status || '';
  const d = await api.get(`/api/invoices?q=${encodeURIComponent(q)}&status=${f}`);
  $('#inv-body').innerHTML = tableBlock({rows: d.rows, truncated: 0, total: d.rows.length});
}

async function loadPayments() {
  const d = await api.get('/api/payments');
  const s = d.summary;
  $('#pay-body').innerHTML = `
    <div class="card"><h4>Remesa ${esc(d.remesa_id)}</h4>
      <div class="sub">${d.paid.length} pagadas · ${d.queued.length} en cola por ${eur(s.queued_total)}</div>
      <div class="row-actions">
        <a class="btn primary" href="/api/remesa.xml">Descargar SEPA pain.001</a>
        ${d.queued.length ? `<button class="btn" data-act="approve" data-ids="${esc(d.queued.map(r => r.file_id).join(','))}">Aprobar las ${d.queued.length} en cola</button>` : ''}
        <button class="btn" data-act="notify_blocked">Avisar a los proveedores retenidos</button></div></div>
    ${d.blocked_payments.length ? `<div class="card"><h4>Pagos duplicados bloqueados · ${d.blocked_payments.length}</h4>
      <div class="sub">La clave de idempotencia ya estaba reclamada; el segundo intento no movio dinero.</div>
      <div class="body">${d.blocked_payments.map(p => `<div class="flip"><span class="mono">${esc(p.file_id)}</span><span class="mono sub">${esc(p.idempotency_key)}</span></div>`).join('')}</div></div>` : ''}
    ${outboxBlock({emails: d.outbox})}
    ${(d.sent || []).length ? `<div class="card"><h4>Correos enviados · ${d.sent.length}</h4>
      <div class="sub">El transporte real de cada envio queda registrado: sandbox significa que no salio a internet.</div>
      <div class="body">${d.sent.map(m => `<div class="flip"><span>${esc(m.subject)} <span class="sub">${esc(m.to || '')}</span></span><span class="sub">${esc(m.transport === 'smtp-sandbox' ? 'sandbox · no salio a internet' : m.transport || '')} · ${esc((m.ts || '').slice(11, 19))}</span></div>`).join('')}</div></div>` : ''}
    ${(d.failed || []).length ? `<div class="card"><h4>Correos con problema · ${d.failed.length}</h4>
      <div class="body">${d.failed.map(m => `<div class="flip"><span>${esc(m.subject)} <span class="sub">${esc(m.to || '')}</span></span><span class="sub">${esc(m.reason || '')}</span></div>`).join('')}</div></div>` : ''}
    ${reportBlock(d.report)}
    <div class="card"><h4>Pagadas</h4>${tableBlock({rows: d.paid, truncated: 0})}</div>
    <div class="card"><h4>En cola</h4>${tableBlock({rows: d.queued, truncated: 0})}</div>`;
}

async function loadRules() {
  const d = await api.get('/api/rules');
  $('#rules-body').innerHTML = `
    <div class="card"><h4>Ruleset vigente · ${esc(d.ruleset.version)}</h4>
      <div class="sub">El motor es determinista: estos parametros, y solo estos, deciden.</div>
      <pre class="doc">${esc(JSON.stringify(d.ruleset, null, 2))}</pre></div>
    <div class="card"><h4>Historial de cambios</h4>
      ${d.history.length ? d.history.map(h => `<div class="flip"><span><b>${esc(h.title)}</b>
        <br><span class="sub">«${esc(h.source_text || '')}»</span></span>
        <span class="mono sub">${esc(h.from_version)} → ${esc(h.to_version)}<br>${esc((h.ts || '').slice(0, 19).replace('T', ' '))}</span></div>`).join('')
      : '<div class="sub">Sin cambios todavia. Dile al agente una norma nueva en el chat.</div>'}</div>`;
}

async function loadActivity() {
  const d = await api.get('/api/activity');
  $('#act-body').innerHTML = `<div class="card"><h4>Actividad · ${d.totals.events} eventos</h4>
    <div class="sub">Append-only. Ninguna fila se sobrescribe: el estado actual es el pliegue de esto.</div></div>
    <div class="tbl-wrap" style="margin-top:12px"><table>
    <thead><tr><th>#</th><th>Hora</th><th>Actor</th><th>Evento</th><th>Fichero</th><th style="text-align:right">Coste</th></tr></thead>
    <tbody>${d.events.map(e => `<tr data-file="${esc(e.file_id || '')}"><td class="mono">${esc(e.seq)}</td>
      <td class="mono">${esc((e.ts || '').slice(11, 19))}</td><td class="sub">${esc(e.actor)}</td>
      <td><b>${esc(e.kind)}</b></td><td class="mono sub">${esc(e.file_id || '')}</td>
      <td class="num sub" style="text-align:right">${e.cost_usd ? e.cost_usd.toFixed(5) : ''}</td></tr>`).join('')}</tbody></table></div>`;
}

async function openDrawer(fileId) {
  if (!fileId) return;
  const b = await api.get('/api/trace?file_id=' + encodeURIComponent(fileId));
  if (b.error) return;
  $('#drawer-title').textContent = fileId;
  $('#drawer-body').innerHTML = traceCard(b);
  $('#drawer').classList.add('open');
}

/* ---------------------------------------------------------------- wiring */
document.addEventListener('click', async e => {
  const nav = e.target.closest('.nav-item');
  if (nav) {
    VIEW = nav.dataset.view;
    $$('.nav-item').forEach(n => n.classList.toggle('active', n === nav));
    $$('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + VIEW));
    return refreshView();
  }
  const askEl = e.target.closest('[data-ask]');
  if (askEl) {
    const q = askEl.dataset.ask;
    VIEW = 'agent';
    $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === 'agent'));
    $$('.view').forEach(v => v.classList.toggle('active', v.id === 'view-agent'));
    $('#drawer').classList.remove('open');
    return ask(q);
  }
  const act = e.target.closest('[data-act]');
  if (act) {
    const a = act.dataset.act;
    const payload = {action: a};
    if (act.dataset.ids) payload.file_ids = act.dataset.ids.split(',').filter(Boolean);
    if (act.dataset.tpl) payload.template = act.dataset.tpl;
    if (act.dataset.seq) payload.seq = +act.dataset.seq;
    if (act.dataset.to) payload.to = act.dataset.to;
    if (act.dataset.prop) {
      payload.proposal = JSON.parse(act.dataset.prop);
      payload.reprocess = act.dataset.reprocess === '1';
    }
    return doAction(payload, act);
  }
  if (e.target.closest('[data-dismiss]')) return e.target.closest('.card').remove();
  const tr = e.target.closest('tr[data-file]');
  if (tr && tr.dataset.file) return openDrawer(tr.dataset.file);
  if (e.target.id === 'drawer-close') return $('#drawer').classList.remove('open');
  if (e.target.id === 'btn-reseed') {
    if (confirm('Regenerar el dataset desde cero?')) {
      await api.post('/api/action', {action: 'reseed'});
      location.reload();
    }
  }
});

$('#composer').addEventListener('submit', e => {
  e.preventDefault();
  const v = $('#q').value; $('#q').value = '';
  ask(v);
});
$('#inv-q')?.addEventListener('input', () => { clearTimeout(window._t); window._t = setTimeout(loadInvoices, 180); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#drawer').classList.remove('open'); });

const CHIPS = ['Que necesita mi atencion', 'Que duplicados has parado', 'Cuanto cuesta procesarlo',
               'Ensename el cierre del dia', 'Papeleria Ruzafa por debajo de 200 EUR sin pedido, paga'];
$('#chips').innerHTML = CHIPS.map(c => `<button class="chip" data-ask="${esc(c)}">${esc(c)}</button>`).join('');
$('#inv-filters').innerHTML = ['', 'escalated', 'queued', 'paid', 'blocked']
  .map(s => `<button class="btn" data-f="${s}">${s || 'todas'}</button>`).join('');
$('#inv-filters').addEventListener('click', e => {
  const b = e.target.closest('[data-f]'); if (!b) return;
  $('#inv-filters').dataset.status = b.dataset.f; loadInvoices();
});

bubble('agent', `<div class="hero"><h1>Buenos dias, Alberto</h1>
  <p>Sin facturas procesadas hasta que la ingesta lo confirme; el estado real va aqui abajo.</p></div>`);
loadBrief().then(() => setInterval(loadBrief, 20000));

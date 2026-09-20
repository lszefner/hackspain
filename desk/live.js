/* Live desk: paginated read models, no exported invoice fixtures. */
const INV = {q: '', action: '', lifecycle: 'processed', page: 1};
let invReady = false, sumReady = false, rulesReady = false, invTimer;
let invoiceRequest, dossierRequest, dossierFile = null, dossierFocus;
const actionName = v => ({PAGAR:'Recommend pay',ESCALAR:'Review',NO_PAGAR:'Do not pay'}[v] || 'Not evaluated');
const actionClass = v => ({PAGAR:'PAY',ESCALAR:'ESCALATE',NO_PAGAR:'DONOTPAY'}[v] || 'ESCALATE');
const money = (n, currency = 'EUR') => {
  if (!currency || currency === 'UNKNOWN') return n == null ? 'Currency not recorded' : `${Number(n).toFixed(2)} · currency not recorded`;
  if (n == null) return 'Amount not recorded';
  try { return new Intl.NumberFormat('en-GB',{style:'currency',currency}).format(Number(n)); }
  catch { return `${Number(n).toFixed(2)} ${currency || 'currency not recorded'}`; }
};
const pretty = value => `<pre class="audit-json">${esc(JSON.stringify(value,null,2))}</pre>`;
const dateTime = value => value ? new Date(value).toLocaleString() : 'Time not recorded';
async function liveGet(path, signal) {
  const response = await fetch(path,{cache:'no-store',signal});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}
function failure(target,error) {
  if (error.name === 'AbortError') return;
  target.innerHTML = `<div class="empty-note" role="alert">${esc(error.message)}<br>Check the backend connection and use Refresh to retry.</div>`;
}
function filters(extra = {}) {
  return new URLSearchParams({q:INV.q,action:INV.action,lifecycle:INV.lifecycle, ...extra});
}
const actions = [['','All'],['PAGAR','Recommend pay'],['ESCALAR','Review'],['NO_PAGAR','Do not pay']];
$('#inv-action').innerHTML = actions.map(([value,label]) => `<button data-a="${value}" class="${value ? '' : 'on'}">${label}</button>`).join('');
$('#inv-q').insertAdjacentHTML('afterend', `<label class="live-filter">Stage <select id="inv-stage"><option value="">All recorded</option><option value="processed" selected>Processed</option><option value="extracted">Extracted</option><option value="processing">Processing</option><option value="error">Errors</option></select></label><button class="btn" id="live-refresh">Refresh</button>`);
function figure(kind,label,total,n,currency) {
  return `<span class="fig ${kind}"><span class="lbl2">${label}</span><span class="amt">${currency === 'UNKNOWN' ? '—' : esc(money(total,currency))}</span><span class="n2">${n} invoices</span></span>`;
}
async function loadInvoices() {
  invoiceRequest?.abort(); invoiceRequest = new AbortController();
  $('#inv-body').innerHTML = '<p class="empty-note" role="status">Loading recorded invoices…</p>';
  try {
    const d = await liveGet('/api/lanes?'+filters({page:String(INV.page),limit:'25'}),invoiceRequest.signal);
    $('#inv-count').textContent = `${d.matched} of ${d.grand} recorded invoices`;
    const countKeys = {'PAGAR':'PAY','ESCALAR':'ESCALATE','NO_PAGAR':'DO NOT PAY'};
    $('#inv-action').querySelectorAll('button').forEach(button => {
      const label = actions.find(([key]) => key === button.dataset.a)[1];
      button.textContent = label + (button.dataset.a ? ` · ${d.counts[countKeys[button.dataset.a]] || 0}` : '');
    });
    $('#inv-body').innerHTML = !d.lanes.length ? '<p class="empty-note">No recorded invoices match these filters.</p>' :
      `<div class="lanes">${d.lanes.map(l => `<div class="lane" data-vendor="${esc(l.id)}" data-currency="${esc(l.currency)}"><button class="lane-head" type="button" aria-expanded="false"><span class="cv2"></span><span class="who3"><span class="nm2">${esc(l.name)}</span><span class="sub2">${l.count} · ${esc(l.currency === 'UNKNOWN' ? 'currency not recorded' : l.currency)}${l.missing_amounts ? ` · ${l.missing_amounts} missing amounts` : ''}</span></span><span class="figs">${figure('pay','Recommend pay',l.pay_total,l.pay_n,l.currency)}${figure('rev','Review',l.review_total,l.review_n,l.currency)}${figure('stop','Do not pay',l.nopay_total,l.nopay_n,l.currency)}</span></button><div class="lane-body"><div class="in2"></div></div></div>`).join('')}</div>`;
    $('#inv-body').insertAdjacentHTML('beforeend', `<div class="live-pages"><button class="btn" id="inv-prev" ${INV.page===1?'disabled':''}>Previous</button><span>Supplier page ${INV.page} · ${d.supplier_count} groups</span><button class="btn" id="inv-next" ${INV.page*25>=d.supplier_count?'disabled':''}>Next</button></div>`);
    $('#inv-prev').onclick = () => {INV.page--;loadInvoices();};
    $('#inv-next').onclick = () => {INV.page++;loadInvoices();};
    invReady = true;
  } catch(error) { failure($('#inv-body'),error); }
}
function invoiceRows(rows) {
  return `<div class="itbl"><div class="ihead"><span>Invoice</span><span>Number</span><span>Issued</span><span>Amount</span><span>Recommendation</span><span>Reason / stage</span></div>${rows.map(r => `<button type="button" class="irow" data-file="${esc(r.file_id)}"><span class="f">${esc(r.file_id)}</span><span class="n">${esc(r.number || 'Not recorded')}</span><span class="d">${esc(r.date || '—')}</span><span class="a">${esc(money(r.total,r.currency))}</span><span class="k ${actionClass(r.verdict)}">${actionName(r.verdict)}</span><span class="b">${esc(r.reason || r.lifecycle)}${r.attention_required ? ' · attention required' : ''}</span></button>`).join('')}</div>`;
}
async function loadLane(lane,page=1) {
  const host = lane.querySelector('.in2'); host.innerHTML = '<p role="status" class="empty-note">Loading invoices…</p>';
  try {
    const d = await liveGet('/api/invoices?'+filters({vendor:lane.dataset.vendor,currency:lane.dataset.currency,page:String(page),limit:'25'}));
    if (!lane.isConnected) return;
    host.innerHTML = `<div class="pad">${invoiceRows(d.rows)}<div class="live-pages"><button class="btn" data-page="${page-1}" ${page===1?'disabled':''}>Previous</button><span>Page ${page} · ${d.matched} invoices</span><button class="btn" data-page="${page+1}" ${page*25>=d.matched?'disabled':''}>Next</button></div></div>`;
    host.querySelectorAll('[data-page]').forEach(b => b.onclick = () => loadLane(lane,Number(b.dataset.page)));
  } catch(error) {failure(host,error);}
}
$('#inv-q').addEventListener('input',e => {INV.q=e.target.value;INV.page=1;clearTimeout(invTimer);invTimer=setTimeout(loadInvoices,200);});
$('#inv-stage').onchange = e => {INV.lifecycle=e.target.value;INV.page=1;loadInvoices();};
$('#inv-action').onclick = e => {
  const b=e.target.closest('button');if(!b)return;INV.action=b.dataset.a;INV.page=1;
  $('#inv-action').querySelectorAll('button').forEach(x=>x.classList.toggle('on',x===b));loadInvoices();
};
$('#live-refresh').onclick = () => {sumReady=false;rulesReady=false;loadInvoices();};
$('#view-invoices').onclick = e => {
  const head=e.target.closest('.lane-head');
  if(head){const lane=head.closest('.lane');const open=!lane.classList.contains('open');lane.classList.toggle('open',open);head.setAttribute('aria-expanded',String(open));if(open)loadLane(lane);return;}
  const row=e.target.closest('[data-file]');if(row)openDossier(row.dataset.file);
};
function ruleHTML(rule) {
  return `<details class="live-rule"><summary><strong>${esc(rule.rule_id)}</strong><span class="vd2 ${rule.status==='PASS'?'PASS':'BLOCKS'}">${esc(rule.status)}</span></summary><p>${esc(rule.explanation)}</p><p class="mut">Consequence: ${esc(rule.applied_consequence || 'None recorded')}</p><details><summary>Evidence and reasoning</summary>${pretty({inputs:rule.inputs,evidence_refs:rule.evidence_refs,trace:rule.trace,rule_ref:rule.rule_ref})}</details></details>`;
}
async function openDossier(file) {
  dossierRequest?.abort();dossierRequest=new AbortController();dossierFile=file;dossierFocus=document.activeElement;
  $('#dos-file').textContent=file;$('#dos-sub').textContent='Loading saved result…';$('#dos-side').innerHTML='<p role="status">Loading invoice…</p>';
  $('#dos-frame').src='about:blank';$('#dossier').classList.add('on');$('#dossier').setAttribute('aria-hidden','false');$('#scrim').classList.add('on');$('#dos-x').focus();
  try {
    const d=await liveGet('/api/dossier?file='+encodeURIComponent(file),dossierRequest.signal);
    const r=d.row, checks=d.checks || [], passed=checks.filter(c=>c.status==='PASS'), others=checks.filter(c=>c.status!=='PASS');
    $('#dos-sub').textContent=`${r.vendor} · ${money(r.total,r.currency)} · ${actionName(d.salida.verdict)}`;
    const reasons=checks.filter(c=>!['PASS','NOT_APPLICABLE'].includes(c.status)).map(c=>c.explanation);
    const reason=reasons[0] || ({evaluator_review_disabled:'The evaluator produced this recommendation; contextual review was explicitly disabled.',evaluator_confirmed_by_review:'The stored review did not challenge the evaluator recommendation.',review_unavailable:'Contextual review is not available.',no_evaluation:'No completed evaluation is recorded.'}[d.salida.basis] || 'See the recorded rule results below.');
    $('#dos-side').innerHTML=`<div class="why-box ${actionClass(d.salida.verdict)}"><span class="lbl3">${actionName(d.salida.verdict)} · ${esc(d.salida.basis)}</span><div class="txt3">${esc(reason)}</div>${d.attention_required?'<p>Attention required. Review the limitations below.</p>':''}<p>Recommendation only. Resolution and payment are not recorded.</p></div>
      <div class="dos-acts"><button class="btn ask" id="dos-ask">Ask Albertito about this invoice</button>${d.pdf_available?'<button class="btn" id="load-pdf">Open original PDF</button>':'<span>Original PDF not recorded</span>'}</div>
      <div class="sec"><h5>Lifecycle</h5><ul class="tl">${d.lifecycle.map(s=>`<li><div class="k2">${esc(s.stage)}</div><div class="d2">${esc(s.state || 'not_recorded')}</div><div class="m2">${esc(dateTime(s.at))}</div></li>`).join('')}</ul></div>
      <div class="sec"><h5>Invoice</h5><dl>${Object.entries({Supplier:r.vendor,Number:r.number,Issued:r.date,Amount:money(r.total,r.currency),Stage:r.lifecycle,Review:r.review_status}).map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v ?? 'Not recorded')}</dd></div>`).join('')}</dl></div>
      <div class="sec"><h5>Rule results · ${checks.length} recorded</h5>${others.map(ruleHTML).join('')}${!checks.length?'<p>No rule results recorded.</p>':''}<details><summary>${passed.length} passed checks</summary>${passed.map(ruleHTML).join('')}</details></div>
      <div class="sec"><h5>Contextual review · ${esc(d.review?.status || 'not recorded')}</h5>${(d.review?.findings || []).map(f=>`<p><strong>${esc(f.kind)} · ${esc(f.severity)}</strong></p>${pretty(f)}`).join('')}<details><summary>Review assessments and limitations</summary>${pretty(d.review)}</details></div>
      <div class="sec"><details><summary>Extracted fields and evidence</summary>${pretty(d.invoice)}${pretty(d.evidence)}</details></div>
      <div class="sec"><details><summary>Ruleset and evaluation</summary>${pretty(d.ruleset)}${pretty(d.evaluation)}</details></div>
      <div class="sec"><button class="btn" id="load-trace">Load processing attempts and full audit trail</button><div id="live-trace"></div></div>`;
    const pdf=$('#load-pdf');if(pdf)pdf.onclick=()=>{$('#dos-frame').src='/api/pdf?file='+encodeURIComponent(file)+'#view=FitH';$('#dos-frame').scrollIntoView({block:'nearest'});};
    $('#dos-ask').onclick=()=>{closeDossier();document.querySelector('[data-view="agent"]').click();ask(`Explain the result and outstanding rules for ${file}`);};
    $('#load-trace').onclick=async()=>{
      const target=$('#live-trace');target.textContent='Loading stored audit trail…';
      try{const flow=await liveGet('/api/engine/factura/'+encodeURIComponent(file)+'/flujo',dossierRequest.signal);if(dossierFile===file)target.innerHTML=pretty(flow);}catch(error){failure(target,error);}
    };
  } catch(error){failure($('#dos-side'),error);}
}
function closeDossier(){dossierRequest?.abort();dossierFile=null;$('#dossier').classList.remove('on');$('#dossier').setAttribute('aria-hidden','true');$('#scrim').classList.remove('on');$('#dos-frame').src='about:blank';dossierFocus?.focus();}
$('#dos-x').onclick=closeDossier;$('#scrim').onclick=closeDossier;
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDossier();if(e.key==='Tab' && dossierFile){const nodes=[...$('#dossier').querySelectorAll('button:not([disabled]),summary,a[href],iframe')];const first=nodes[0],last=nodes[nodes.length-1];if(e.shiftKey && document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey && document.activeElement===last){e.preventDefault();first.focus();}}});
async function loadSummary(){
  $('#sum-body').innerHTML='<p role="status">Loading recorded totals…</p>';
  try{const d=await liveGet('/api/summary');$('#sum-body').innerHTML=`<h2>Recorded invoices</h2><p class="lede">${d.total} persisted invoices · ${d.suppliers} suppliers. Amounts are separated by currency; missing amounts are excluded.</p><div class="stats">${d.totals.map(t=>`<div class="stat"><span class="k5">${actionName(t.verdict)} · ${esc(t.currency)}</span><div class="v5">${t.currency === 'UNKNOWN' ? 'Not aggregated' : esc(money(t.total,t.currency))}</div><div class="s5">${t.n} invoices · ${t.missing_amounts} missing amounts</div></div>`).join('')}</div><div class="card2"><h3>Processing stages</h3>${Object.entries(d.lifecycle).map(([k,v])=>`<p>${esc(k)}: ${v}</p>`).join('')}</div><button class="btn" id="refresh-summary">Refresh</button>`;$('#refresh-summary').onclick=loadSummary;sumReady=true;}catch(error){failure($('#sum-body'),error);sumReady=false;}
}
async function loadRules(){
  $('#rules-page').innerHTML='<p role="status">Loading recorded rule results…</p>';
  try{const d=await liveGet('/api/rules');$('#rules-page').innerHTML=`<h2>Rules observed in saved evaluations</h2><p class="lede">${d.checked} evaluated invoices. Counts retain each original status and ruleset identity.</p>${d.rules.map(r=>`<details class="live-rule"><summary><strong>${esc(r.rule_id)}</strong><span>${esc(r.status)} · ${r.n}</span></summary>${pretty(r.ruleset)}</details>`).join('') || '<p>No evaluations recorded yet.</p>'}<button class="btn" id="refresh-rules">Refresh</button>`;$('#refresh-rules').onclick=loadRules;rulesReady=true;}catch(error){failure($('#rules-page'),error);rulesReady=false;}
}

const initialView = new URLSearchParams(location.search);
const requestedView = initialView.get('view');
if (['agent','invoices','summary','rules'].includes(requestedView)) document.querySelector(`[data-view="${requestedView}"]`).click();
if (initialView.get('invoice')) openDossier(initialView.get('invoice'));

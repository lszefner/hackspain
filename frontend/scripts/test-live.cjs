// Contract tests use mocked fetch only: no provider calls or shared database.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const cache = new Map();
function load(relative) {
  const filename = path.resolve(__dirname, '..', relative);
  if (cache.has(filename)) return cache.get(filename).exports;
  const mod = new Module(filename, module);
  mod.filename = filename;
  mod.paths = Module._nodeModulePaths(path.dirname(filename));
  cache.set(filename, mod);
  const originalRequire = mod.require.bind(mod);
  mod.require = (specifier) => {
    if (specifier.startsWith('@/')) return load('src/' + specifier.slice(2) + '.ts');
    if (specifier.startsWith('.')) return load(path.relative(path.resolve(__dirname, '..'), path.resolve(path.dirname(filename), specifier + '.ts')));
    return originalRequire(specifier);
  };
  mod._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, filename);
  return mod.exports;
}
(async () => {
  const tools = load('src/lib/desk/agent-tools.ts');
  let requests=[];
  global.fetch=async (url) => { requests.push(String(url)); return Response.json({rows:[],matched:0}); };
  await tools.runInvoiceTool('search_invoices',{q:'supplier & invoice',page:2});
  const url = new URL(requests[0]);
  assert.equal(url.searchParams.get('q'),'supplier & invoice');
  assert.equal(url.searchParams.get('limit'),'25');
  assert.equal(url.searchParams.get('page'),'2');
  await assert.rejects(tools.runInvoiceTool('get_invoice',{file_id:'../private'}));
  await assert.rejects(tools.runInvoiceTool('pay_invoice',{}));
  assert.equal(requests.length,1);
  assert.equal(JSON.parse(tools.boundedEvidence('x'.repeat(40_000))).incomplete,true);
  const backend=load('src/lib/desk/backend.ts');
  global.fetch=async()=>Response.json({error:'invoice_not_recorded'},{status:404});
  assert.equal((await backend.proxyDesk('invoice')).status,404);
  global.fetch=async()=>{throw new Error('offline');};
  const offline=await backend.proxyDesk('suppliers');
  assert.equal(offline.status,503);
  assert.match((await offline.json()).error,/unavailable/);

  const chat=load('src/app/api/chat/route.ts');
  process.env.HELMCODE_API_KEY='synthetic-test-key';
  let providerCalls=0;
  global.fetch=async(url,options)=>{
    if(String(url).includes('/api/ui/'))return Response.json({total:2,totals:[{currency:'EUR',total:30,n:2,verdict:'ESCALAR'}]});
    providerCalls++;
    if(providerCalls===1)return Response.json({choices:[{message:{role:'assistant',content:null,tool_calls:[{id:'call1',type:'function',function:{name:'invoice_totals',arguments:'{}'}}]}}]});
    const body=JSON.parse(options.body);
    const evidence=body.messages.find(m=>m.role==='tool');
    assert.equal(JSON.parse(evidence.content).total,2);
    return Response.json({choices:[{message:{content:'Two recorded invoices need review.'}}]});
  };
  const response=await chat.POST(new Request('http://local/api/chat',{method:'POST',body:JSON.stringify({text:'How many invoices?'})}));
  assert.match(await response.text(),/Two recorded invoices/);
  assert.equal(providerCalls,2);
  console.log('Passed: bounded read tools, unknown tool and traversal rejection, error propagation, evidence limits, mocked model-tool loop.');
})().catch(error=>{console.error(error);process.exitCode=1;});

// Testes do recetor sem dependencias nem conta na Cloudflare:  node teste.mjs   (Node 20 ou mais recente)
import assert from 'node:assert/strict';
import { Buffer } from 'node:buffer';
import { createHmac } from 'node:crypto';
import worker, { verificar, fontes } from './worker.js';

class KV {
  constructor() { this.m = new Map(); }
  async get(k) { return this.m.has(k) ? this.m.get(k) : null; }
  async put(k, v) { this.m.set(k, v); }
  async delete(k) { this.m.delete(k); }
  async list({ prefix, limit }) {
    const keys = [...this.m.keys()].filter((k) => k.startsWith(prefix)).sort();
    return { keys: keys.slice(0, limit).map((name) => ({ name })), list_complete: keys.length <= limit };
  }
}

const SEG_A = 'whsec_' + Buffer.from('segredo-de-teste-loja-a-123456').toString('base64');   // forma Standard Webhooks
const SEG_B = 'segredo_cru_da_fonte_b_sem_base64!';                                        // forma "tal e qual"
const TOKEN = 'x'.repeat(48);
const env = () => ({ FILA: new KV(), FONTES: 'loja-a, fonteb', SEGREDO_LOJA_A: SEG_A, SEGREDO_FONTEB: SEG_B, TOKEN_FILA: TOKEN });

function assinar(corpo, seg, { wid = 'msg_teste1', ts = Math.floor(Date.now() / 1000), base64 = false } = {}) {
  const chave = base64 ? Buffer.from(seg.split('_').slice(1).join('_'), 'base64') : Buffer.from(seg);
  const sig = createHmac('sha256', chave).update(`${wid}.${ts}.${corpo}`).digest('base64');
  return { 'webhook-id': wid, 'webhook-timestamp': String(ts), 'webhook-signature': 'v1,' + sig, 'content-type': 'application/json' };
}
const pedir = (e, caminho, init = {}) => worker.fetch(new Request('https://recetor.exemplo.workers.dev' + caminho, init), e);
const auth = (t = TOKEN) => ({ authorization: 'Bearer ' + t });

let n = 0;
const t = async (nome, f) => { await f(); n++; console.log('ok -', nome); };

const corpo = JSON.stringify({ id: 'msg_teste1', type: 'payment.succeeded', data: { id: 'pay_xxxxxxx', total: 1 } });

await t('saude', async () => {
  const r = await pedir(env(), '/saude');
  assert.equal(r.status, 200); assert.equal(await r.text(), 'ok');
});

await t('fontes: lista limpa a partir de FONTES', async () => {
  assert.deepEqual(fontes({ FONTES: ' loja-a, fonteb ,MAL/,' }), ['loja-a', 'fonteb']);
  assert.deepEqual(fontes({}), []);
});

await t('assinatura boa (segredo whsec_ em base64) -> 200 e fica na fila', async () => {
  const e = env();
  const r = await pedir(e, '/w/loja-a', { method: 'POST', body: corpo, headers: assinar(corpo, SEG_A, { base64: true }) });
  assert.equal(r.status, 200);
  const chaves = [...e.FILA.m.keys()].filter((k) => k.startsWith('ev:'));
  assert.equal(chaves.length, 1);
  assert.equal(e.FILA.m.get('marca'), chaves[0]);
  const reg = JSON.parse(e.FILA.m.get(chaves[0]));
  assert.equal(reg.fonte, 'loja-a');
  assert.equal(Buffer.from(reg.corpo_b64, 'base64').toString(), corpo);   // bytes exatos para voltar a verificar em casa
});

await t('assinatura boa (segredo tal e qual) -> 200', async () => {
  const r = await pedir(env(), '/w/fonteb', { method: 'POST', body: corpo, headers: assinar(corpo, SEG_B) });
  assert.equal(r.status, 200);
});

await t('assinatura de outra fonte -> 401 e nada guardado', async () => {
  const e = env();
  const r = await pedir(e, '/w/loja-a', { method: 'POST', body: corpo, headers: assinar(corpo, SEG_B) });
  assert.equal(r.status, 401); assert.equal(e.FILA.m.size, 0);
});

await t('assinatura falsa / corpo mudado / sem cabecalhos -> 401', async () => {
  const e = env();
  const h = assinar(corpo, SEG_B);
  let r = await pedir(e, '/w/fonteb', { method: 'POST', body: corpo + ' ', headers: h });
  assert.equal(r.status, 401);
  r = await pedir(e, '/w/fonteb', { method: 'POST', body: corpo, headers: { ...h, 'webhook-signature': 'v1,AAAA' } });
  assert.equal(r.status, 401);
  r = await pedir(e, '/w/fonteb', { method: 'POST', body: corpo, headers: { 'content-type': 'application/json' } });
  assert.equal(r.status, 401);
  assert.equal(e.FILA.m.size, 0);
});

await t('mais de 5 min (antigo ou no futuro) -> 401', async () => {
  const e = env();
  for (const ts of [Math.floor(Date.now() / 1000) - 301, Math.floor(Date.now() / 1000) + 400]) {
    const r = await pedir(e, '/w/fonteb', { method: 'POST', body: corpo, headers: assinar(corpo, SEG_B, { ts }) });
    assert.equal(r.status, 401);
  }
  const ts = Math.floor(Date.now() / 1000) - 299;
  assert.equal(await verificar(new TextEncoder().encode(corpo), 'msg_teste1', String(ts),
    assinar(corpo, SEG_B, { ts })['webhook-signature'], SEG_B), '');
});

await t('fonte desconhecida -> 404; GET no webhook -> 405; corpo grande -> 413', async () => {
  const e = env();
  assert.equal((await pedir(e, '/w/outra', { method: 'POST', body: corpo })).status, 404);
  assert.equal((await pedir(e, '/w/loja-a')).status, 405);
  const grande = 'a'.repeat(300 * 1024);
  assert.equal((await pedir(e, '/w/loja-a', { method: 'POST', body: grande, headers: assinar(grande, SEG_A) })).status, 413);
});

await t('fonte na lista mas sem segredo -> 401', async () => {
  const e = { ...env(), FONTES: 'loja-a,semsegredo' };
  const r = await pedir(e, '/w/semsegredo', { method: 'POST', body: corpo, headers: assinar(corpo, SEG_B) });
  assert.equal(r.status, 401);
});

await t('fila sem token / token errado / token fraco -> 401', async () => {
  const e = env();
  assert.equal((await pedir(e, '/fila')).status, 401);
  assert.equal((await pedir(e, '/fila', { headers: auth('y'.repeat(48)) })).status, 401);
  assert.equal((await pedir(e, '/fila/confirmar', { method: 'POST', body: '{"chaves":[]}', headers: auth('z') })).status, 401);
  const e2 = { ...env(), TOKEN_FILA: 'curto' };   // token fraco = fila fechada
  assert.equal((await pedir(e2, '/fila', { headers: auth('curto') })).status, 401);
});

await t('tirar da fila, marca sem novidades, confirmar apaga', async () => {
  const e = env();
  for (const [i, fonte, seg] of [[1, 'loja-a', SEG_A], [2, 'fonteb', SEG_B]]) {
    const c = corpo.replace('msg_teste1', 'msg_t' + i);
    const r = await pedir(e, '/w/' + fonte, { method: 'POST', body: c, headers: assinar(c, seg, { wid: 'msg_t' + i }) });
    assert.equal(r.status, 200);
    await new Promise((ok) => setTimeout(ok, 3));
  }
  let r = await (await pedir(e, '/fila?depois=', { headers: auth() })).json();
  assert.equal(r.eventos.length, 2);
  assert.deepEqual(r.eventos.map((x) => x.id_webhook), ['msg_t1', 'msg_t2']);   // por ordem de chegada
  assert.equal(r.marca, r.eventos[1].chave);
  const s = await (await pedir(e, '/fila?depois=' + encodeURIComponent(r.marca), { headers: auth() })).json();
  assert.equal(s.sem_novidades, true); assert.equal(s.eventos.length, 0);
  const tudo = await (await pedir(e, '/fila?tudo=1&depois=' + encodeURIComponent(r.marca), { headers: auth() })).json();
  assert.equal(tudo.eventos.length, 2);
  const c = await pedir(e, '/fila/confirmar', { method: 'POST', body: JSON.stringify({ chaves: [r.eventos[0].chave] }), headers: auth() });
  assert.equal((await c.json()).apagadas, 1);
  r = await (await pedir(e, '/fila', { headers: auth() })).json();
  assert.deepEqual(r.eventos.map((x) => x.id_webhook), ['msg_t2']);
  assert.equal((await pedir(e, '/fila/confirmar', { method: 'POST', body: '{"chaves":["marca"]}', headers: auth() })).status, 400);
  assert.equal((await pedir(e, '/fila/confirmar', { method: 'POST', body: 'nao json', headers: auth() })).status, 400);
});

await t('caminho desconhecido -> 404', async () => {
  assert.equal((await pedir(env(), '/')).status, 404);
});

console.log(`\n${n} testes OK`);

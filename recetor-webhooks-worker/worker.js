// recetor-webhooks - Cloudflare Worker que recebe webhooks assinados e os guarda numa fila (KV) para um servidor
// em casa os ir buscar. Nao precisa de portas abertas em casa nem de expor o teu n8n.
// Formato de assinatura: "Standard Webhooks" (cabecalhos webhook-id, webhook-timestamp, webhook-signature "v1,<base64>",
// HMAC-SHA256 de "<id>.<timestamp>.<corpo>"). A Whop, o Svix, o Resend, o Clerk e outros usam este formato.
//
//   POST /w/<fonte>          fonte = uma das de FONTES (ex.: whop). Verifica a assinatura com SEGREDO_<FONTE>,
//                            recusa com mais de 5 min (401), guarda o corpo + cabecalhos e responde 200.
//   GET  /fila?depois=<marca>  (Bearer TOKEN_FILA) eventos por confirmar, por ordem de chegada (max. 50).
//                            Se <marca> = ultima chegada, responde logo vazio (poupa as "list" do KV).
//   POST /fila/confirmar     (Bearer TOKEN_FILA) {"chaves": ["ev:..."]} -> apaga-as.
//   GET  /saude              200 "ok".
//
// Variaveis (wrangler.toml): FONTES = "whop,outra".  Segredos (wrangler secret put, nunca no codigo):
// SEGREDO_<FONTE> (o segredo de cada webhook) e TOKEN_FILA (32+ caracteres).
// Ligacoes: FILA (KV, obrigatoria), LIMITE (rate limit por IP, opcional).

const TOLERANCIA = 300;              // s (o padrao pede 5 min)
const MAX_CORPO = 256 * 1024;        // webhooks normais tem poucos KB
const TTL = 7 * 24 * 3600;           // 7 dias na fila, no maximo
const LOTE = 50;
const enc = new TextEncoder();

const json = (o, status = 200) => new Response(JSON.stringify(o), {
  status, headers: { 'content-type': 'application/json', 'cache-control': 'no-store', 'x-robots-tag': 'noindex' } });
const texto = (t, status = 200) => new Response(t, {
  status, headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store', 'x-robots-tag': 'noindex' } });

function b64(bytes) {
  let s = '';
  const u = new Uint8Array(bytes);
  for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000));
  return btoa(s);
}
function deB64(t) {
  const s = atob(t);
  const u = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i);
  return u;
}

// fontes configuradas: "whop, outra" -> ['whop', 'outra'] (so a-z, 0-9 e -)
export function fontes(env) {
  return String(env.FONTES || '').split(',').map((f) => f.trim().toLowerCase()).filter((f) => /^[a-z0-9-]{1,30}$/.test(f));
}
const segredoDe = (env, fonte) => env['SEGREDO_' + fonte.toUpperCase().replace(/-/g, '_')];

// comparacao a tempo constante (sobre os resumos SHA-256, para o comprimento nao vazar)
async function iguais(a, b) {
  const [x, y] = await Promise.all([a, b].map((v) => crypto.subtle.digest('SHA-256', typeof v === 'string' ? enc.encode(v) : v)));
  const u = new Uint8Array(x), w = new Uint8Array(y);
  let d = 0;
  for (let i = 0; i < u.length; i++) d |= u[i] ^ w[i];
  return d === 0;
}

// O padrao Standard Webhooks usa o segredo "whsec_<base64>" descodificado; alguns fornecedores (ex.: Whop) dizem para
// usar o segredo tal e qual. Aceitam-se as duas formas.
function chavesHmac(segredo) {
  const out = [enc.encode(segredo)];
  const resto = segredo.includes('_') ? segredo.slice(segredo.indexOf('_') + 1) : segredo;
  try {
    if (/^[A-Za-z0-9+/]+=*$/.test(resto)) out.push(deB64(resto + '='.repeat((4 - (resto.length % 4)) % 4)));
  } catch { /* nao e base64 */ }
  return out;
}

export async function verificar(corpo, wid, wts, wsig, segredo, agora = Date.now() / 1000) {
  if (!(wid && wts && wsig && segredo)) return 'faltam cabecalhos';
  if (!/^\d{1,12}$/.test(wts)) return 'timestamp invalido';
  if (Math.abs(agora - Number(wts)) > TOLERANCIA) return 'timestamp fora dos 5 minutos';
  const assinado = new Uint8Array([...enc.encode(`${wid}.${wts}.`), ...new Uint8Array(corpo)]);
  const recebidas = wsig.split(' ').filter((p) => p.startsWith('v1,')).map((p) => p.slice(3)).slice(0, 5);
  if (!recebidas.length) return 'sem assinatura v1';
  for (const k of chavesHmac(segredo)) {
    const chave = await crypto.subtle.importKey('raw', k, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    const esperado = b64(await crypto.subtle.sign('HMAC', chave, assinado));
    for (const r of recebidas) if (await iguais(esperado, r)) return '';
  }
  return 'assinatura invalida';
}

async function autorizado(pedido, env) {
  const h = pedido.headers.get('authorization') || '';
  if (!env.TOKEN_FILA || env.TOKEN_FILA.length < 32 || !h.startsWith('Bearer ')) return false;
  return iguais(h.slice(7), env.TOKEN_FILA);
}

async function limitar(pedido, env, chave) {
  if (!env.LIMITE) return false;
  try {
    const { success } = await env.LIMITE.limit({ key: chave + ':' + (pedido.headers.get('cf-connecting-ip') || '?') });
    return !success;
  } catch { return false; }
}

async function receber(pedido, env, fonte) {
  if (await limitar(pedido, env, 'w')) return json({ ok: false, erro: 'devagar' }, 429);
  const tam = Number(pedido.headers.get('content-length') || 0);
  if (tam > MAX_CORPO) return json({ ok: false, erro: 'grande' }, 413);
  const corpo = await pedido.arrayBuffer();
  if (corpo.byteLength > MAX_CORPO) return json({ ok: false, erro: 'grande' }, 413);
  if (!corpo.byteLength) return json({ ok: false, erro: 'vazio' }, 400);
  const h = pedido.headers;
  const wid = h.get('webhook-id') || '', wts = h.get('webhook-timestamp') || '', wsig = h.get('webhook-signature') || '';
  if (wid.length > 200 || wsig.length > 1000) return json({ ok: false }, 401);
  const motivo = await verificar(corpo, wid, wts, wsig, segredoDe(env, fonte));
  if (motivo) return json({ ok: false }, 401);   // o motivo fica so do nosso lado
  const agora = Date.now();
  const chave = `ev:${String(agora).padStart(14, '0')}:${fonte}:${wid.replace(/[^A-Za-z0-9_.-]/g, '').slice(0, 80)}`;
  const reg = { fonte, id_webhook: wid, hora_webhook: wts, assinatura_webhook: wsig, recebido: agora, corpo_b64: b64(corpo) };
  await env.FILA.put(chave, JSON.stringify(reg), { expirationTtl: TTL });
  await env.FILA.put('marca', chave, { expirationTtl: TTL });
  return json({ ok: true });
}

async function lerFila(pedido, env, url) {
  if (await limitar(pedido, env, 'fila')) return json({ erro: 'devagar' }, 429);
  if (!(await autorizado(pedido, env))) return json({ erro: 'proibido' }, 401);
  const marca = (await env.FILA.get('marca', { cacheTtl: 30 })) || '';
  const depois = url.searchParams.get('depois');
  if (depois !== null && depois === marca && url.searchParams.get('tudo') !== '1') {
    return json({ eventos: [], marca, sem_novidades: true });
  }
  const lista = await env.FILA.list({ prefix: 'ev:', limit: LOTE });
  const eventos = [];
  for (const k of lista.keys) {
    const v = await env.FILA.get(k.name);
    if (!v) continue;   // apagado entretanto
    try { eventos.push({ chave: k.name, ...JSON.parse(v) }); } catch { /* ignora */ }
  }
  return json({ eventos, marca, mais: !lista.list_complete });
}

async function confirmar(pedido, env) {
  if (await limitar(pedido, env, 'fila')) return json({ erro: 'devagar' }, 429);
  if (!(await autorizado(pedido, env))) return json({ erro: 'proibido' }, 401);
  let chaves;
  try {
    chaves = (await pedido.json()).chaves;
  } catch { return json({ erro: 'pedido' }, 400); }
  if (!Array.isArray(chaves) || chaves.length > 100 || !chaves.every((c) => typeof c === 'string' && /^ev:[\w:.-]{1,200}$/.test(c))) {
    return json({ erro: 'pedido' }, 400);
  }
  await Promise.all(chaves.map((c) => env.FILA.delete(c)));
  return json({ apagadas: chaves.length });
}

export default {
  async fetch(pedido, env) {
    const url = new URL(pedido.url);
    const p = url.pathname.replace(/\/+$/, '') || '/';
    const m = p.match(/^\/w\/([a-z0-9-]+)$/);
    try {
      if (m && fontes(env).includes(m[1])) {
        return pedido.method === 'POST' ? await receber(pedido, env, m[1]) : json({ erro: 'metodo' }, 405);
      }
      if (p === '/saude') return texto('ok');
      if (p === '/fila' && pedido.method === 'GET') return await lerFila(pedido, env, url);
      if (p === '/fila/confirmar' && pedido.method === 'POST') return await confirmar(pedido, env);
      return texto('nao encontrado', 404);
    } catch (e) {
      console.log('erro', String((e && e.message) || e).slice(0, 200));
      return json({ ok: false, erro: 'interno' }, 500);   // o fornecedor volta a tentar
    }
  },
};

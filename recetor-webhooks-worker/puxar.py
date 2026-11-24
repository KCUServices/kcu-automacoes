"""recetor-webhooks - lado de casa: vai buscar ao Worker os webhooks em fila, VOLTA A VERIFICAR a assinatura aqui
(com os segredos do teu ficheiro de ambiente), entrega-os ao destino (ex.: um webhook do n8n na rede de casa) e so
depois confirma ao Worker (que os apaga). Se o destino falhar, o evento fica no Worker e tenta-se outra vez.

So biblioteca padrao do Python. Corre de 30 em 30 s pelo puxar.timer (ou por cron).

  puxar.py          uma volta
  puxar.py ver      mostra a fila do Worker sem a tirar (fonte, tipo, id, idade; sem o conteudo)
  puxar.py estado   estado local

Variaveis (ficheiro de ambiente, ver .env.exemplo):
  RECETOR_URL            https://recetor.<a-tua-conta>.workers.dev
  RECETOR_TOKEN          o mesmo TOKEN_FILA do Worker
  SEGREDO_<FONTE>        o mesmo segredo de cada webhook (ex.: SEGREDO_WHOP)
  DESTINO_<FONTE>        para onde entregar (ex.: http://10.0.0.20:5678/webhook/webhooks-whop); DESTINO = por omissao
  REASSINAR_S            eventos mais velhos do que isto sao reassinados com o mesmo id (omissao 240 s), porque o destino
                         provavelmente recusa assinaturas com mais de 5 min
  AVISO_URL / AVISO_TOKEN / AVISO_CANAL   opcional: POST /aviso do Discord aprovacoes depois de 6 falhas seguidas
  PUXAR_ESTADO           ficheiro de estado (omissao /var/lib/recetor-webhooks/estado.json)
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ESTADO = os.environ.get('PUXAR_ESTADO', '/var/lib/recetor-webhooks/estado.json')
TOLERANCIA = 300
VARRER = 600        # de 10 em 10 min le a fila toda mesmo sem marca nova (o KV e "eventualmente consistente")
AVISO_FALHAS = 6


def log(msg):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), msg, flush=True)


def carregar():
    try:
        with open(ESTADO, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def guardar(e):
    os.makedirs(os.path.dirname(ESTADO) or '.', exist_ok=True)
    tmp = ESTADO + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(e, f)
    os.replace(tmp, ESTADO)


def var_fonte(prefixo, fonte):
    return os.environ.get(prefixo + '_' + fonte.upper().replace('-', '_'), '')


# ---------------------------------------------------------------- assinatura (Standard Webhooks)

def chaves_hmac(segredo):
    """O padrao usa "whsec_<base64>" descodificado; alguns fornecedores usam o segredo tal e qual. Aceitam-se os dois."""
    yield segredo.encode('utf-8')
    resto = segredo.split('_', 1)[1] if '_' in segredo else segredo
    try:
        yield base64.b64decode(resto + '=' * (-len(resto) % 4), validate=True)
    except Exception:
        pass


def verificar(corpo, wid, wts, wsig, segredo):
    """Devolve a chave HMAC que validou a assinatura (ou None)."""
    if not (wid and wts and wsig and segredo):
        return None
    assinado = f'{wid}.{wts}.'.encode('utf-8') + corpo
    recebidas = [p.split(',', 1)[1] for p in wsig.split() if p.startswith('v1,')]
    for k in chaves_hmac(segredo):
        esperado = base64.b64encode(hmac.new(k, assinado, hashlib.sha256).digest()).decode()
        if any(hmac.compare_digest(esperado, r) for r in recebidas):
            return k
    return None


def assinar(corpo, wid, k):
    """Assinatura nova (mesmo id, hora de agora) com a mesma chave que validou o original."""
    ts = str(int(time.time()))
    sig = base64.b64encode(hmac.new(k, f'{wid}.{ts}.'.encode() + corpo, hashlib.sha256).digest()).decode()
    return {'webhook-id': wid, 'webhook-timestamp': ts, 'webhook-signature': 'v1,' + sig}


# ---------------------------------------------------------------- Worker e destino

def worker(caminho, corpo=None):
    req = urllib.request.Request(os.environ['RECETOR_URL'].rstrip('/') + caminho,
                                 data=json.dumps(corpo).encode() if corpo is not None else None,
                                 method='POST' if corpo is not None else 'GET',
                                 headers={'Authorization': 'Bearer ' + os.environ['RECETOR_TOKEN'],
                                          'Content-Type': 'application/json', 'User-Agent': 'recetor-webhooks/1'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def entregar(ev):
    """Devolve 'ok' ou 'invalido' (nunca vai passar: confirma-se e descarta-se); levanta erro para tentar depois."""
    fonte = ev.get('fonte', '')
    segredo = var_fonte('SEGREDO', fonte)
    destino = var_fonte('DESTINO', fonte) or os.environ.get('DESTINO', '')
    if not segredo or not destino:
        raise RuntimeError(f'falta SEGREDO_/DESTINO_ para a fonte {fonte!r}')
    corpo = base64.b64decode(ev['corpo_b64'])
    wid, wts, wsig = ev.get('id_webhook', ''), ev.get('hora_webhook', ''), ev.get('assinatura_webhook', '')
    try:
        ts = int(wts)
    except ValueError:
        return 'invalido'
    # a frescura ja foi verificada pelo Worker a chegada; aqui confirma-se a assinatura e que bate com a hora de chegada
    chave = verificar(corpo, wid, wts, wsig, segredo)
    if chave is None or abs(ev.get('recebido', 0) / 1000 - ts) > TOLERANCIA + 60:
        log(f'evento {wid[:40]} rejeitado aqui (assinatura ou hora)')
        return 'invalido'
    if time.time() - ts > int(os.environ.get('REASSINAR_S', '240')):
        cab = assinar(corpo, wid, chave)
    else:
        cab = {'webhook-id': wid, 'webhook-timestamp': wts, 'webhook-signature': wsig}
    req = urllib.request.Request(destino, data=corpo, method='POST', headers={
        'Content-Type': 'application/json', 'User-Agent': 'recetor-webhooks/1', 'X-Recetor-Fonte': fonte, **cab})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            r.read()
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403, 422):   # o destino recusou de vez: nao adianta repetir
            log(f'evento {wid[:40]}: destino devolveu {e.code}, descartado')
            return 'invalido'
        raise RuntimeError(f'destino {e.code}')
    return 'ok'


def avisar(e, erro):
    url, token = os.environ.get('AVISO_URL'), os.environ.get('AVISO_TOKEN')
    if not (url and token) or e.get('falhas', 0) < AVISO_FALHAS or time.time() - e.get('avisado', 0) < 6 * 3600:
        return
    corpo = {'canal': os.environ.get('AVISO_CANAL', 'alertas'), 'nivel': 'erro', 'mencionar': True,
             'titulo': f'Recetor de webhooks: {e["falhas"]} falhas seguidas',
             'descricao': 'Os webhooks ficam guardados no Worker até 7 dias. Vê o journal do puxar.service.',
             'campos': [{'nome': 'Último erro', 'valor': str(erro)[:300]}]}
    try:
        req = urllib.request.Request(url, data=json.dumps(corpo).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', 'X-Token': token})
        urllib.request.urlopen(req, timeout=20).read()
        e['avisado'] = time.time()
    except Exception as x:
        log(f'nem o aviso saiu: {x}')


def volta():
    e = carregar()
    agora = time.time()
    if agora < e.get('proxima', 0):
        return
    confirmadas = e.get('confirmadas') or []
    try:
        for _ in range(5):   # no maximo 5 lotes de 50 por volta
            tudo = agora - e.get('varrido', 0) > VARRER
            r = worker('/fila?depois=' + urllib.parse.quote(e.get('marca', '')) + ('&tudo=1' if tudo else ''))
            if tudo:
                e['varrido'] = agora
            if r.get('sem_novidades'):
                break
            feitas, erro = [], None
            for ev in r.get('eventos') or []:
                try:
                    res = entregar(ev)
                    feitas.append(ev['chave'])
                    log(f"evento {ev.get('fonte')} {ev.get('id_webhook', '')[:40]} -> {res} "
                        f"({time.time() - ev.get('recebido', 0) / 1000:.0f} s depois de chegar ao Worker)")
                except Exception as x:   # fica no Worker; os seguintes tambem esperam (ordem de chegada)
                    erro = x
                    break
            if feitas:
                worker('/fila/confirmar', {'chaves': feitas})
                confirmadas = (confirmadas + feitas)[-300:]
            if erro:
                raise erro
            if r.get('marca', '') in confirmadas or not r.get('marca'):
                e['marca'] = r.get('marca', '')
            if not r.get('mais'):
                break
        e['falhas'], e['proxima'] = 0, 0
    except Exception as x:
        e['falhas'] = e.get('falhas', 0) + 1
        e['proxima'] = time.time() + min(300, 30 * 2 ** min(e['falhas'], 4))   # espera crescente, maximo 5 min
        log(f"volta falhou ({e['falhas']} seguidas): {str(x)[:200]}")
        avisar(e, x)
    e['confirmadas'] = confirmadas
    e['ultima'] = time.time()
    guardar(e)


def ver():
    r = worker('/fila?tudo=1')
    print('marca:', r.get('marca'), '| mais:', r.get('mais'))
    for ev in r.get('eventos') or []:
        try:
            tipo = json.loads(base64.b64decode(ev['corpo_b64'])).get('type')
        except Exception:
            tipo = '?'
        print(ev['chave'][:40], ev.get('fonte'), tipo, ev.get('id_webhook', '')[:40],
              f"{time.time() - ev.get('recebido', 0) / 1000:.0f}s")


if __name__ == '__main__':
    for k in ('RECETOR_URL', 'RECETOR_TOKEN'):
        if not os.environ.get(k):
            sys.exit(f'falta a variavel {k}')
    a = sys.argv[1:]
    if not a:
        volta()
    elif a[0] == 'ver':
        ver()
    elif a[0] == 'estado':
        e = carregar()
        e['confirmadas'] = len(e.get('confirmadas') or [])
        print(json.dumps(e, indent=1))
    else:
        sys.exit(__doc__)

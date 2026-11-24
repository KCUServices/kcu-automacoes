#!/usr/bin/env python3
"""Sonda do n8n: faz, a pedido dos workflows, o que o n8n nao consegue fazer sozinho.

Corre no anfitriao do Docker onde esta o contentor do n8n (so biblioteca padrao do Python, como root por causa do
`docker exec`). So escuta na rede interna do Docker e pede um token (cabecalho X-Token). Nunca devolve nem regista
tokens: so metadados (conta, validade, erros).

  GET  /saude                  -> {"ok": true}
  POST /instagram/renovar      -> renova os tokens do Instagram das credenciais n8n configuradas e grava o token novo
                                  na MESMA credencial (n8n export:credentials --decrypted / import:credentials, dentro
                                  do contentor). Corpo opcional: {"teste": true} (so verifica) ou {"forcar": true}.
  GET  /instagram/estado       -> validade conhecida de cada token
  GET  /certificados?hosts=a,b -> validade do certificado TLS de cada host (porta 443, com SNI)
  GET  /tunel                  -> estado do contentor do tunel (ex.: cloudflared), se configurado

Porque uma sonda: um workflow do n8n nao consegue alterar as suas proprias credenciais. A API publica do n8n obrigava a
uma chave de API com poder sobre tudo e nao devolve o valor das credenciais. O CLI do n8n dentro do contentor e o
mecanismo oficial: cifra com a chave do proprio n8n e mantem o MESMO id (os workflows nao notam nada).

Configuracao: /etc/n8n-sonda/sonda.json (ver sonda.exemplo.json) e /etc/n8n-sonda/sonda.env
(SONDA_TOKEN, SONDA_ESCUTA, SONDA_ORIGENS). Estado: /var/lib/n8n-sonda/instagram.json.
Copias cifradas das credenciais antes de cada escrita: /var/lib/n8n-sonda/copias/ (as 10 ultimas por credencial).
"""
import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import logging
import logging.handlers
import os
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG = '/etc/n8n-sonda/sonda.json'
AMBIENTE = '/etc/n8n-sonda/sonda.env'
ESTADO = '/var/lib/n8n-sonda/instagram.json'
COPIAS = '/var/lib/n8n-sonda/copias'
LOGS = '/var/log/n8n-sonda'
IG = 'https://graph.instagram.com'
VALIDADE_PADRAO_DIAS = 60      # token de longa duracao do Instagram (gerado no painel ou renovado)
IDADE_MINIMA_H = 24            # a Meta so renova tokens com pelo menos 24 h
RENOVAR_SE_FALTAM_DIAS = 45    # renova se faltarem menos de 45 dias (ou se a validade nao for conhecida)

os.makedirs(LOGS, exist_ok=True)
log = logging.getLogger('sonda')
_h = logging.handlers.RotatingFileHandler(f'{LOGS}/sonda.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8')
_h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
log.addHandler(_h)
log.setLevel(logging.INFO)

trinco_ig = threading.Lock()


def agora():
    return dt.datetime.now(dt.timezone.utc)


def iso(d):
    return d.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if d else None


def de_iso(s):
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00')) if s else None


def carregar_ambiente():
    if os.path.exists(AMBIENTE):
        for linha in open(AMBIENTE, encoding='utf-8'):
            linha = linha.strip()
            if linha and not linha.startswith('#') and '=' in linha:
                k, v = linha.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())


def config():
    with open(CONFIG, encoding='utf-8') as f:
        return json.load(f)


def ler_estado():
    try:
        with open(ESTADO, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def gravar_estado(e):
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    tmp = ESTADO + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(e, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ESTADO)


def impressao(token):
    """So uma impressao curta (sha256) para saber se o token mudou; nunca o token."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- n8n (CLI dentro do contentor)

def contentor_n8n():
    return config().get('n8n_container') or 'n8n'


def docker(*args, entrada=None, tempo=120):
    r = subprocess.run(['docker', *args], input=entrada, capture_output=True, timeout=tempo)
    if r.returncode != 0:
        raise RuntimeError(f"docker {args[0]} {args[1] if len(args) > 1 else ''}: codigo {r.returncode}: "
                           f"{r.stderr.decode(errors='replace')[-300:]}")
    return r.stdout


def exportar(cred_id, decifrada):
    n8n = contentor_n8n()
    caminho = f'/tmp/sonda-{cred_id}-{os.getpid()}-{threading.get_ident()}.json'
    try:
        args = ['exec', n8n, 'n8n', 'export:credentials', f'--id={cred_id}', f'--output={caminho}']
        if decifrada:
            args.insert(-1, '--decrypted')
        docker(*args)
        dados = json.loads(docker('exec', n8n, 'cat', caminho))
    finally:
        try:
            docker('exec', n8n, 'rm', '-f', caminho)
        except Exception:
            pass
    if not isinstance(dados, list) or len(dados) != 1 or dados[0].get('id') != cred_id:
        raise RuntimeError(f'credencial {cred_id} nao encontrada no n8n')
    return dados[0]


def copia_cifrada(cred_id):
    """Guarda a credencial tal como esta (cifrada pelo n8n) antes de a mudar. Restaurar: n8n import:credentials."""
    os.makedirs(COPIAS, mode=0o700, exist_ok=True)
    c = exportar(cred_id, decifrada=False)
    nome = f"{COPIAS}/{cred_id}-{agora().strftime('%Y%m%d-%H%M%S')}.json"
    fd = os.open(nome, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump([c], f)
    for x in sorted(x for x in os.listdir(COPIAS) if x.startswith(cred_id + '-'))[:-10]:
        os.remove(os.path.join(COPIAS, x))
    return nome


def importar(cred):
    """Grava a credencial (mesmo id) pelo CLI do n8n: substitui pelo id, mantem dono e partilhas."""
    n8n = contentor_n8n()
    caminho = f'/tmp/sonda-imp-{cred["id"]}-{os.getpid()}.json'
    try:
        docker('exec', '-i', n8n, 'sh', '-c', f'umask 077; cat > {caminho}', entrada=json.dumps([cred]).encode())
        saida = docker('exec', n8n, 'n8n', 'import:credentials', f'--input={caminho}').decode(errors='replace')
    finally:
        try:
            docker('exec', n8n, 'rm', '-f', caminho)
        except Exception:
            pass
    if 'Successfully imported 1' not in saida:
        raise RuntimeError('import:credentials nao confirmou: ' + saida.strip()[-200:])


# ---------------------------------------------------------------- Instagram

def pedir(url, cabecalhos=None, tempo=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'n8n-sonda/1', **(cabecalhos or {})})
    try:
        with urllib.request.urlopen(req, timeout=tempo) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        try:
            corpo = json.loads(e.read() or b'{}')
        except Exception:
            corpo = {}
        return e.code, corpo


def erro_meta(corpo):
    e = (corpo or {}).get('error') or {}
    return f"{e.get('type', '')} {e.get('code', '')}: {e.get('message', '')}".strip()[:300] or 'erro desconhecido'


def quem_sou(token):
    s, c = pedir(f'{IG}/v23.0/me?fields=user_id,username', {'Authorization': 'Bearer ' + token})
    if s == 200 and c.get('username'):
        return c['username'], None
    return None, erro_meta(c)


def tratar_conta(conta, estado, forcar, teste):
    cid = conta['id']
    r = {'id': cid, 'conta': conta.get('conta'), 'credencial': None, 'ok': False, 'acao': None,
         'expira_em': None, 'dias': None, 'estimado': False, 'erro': None}
    reg = estado.setdefault(cid, {})
    try:
        cred = exportar(cid, decifrada=True)
        r['credencial'] = cred.get('name')
        valor = str((cred.get('data') or {}).get('value') or '')
        if not valor.startswith('Bearer ') or len(valor) < 40:
            raise RuntimeError('a credencial nao tem o formato "Bearer <token>" no campo Value')
        token = valor[len('Bearer '):].strip()
        imp = impressao(token)
        atualizada = de_iso(cred.get('updatedAt')) or agora()
        if reg.get('impressao') != imp:
            # token novo (gerado a mao) ou primeira vez: validade estimada a partir da ultima alteracao da credencial
            reg.update(impressao=imp, expira_em=iso(atualizada + dt.timedelta(days=VALIDADE_PADRAO_DIAS)), estimado=True,
                       obtido_em=iso(atualizada))
        utilizador, erro = quem_sou(token)
        reg['verificado_em'] = iso(agora())
        if not utilizador:
            raise RuntimeError(f'token invalido: {erro}')
        r['conta'] = reg['conta'] = utilizador
        idade_h = (agora() - de_iso(reg['obtido_em'])).total_seconds() / 3600
        faltam = (de_iso(reg['expira_em']) - agora()).total_seconds() / 86400
        precisa = forcar or reg.get('estimado') or faltam < RENOVAR_SE_FALTAM_DIAS
        if teste:
            r['acao'] = 'verificado (teste: nao renovado)'
        elif idade_h < IDADE_MINIMA_H:
            r['acao'] = f'nao renovado: o token tem {idade_h:.0f} h (a Meta pede pelo menos {IDADE_MINIMA_H} h)'
        elif not precisa:
            r['acao'] = f'nao renovado: faltam {faltam:.0f} dias'
        else:
            q = urllib.parse.urlencode({'grant_type': 'ig_refresh_token', 'access_token': token})
            s, c = pedir(f'{IG}/refresh_access_token?{q}')
            novo = c.get('access_token') if s == 200 else None
            if not novo or not c.get('expires_in'):
                raise RuntimeError(f'a renovacao falhou: HTTP {s} {erro_meta(c)}')
            expira = agora() + dt.timedelta(seconds=int(c['expires_in']))
            if novo != token:
                u2, e2 = quem_sou(novo)
                if not u2:
                    raise RuntimeError(f'o token novo nao funciona ({e2}); a credencial NAO foi alterada')
                copia = copia_cifrada(cid)
                cred['data']['value'] = 'Bearer ' + novo
                cred['updatedAt'] = iso(agora()).replace('Z', '.000Z')
                importar({k: cred[k] for k in ('id', 'name', 'type', 'data', 'createdAt', 'updatedAt') if k in cred})
                if str((exportar(cid, decifrada=True).get('data') or {}).get('value')) != 'Bearer ' + novo:
                    raise RuntimeError(f'o n8n nao ficou com o token novo; copia anterior em {copia}')
                imp = impressao(novo)
                r['acao'] = 'renovado (token novo gravado na credencial)'
            else:
                r['acao'] = 'renovado (a Meta prolongou o mesmo token)'
            reg.update(impressao=imp, expira_em=iso(expira), estimado=False, obtido_em=iso(agora()), renovado_em=iso(agora()))
            log.info('instagram %s (%s) renovado ate %s', cid, utilizador, reg['expira_em'])
        reg['ultimo_erro'] = None
        r['ok'] = True
    except Exception as e:
        r['erro'] = str(e)[:400]
        reg['ultimo_erro'] = r['erro']
        log.warning('instagram %s: %s', cid, r['erro'])
    if reg.get('expira_em'):
        r['expira_em'] = reg['expira_em']
        r['dias'] = round((de_iso(reg['expira_em']) - agora()).total_seconds() / 86400, 1)
        r['estimado'] = bool(reg.get('estimado'))
    return r


def instagram_renovar(forcar=False, teste=False):
    if not trinco_ig.acquire(timeout=1):
        return {'erro': 'ja esta a correr uma renovacao'}
    try:
        estado = ler_estado()
        contas = [tratar_conta(c, estado, forcar, teste) for c in config().get('instagram', [])]
        gravar_estado(estado)
        return {'quando': iso(agora()), 'teste': teste, 'contas': contas}
    finally:
        trinco_ig.release()


def instagram_estado():
    estado = ler_estado()
    contas = []
    for c in config().get('instagram', []):
        reg = estado.get(c['id'], {})
        exp = de_iso(reg.get('expira_em'))
        contas.append({'id': c['id'], 'conta': reg.get('conta') or c.get('conta'), 'expira_em': reg.get('expira_em'),
                       'dias': round((exp - agora()).total_seconds() / 86400, 1) if exp else None,
                       'estimado': bool(reg.get('estimado')), 'renovado_em': reg.get('renovado_em'),
                       'verificado_em': reg.get('verificado_em'), 'ultimo_erro': reg.get('ultimo_erro')})
    return {'contas': contas}


# ---------------------------------------------------------------- certificados e tunel

def certificado(host):
    r = {'host': host, 'expira_em': None, 'dias': None, 'emissor': None, 'erro': None}
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=15) as s:
            with ctx.wrap_socket(s, server_hostname=host) as t:
                cert = t.getpeercert()
        fim = dt.datetime.fromtimestamp(ssl.cert_time_to_seconds(cert['notAfter']), dt.timezone.utc)
        r['expira_em'] = iso(fim)
        r['dias'] = round((fim - agora()).total_seconds() / 86400, 1)
        org = dict(x[0] for x in cert.get('issuer', ()))
        r['emissor'] = org.get('organizationName') or org.get('commonName')
    except Exception as e:
        r['erro'] = f'{type(e).__name__}: {e}'[:300]
    return r


def tunel():
    """Contentor do tunel (ex.: cloudflared): a correr, reinicios e, se tiver metricas, ligacoes prontas."""
    cfg = config()
    nome = cfg.get('tunel_container')
    if not nome:
        return {'configurado': False}
    r = {'configurado': True, 'container': nome, 'a_correr': False, 'estado': None, 'reinicios': None, 'desde': None,
         'ligacoes': None, 'erro': None}
    try:
        info = json.loads(docker('inspect', nome))[0]
        st = info['State']
        r.update(a_correr=bool(st.get('Running')), estado=st.get('Status'), reinicios=info.get('RestartCount'),
                 desde=st.get('StartedAt', '')[:19] + 'Z')
        # cloudflared expoe /ready nas metricas (porta aleatoria 20241-20245 por omissao), dentro do contentor
        if st.get('Running') and st.get('Pid'):
            for porta in cfg.get('tunel_portas_metricas', [20241, 20242, 20243, 20244, 20245]):
                p = subprocess.run(['nsenter', '-t', str(st['Pid']), '-n', 'curl', '-s', '-m', '3',
                                    f'http://127.0.0.1:{int(porta)}/ready'], capture_output=True, timeout=10)
                if p.returncode == 0 and p.stdout:
                    try:
                        r['ligacoes'] = json.loads(p.stdout).get('readyConnections')
                        break
                    except Exception:
                        pass
        if r['ligacoes'] is None and r['a_correr']:
            r['ligacoes'] = 1   # sem metricas: conta como ligado se estiver a correr
    except Exception as e:
        r['erro'] = str(e)[:300]
    return r


# ---------------------------------------------------------------- HTTP

class Pedido(BaseHTTPRequestHandler):
    server_version = 'n8n-sonda/1'

    def log_message(self, fmt, *args):
        pass

    def _autorizado(self):
        token = os.environ.get('SONDA_TOKEN', '')
        try:
            rede = ipaddress.ip_address(self.client_address[0]) in ipaddress.ip_network(
                os.environ.get('SONDA_ORIGENS', '172.17.0.0/16'), strict=False)
        except ValueError:
            rede = False
        ok = rede and len(token) >= 32 and hmac.compare_digest(self.headers.get('X-Token', ''), token)
        if not ok:
            log.warning('pedido recusado de %s (rede=%s)', self.client_address[0], rede)
        return ok

    def _responder(self, codigo, corpo):
        dados = json.dumps(corpo, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == '/saude':
            return self._responder(200, {'ok': True})
        if not self._autorizado():
            return self._responder(403, {'erro': 'proibido'})
        if u.path == '/instagram/estado':
            return self._responder(200, instagram_estado())
        if u.path == '/certificados':
            hosts = [h.strip() for h in urllib.parse.parse_qs(u.query).get('hosts', [''])[0].split(',') if h.strip()][:20]
            return self._responder(200, {'certificados': [certificado(h) for h in hosts]})
        if u.path == '/tunel':
            return self._responder(200, tunel())
        return self._responder(404, {'erro': 'nao existe'})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if not self._autorizado():
            return self._responder(403, {'erro': 'proibido'})
        try:
            n = int(self.headers.get('Content-Length') or 0)
            corpo = json.loads(self.rfile.read(n) or b'{}') if n else {}
        except Exception:
            return self._responder(400, {'erro': 'pedido'})
        if u.path == '/instagram/renovar':
            r = instagram_renovar(forcar=bool(corpo.get('forcar')), teste=bool(corpo.get('teste')))
            return self._responder(409 if 'erro' in r else 200, r)
        return self._responder(404, {'erro': 'nao existe'})


def main():
    carregar_ambiente()
    escuta = os.environ.get('SONDA_ESCUTA', '172.17.0.1:8791')
    anfitriao, porta = escuta.rsplit(':', 1)
    for tentativa in range(60):   # a rede do Docker pode ainda nao existir no arranque
        try:
            srv = ThreadingHTTPServer((anfitriao, int(porta)), Pedido)
            break
        except OSError as e:
            if tentativa == 59:
                raise
            log.warning('ainda nao consigo escutar em %s (%s); tento outra vez', escuta, e)
            time.sleep(5)
    log.info('sonda a escutar em %s', escuta)
    srv.serve_forever()


if __name__ == '__main__':
    main()

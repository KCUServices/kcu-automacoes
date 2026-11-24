"""Discord aprovacoes - partes comuns: configuracao, registos, base de dados SQLite e API REST do Discord.

Tudo o que e segredo ou muda de casa para casa vem do ficheiro de ambiente (ver .env.exemplo).
Nada aqui fala com o gateway do Discord: isso e so no bot.py.
"""
import ipaddress
import json
import logging
import logging.handlers
import os
import secrets
import sqlite3
import time
import urllib.error
import urllib.request

FICHEIRO_AMBIENTE = os.environ.get('APROVACOES_ENV', '/etc/discord-aprovacoes/discord-aprovacoes.env')
API = 'https://discord.com/api/v10'
AGENTE = 'DiscordBot (discord-aprovacoes, 1.0)'


def carregar_ambiente():
    """Le CHAVE=VALOR do ficheiro de ambiente (o systemd tambem o le; a ferramenta.py corre fora do systemd)."""
    if os.path.exists(FICHEIRO_AMBIENTE):
        for linha in open(FICHEIRO_AMBIENTE, encoding='utf-8'):
            linha = linha.strip()
            if not linha or linha.startswith('#') or '=' not in linha:
                continue
            k, v = linha.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for k in ('DISCORD_BOT_TOKEN', 'APROVADORES'):
        if not os.environ.get(k):
            raise SystemExit(f'falta {k} em {FICHEIRO_AMBIENTE}')


def pasta_dados():
    return os.environ.get('PASTA_DADOS', '/var/lib/discord-aprovacoes')


def registo(nome):
    """Log em ficheiro com rotacao (um ficheiro por processo)."""
    pasta = os.environ.get('PASTA_LOGS', '/var/log/discord-aprovacoes')
    os.makedirs(pasta, exist_ok=True)
    log = logging.getLogger(nome)
    if not log.handlers:
        h = logging.handlers.RotatingFileHandler(os.path.join(pasta, f'{nome}.log'), maxBytes=1_000_000, backupCount=3,
                                                 encoding='utf-8')
        h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log


# ---------------------------------------------------------------- quem pode o que

def aprovadores():
    """IDs de utilizador do Discord que podem carregar nos botoes (separados por virgulas)."""
    return [x.strip() for x in os.environ.get('APROVADORES', '').split(',') if x.strip()]


def prefixo_botoes():
    """Prefixo do custom_id dos botoes. O bot so trata cliques com este prefixo e ignora todos os outros."""
    return os.environ.get('PREFIXO_BOTOES', 'apr')


def origem_permitida(ip):
    """IP de quem chama a API local: so os de API_ORIGENS (IPs ou redes, ex.: 10.0.0.5,10.0.1.0/24)."""
    try:
        quem = ipaddress.ip_address(ip or '')
    except ValueError:
        return False
    for o in os.environ.get('API_ORIGENS', '127.0.0.1').split(','):
        o = o.strip()
        if not o:
            continue
        try:
            if quem in ipaddress.ip_network(o, strict=False):
                return True
        except ValueError:
            continue
    return False


def token_certo(recebido):
    token = os.environ.get('API_TOKEN', '')
    return len(token) >= 32 and secrets.compare_digest((recebido or '').encode(), token.encode())


def canal_id(nome):
    """Nome logico do canal (ex.: alertas) -> CANAL_ALERTAS_ID no ficheiro de ambiente."""
    if not isinstance(nome, str) or not nome or len(nome) > 30 or not all(c.isalnum() or c == '_' for c in nome):
        raise ValueError('canal invalido')
    cid = os.environ.get(f'CANAL_{nome.upper()}_ID')
    if not cid:
        raise ValueError(f'canal desconhecido: {nome}')
    return cid


def texto(v, n):
    v = '' if v is None else str(v)
    return v if len(v) <= n else v[:n - 1] + '…'


# ---------------------------------------------------------------- base de dados

def ligar():
    os.makedirs(pasta_dados(), exist_ok=True)
    db = sqlite3.connect(os.path.join(pasta_dados(), 'estado.db'), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    return db


# ---------------------------------------------------------------- Discord (REST)

def _pedido(metodo, caminho, dados, tipo, tempo):
    for _ in range(5):
        req = urllib.request.Request(API + caminho, data=dados, method=metodo, headers={
            'Authorization': 'Bot ' + os.environ['DISCORD_BOT_TOKEN'], 'Content-Type': tipo, 'User-Agent': AGENTE})
        try:
            with urllib.request.urlopen(req, timeout=tempo) as r:
                txt = r.read()
                return json.loads(txt) if txt else None
        except urllib.error.HTTPError as e:
            if e.code == 429:   # limite de pedidos: o Discord diz quanto esperar
                time.sleep(float(json.loads(e.read() or b'{}').get('retry_after', 2)) + 0.5)
                continue
            raise RuntimeError(f'Discord {metodo} {caminho}: {e.code} {e.read()[:300]!r}')
    raise RuntimeError('Discord: limite de pedidos')


def discord(metodo, caminho, corpo=None):
    dados = json.dumps(corpo).encode() if corpo is not None else None
    return _pedido(metodo, caminho, dados, 'application/json', 20)


def discord_com_ficheiro(metodo, caminho, corpo, nome, mime, binario):
    """Mensagem com um anexo (multipart/form-data: payload_json + files[0])."""
    fronteira = 'apr' + secrets.token_hex(12)
    dados = b''.join([
        f'--{fronteira}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
        f'Content-Type: application/json\r\n\r\n'.encode() + json.dumps(corpo).encode() + b'\r\n',
        f'--{fronteira}\r\nContent-Disposition: form-data; name="files[0]"; filename="{nome}"\r\n'
        f'Content-Type: {mime}\r\n\r\n'.encode() + binario + b'\r\n',
        f'--{fronteira}--\r\n'.encode()])
    return _pedido(metodo, caminho, dados, f'multipart/form-data; boundary={fronteira}', 60)


def mencoes_permitidas():
    """So os aprovadores podem ser mencionados (nunca @everyone nem cargos)."""
    return {'parse': [], 'users': aprovadores()[:100]}

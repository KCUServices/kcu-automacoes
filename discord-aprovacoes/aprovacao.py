"""Discord aprovacoes - pedidos de aprovacao com botoes (quem pede -> Discord -> clique de um aprovador -> quem pediu).

Fluxo:
  1. um programa teu (n8n, script, ...) faz POST /aprovacao com titulo, campos, imagem opcional e botoes;
     sai uma mensagem no canal com os botoes;
  2. um aprovador carrega num botao (custom_id "<prefixo>:<botao>:apr-..."); quem nao esta em APROVADORES
     recebe "so os aprovadores podem decidir" e nada muda;
  3. a decisao fica gravada aqui e chega a quem pediu de uma de duas formas:
     - modo "empurrar": o bot chama o callback (um URL que comeca por CALLBACK_PREFIXO), e repete de minuto a
       minuto ate 30 vezes se o outro lado estiver em baixo;
     - modo "puxar" (sem callback): quem pediu pergunta GET /aprovacoes?origem=...&estado=decidido
       (serve para um PC sem endereco fixo, que so faz pedidos para fora);
  4. quem pediu "reserva" a execucao (POST /aprovacao/<id>/reservar: so uma vez, cliques repetidos nao fazem
     nada a dobrar), faz o trabalho e fecha com POST /aprovacao/<id>/atualizar.
Sem botoes = so um aviso (fica logo "concluido").

Imagem (opcional): guardada so aqui (PASTA_DADOS/imagens, 600) e anexada a mensagem. Se um servico de fora tiver
de ir buscar a imagem (ex.: a API do Instagram so aceita um URL publico), pede-se uma chave de uso curto
(POST /aprovacao/<id>/chave, 15 min) e serve-se a imagem num endpoint publico teu que a le de
GET /aprovacao-imagem/<chave> (que tambem precisa do token).
"""
import base64
import json
import os
import re
import secrets
import threading
import time
import urllib.request

import comum

MAX_IMAGEM = 10 * 1024 * 1024
CHAVE_VALIDADE = 15 * 60
MAX_TENTATIVAS = 30
GUARDAR_IMAGEM_DIAS = 7
ESTILOS = {'primario': 1, 'secundario': 2, 'sucesso': 3, 'perigo': 4}
CORES = {'pendente': 0xF1C40F, 'decidido': 0x3498DB, 'a_executar': 0x3498DB, 'concluido': 0x95A5A6}
CORES_NOMES = {'verde': 0x2ECC71, 'vermelho': 0xE74C3C, 'amarelo': 0xF1C40F, 'azul': 0x3498DB, 'cinzento': 0x95A5A6,
               'laranja': 0xE67E22, 'roxo': 0x9B59B6}
RE_ID = re.compile(r'^apr-[a-z0-9-]{4,48}$')
RE_BOTAO = re.compile(r'^[a-z_]{1,20}$')
RE_ORIGEM = re.compile(r'^[a-z0-9-]{3,40}$')
_trinco_local = threading.Lock()


class trinco:
    """Tranca entre processos (bot e ferramenta.py): uma decisao/reserva de cada vez."""
    def __enter__(self):
        try:
            import fcntl
        except ImportError:   # Windows (so para testes): tranca dentro do processo
            _trinco_local.acquire()
            self.f = None
            return
        os.makedirs(comum.pasta_dados(), exist_ok=True)
        self.f = open(os.path.join(comum.pasta_dados(), 'aprovacao.lock'), 'w')
        fcntl.flock(self.f, fcntl.LOCK_EX)

    def __exit__(self, *a):
        if self.f is None:
            _trinco_local.release()
        else:
            self.f.close()


def pasta_imagens():
    return os.path.join(comum.pasta_dados(), 'imagens')


# ---------------------------------------------------------------- base de dados

def ligar():
    db = comum.ligar()
    db.execute('''CREATE TABLE IF NOT EXISTS aprovacoes (
        id TEXT PRIMARY KEY,           -- apr-...
        origem TEXT NOT NULL,          -- quem pediu (ex.: n8n-instagram, melhorias)
        canal TEXT NOT NULL,           -- nome logico do canal (CANAL_<NOME>_ID)
        msg_id TEXT,
        estado TEXT NOT NULL,          -- pendente | decidido | a_executar | concluido
        decisao TEXT,                  -- id do botao carregado
        decidido_por TEXT,
        decidido_em REAL,
        simulado INTEGER DEFAULT 0,
        dados TEXT NOT NULL,           -- titulo, descricao, campos, botoes, callback, contexto, fase, nota... (JSON)
        callback_estado TEXT,          -- por_enviar | enviado | falhou (NULL no modo puxar)
        callback_tentativas INTEGER DEFAULT 0,
        chave TEXT,                    -- chave de uso curto para servir a imagem
        chave_expira REAL,
        criado_em REAL,
        atualizado_em REAL)''')
    db.execute('CREATE INDEX IF NOT EXISTS aprovacoes_origem ON aprovacoes (origem, estado)')
    db.commit()
    return db


def obter(db, aid):
    r = db.execute('SELECT * FROM aprovacoes WHERE id=?', (aid,)).fetchone()
    return dict(r) if r else None


def gravar(db, a):
    a['atualizado_em'] = time.time()
    cols = ['id', 'origem', 'canal', 'msg_id', 'estado', 'decisao', 'decidido_por', 'decidido_em', 'simulado', 'dados',
            'callback_estado', 'callback_tentativas', 'chave', 'chave_expira', 'criado_em', 'atualizado_em']
    db.execute(f'INSERT OR REPLACE INTO aprovacoes ({",".join(cols)}) VALUES ({",".join("?" * len(cols))})',
               [a.get(c) for c in cols])
    db.commit()


# ---------------------------------------------------------------- validacao

def _botoes_validos(botoes):
    saida = []
    for b in (botoes or [])[:5]:
        if not isinstance(b, dict) or not RE_BOTAO.match(str(b.get('id', ''))):
            raise ValueError('botao com id invalido (so a-z e _)')
        saida.append({'id': b['id'], 'rotulo': comum.texto(b.get('rotulo') or b['id'], 80), 'emoji': b.get('emoji'),
                      'estilo': b.get('estilo') if b.get('estilo') in ESTILOS else 'secundario',
                      'resposta': comum.texto(b.get('resposta'), 1800) if b.get('resposta') else None})
    return saida


def _ligacoes_validas(ligacoes):
    return [{'rotulo': comum.texto(l.get('rotulo') or 'Abrir', 80), 'url': l['url']} for l in (ligacoes or [])[:5]
            if isinstance(l, dict) and str(l.get('url', '')).startswith('https://') and len(l['url']) <= 512]


def _campos_validos(campos):
    return [{'nome': comum.texto(c.get('nome'), 250), 'valor': comum.texto(c.get('valor'), 1024), 'inline': bool(c.get('inline'))}
            for c in (campos or [])[:20] if isinstance(c, dict)]


def _callback_valido(url):
    """So se aceitam callbacks para URLs que comecam por CALLBACK_PREFIXO (vazio = callbacks desligados)."""
    prefixo = os.environ.get('CALLBACK_PREFIXO', '')
    return bool(prefixo) and isinstance(url, str) and url.startswith(prefixo) and len(url) < 300


# ---------------------------------------------------------------- criar / atualizar

def criar(db, p):
    """Cria a aprovacao e a mensagem. Idempotente: o mesmo id devolve a que ja existe."""
    aid = p.get('id') or 'apr-' + secrets.token_hex(6)
    if not RE_ID.match(aid):
        raise ValueError('id invalido (apr- seguido de a-z, 0-9 e -)')
    existente = obter(db, aid)
    if existente:
        return {'id': aid, 'msg_id': existente['msg_id'], 'estado': existente['estado'], 'existente': True}
    origem = str(p.get('origem') or '')
    if not RE_ORIGEM.match(origem):
        raise ValueError('origem invalida (a-z, 0-9 e -)')
    comum.canal_id(p.get('canal'))
    botoes = _botoes_validos(p.get('botoes'))
    callback = p.get('callback') or None
    if callback and not _callback_valido(callback):
        raise ValueError('callback fora de CALLBACK_PREFIXO')
    dados = {'titulo': comum.texto(p.get('titulo'), 250), 'descricao': comum.texto(p.get('descricao'), 4000),
             'campos': _campos_validos(p.get('campos')), 'cor': p.get('cor'), 'botoes': botoes,
             'ligacoes': _ligacoes_validas(p.get('ligacoes')), 'callback': callback, 'contexto': p.get('contexto') or {},
             'fase': p.get('fase'), 'nota': p.get('nota'), 'resultado': p.get('resultado'),
             'mencionar': bool(p.get('mencionar')), 'teste': bool(p.get('teste')),
             'rodape': comum.texto(p.get('rodape'), 200) if p.get('rodape') else None}
    if p.get('imagem_b64'):
        binario = base64.b64decode(p['imagem_b64'])
        if len(binario) > MAX_IMAGEM:
            raise ValueError('imagem grande demais (maximo 10 MB)')
        if binario[:3] == b'\xff\xd8\xff':
            ext, mime = 'jpg', 'image/jpeg'
        elif binario[:8] == b'\x89PNG\r\n\x1a\n':
            ext, mime = 'png', 'image/png'
        else:
            raise ValueError('imagem tem de ser JPEG ou PNG')
        os.makedirs(pasta_imagens(), mode=0o700, exist_ok=True)
        ficheiro = os.path.join(pasta_imagens(), f'{aid}.{ext}')
        fd = os.open(ficheiro, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(binario)
        dados['imagem'] = {'ficheiro': ficheiro, 'nome': f'imagem.{ext}', 'mime': mime, 'bytes': len(binario)}
    a = {'id': aid, 'origem': origem, 'canal': p['canal'], 'estado': 'pendente' if botoes else 'concluido',
         'dados': json.dumps(dados, ensure_ascii=False), 'callback_tentativas': 0, 'criado_em': time.time()}
    gravar(db, a)
    try:
        a['msg_id'] = publicar(a, novo=True)
    except Exception:
        db.execute('DELETE FROM aprovacoes WHERE id=?', (aid,))   # sem mensagem nao ha aprovacao: quem pediu pode repetir
        db.commit()
        _apagar_imagem(dados)
        raise
    gravar(db, a)
    return {'id': aid, 'msg_id': a['msg_id'], 'estado': a['estado'], 'existente': False}


def atualizar(db, aid, p):
    """Quem pediu muda o estado/resultado. estado 'pendente' volta a mostrar botoes (os de p['botoes'] ou os mesmos)."""
    a = obter(db, aid)
    if not a:
        raise KeyError(aid)
    d = json.loads(a['dados'])
    for k in ('nota', 'resultado', 'fase', 'cor'):
        if k in p:
            d[k] = p[k]
    if 'botoes' in p:
        d['botoes'] = _botoes_validos(p['botoes'])
    if p.get('ligacoes') is not None:
        d['ligacoes'] = _ligacoes_validas(p['ligacoes'])
    if p.get('contexto'):
        d.setdefault('contexto', {}).update(p['contexto'])
    for novo in _campos_validos(p.get('campos_extra')):   # junta ou substitui pelo nome
        d['campos'] = [x for x in d.get('campos', []) if x['nome'] != novo['nome']] + [novo]
    estado = p.get('estado')
    if estado == 'pendente':
        if not d.get('botoes'):
            raise ValueError('pendente sem botoes')
        a.update(estado='pendente', decisao=None, decidido_por=None, decidido_em=None, callback_estado=None,
                 callback_tentativas=0)
    elif estado in ('concluido', 'a_executar'):
        a['estado'] = estado
    elif estado:
        raise ValueError('estado invalido')
    if a['estado'] == 'concluido':
        a['chave'] = a['chave_expira'] = None
    if p.get('apagar_imagem'):
        a['chave'] = a['chave_expira'] = None
        _apagar_imagem(d)
    a['dados'] = json.dumps(d, ensure_ascii=False)
    gravar(db, a)
    publicar(a)
    return resumo(a)


def _apagar_imagem(d):
    im = d.get('imagem') or {}
    if im.get('ficheiro') and os.path.exists(im['ficheiro']):
        os.remove(im['ficheiro'])
    if im:
        im['apagada'] = True


# ---------------------------------------------------------------- decisao (o mesmo codigo para o botao e para o teste)

def decidir(db, aid, botao, utilizador_id, simulado=False):
    """Regista o clique. Devolve (ok, texto para mostrar so a quem carregou)."""
    with trinco():
        a = obter(db, aid)
        if not a:
            return False, 'Esta aprovação já não existe.'
        if not simulado and str(utilizador_id) not in comum.aprovadores():
            return False, 'Só os aprovadores podem decidir isto.'
        if a['estado'] != 'pendente':
            return False, 'Isto já foi decidido.'
        d = json.loads(a['dados'])
        b = next((x for x in d.get('botoes', []) if x['id'] == botao), None)
        if not b:
            return False, 'Botão inválido para esta aprovação.'
        a.update(estado='decidido', decisao=botao, decidido_por=str(utilizador_id), decidido_em=time.time(),
                 simulado=1 if simulado else 0, callback_estado='por_enviar' if d.get('callback') else None,
                 callback_tentativas=0)
        gravar(db, a)
    return True, b.get('resposta') or f"{b['rotulo']}: registado."


def enviar_callback(db, aid):
    """Modo empurrar: avisa quem pediu. Devolve True se o outro lado aceitou (2xx)."""
    a = obter(db, aid)
    if not a or a['estado'] != 'decidido' or a.get('callback_estado') != 'por_enviar':
        return False
    d = json.loads(a['dados'])
    if not _callback_valido(d.get('callback')):
        return False
    corpo = json.dumps({'id': aid, 'origem': a['origem'], 'decisao': a['decisao'], 'simulado': bool(a['simulado']),
                        'decidido_em': a['decidido_em'], 'contexto': d.get('contexto') or {}}).encode()
    req = urllib.request.Request(d['callback'], data=corpo, method='POST', headers={
        'Content-Type': 'application/json',
        'X-Token': os.environ.get('CALLBACK_TOKEN') or os.environ.get('API_TOKEN', '')})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ok = 200 <= r.status < 300
    except Exception:
        ok = False
    with trinco():
        a = obter(db, aid)
        if a['estado'] != 'decidido':
            return ok
        if ok:
            a['callback_estado'] = 'enviado'
        else:
            a['callback_tentativas'] = (a.get('callback_tentativas') or 0) + 1
            if a['callback_tentativas'] >= MAX_TENTATIVAS:
                a['callback_estado'] = 'falhou'
                d['nota'] = (f'⚠️ Quem pediu não respondeu depois de {MAX_TENTATIVAS} tentativas: nada foi feito. '
                             f'Vê se está ligado e corre `ferramenta.py reenviar {aid}`.')
                a['dados'] = json.dumps(d, ensure_ascii=False)
        gravar(db, a)
    if a.get('callback_estado') == 'falhou':
        publicar(a)
    return ok


def por_enviar(db):
    return [r[0] for r in db.execute("SELECT id FROM aprovacoes WHERE estado='decidido' AND callback_estado='por_enviar'")]


def reservar(db, aid, fases=None):
    """Passa a 'a_executar' uma unica vez. Aceita 'decidido' (clique) ou 'pendente' numa das fases dadas (ex.: agendado)."""
    with trinco():
        a = obter(db, aid)
        if not a:
            return {'ok': False, 'motivo': 'nao existe'}
        d = json.loads(a['dados'])
        pode = a['estado'] == 'decidido' or (a['estado'] == 'pendente' and fases and d.get('fase') in fases)
        if not pode:
            return {'ok': False, 'motivo': f"estado {a['estado']}", 'estado': a['estado'], 'decisao': a['decisao']}
        decisao = a['decisao']
        a['estado'] = 'a_executar'
        if a.get('callback_estado') == 'por_enviar':
            a['callback_estado'] = 'enviado'
        gravar(db, a)
    try:
        publicar(a)
    except Exception:
        pass
    return {'ok': True, 'decisao': decisao, 'simulado': bool(a['simulado']), 'contexto': d.get('contexto') or {},
            'fase': d.get('fase')}


# ---------------------------------------------------------------- imagem de uso curto

def criar_chave(db, aid):
    with trinco():
        a = obter(db, aid)
        if not a:
            raise KeyError(aid)
        im = json.loads(a['dados']).get('imagem') or {}
        if not im.get('ficheiro') or not os.path.exists(im['ficheiro']):
            raise ValueError('sem imagem')
        a['chave'] = secrets.token_urlsafe(32)
        a['chave_expira'] = time.time() + CHAVE_VALIDADE
        gravar(db, a)
    return {'chave': a['chave'], 'expira': a['chave_expira'], 'mime': im['mime']}


def imagem_por_chave(db, chave):
    if not chave or len(chave) < 30:
        return None
    r = db.execute('SELECT * FROM aprovacoes WHERE chave=? AND chave_expira>?', (chave, time.time())).fetchone()
    if not r:
        return None
    im = json.loads(r['dados']).get('imagem') or {}
    if not im.get('ficheiro') or not os.path.exists(im['ficheiro']):
        return None
    with open(im['ficheiro'], 'rb') as f:
        return f.read(), im['mime']


# ---------------------------------------------------------------- listas e limpeza

def resumo(a):
    d = json.loads(a['dados'])
    return {'id': a['id'], 'origem': a['origem'], 'estado': a['estado'], 'decisao': a['decisao'],
            'simulado': bool(a['simulado']), 'fase': d.get('fase'), 'contexto': d.get('contexto') or {},
            'resultado': d.get('resultado'), 'callback_estado': a.get('callback_estado'), 'msg_id': a['msg_id'],
            'teste': d.get('teste', False), 'criado_em': a['criado_em'], 'atualizado_em': a['atualizado_em'],
            'decidido_em': a['decidido_em']}


def listar(db, origem, estado=None, fase=None, desde=None):
    if not RE_ORIGEM.match(origem or ''):
        raise ValueError('origem invalida')
    sql, args = 'SELECT * FROM aprovacoes WHERE origem=?', [origem]
    if estado:
        sql += ' AND estado=?'
        args.append(estado)
    if desde:
        sql += ' AND atualizado_em>=?'
        args.append(float(desde))
    linhas = [resumo(dict(r)) for r in db.execute(sql + ' ORDER BY criado_em', args)]
    return [l for l in linhas if not fase or l['fase'] == fase]


def limpar(db):
    """Chaves expiradas fora; imagens de aprovacoes fechadas ha mais de 7 dias apagadas."""
    db.execute('UPDATE aprovacoes SET chave=NULL, chave_expira=NULL WHERE chave IS NOT NULL AND chave_expira<?', (time.time(),))
    db.commit()
    limite = time.time() - GUARDAR_IMAGEM_DIAS * 86400
    for r in db.execute("SELECT * FROM aprovacoes WHERE estado='concluido' AND atualizado_em<?", (limite,)).fetchall():
        d = json.loads(r['dados'])
        if (d.get('imagem') or {}).get('ficheiro') and not d['imagem'].get('apagada'):
            _apagar_imagem(d)
            db.execute('UPDATE aprovacoes SET dados=? WHERE id=?', (json.dumps(d, ensure_ascii=False), r['id']))
    db.commit()


# ---------------------------------------------------------------- Discord

def desenhar(a):
    d = json.loads(a['dados'])
    teste = '[teste] ' if d.get('teste') else ''
    campos = list(d.get('campos') or [])
    rotulos = {b['id']: ((b.get('emoji') or '') + ' ' + b['rotulo']).strip() for b in d.get('botoes') or []}
    if a['estado'] == 'pendente':
        estado = d.get('nota') or '⏳ À espera de decisão'
        cor = CORES['pendente']
    elif a['estado'] == 'decidido':
        quem = ' (clique simulado)' if a.get('simulado') else ''
        estado = f"{rotulos.get(a['decisao'], a['decisao'])}{quem}\n" + (d.get('nota') or '⏳ A executar…')
        cor = CORES['decidido']
    elif a['estado'] == 'a_executar':
        estado = d.get('nota') or '⚙️ A executar…'
        cor = CORES['a_executar']
    else:
        estado = d.get('resultado') or d.get('nota') or ''
        cor = CORES['concluido']
    if d.get('cor') in CORES_NOMES:
        cor = CORES_NOMES[d['cor']]
    if estado:
        campos.append({'nome': 'Estado', 'valor': estado, 'inline': False})
    embed = {'title': comum.texto(teste + (d.get('titulo') or ''), 256), 'color': cor,
             'fields': [{'name': comum.texto(c['nome'], 256) or '-', 'value': comum.texto(c['valor'], 1024) or '-',
                         'inline': c.get('inline', False)} for c in campos][:25],
             'footer': {'text': comum.texto(f"{teste}{d.get('rodape') or a['origem']} · {a['id']}", 2000)}}
    if d.get('descricao'):
        embed['description'] = comum.texto(d['descricao'], 4000)
    im = d.get('imagem')
    if im:   # o anexo fica na mensagem do Discord mesmo depois de a copia local ser apagada
        embed['image'] = {'url': f"attachment://{im['nome']}"}
    linha = []
    if a['estado'] == 'pendente':
        for b in d.get('botoes') or []:
            x = {'type': 2, 'style': ESTILOS[b['estilo']], 'label': b['rotulo'],
                 'custom_id': f"{comum.prefixo_botoes()}:{b['id']}:{a['id']}"}
            if b.get('emoji'):
                x['emoji'] = {'name': b['emoji']}
            linha.append(x)
    componentes = [{'type': 1, 'components': linha}] if linha else []
    ligacoes = [{'type': 2, 'style': 5, 'label': l['rotulo'], 'url': l['url']} for l in d.get('ligacoes') or []]
    if ligacoes:
        componentes.append({'type': 1, 'components': ligacoes})
    return {'embeds': [embed], 'components': componentes, 'allowed_mentions': comum.mencoes_permitidas()}


def publicar(a, novo=False):
    """Cria (com o anexo da imagem) ou edita a mensagem. Na edicao o anexo fica (nao se manda 'attachments')."""
    corpo = desenhar(a)
    canal = comum.canal_id(a['canal'])
    d = json.loads(a['dados'])
    if a.get('msg_id') and not novo:
        comum.discord('PATCH', f"/channels/{canal}/messages/{a['msg_id']}", corpo)
        return a['msg_id']
    if d.get('mencionar'):
        corpo['content'] = ' '.join(f'<@{u}>' for u in comum.aprovadores()) + ' ' + comum.texto(d.get('titulo'), 150)
    im = d.get('imagem')
    if im and os.path.exists(im.get('ficheiro', '')):
        with open(im['ficheiro'], 'rb') as f:
            m = comum.discord_com_ficheiro('POST', f'/channels/{canal}/messages', corpo, im['nome'], im['mime'], f.read())
    else:
        corpo['embeds'][0].pop('image', None)
        m = comum.discord('POST', f'/channels/{canal}/messages', corpo)
    return m['id']

"""Pasta -> aprovacao no Discord -> Instagram. Corre no teu PC de 2 em 2 minutos (tarefa agendada ou cron).

  imagem.jpg            a imagem (JPEG ou PNG; o PNG e convertido para JPEG se tiveres o Pillow instalado)
  imagem.txt            a legenda
  imagem.quando.txt     (opcional) hora local, ex.: 2026-11-30 18:30  (ou 30/11/2026 18:30)
  imagem.teste.txt      (opcional) modo teste: o Instagram cria o contentor mas NADA e publicado

Este script nunca fala com o Instagram: manda a imagem ao n8n (que valida e pede aprovacao no Discord) e, depois da
decisao, move os ficheiros para publicados/ ou recusados/. Nada e publicado sem um clique no Discord.

Variaveis de ambiente:
  PUBLICAR_PASTA   pasta a vigiar (omissao: ./publicar ao lado deste script)
  PUBLICAR_N8N     URL base dos webhooks do n8n na rede de casa, ex.: http://10.0.0.20:5678/webhook
  PUBLICAR_TOKEN   token do cabecalho X-Token (o mesmo da credencial "Pasta de publicar" no n8n)
"""
import base64
import datetime as dt
import hashlib
import io
import json
import os
import shutil
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PASTA = Path(os.environ.get('PUBLICAR_PASTA') or AQUI / 'publicar')
PUBLICADOS = PASTA / 'publicados'
RECUSADOS = PASTA / 'recusados'
ESTADO = PASTA / '.estado.json'
LOG = PASTA / '.publicar.log'
IMAGENS = ('.jpg', '.jpeg', '.png')
ESTAVEL_S = 20                 # o ficheiro tem de estar parado ha 20 s (copias a meio nao seguem)
MAX_BYTES = 8 * 1024 * 1024


def log(msg):
    PASTA.mkdir(parents=True, exist_ok=True)
    linha = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(linha + '\n')
    if LOG.stat().st_size > 2_000_000:
        shutil.move(str(LOG), str(LOG) + '.1')
    try:
        print(linha)
    except Exception:
        pass


def n8n(metodo, caminho, corpo=None, timeout=180):
    base, token = os.environ.get('PUBLICAR_N8N', '').rstrip('/'), os.environ.get('PUBLICAR_TOKEN', '')
    if not base or len(token) < 16:
        raise RuntimeError('faltam as variaveis PUBLICAR_N8N e/ou PUBLICAR_TOKEN')
    req = urllib.request.Request(f'{base}/{caminho}', method=metodo,
                                 data=json.dumps(corpo).encode('utf-8') if corpo is not None else None,
                                 headers={'Content-Type': 'application/json', 'X-Token': token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b'{}')
        except Exception:
            return e.code, {}


def carregar_estado():
    if ESTADO.exists():
        return json.loads(ESTADO.read_text(encoding='utf-8'))
    return {'ficheiros': {}}


def guardar_estado(est):
    tmp = ESTADO.with_suffix('.tmp')
    tmp.write_text(json.dumps(est, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, ESTADO)


def ler_texto(p):
    if not p.exists():
        return ''
    b = p.read_bytes()
    for enc in ('utf-8-sig', 'cp1252'):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode('utf-8', 'replace')


def preparar_imagem(caminho):
    """Devolve (bytes, nota). JPEG normal segue igual; PNG ou foto rodada (EXIF) -> JPEG com o Pillow, se existir."""
    original = caminho.read_bytes()
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return original, None   # sem Pillow: o n8n valida e diz o que falta
    with Image.open(io.BytesIO(original)) as im:
        rodada = (im.getexif() or {}).get(0x0112, 1) not in (1, None)
        if im.format == 'JPEG' and not rodada and im.mode in ('RGB', 'L') and len(original) <= MAX_BYTES:
            return original, None
        nota = 'convertida de PNG para JPEG' if im.format == 'PNG' else ('endireitada (EXIF)' if rodada else 'recomprimida')
        im = ImageOps.exif_transpose(im)
        if im.mode in ('RGBA', 'LA', 'P'):
            im = im.convert('RGBA')
            fundo = Image.new('RGB', im.size, (255, 255, 255))
            fundo.paste(im, mask=im.split()[-1])
            im = fundo
        elif im.mode != 'RGB':
            im = im.convert('RGB')
        for q in (95, 90, 85, 80, 75):
            saida = io.BytesIO()
            im.save(saida, 'JPEG', quality=q, optimize=True)
            if saida.tell() <= MAX_BYTES:
                break
        return saida.getvalue(), nota


def ficheiros_do_par(img):
    base = str(img.with_suffix(''))
    return {'imagem': img, 'legenda': Path(base + '.txt'), 'quando': Path(base + '.quando.txt'),
            'teste': Path(base + '.teste.txt'), 'erro': Path(base + '.erro.txt')}


def impressao(par):
    h = hashlib.sha256()
    for k in ('imagem', 'legenda', 'quando', 'teste'):
        h.update(par[k].read_bytes() if par[k].exists() else b'-')
        h.update(b'\0')
    return h.hexdigest()


def mover(par, destino, texto):
    destino.mkdir(exist_ok=True)
    prefixo = dt.datetime.now().strftime('%Y-%m-%d_%H%M_')
    for k in ('imagem', 'legenda', 'quando', 'teste'):
        if par[k].exists():
            shutil.move(str(par[k]), str(destino / (prefixo + par[k].name)))
    if par['erro'].exists():
        par['erro'].unlink()
    (destino / (prefixo + par['imagem'].stem + '.resultado.txt')).write_text(texto + '\n', encoding='utf-8')


def escrever_erro(par, motivos):
    par['erro'].write_text('Esta imagem NAO foi enviada para aprovacao. Corrige e grava: o script volta a mandar sozinho.\n\n'
                           + '\n'.join('- ' + m for m in motivos) + '\n', encoding='utf-8')


def volta():
    PASTA.mkdir(parents=True, exist_ok=True)
    est = carregar_estado()
    fich = est.setdefault('ficheiros', {})

    # 1. o que foi decidido no Discord (via n8n)
    codigo, r = n8n('GET', 'publicar-estado', timeout=60)
    if codigo != 200 or not r.get('ok'):
        log(f'o n8n nao respondeu ao estado ({codigo}); tento na proxima volta')
        return
    remoto = {p['pub_id']: p for p in r.get('publicacoes', []) if p.get('pub_id')}
    for nome, e in list(fich.items()):
        info = remoto.get(e.get('pub_id'))
        if not info:
            continue
        par = ficheiros_do_par(PASTA / nome)
        fase = info.get('fase')
        if fase != e.get('fase'):
            log(f'{nome}: {e.get("fase")} -> {fase}')
            e['fase'], e['fase_em'] = fase, time.time()
        if fase == 'publicado' and par['imagem'].exists():
            mover(par, PUBLICADOS, f"Publicado: {info.get('link') or ''}\n{info.get('resultado') or ''}")
            del fich[nome]
        elif fase in ('recusado', 'teste_contentor') and par['imagem'].exists():
            mover(par, RECUSADOS, info.get('resultado') or 'Nao publicado.')
            del fich[nome]
        elif fase == 'invalido' and par['imagem'].exists() and not par['erro'].exists():
            escrever_erro(par, info.get('motivos') or ['ver o canal no Discord'])
    guardar_estado(est)

    # 2. imagens novas ou mudadas
    agora = time.time()
    presentes = set()
    for img in sorted(PASTA.iterdir()):
        if not img.is_file() or img.suffix.lower() not in IMAGENS:
            continue
        nome = img.name
        presentes.add(nome)
        par = ficheiros_do_par(img)
        tempos = [p.stat().st_mtime for k, p in par.items() if k != 'erro' and p.exists()]
        if agora - max(tempos) < ESTAVEL_S:
            continue   # ainda a ser copiado ou gravado
        if not par['legenda'].exists():
            if agora - max(tempos) > 300 and not par['erro'].exists():
                escrever_erro(par, [f'falta a legenda: cria {par["legenda"].name} (mesmo nome da imagem, .txt)'])
            continue
        imp = impressao(par)
        e = fich.get(nome)
        if e and e.get('impressao') == imp and not (e.get('fase') == 'editar' and max(tempos) > e.get('fase_em', 0)):
            continue   # igual ao que ja foi mandado
        if e and e.get('fase') in ('pendente', 'agendado', 'erro') and e.get('pub_id'):
            n8n('POST', 'publicar-entrada', {'acao': 'retirar', 'pub_id': e['pub_id'], 'motivo': 'substituido'})
        envios = (e or {}).get('envios', 0) + 1
        pub_id = hashlib.sha256(f'{nome}|{imp}|{envios}'.encode()).hexdigest()[:16]
        try:
            jpeg, nota = preparar_imagem(img)
        except Exception as ex:
            escrever_erro(par, [f'nao consegui abrir a imagem: {ex}'])
            fich[nome] = {'impressao': imp, 'pub_id': None, 'envios': envios, 'fase': 'invalido'}
            continue
        corpo = {'pub_id': pub_id, 'nome': img.stem + '.jpg' if nota and img.suffix.lower() == '.png' else nome,
                 'imagem_b64': base64.b64encode(jpeg).decode('ascii'), 'legenda': ler_texto(par['legenda']),
                 'quando': ler_texto(par['quando']).strip(), 'teste': par['teste'].exists(), 'convertido': nota}
        codigo, r = n8n('POST', 'publicar-entrada', corpo)
        if codigo != 200 or not r.get('ok'):
            log(f'{nome}: o n8n recusou ou falhou ({codigo}); tento na proxima volta')
            continue
        fich[nome] = {'impressao': imp, 'pub_id': pub_id, 'envios': envios, 'fase': r.get('estado'), 'fase_em': agora}
        if r.get('estado') == 'invalido':
            escrever_erro(par, r.get('motivos') or [])
            log(f'{nome}: invalida')
        else:
            if par['erro'].exists():
                par['erro'].unlink()
            log(f'{nome}: enviada para aprovacao{" [teste]" if corpo["teste"] else ""}')
        guardar_estado(est)

    # 3. imagens que sairam da pasta antes da decisao: fecha a aprovacao (nada e publicado)
    for nome, e in list(fich.items()):
        if nome not in presentes:
            if e.get('fase') in ('pendente', 'agendado', 'erro') and e.get('pub_id'):
                n8n('POST', 'publicar-entrada', {'acao': 'retirar', 'pub_id': e['pub_id'], 'motivo': 'retirado'})
                log(f'{nome}: saiu da pasta -> aprovacao retirada')
            del fich[nome]
    guardar_estado(est)


def avisar_erro(texto):
    """Avisa no Discord (via n8n) no maximo uma vez por hora para o mesmo erro."""
    try:
        est = carregar_estado()
        marca = hashlib.sha1(texto.encode()).hexdigest()[:12]
        ultimo = est.get('ultimo_erro') or {}
        if ultimo.get('marca') == marca and time.time() - ultimo.get('em', 0) < 3600:
            return
        n8n('POST', 'publicar-entrada', {'acao': 'erro', 'erro': texto[:1500]}, timeout=30)
        est['ultimo_erro'] = {'marca': marca, 'em': time.time()}
        guardar_estado(est)
    except Exception:
        pass


if __name__ == '__main__':
    try:
        volta()
    except Exception as ex:
        log('ERRO\n' + traceback.format_exc())
        avisar_erro(f'{type(ex).__name__}: {ex}')
        sys.exit(1)

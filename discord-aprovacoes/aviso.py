"""Discord aprovacoes - avisos simples (POST /aviso): uma mensagem com cor por nivel, campos e botoes-link.

Nao guarda estado e nao tem botoes com acao. Quem chama e que decide quando avisar (e evita repeticoes).

Corpo (JSON):
  canal      nome logico, ex. "alertas" -> CANAL_ALERTAS_ID (so os de AVISO_CANAIS, por omissao "alertas")
  titulo     obrigatorio
  descricao  opcional
  nivel      erro | aviso | ok | info (cor e icone)
  campos     [{nome, valor, inline}] (ate 15)
  links      [{texto, url}] (ate 5, so https://)
  mencionar  true -> menciona os aprovadores
  teste      true -> "[teste]" no titulo e no rodape
  rodape     opcional
"""
import asyncio
import json
import os

from aiohttp import web

import comum

NIVEIS = {'erro': (0xE74C3C, '🔴'), 'aviso': (0xF1C40F, '🟡'), 'ok': (0x2ECC71, '🟢'), 'info': (0x3498DB, 'ℹ️')}


def _canal(chave):
    permitidas = [c.strip() for c in os.environ.get('AVISO_CANAIS', 'alertas').split(',') if c.strip()]
    if chave not in permitidas:
        return None
    try:
        return comum.canal_id(chave)
    except ValueError:
        return None


def desenhar(c):
    cor, icone = NIVEIS.get(c.get('nivel'), NIVEIS['info'])
    teste = '[teste] ' if c.get('teste') else ''
    embed = {'title': comum.texto(f"{teste}{icone} {c['titulo']}", 250), 'color': cor}
    if c.get('descricao'):
        embed['description'] = comum.texto(c['descricao'], 3500)
    campos = [{'name': comum.texto(f['nome'], 250), 'value': comum.texto(f.get('valor'), 1000) or '-',
               'inline': bool(f.get('inline'))}
              for f in (c.get('campos') or [])[:15] if isinstance(f, dict) and f.get('nome')]
    if campos:
        embed['fields'] = campos
    rodape = ' · '.join(x for x in (teste.strip(), str(c.get('rodape') or '')) if x)
    if rodape:
        embed['footer'] = {'text': comum.texto(rodape, 300)}
    corpo = {'embeds': [embed], 'allowed_mentions': {'parse': [], 'users': []}}
    botoes = [{'type': 2, 'style': 5, 'label': comum.texto(l.get('texto') or 'Abrir', 80), 'url': l['url']}
              for l in (c.get('links') or [])[:5]
              if isinstance(l, dict) and str(l.get('url', '')).startswith('https://') and len(l['url']) <= 512]
    if botoes:
        corpo['components'] = [{'type': 1, 'components': botoes}]
    if c.get('mencionar'):
        corpo['content'] = ' '.join(f'<@{u}>' for u in comum.aprovadores())
        corpo['allowed_mentions'] = comum.mencoes_permitidas()
    return corpo


def enviar(c):
    m = comum.discord('POST', f"/channels/{_canal(c['canal'])}/messages", desenhar(c))
    return m['id']


def rota(autorizado, log):
    async def http_aviso(pedido):
        if not autorizado(pedido):
            log.warning('HTTP /aviso recusado de %s', pedido.remote)
            return web.json_response({'erro': 'proibido'}, status=403)
        try:
            c = json.loads(await pedido.text())
            assert isinstance(c, dict) and isinstance(c.get('titulo'), str) and c['titulo'].strip()
            assert isinstance(c.get('canal'), str)
        except Exception:
            return web.json_response({'erro': 'pedido'}, status=400)
        if not _canal(c['canal']):
            return web.json_response({'erro': 'canal desconhecido'}, status=400)
        try:
            msg = await asyncio.to_thread(enviar, c)
        except Exception as e:
            log.exception('aviso falhou (%s)', c.get('canal'))
            return web.json_response({'erro': str(e)[:200]}, status=502)
        log.info('aviso %s %s -> %s', c['canal'], comum.texto(c['titulo'], 80), msg)
        return web.json_response({'ok': True, 'msg_id': msg})
    return web.post('/aviso', http_aviso)

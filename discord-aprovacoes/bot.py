"""Discord aprovacoes - o servico: liga-se ao Discord (cliques nos botoes) e abre uma API HTTP local.

Discord:
- liga-se SEM intents (Intents.none()): nao recebe mensagens nem conteudo, so as interacoes (essas chegam sempre);
- so trata cliques cujo custom_id comeca por "<PREFIXO_BOTOES>:"; todos os outros sao ignorados.
  Por isso pode usar o token de um bot que ja tens a correr (uma segunda sessao do mesmo bot), desde que esse
  bot tambem ignore os cliques que nao sao dele. Ver o README.

HTTP (so na rede de casa: API_ESCUTA, com token X-Token e so dos IPs de API_ORIGENS):
  POST /aviso                         aviso simples (aviso.py)
  POST /aprovacao                     pedido de aprovacao com botoes (aprovacao.py)
  GET  /aprovacao/{id}                estado de uma aprovacao
  POST /aprovacao/{id}/reservar       passa a "a executar" uma unica vez
  POST /aprovacao/{id}/atualizar      muda estado, nota, resultado, botoes, ligacoes
  POST /aprovacao/{id}/chave          chave de 15 min para servir a imagem
  GET  /aprovacao-imagem/{chave}      bytes da imagem (so com token)
  GET  /aprovacoes?origem=&estado=&fase=&desde=
  GET  /saude                         {"ok": true} (sem token; nao diz nada)
"""
import asyncio
import json
import logging
import os

import discord
from aiohttp import web

import aprovacao
import aviso
import comum

comum.carregar_ambiente()
log = comum.registo('bot')


def autorizado(pedido):
    return comum.origem_permitida(pedido.remote) and comum.token_certo(pedido.headers.get('X-Token', ''))


def _com_db(funcao, *args):
    db = aprovacao.ligar()
    try:
        return funcao(db, *args)
    finally:
        db.close()


async def _http(pedido, funcao, *args, ler_json=True):
    if not autorizado(pedido):
        log.warning('HTTP recusado de %s (%s)', pedido.remote, pedido.path)
        return web.json_response({'erro': 'proibido'}, status=403)
    try:
        corpo = json.loads(await pedido.text() or '{}') if ler_json else None
        if ler_json and not isinstance(corpo, dict):
            raise ValueError
    except Exception:
        return web.json_response({'erro': 'pedido'}, status=400)
    try:
        r = await asyncio.to_thread(_com_db, funcao, *args, *([corpo] if ler_json else []))
    except KeyError:
        return web.json_response({'erro': 'nao existe'}, status=404)
    except ValueError as e:
        return web.json_response({'erro': str(e)}, status=400)
    except Exception as e:
        log.exception('erro em %s', getattr(funcao, '__name__', '?'))
        return web.json_response({'erro': f'{type(e).__name__}: {e}'[:300]}, status=500)
    return web.json_response(r)


async def http_criar(pedido):
    return await _http(pedido, aprovacao.criar)


async def http_obter(pedido):
    def obter(db, aid):
        a = aprovacao.obter(db, aid)
        if not a:
            raise KeyError(aid)
        return aprovacao.resumo(a)
    return await _http(pedido, obter, pedido.match_info['aid'], ler_json=False)


async def http_atualizar(pedido):
    return await _http(pedido, aprovacao.atualizar, pedido.match_info['aid'])


async def http_reservar(pedido):
    aid = pedido.match_info['aid']
    return await _http(pedido, lambda db, c: aprovacao.reservar(db, aid, c.get('fases')))


async def http_chave(pedido):
    return await _http(pedido, aprovacao.criar_chave, pedido.match_info['aid'], ler_json=False)


async def http_listar(pedido):
    q = pedido.query
    return await _http(pedido, lambda db: aprovacao.listar(db, q.get('origem', ''), q.get('estado'), q.get('fase'),
                                                           q.get('desde')), ler_json=False)


async def http_imagem(pedido):
    if not autorizado(pedido):
        return web.json_response({'erro': 'proibido'}, status=403)
    r = await asyncio.to_thread(_com_db, aprovacao.imagem_por_chave, pedido.match_info['chave'])
    if not r:
        return web.json_response({'erro': 'nao existe'}, status=404)
    return web.Response(body=r[0], content_type=r[1], headers={'Cache-Control': 'no-store'})


async def http_saude(pedido):
    return web.json_response({'ok': True})


async def _callback(aid):
    ok = await asyncio.to_thread(_com_db, aprovacao.enviar_callback, aid)
    log.info('callback da aprovacao %s -> %s', aid, 'ok' if ok else 'falhou (tenta outra vez)')


async def ciclo():
    """De minuto a minuto: volta a enviar os callbacks que falharam; limpa chaves e imagens antigas."""
    while True:
        await asyncio.sleep(60)
        try:
            for aid in await asyncio.to_thread(_com_db, aprovacao.por_enviar):
                await _callback(aid)
            await asyncio.to_thread(_com_db, aprovacao.limpar)
        except Exception:
            log.exception('ciclo')


class Cliente(discord.Client):
    async def setup_hook(self):
        escuta = os.environ.get('API_ESCUTA', '127.0.0.1:8790')
        app = web.Application(client_max_size=16 * 1024 * 1024)   # pedidos com imagem (ate 10 MB em base64)
        app.add_routes([web.get('/saude', http_saude), aviso.rota(autorizado, log),
                        web.post('/aprovacao', http_criar), web.get('/aprovacao/{aid}', http_obter),
                        web.post('/aprovacao/{aid}/atualizar', http_atualizar),
                        web.post('/aprovacao/{aid}/reservar', http_reservar),
                        web.post('/aprovacao/{aid}/chave', http_chave),
                        web.get('/aprovacao-imagem/{chave}', http_imagem),
                        web.get('/aprovacoes', http_listar)])
        corredor = web.AppRunner(app, access_log=None)
        await corredor.setup()
        anfitriao, porta = escuta.rsplit(':', 1)
        await web.TCPSite(corredor, anfitriao, int(porta)).start()
        asyncio.get_running_loop().create_task(ciclo())
        log.info('API em %s', escuta)

    async def on_ready(self):
        log.info('ligado ao Discord como %s', self.user)

    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get('custom_id', '')
        if not cid.startswith(comum.prefixo_botoes() + ':'):
            return   # nao e nosso: deixar para o outro bot (nao responder!)
        try:
            _, botao, aid = cid.split(':', 2)
        except ValueError:
            return
        quem = interaction.user.id
        ok, texto = await asyncio.to_thread(_com_db, aprovacao.decidir, aid, botao, quem)
        log.info('clique %s na aprovacao %s por %s -> %s', botao, aid, quem, ok)
        await interaction.response.send_message(texto, ephemeral=True)   # so quem carregou ve a resposta
        if not ok:
            return
        try:
            await asyncio.to_thread(_com_db, lambda db: aprovacao.publicar(aprovacao.obter(db, aid)))
        except Exception:
            log.exception('falhou editar a mensagem da aprovacao %s', aid)
        await _callback(aid)


def main():
    cliente = Cliente(intents=discord.Intents.none())
    cliente.run(os.environ['DISCORD_BOT_TOKEN'], log_level=logging.WARNING)   # avisos do discord.py vao para o journal


if __name__ == '__main__':
    main()

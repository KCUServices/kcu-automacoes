"""Discord aprovacoes - ferramenta local (corre no mesmo servidor, como root ou como o utilizador do servico).

  ferramenta.py listar [origem]             aprovacoes guardadas (sem imagens nem contexto)
  ferramenta.py simular <apr-...> <botao>   o mesmo decidir() do botao + o mesmo callback (para testes)
  ferramenta.py reenviar <apr-...>          volta a tentar o callback (depois de 30 falhas)
  ferramenta.py aviso-teste <canal>         manda um aviso [teste] para o canal (ex.: alertas)
  ferramenta.py aprovacao-teste <canal>     cria uma aprovacao [teste] com 2 botoes, sem callback (modo puxar)
"""
import secrets
import sys

import aprovacao
import aviso
import comum


def main():
    comum.carregar_ambiente()
    log = comum.registo('ferramenta')
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    db = aprovacao.ligar()
    cmd = a[0]
    if cmd == 'listar':
        sql, args = 'SELECT id, origem, canal, estado, decisao, simulado, callback_estado, msg_id FROM aprovacoes', []
        if len(a) > 1:
            sql, args = sql + ' WHERE origem=?', [a[1]]
        for r in db.execute(sql + ' ORDER BY criado_em', args):
            print(dict(r))
    elif cmd == 'simular' and len(a) == 3:
        ok, texto = aprovacao.decidir(db, a[1], a[2], 'simulado', simulado=True)
        log.info('clique SIMULADO %s na aprovacao %s -> %s', a[2], a[1], ok)
        print(ok, texto)
        if ok:
            aprovacao.publicar(aprovacao.obter(db, a[1]))
            if aprovacao.por_enviar(db):
                print('callback', 'ok' if aprovacao.enviar_callback(db, a[1]) else 'falhou (o bot tenta de minuto a minuto)')
    elif cmd == 'reenviar' and len(a) == 2:
        db.execute("UPDATE aprovacoes SET callback_estado='por_enviar', callback_tentativas=0 WHERE id=? AND estado='decidido'",
                   (a[1],))
        db.commit()
        print('callback', 'ok' if aprovacao.enviar_callback(db, a[1]) else 'falhou')
    elif cmd == 'aviso-teste' and len(a) == 2:
        print('mensagem', aviso.enviar({'canal': a[1], 'titulo': 'Aviso de teste', 'nivel': 'ok', 'teste': True,
                                       'descricao': 'Se estás a ler isto, o serviço consegue escrever neste canal.'}))
    elif cmd == 'aprovacao-teste' and len(a) == 2:
        r = aprovacao.criar(db, {
            'id': 'apr-teste-' + secrets.token_hex(4), 'origem': 'teste', 'canal': a[1], 'teste': True,
            'titulo': 'Aprovação de teste', 'descricao': 'Carrega num botão. Nada acontece além de ficar registado.',
            'botoes': [{'id': 'sim', 'rotulo': 'Sim', 'emoji': '✅', 'estilo': 'sucesso'},
                       {'id': 'nao', 'rotulo': 'Não', 'emoji': '❌', 'estilo': 'perigo'}]})
        print(r)
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()

"""Melhorias com IA - de uma issue (ou ideia nas Discussions) no GitHub ate um pull request aprovado no Discord.

Cada volta (tarefa agendada de 5 em 5 min, so quando o PC esta ligado):
  1. executa as decisoes que ja foram tomadas no Discord (Aprovar / Recusar / Ignorar);
  2. atualiza o estado do CI dos pull requests em aberto na mensagem do Discord;
  3. procura issues e ideias novas nos repositorios da configuracao;
  4. analisa UMA de cada vez com o Claude Code sem interface (claude -p), num git worktree separado e com
     ferramentas limitadas; se fizer sentido, prepara um ramo + pull request e pede aprovacao no Discord.

Nada entra no ramo principal sem o botao Aprovar. Comentarios publicos so depois da decisao.
O Claude Code usa o login da TUA subscricao: as variaveis ANTHROPIC_* sao tiradas do ambiente antes de o chamar,
para nunca gastar uma chave de API paga por engano.

  python melhorias.py            uma volta
  python melhorias.py --estado   mostra o estado local
  python melhorias.py --verificar   confirma a configuracao (programas, login do gh, servico de aprovacoes)
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AQUI = Path(__file__).resolve().parent
CFG = json.loads((AQUI / 'config.json').read_text(encoding='utf-8'))
ORG = CFG['org']
BASE = CFG.get('ramo_base', 'main')
APR = CFG['aprovacoes']
ESTADO = AQUI / 'estado.json'
LOG = AQUI / 'logs' / 'melhorias.log'
TRABALHO = AQUI / 'trabalho'
TRANCA = AQUI / 'volta.lock'
SEM_JANELA = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def programa(nome):
    """Caminho do programa: o da configuracao, ou o que estiver no PATH."""
    p = (CFG.get('programas') or {}).get(nome) or nome
    return shutil.which(p) or p


# ------------------------------------------------------------------ utilitarios

def log(msg):
    LOG.parent.mkdir(exist_ok=True)
    linha = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(linha + '\n')
    if LOG.stat().st_size > 2_000_000:
        shutil.move(str(LOG), str(LOG.with_suffix('.log.1')))
    try:
        print(linha)
    except Exception:
        pass


def correr(cmd, cwd=None, entrada=None, timeout=300, verificar=True, env=None):
    r = subprocess.run(cmd, cwd=cwd, input=entrada, capture_output=True, timeout=timeout, env=env,
                       text=True, encoding='utf-8', errors='replace', creationflags=SEM_JANELA)
    if verificar and r.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} {' '.join(cmd[1:4])}: {(r.stderr or r.stdout).strip()[:600]}")
    return r


def gh(*args, verificar=True):
    return correr([programa('gh'), *args], verificar=verificar).stdout


def gh_json(*args):
    return json.loads(gh(*args) or 'null')


def git(cwd, *args, verificar=True, timeout=300):
    # credencial do gh so para este comando (nao fica guardada nenhuma credencial no git)
    base = [programa('git'), '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential']
    return correr(base + list(args), cwd=cwd, verificar=verificar, timeout=timeout)


def api(metodo, caminho, corpo=None):
    """Fala com o servico Discord aprovacoes (rede de casa, token no cabecalho X-Token)."""
    token = os.environ.get('APROVACOES_TOKEN', '')
    if len(token) < 32:
        raise RuntimeError('falta a variavel de ambiente APROVACOES_TOKEN')
    req = urllib.request.Request(APR['url'].rstrip('/') + caminho, method=metodo,
                                 data=json.dumps(corpo, ensure_ascii=False).encode('utf-8') if corpo is not None else None,
                                 headers={'Content-Type': 'application/json', 'X-Token': token})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b'null')
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'aprovacoes {metodo} {caminho}: {e.code} {e.read()[:200]!r}')


# ------------------------------------------------------------------ privacidade

_PADROES = None


def padroes_privados():
    """Uma expressao regular por linha (linhas vazias e # ignoradas). Sem o ficheiro, nada e publicado."""
    global _PADROES
    if _PADROES is None:
        f = AQUI / CFG.get('padroes_privados', 'padroes_privados.txt')
        if not f.exists():
            raise RuntimeError(f'falta {f.name}: copia padroes_privados.exemplo.txt e acrescenta os teus dados')
        linhas = [l.strip() for l in f.read_text(encoding='utf-8').splitlines() if l.strip() and not l.lstrip().startswith('#')]
        _PADROES = re.compile('|'.join(f'(?:{l})' for l in linhas), re.I) if linhas else re.compile(r'(?!x)x')
    return _PADROES


def texto_publico_ok(*textos):
    return not any(padroes_privados().search(t or '') for t in textos)


# ------------------------------------------------------------------ estado local

def carregar_estado():
    if ESTADO.exists():
        return json.loads(ESTADO.read_text(encoding='utf-8'))
    return {'itens': {}, 'analises_por_dia': {}}


def guardar_estado(est):
    tmp = ESTADO.with_suffix('.tmp')
    tmp.write_text(json.dumps(est, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, ESTADO)


def alerta_id(repo, origem, numero):
    nome = re.sub(r'[^a-z0-9]+', '-', repo.lower()).strip('-')[:28] or 'repo'
    return f"apr-mel-{nome}-{'i' if origem == 'issue' else 'd'}{numero}"


# ------------------------------------------------------------------ 1. descobrir pedidos novos

Q_DISCUSSOES = '''query($org:String!){ organization(login:$org){ repositories(first:50){ nodes{ name
  discussions(first:15, orderBy:{field:CREATED_AT, direction:DESC}){ nodes{ id number title url body createdAt
    author{ login } category{ slug } } } } } } }'''


def descobrir(est):
    inicio = CFG['inicio']
    for repo in CFG['repos']:
        for i in gh_json('api', '-X', 'GET', f'repos/{ORG}/{repo}/issues', '-f', 'state=open', '-f', f'since={inicio}',
                         '-f', 'per_page=50'):
            if 'pull_request' in i or i['created_at'] < inicio or (i.get('user') or {}).get('type') == 'Bot':
                continue
            chave = f"{repo}#i{i['number']}"
            if chave not in est['itens']:
                est['itens'][chave] = novo_item(repo, 'issue', i['number'], i['node_id'], i['html_url'], i['title'],
                                                i.get('body') or '', i['user']['login'])
                log(f'novo pedido: {chave}')
    if not CFG.get('categorias_discussao'):
        return
    dados = gh_json('api', 'graphql', '-f', f'query={Q_DISCUSSOES}', '-f', f'org={ORG}')
    for r in dados['data']['organization']['repositories']['nodes']:
        if r['name'] not in CFG['repos']:
            continue
        for d in r['discussions']['nodes']:
            if d['category']['slug'] not in CFG['categorias_discussao'] or d['createdAt'] < inicio:
                continue
            chave = f"{r['name']}#d{d['number']}"
            if chave not in est['itens']:
                est['itens'][chave] = novo_item(r['name'], 'discussao', d['number'], d['id'], d['url'], d['title'],
                                                d.get('body') or '', (d.get('author') or {}).get('login', '?'))
                log(f'nova ideia: {chave}')


def novo_item(repo, origem, numero, no_id, url, titulo, corpo, autor):
    return {'repo': repo, 'origem': origem, 'numero': numero, 'no_id': no_id, 'url': url, 'titulo': titulo,
            'corpo': corpo, 'autor': autor, 'alerta_id': alerta_id(repo, origem, numero), 'estado': 'novo',
            'tentativas': 0, 'criado': dt.datetime.now().isoformat(timespec='seconds'),
            'teste': titulo.lower().startswith('[teste]')}


# ------------------------------------------------------------------ 2. analisar e preparar o PR

def comentarios_de(item):
    if item['origem'] == 'issue':
        cs = gh_json('api', f"repos/{ORG}/{item['repo']}/issues/{item['numero']}/comments")
        return '\n---\n'.join(f"{c['user']['login']}: {c['body']}" for c in cs[:20]) or '(nenhum)'
    q = 'query($id:ID!){ node(id:$id){ ... on Discussion { comments(first:20){ nodes{ author{login} body } } } } }'
    d = gh_json('api', 'graphql', '-f', f'query={q}', '-f', f"id={item['no_id']}")
    cs = d['data']['node']['comments']['nodes']
    return '\n---\n'.join(f"{(c.get('author') or {}).get('login', '?')}: {c['body']}" for c in cs) or '(nenhum)'


def preparar_worktree(item):
    pasta_repo = CFG['repos'][item['repo']]
    ramo = f"melhoria/{'issue' if item['origem'] == 'issue' else 'ideia'}-{item['numero']}"
    wt = TRABALHO / item['alerta_id']
    git(pasta_repo, 'fetch', '-q', 'origin', BASE)
    if wt.exists():
        git(pasta_repo, 'worktree', 'remove', '--force', str(wt), verificar=False)
        shutil.rmtree(wt, ignore_errors=True)
    git(pasta_repo, 'worktree', 'prune')
    git(pasta_repo, 'worktree', 'add', '-q', '-B', ramo, str(wt), f'origin/{BASE}')
    return ramo, wt


def limpar_worktree(item):
    wt = TRABALHO / item['alerta_id']
    pasta_repo = CFG['repos'][item['repo']]
    if wt.exists():
        git(pasta_repo, 'worktree', 'remove', '--force', str(wt), verificar=False)
        shutil.rmtree(wt, ignore_errors=True)
    git(pasta_repo, 'worktree', 'prune', verificar=False)
    if item.get('ramo'):
        git(pasta_repo, 'branch', '-D', item['ramo'], verificar=False)


def chamar_agente(item, wt):
    """Claude Code sem interface. So pode ler e editar ficheiros do worktree: sem terminal, sem web, sem MCP."""
    modelo = (AQUI / 'prompt_analise.md').read_text(encoding='utf-8')
    prompt = modelo.format(repo=item['repo'], numero=item['numero'], autor=item['autor'], titulo=item['titulo'],
                           corpo=item['corpo'][:8000] or '(vazio)', comentarios=comentarios_de(item)[:6000],
                           tipo_texto='issue' if item['origem'] == 'issue' else 'ideia nas Discussions')
    cmd = [programa('claude'), '-p', '--output-format', 'json', '--model', CFG.get('modelo', 'sonnet'),
           '--permission-mode', 'acceptEdits', '--allowedTools', 'Read,Edit,Write,Glob,Grep',
           '--disallowedTools', 'Bash,WebFetch,WebSearch,Task,NotebookEdit',
           '--strict-mcp-config', '--setting-sources', 'project', '--max-turns', str(CFG.get('max_turnos', 40))]
    env_limpo = {k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')}   # nunca usar chave paga
    env_limpo.pop('APROVACOES_TOKEN', None)   # o agente nao precisa de nenhum segredo
    r = subprocess.run(cmd, cwd=wt, input=prompt, capture_output=True, timeout=CFG.get('tempo_max_agente_s', 1500),
                       text=True, encoding='utf-8', errors='replace', creationflags=SEM_JANELA, env=env_limpo)
    try:
        saida = json.loads(r.stdout)
    except Exception:
        raise RuntimeError(f'Claude Code nao respondeu em JSON (codigo {r.returncode}): {(r.stderr or r.stdout)[:400]}')
    if saida.get('is_error'):
        raise RuntimeError(f"Claude Code devolveu erro: {str(saida.get('result'))[:400]}")
    texto = saida.get('result') or ''
    blocos = re.findall(r'```json\s*(\{.*?\})\s*```', texto, re.S) or re.findall(r'(\{.*\})', texto, re.S)
    if not blocos:
        raise RuntimeError(f'resposta sem JSON: {texto[:300]}')
    res = json.loads(blocos[-1])
    log(f"agente {item['alerta_id']}: {res.get('veredicto')} / {res.get('categoria')} ({saida.get('num_turns')} turnos)")
    return res


def verificar_diff(wt):
    """Regras antes de qualquer push: devolve (ficheiros, estatistica, problema ou None)."""
    git(wt, 'add', '-A')
    nomes = [l for l in git(wt, 'diff', '--cached', '--name-only').stdout.splitlines() if l.strip()]
    num = [l.split('\t') for l in git(wt, 'diff', '--cached', '--numstat').stdout.splitlines() if l.strip()]
    mais = sum(int(a) for a, _, _ in num if a != '-')
    menos = sum(int(b) for _, b, _ in num if b != '-')
    est = f"{len(nomes)} ficheiro(s), +{mais} / -{menos}"
    if not nomes:
        return nomes, est, 'sem alteracoes'
    for n in nomes:
        nl = n.lower()
        if any(nl.startswith(p.lower()) or ('/' + p.lower()) in ('/' + nl) for p in CFG.get('caminhos_proibidos', [])):
            return nomes, est, f'mexe num caminho proibido: {n}'
    if any(a == '-' for a, _, _ in num):
        return nomes, est, 'tem ficheiros binarios'
    if len(nomes) > CFG.get('max_ficheiros', 15) or mais + menos > CFG.get('max_linhas', 400):
        return nomes, est, f'alteracao grande demais ({est})'
    for linha in git(wt, 'diff', '--cached', '-U0').stdout.splitlines():
        if linha.startswith('+') and not linha.startswith('+++') and padroes_privados().search(linha):
            return nomes, est, 'o diff tem dados privados (padroes_privados.txt)'
    return nomes, est, None


def pedir_aprovacao(item, rel):
    """Cria a mensagem com botoes no Discord (modo puxar: sem callback; a decisao e lida na volta seguinte)."""
    teste = '[teste] ' if item['teste'] else ''
    icone = '💡' if item['origem'] == 'discussao' else '🐞'
    campos = [{'nome': 'O que foi pedido', 'valor': rel.get('resumo_pedido') or '-'},
              {'nome': 'O que encontrei', 'valor': rel.get('analise') or '-'}]
    ligacoes = [{'rotulo': 'Ver pedido', 'url': item['url']}]
    if rel['tipo'] == 'pr':
        alt = (rel.get('alteracao') or '') + '\n\n**Ficheiros:** ' + ', '.join(f'`{f}`' for f in rel['ficheiros'][:15])
        alt += f"\n**Diff:** {rel['estatisticas']}"
        campos += [{'nome': 'Alteração proposta', 'valor': alt},
                   {'nome': 'Pull request', 'valor': f"[#{rel['pr_numero']}]({rel['pr_url']}) · ramo `{rel['ramo']}`"},
                   {'nome': 'Riscos', 'valor': rel.get('riscos') or '-'},
                   {'nome': 'CI', 'valor': rel.get('ci') or '🟡 A correr…'}]
        botoes = [{'id': 'aprovar', 'rotulo': 'Aprovar', 'emoji': '✅', 'estilo': 'sucesso',
                   'resposta': 'Aprovado: junto ao ramo principal na próxima volta, se o CI estiver verde.'},
                  {'id': 'recusar', 'rotulo': 'Recusar', 'emoji': '❌', 'estilo': 'perigo',
                   'resposta': 'Recusado: fecho o PR e deixo um comentário educado na próxima volta.'}]
        ligacoes.append({'rotulo': 'Ver PR', 'url': rel['pr_url']})
    else:
        if rel.get('sugestao_resposta'):
            campos.append({'nome': 'Sugestão de resposta (não publicada)', 'valor': rel['sugestao_resposta']})
        if rel.get('nota_tecnica'):
            campos.append({'nome': 'Nota', 'valor': rel['nota_tecnica']})
        botoes = [{'id': 'ignorar', 'rotulo': 'Fechar alerta', 'emoji': '🙈', 'estilo': 'secundario',
                   'resposta': 'Alerta fechado. Nada foi publicado; se quiseres responder, usa «Ver pedido».'}]
    item['relatorio'] = rel   # guardado antes: se o servico estiver em baixo, a volta seguinte so repete este passo
    item['estado'] = 'por_publicar'
    api('POST', '/aprovacao', {
        'id': item['alerta_id'], 'origem': APR.get('origem', 'melhorias'), 'canal': APR.get('canal', 'melhorias'),
        'titulo': f"{teste}{icone} {item['repo']} #{item['numero']} — {item['titulo']}",
        'descricao': f"**Veredicto:** {rel.get('veredicto_texto', '-')}\n**Tipo:** "
                     f"{'ideia nas Discussions' if item['origem'] == 'discussao' else 'issue'}",
        'campos': campos, 'botoes': botoes, 'ligacoes': ligacoes, 'mencionar': True, 'teste': item['teste'],
        'contexto': {'repo': item['repo'], 'numero': item['numero'], 'tipo': rel['tipo']},
        'rodape': 'melhorias com IA'})
    item['estado'] = 'alertado'
    item['tipo'] = rel['tipo']
    log(f"aprovacao pedida {item['alerta_id']} ({rel['tipo']})")


def analisar(est, item):
    hoje = dt.date.today().isoformat()
    feitas = est['analises_por_dia'].get(hoje, 0)
    item['tentativas'] += 1
    if feitas >= CFG.get('max_analises_dia', 6):
        return pedir_aprovacao(item, {'tipo': 'info', 'veredicto_texto': '⏸️ Não analisado (limite diário atingido)',
                                      'resumo_pedido': item['titulo'], 'analise': '-',
                                      'nota_tecnica': 'Proteção contra spam: vê o pedido tu mesmo.'})
    est['analises_por_dia'] = {hoje: feitas + 1}
    ramo, wt = preparar_worktree(item)
    item['ramo'] = ramo
    res = chamar_agente(item, wt)
    rel = {'resumo_pedido': res.get('resumo_pedido'), 'analise': res.get('analise'), 'riscos': res.get('riscos'),
           'sugestao_resposta': res.get('sugestao_resposta')}
    nomes, estat, problema = verificar_diff(wt)

    if res.get('veredicto') == 'corrigir' and problema is None:
        titulo = (res.get('titulo_pr') or '').strip()[:100] or f"Correção pedida em #{item['numero']}"
        msg = (res.get('mensagem_commit') or titulo).strip()
        ref = f"#{item['numero']}" if item['origem'] == 'issue' else f"a ideia #{item['numero']} (Discussions)"
        corpo = (f"Proposta preparada automaticamente para {ref}.\n\n**O que muda:** {res.get('alteracao')}\n\n"
                 f"**Riscos:** {res.get('riscos')}\n\nFica à espera de revisão: nada entra no `{BASE}` sem aprovação.")
        if not texto_publico_ok(titulo, msg, corpo):
            problema = 'o titulo/texto do PR tinha dados privados'
        else:
            autor = CFG.get('autor_commits') or {}
            ident = (['-c', f"user.name={autor['nome']}", '-c', f"user.email={autor['email']}"]
                     if autor.get('nome') and autor.get('email') else [])
            git(wt, *ident, 'commit', '-q', '-m', msg)
            p = git(wt, 'push', '--force', '-u', 'origin', ramo, verificar=False, timeout=180)
            if p.returncode != 0:
                problema = 'o push falhou (um hook pre-push bloqueou?): ' + (p.stdout + p.stderr)[-300:]
            else:
                f = TRABALHO / f"{item['alerta_id']}.pr.md"
                f.write_text(corpo, encoding='utf-8')
                existente = gh_json('pr', 'list', '--repo', f"{ORG}/{item['repo']}", '--head', ramo, '--state', 'open',
                                    '--json', 'url')
                if existente:   # volta repetida depois de uma falha: reaproveita o PR
                    url = existente[0]['url']
                    gh('pr', 'edit', url, '--title', titulo, '--body-file', str(f))
                else:
                    url = gh('pr', 'create', '--repo', f"{ORG}/{item['repo']}", '--base', BASE, '--head', ramo,
                             '--title', titulo, '--body-file', str(f)).strip().splitlines()[-1]
                f.unlink()
                pr = gh_json('pr', 'view', url, '--json', 'number,url,headRefOid')
                item.update(pr_numero=pr['number'], pr_url=pr['url'], sha=pr['headRefOid'], ci_texto='🟡 A correr…')
                rel.update(tipo='pr', veredicto_texto=f"🛠️ Válido ({res.get('categoria')}): preparei uma alteração",
                           alteracao=res.get('alteracao'), ficheiros=nomes, estatisticas=estat, pr_numero=pr['number'],
                           pr_url=pr['url'], ramo=ramo, ci=item['ci_texto'])
                log(f"PR criado {pr['url']}")
                return pedir_aprovacao(item, rel)

    nota = None
    if res.get('veredicto') == 'corrigir':
        nota = f'O agente quis alterar, mas a alteração foi descartada ({problema}). Nada foi publicado.'
    rel.update(tipo='info', nota_tecnica=nota,
               veredicto_texto='⚠️ Alteração descartada' if nota else f"💬 Não acionável ({res.get('categoria')})")
    limpar_worktree(item)
    return pedir_aprovacao(item, rel)


# ------------------------------------------------------------------ 3. CI

def estado_ci(repo, sha):
    runs = gh_json('api', f'repos/{ORG}/{repo}/commits/{sha}/check-runs', '-X', 'GET', '-f', 'per_page=50')['check_runs']
    if not runs:
        return 'a_correr', '🟡 À espera que o GitHub Actions arranque…'
    if any(r['status'] != 'completed' for r in runs):
        return 'a_correr', '🟡 A correr…'
    maus = sorted({r['name'] for r in runs if r['conclusion'] not in ('success', 'skipped', 'neutral')})
    if maus:
        return 'falhou', '🔴 Falhou: ' + ', '.join(maus)
    return 'verde', '🟢 Verde (' + ', '.join(sorted({r['name'] for r in runs})) + ')'


def atualizar_ci(est):
    for item in est['itens'].values():
        if item['estado'] not in ('alertado', 'a_juntar') or not item.get('sha'):
            continue
        _, texto = estado_ci(item['repo'], item['sha'])
        if texto != item.get('ci_texto'):
            api('POST', f"/aprovacao/{item['alerta_id']}/atualizar", {'campos_extra': [{'nome': 'CI', 'valor': texto}]})
            item['ci_texto'] = texto
            log(f"CI {item['alerta_id']}: {texto}")


# ------------------------------------------------------------------ 4. decisoes

def fechar_alerta(item, resultado, cor):
    api('POST', f"/aprovacao/{item['alerta_id']}/atualizar", {'estado': 'concluido', 'resultado': resultado, 'cor': cor})


def reabrir(item, nota):
    api('POST', f"/aprovacao/{item['alerta_id']}/atualizar", {'estado': 'pendente', 'nota': nota, 'cor': 'laranja'})
    item['estado'] = 'alertado'


def comentar(item, texto):
    if not texto_publico_ok(texto):
        raise RuntimeError('o comentario tinha dados privados; nao publicado')
    if item['origem'] == 'issue':
        gh('api', f"repos/{ORG}/{item['repo']}/issues/{item['numero']}/comments", '-f', f'body={texto}')
    else:
        q = 'mutation($id:ID!,$b:String!){ addDiscussionComment(input:{discussionId:$id, body:$b}){ comment{ id } } }'
        gh('api', 'graphql', '-f', f'query={q}', '-f', f"id={item['no_id']}", '-f', f'b={texto}')


def fechar_issue(item, motivo):
    if item['origem'] == 'issue':
        gh('api', '-X', 'PATCH', f"repos/{ORG}/{item['repo']}/issues/{item['numero']}", '-f', 'state=closed',
           '-f', f'state_reason={motivo}')


def juntar(item):
    """Aprovado: so junta com o CI verde. Devolve True quando terminou (juntou ou desistiu)."""
    repo, n = item['repo'], item['pr_numero']
    pr = gh_json('api', f'repos/{ORG}/{repo}/pulls/{n}')
    if pr['state'] == 'closed' and not pr['merged']:
        fechar_alerta(item, '⚠️ O PR já estava fechado: nada feito.', 'laranja')
        return True
    if not pr['merged']:
        sha = pr['head']['sha']
        item['sha'] = sha
        ci, texto = estado_ci(repo, sha)
        if ci == 'a_correr':
            api('POST', f"/aprovacao/{item['alerta_id']}/atualizar",
                {'estado': 'a_executar', 'nota': f'✅ Aprovado — à espera do CI para juntar ({texto}).'})
            item['estado'] = 'a_juntar'
            return False
        if ci != 'verde':
            reabrir(item, f'Aprovação recebida, mas o CI não está verde ({texto}): NÃO juntei. Corrige o PR ou recusa.')
            return False
        r = correr([programa('gh'), 'pr', 'merge', str(n), '--repo', f'{ORG}/{repo}', '--squash', '--delete-branch',
                    '--match-head-commit', sha], verificar=False)
        if r.returncode != 0:
            reabrir(item, 'O GitHub recusou juntar (conflito ou regras do ramo): ' + (r.stderr or r.stdout)[-300:])
            return False
    comentar(item, CFG.get('textos', {}).get('aprovado', 'Obrigado pelo pedido! 🙌 A alteração foi revista e já está '
                                                         'no ramo principal (#{pr}).').replace('{pr}', str(n)))
    fechar_issue(item, 'completed')
    limpar_worktree(item)
    fechar_alerta(item, f'✅ Aprovado e juntado (PR #{n}). Comentário de agradecimento publicado.', 'verde')
    return True


def recusar(item):
    repo, n = item['repo'], item['pr_numero']
    gh('api', '-X', 'PATCH', f'repos/{ORG}/{repo}/pulls/{n}', '-f', 'state=closed')
    gh('api', '-X', 'DELETE', f"repos/{ORG}/{repo}/git/refs/heads/{item['ramo']}", verificar=False)
    comentar(item, CFG.get('textos', {}).get('recusado', 'Obrigado pelo pedido! Analisámos e, para já, não vamos avançar '
                                                         'com esta alteração. Se tiveres mais detalhes, comenta aqui.'))
    fechar_issue(item, 'not_planned')
    limpar_worktree(item)
    fechar_alerta(item, f'❌ Recusado: PR #{n} fechado, ramo apagado, comentário publicado.', 'vermelho')


def processar_decisoes(est):
    por_alerta = {i['alerta_id']: i for i in est['itens'].values()}
    # aprovados que ficaram a espera do CI numa volta anterior
    for item in por_alerta.values():
        if item['estado'] == 'a_juntar':
            try:
                if juntar(item):
                    item['estado'] = 'concluido'
            except Exception as e:
                log(f"ERRO a juntar {item['alerta_id']}: {e}")
            guardar_estado(est)
    for d in api('GET', '/aprovacoes?' + urllib.parse.urlencode({'origem': APR.get('origem', 'melhorias'), 'estado': 'decidido'})):
        item = por_alerta.get(d['id'])
        if not item:
            log(f"decisao para um alerta que nao conheco: {d['id']}")
            continue
        if not api('POST', f"/aprovacao/{d['id']}/reservar", {}).get('ok'):
            continue   # outra volta ja pegou nela
        log(f"decisao {d['decisao']} em {d['id']}{' (simulada)' if d.get('simulado') else ''}")
        try:
            if d['decisao'] == 'aprovar':
                item['estado'] = 'concluido' if juntar(item) else item['estado']
            elif d['decisao'] == 'recusar':
                recusar(item)
                item['estado'] = 'concluido'
            else:
                limpar_worktree(item)
                fechar_alerta(item, '🙈 Fechado — nada foi publicado.', 'cinzento')
                item['estado'] = 'concluido'
            item['decisao'] = d['decisao']
        except Exception as e:
            log(f"ERRO a executar {d['decisao']} em {d['id']}: {e}\n{traceback.format_exc()}")
            try:
                reabrir(item, f'Erro ao executar ({str(e)[:300]}); nada mais foi feito. Podes tentar outra vez.')
            except Exception:
                pass
        guardar_estado(est)


# ------------------------------------------------------------------ volta principal

def main():
    if TRANCA.exists() and time.time() - TRANCA.stat().st_mtime < 3 * 3600:
        return   # outra volta ainda a correr (a analise pode demorar)
    TRANCA.write_text(str(os.getpid()))
    try:
        TRABALHO.mkdir(exist_ok=True)
        est = carregar_estado()
        for passo in (processar_decisoes, atualizar_ci, descobrir):
            try:
                passo(est)
            except Exception as e:
                log(f'ERRO em {passo.__name__}: {e}')
            guardar_estado(est)
        for item in est['itens'].values():   # pedidos de aprovacao que nao chegaram ao servico na volta anterior
            if item['estado'] == 'por_publicar' and item.get('relatorio'):
                try:
                    pedir_aprovacao(item, item['relatorio'])
                except Exception as e:
                    log(f"ERRO a pedir aprovacao de {item['alerta_id']}: {e}")
        guardar_estado(est)
        for item in est['itens'].values():   # uma analise por volta
            if item['estado'] == 'novo':
                try:
                    analisar(est, item)
                except Exception as e:
                    log(f"ERRO a analisar {item['alerta_id']}: {e}\n{traceback.format_exc()}")
                    if item['estado'] != 'novo':
                        guardar_estado(est)   # a analise terminou; so falhou o pedido ao servico (repete-se depois)
                        break
                    limpar_worktree(item)
                    if item['tentativas'] >= 2:
                        try:
                            pedir_aprovacao(item, {'tipo': 'info', 'veredicto_texto': '⚠️ A análise automática falhou',
                                                   'resumo_pedido': item['titulo'], 'analise': '-',
                                                   'nota_tecnica': str(e)[:500]})
                        except Exception as e2:
                            log(f'ERRO a avisar a falha: {e2}')
                guardar_estado(est)
                break
    finally:
        TRANCA.unlink(missing_ok=True)


def verificar():
    """Confirma a configuracao sem mexer em nada."""
    ok = True
    for p in ('gh', 'git', 'claude'):
        caminho = programa(p)
        existe = bool(shutil.which(caminho)) or Path(caminho).exists()
        print(f"{'ok ' if existe else 'FALTA'} {p}: {caminho}")
        ok &= existe
    for repo, pasta in CFG['repos'].items():
        existe = (Path(pasta) / '.git').exists()
        print(f"{'ok ' if existe else 'FALTA'} clone de {repo}: {pasta}")
        ok &= existe
    try:
        padroes_privados()
        print('ok  padroes_privados')
    except Exception as e:
        print('FALTA', e)
        ok = False
    try:
        api('GET', '/aprovacoes?origem=' + APR.get('origem', 'melhorias'))
        print('ok  servico de aprovacoes')
    except Exception as e:
        print('FALTA servico de aprovacoes:', e)
        ok = False
    if any(k.startswith('ANTHROPIC_') for k in os.environ):
        print('nota: ha variaveis ANTHROPIC_* no ambiente; sao tiradas antes de chamar o Claude Code')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--estado':
        print(json.dumps(carregar_estado(), ensure_ascii=False, indent=1))
    elif len(sys.argv) > 1 and sys.argv[1] == '--verificar':
        verificar()
    else:
        main()

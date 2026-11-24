"""Gera os workflows do n8n desta pasta (JSON prontos a importar).

  python gerar.py

1. Muda as constantes da secao CONFIGURACAO (enderecos da tua rede, id da conta do Instagram, canais).
2. Corre este script: reescreve os .json de cada pasta.
3. Importa no n8n (menu ... -> Import from File) e escolhe as tuas credenciais nos nos que as pedem.

As credenciais vao com ids de exemplo ("SUBSTITUIR-..."): o n8n mostra-as como em falta e escolhes as tuas.
Nada aqui tem segredos. Testado com o n8n 2.x.
"""
import json
import uuid
from pathlib import Path

AQUI = Path(__file__).resolve().parent

# ============================================================== CONFIGURACAO (muda isto)

SERVICO = 'http://10.0.0.30:8790'                    # Discord aprovacoes (API local)
SONDA = 'http://172.17.0.1:8791'                     # sonda (IP do anfitriao visto de dentro do contentor do n8n)
N8N_INTERNO = 'http://10.0.0.20:5678/webhook'        # o teu n8n na rede de casa (para o callback do servico)
N8N_PUBLICO = 'https://n8n.example.com/webhook'      # o teu n8n visto da Internet (so para o Instagram ir buscar a imagem)
IG_USER_ID = 'ID_DA_CONTA_INSTAGRAM'                 # id da conta profissional (GET graph.instagram.com/me?fields=user_id)
IG_CONTA = 'a-tua-conta'                             # @ da conta, so para os textos
CANAL_AVISOS = 'alertas'                             # nome logico no servico (CANAL_ALERTAS_ID)
CANAL_PUBLICAR = 'aprovacoes'                        # canal das aprovacoes de publicacao
FUSO = 'Europe/Lisbon'
IG = 'https://graph.instagram.com/v23.0'

CRED_SERVICO = {'httpHeaderAuth': {'id': 'SUBSTITUIR-servico', 'name': 'Discord aprovações (X-Token)'}}
CRED_SONDA = {'httpHeaderAuth': {'id': 'SUBSTITUIR-sonda', 'name': 'Sonda (X-Token)'}}
CRED_IG = {'httpHeaderAuth': {'id': 'SUBSTITUIR-instagram', 'name': 'Instagram (Authorization: Bearer)'}}
CRED_PASTA = {'httpHeaderAuth': {'id': 'SUBSTITUIR-pasta', 'name': 'Pasta de publicar (X-Token)'}}

ID_ERROS = 'autoAvisarErros1'

# ============================================================== ajudantes

NS = uuid.UUID('3f6c2a7e-1b4d-4e8a-9c0f-5d2e7a9b1c34')


def uid(nome):
    return str(uuid.uuid5(NS, nome))


class WF:
    def __init__(self, wid, nome, erro=True, guardar_sucesso=True):
        self.wid, self.nome, self.nos, self.lig = wid, nome, [], {}
        self.erro, self.guardar_sucesso = erro, guardar_sucesso

    def no(self, nome, tipo, versao, params, pos, cred=None, **extra):
        n = {'id': uid(self.wid + nome), 'name': nome, 'type': tipo, 'typeVersion': versao, 'position': pos,
             'parameters': params}
        if cred:
            n['credentials'] = cred
        n.update(extra)
        self.nos.append(n)
        return nome

    def liga(self, de, para, saida=0):
        s = self.lig.setdefault(de, {'main': []})['main']
        while len(s) <= saida:
            s.append([])
        s[saida].append({'node': para, 'type': 'main', 'index': 0})

    def json(self):
        s = {'executionOrder': 'v1', 'timezone': FUSO, 'saveDataErrorExecution': 'all',
             'saveDataSuccessExecution': 'all' if self.guardar_sucesso else 'none', 'saveManualExecutions': True,
             'callerPolicy': 'workflowsFromSameOwner'}
        if self.erro:
            s['errorWorkflow'] = ID_ERROS
        return [{'id': self.wid, 'name': self.nome, 'active': False, 'nodes': self.nos, 'connections': self.lig,
                 'settings': s, 'pinData': {}, 'tags': []}]


def code(w, nome, js, pos, modo='runOnceForAllItems'):
    p = {'jsCode': js.strip() + '\n'}
    if modo != 'runOnceForAllItems':
        p['mode'] = modo
    return w.no(nome, 'n8n-nodes-base.code', 2, p, pos)


def manual(w, pos, nome='Executar à mão'):
    return w.no(nome, 'n8n-nodes-base.manualTrigger', 1, {}, pos)


def agenda(w, nome, regra, pos):
    return w.no(nome, 'n8n-nodes-base.scheduleTrigger', 1.2, {'rule': {'interval': [regra]}}, pos)


def webhook(w, nome, metodo, caminho, pos, cred=None, resposta='responseNode'):
    p = {'httpMethod': metodo, 'path': caminho, 'responseMode': resposta, 'options': {}}
    if cred:
        p['authentication'] = 'headerAuth'
    return w.no(nome, 'n8n-nodes-base.webhook', 2, p, pos, cred, webhookId=uid('wh' + caminho))


def http(w, nome, metodo, url, pos, cred, corpo=None, completo=True, nunca_erro=True, timeout=60000, ficheiro=False,
         on_error='continueRegularOutput', cabecalhos=None, **extra):
    opcoes = {'timeout': timeout}
    resp = {}
    if completo:
        resp['fullResponse'] = True
    if nunca_erro:
        resp['neverError'] = True
    if ficheiro:
        resp['responseFormat'] = 'file'
    if resp:
        opcoes['response'] = {'response': resp}
    p = {'method': metodo, 'url': url, 'options': opcoes}
    if cred:
        p.update(authentication='genericCredentialType', genericAuthType='httpHeaderAuth')
    if cabecalhos:
        p['sendHeaders'] = True
        p['headerParameters'] = {'parameters': [{'name': k, 'value': v} for k, v in cabecalhos.items()]}
    if corpo is not None:
        p.update(sendBody=True, specifyBody='json', jsonBody=corpo)
    if on_error:
        extra['onError'] = on_error
    return w.no(nome, 'n8n-nodes-base.httpRequest', 4.2, p, pos, cred, **extra)


def se(w, nome, expressao, pos):
    return w.no(nome, 'n8n-nodes-base.if', 2.2, {
        'conditions': {'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
                       'conditions': [{'id': uid(nome), 'leftValue': expressao, 'rightValue': True,
                                       'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}],
                       'combinator': 'and'}, 'options': {}}, pos)


def escolha(w, nome, valor, saidas, pos, resto=None):
    regras = [{'conditions': {'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
                              'conditions': [{'id': uid(nome + s), 'leftValue': valor, 'rightValue': s,
                                              'operator': {'type': 'string', 'operation': 'equals'}}],
                              'combinator': 'and'}, 'renameOutput': True, 'outputKey': s} for s in saidas]
    opcoes = {'fallbackOutput': 'extra', 'renameFallbackOutput': resto} if resto else {}
    return w.no(nome, 'n8n-nodes-base.switch', 3.2, {'rules': {'values': regras}, 'options': opcoes}, pos)


def responder(w, nome, pos, corpo='={{ JSON.stringify($json) }}', codigo=200):
    return w.no(nome, 'n8n-nodes-base.respondToWebhook', 1.1, {
        'respondWith': 'json', 'responseBody': corpo, 'options': {'responseCode': codigo}}, pos)


def enviar_aviso(w, pos, nome='Enviar aviso (Discord)'):
    return http(w, nome, 'POST', f'{SERVICO}/aviso', pos, CRED_SERVICO, corpo='={{ JSON.stringify($json.aviso) }}',
                completo=False, nunca_erro=False, on_error=None, retryOnFail=True, maxTries=3, waitBetweenTries=5000)


def sonda(w, nome, pos, metodo, caminho, corpo=None, tempo=60000):
    return http(w, nome, metodo, SONDA + caminho, pos, CRED_SONDA, corpo=corpo, completo=False, nunca_erro=False,
                timeout=tempo, alwaysOutputData=True)


def js(texto, **valores):
    for k, v in valores.items():
        texto = texto.replace(f'__{k}__', v)
    return texto


# ============================================================== 0) avisar erros dos outros workflows

JS_ERRO = r"""
// Recebe o erro de qualquer workflow (Definições → Error workflow) e avisa no Discord.
// O mesmo erro (workflow + nó + mensagem) só é avisado de 6 em 6 horas, para não encher o canal.
// Sem dados (carregaste em «Execute workflow» à mão): simula um erro marcado [teste].
const e = $json.workflow ? $json : { workflow: { id: 'teste', name: '[teste] workflow de exemplo' },
  execution: { id: null, mode: 'teste', lastNodeExecuted: 'Nó de exemplo', error: { message: '[teste] erro simulado' } } };
const wf = e.workflow || {};
const ex = e.execution || {};
const erro = ex.error || (e.trigger && e.trigger.error) || {};
const no = (erro.node && erro.node.name) || ex.lastNodeExecuted || '?';
const msg = String(erro.message || erro.description || 'sem mensagem').slice(0, 900);
const st = $getWorkflowStaticData('global');
st.vistos = st.vistos || {};
const agora = Date.now();
for (const [k, t] of Object.entries(st.vistos)) if (agora - t > 24 * 3600e3) delete st.vistos[k];
const chave = `${wf.id}|${no}|${msg.slice(0, 120)}`;
if (st.vistos[chave] && agora - st.vistos[chave] < 6 * 3600e3) return [];
st.vistos[chave] = agora;
const teste = /\[teste\]/i.test(msg) || /\[teste\]/i.test(wf.name || '');
return [{ json: { aviso: {
  canal: '__CANAL__', nivel: 'erro', mencionar: !teste, teste,
  titulo: `O workflow «${wf.name || wf.id}» falhou`,
  descricao: 'Abre a execução no n8n (Executions) para ver o detalhe. Se era um aviso ou uma publicação, ficou por fazer.',
  campos: [
    { nome: 'Nó', valor: no, inline: true },
    { nome: 'Modo', valor: ex.mode || '?', inline: true },
    { nome: 'Erro', valor: msg },
    { nome: 'Execução', valor: ex.id ? `${ex.id} ${ex.url || ''}` : 'não guardada' },
  ],
  rodape: 'n8n: avisar erros · o mesmo erro só volta a ser avisado ao fim de 6 h',
} } }];
"""


def wf_erros():
    w = WF(ID_ERROS, 'Avisar erros dos workflows → Discord', erro=False)
    w.no('Quando um workflow falha', 'n8n-nodes-base.errorTrigger', 1, {}, [0, 0])
    manual(w, [0, 200], 'Teste à mão (erro simulado)')
    code(w, 'Preparar aviso do erro', js(JS_ERRO, CANAL=CANAL_AVISOS), [240, 0])
    enviar_aviso(w, [480, 0])
    w.liga('Quando um workflow falha', 'Preparar aviso do erro')
    w.liga('Teste à mão (erro simulado)', 'Preparar aviso do erro')
    w.liga('Preparar aviso do erro', 'Enviar aviso (Discord)')
    return w


# ============================================================== 1) renovar tokens do Instagram (com a sonda)

JS_OPCOES = r"""
// Opções desta volta. À mão podes pôr forcar: true (renova já, se o token tiver mais de 24 h)
// ou teste: true (só verifica, não renova; os avisos saem marcados [teste]).
return [{ json: { teste: false, forcar: false } }];
"""

JS_DECIDIR_TOKENS = r"""
// Avisa só quando importa: falhou, faltam 10 dias ou menos, ou houve renovação (verde, sem menção).
const passos = (conta, cred) => [
  '**Como gerar um token novo à mão (5 min):**',
  '1. developers.facebook.com → **My Apps** → a tua app do Instagram.',
  '2. **Instagram → API setup with Instagram business login**.',
  `3. Em «Generate access tokens», **Generate token** na conta **@${conta}** e entra com essa conta.`,
  '4. Copia o token (começa por `IGAA`).',
  `5. No n8n: **Credentials → «${cred || 'a credencial dessa conta'}»** → **Value** = \`Bearer \` + o token → **Save** (mantém o nome).`,
  '6. A renovação automática trata dele 24 h depois.',
].join('\n');
const r = $json;
const teste = !!$('Opções').first().json.teste;
const avisos = [];
const CANAL = '__CANAL__';
const LINK = [{ texto: 'Abrir Meta for Developers', url: 'https://developers.facebook.com/apps/' }];
const data = (iso) => iso ? new Date(iso).toLocaleString('pt-PT', { timeZone: '__FUSO__', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '?';
if (r.error || !Array.isArray(r.contas)) {
  const m = r.error ? (r.error.message || JSON.stringify(r.error)) : (r.erro || JSON.stringify(r).slice(0, 300));
  avisos.push({ canal: CANAL, nivel: 'erro', mencionar: !teste, teste,
    titulo: 'Tokens do Instagram: a renovação não correu',
    descricao: 'A sonda não respondeu ou recusou o pedido. Os tokens NÃO foram renovados nesta volta.\n\nVer `systemctl status n8n-sonda` e `/var/log/n8n-sonda/sonda.log` no anfitrião do n8n.',
    campos: [{ nome: 'Erro', valor: String(m).slice(0, 900) }], rodape: 'n8n: renovar tokens do Instagram' });
} else {
  for (const c of r.contas) {
    if (!c.ok) {
      avisos.push({ canal: CANAL, nivel: 'erro', mencionar: !teste, teste,
        titulo: `Token do Instagram @${c.conta || '?'}: falhou`, descricao: passos(c.conta, c.credencial),
        campos: [{ nome: 'O que aconteceu', valor: c.erro || '?' },
                 { nome: 'Credencial n8n', valor: c.credencial || c.id, inline: true },
                 { nome: 'Validade conhecida', valor: c.expira_em ? `${data(c.expira_em)} (${c.dias} dias${c.estimado ? ', estimada' : ''})` : '?', inline: true }],
        links: LINK, rodape: 'n8n: renovar tokens do Instagram' });
    } else if (c.dias !== null && c.dias <= 10) {
      avisos.push({ canal: CANAL, nivel: 'aviso', mencionar: !teste, teste,
        titulo: `Token do Instagram @${c.conta} caduca em ${Math.floor(c.dias)} dias`,
        descricao: 'A renovação automática ainda não o conseguiu prolongar (' + (c.acao || '?') + ').\n\n' + passos(c.conta, c.credencial),
        campos: [{ nome: 'Caduca', valor: `${data(c.expira_em)}${c.estimado ? ' (estimada)' : ''}`, inline: true }],
        links: LINK, rodape: 'n8n: renovar tokens do Instagram' });
    }
  }
  const renovados = r.contas.filter((c) => c.ok && /^renovado/.test(c.acao || ''));
  if (renovados.length || teste) {
    avisos.push({ canal: CANAL, nivel: 'ok', mencionar: false, teste,
      titulo: teste ? 'Tokens do Instagram verificados (sem renovar)' : `Tokens do Instagram renovados (${renovados.length})`,
      campos: r.contas.map((c) => ({ nome: `@${c.conta || '?'}`,
        valor: `${c.ok ? '✅' : '❌'} ${c.acao || c.erro || '?'}\nVálido até ${data(c.expira_em)}${c.estimado ? ' (estimado)' : ''}` })),
      rodape: 'n8n: renovar tokens do Instagram · o token novo fica na mesma credencial' });
  }
}
return avisos.map((a) => ({ json: { aviso: a } }));
"""


def wf_tokens():
    w = WF('autoTokensInsta1', 'Renovar tokens do Instagram')
    agenda(w, 'Toda a segunda às 10:00', {'field': 'weeks', 'weeksInterval': 1, 'triggerAtDay': [1], 'triggerAtHour': 10,
                                           'triggerAtMinute': 0}, [0, -100])
    manual(w, [0, 100])
    code(w, 'Opções', JS_OPCOES, [240, 0])
    sonda(w, 'Renovar na sonda', [480, 0], 'POST', '/instagram/renovar',
          '={{ JSON.stringify({ teste: $json.teste, forcar: $json.forcar }) }}', tempo=300000)
    code(w, 'Decidir avisos', js(JS_DECIDIR_TOKENS, CANAL=CANAL_AVISOS, FUSO=FUSO), [720, 0])
    enviar_aviso(w, [960, 0])
    for a, b in (('Toda a segunda às 10:00', 'Opções'), ('Executar à mão', 'Opções'), ('Opções', 'Renovar na sonda'),
                 ('Renovar na sonda', 'Decidir avisos'), ('Decidir avisos', 'Enviar aviso (Discord)')):
        w.liga(a, b)
    return w


# ============================================================== 2) vigia do que e publico

ALVOS = [
    {'id': 'site', 'nome': 'O meu site', 'url': 'https://example.com/', 'texto': 'Example Domain', 'cada_min': 5},
    {'id': 'api', 'nome': 'API pública (saúde)', 'url': 'https://example.com/saude', 'exato': 'ok', 'cada_min': 5},
    {'id': 'recetor', 'nome': 'Recetor de webhooks (Worker)', 'url': 'https://recetor-webhooks.a-tua-conta.workers.dev/saude',
     'exato': 'ok', 'cada_min': 15},
]

JS_ESCOLHER = r"""
// O que vigiar. Para acrescentar: copia uma linha (id único, url, e «texto» que tem de aparecer na página
// ou «exato» se a resposta inteira tem de ser igual). cada_min = de quantos em quantos minutos.
const TESTE = false;   // true só em testes: os avisos saem marcados [teste]
const ALVOS = __ALVOS__;
const st = $getWorkflowStaticData('global');
st.ultimo = st.ultimo || {};
const agora = Date.now();
const saida = [];
for (const a of ALVOS) {
  if (agora - (st.ultimo[a.id] || 0) < a.cada_min * 60000 - 60000) continue;   // ainda não é a vez deste
  st.ultimo[a.id] = agora;
  saida.push({ json: { ...a, teste: TESTE } });
}
return saida;
"""

JS_AVALIAR = r"""
// 2 falhas seguidas = aviso «em baixo» (com menção); quando volta a responder = aviso «voltou». Nada pelo meio.
const st = $getWorkflowStaticData('global');
st.alvos = st.alvos || {};
const avisos = [];
const agora = new Date();
const dur = (ms) => { const m = Math.round(ms / 60000); return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`; };
const hora = (d) => new Date(d).toLocaleTimeString('pt-PT', { timeZone: '__FUSO__', hour: '2-digit', minute: '2-digit' });
$input.all().forEach((it, i) => {
  const a = $('Escolher o que testar agora').itemMatching(i).json;
  const j = it.json || {};
  let ok = false, motivo = '';
  if (j.error) {
    motivo = 'sem resposta: ' + String(j.error.message || j.error.description || JSON.stringify(j.error)).slice(0, 200);
  } else {
    const corpo = String(j.body ?? j.data ?? '');
    if (j.statusCode !== 200) motivo = `respondeu HTTP ${j.statusCode} (esperado 200)`;
    else if (a.exato ? corpo.trim() !== a.exato : !corpo.toLowerCase().includes(String(a.texto).toLowerCase()))
      motivo = a.exato ? `respondeu 200 mas não «${a.exato}»` : `respondeu 200 mas sem o texto «${a.texto}»`;
    else ok = true;
  }
  const s = st.alvos[a.id] || { falhas: 0, em_baixo: false };
  if (!ok) {
    s.falhas = (s.falhas || 0) + 1;
    s.motivo = motivo;
    s.desde = s.desde || agora.toISOString();
    if (s.falhas >= 2 && !s.em_baixo) {
      s.em_baixo = true;
      avisos.push({ canal: '__CANAL__', nivel: 'erro', mencionar: !a.teste, teste: a.teste,
        titulo: `Em baixo: ${a.nome}`,
        descricao: `Falhou ${s.falhas} verificações seguidas (desde as ${hora(s.desde)}). Aviso outra vez só quando voltar.`,
        campos: [{ nome: 'O que vi', valor: motivo }, { nome: 'Endereço', valor: a.url.split('?')[0] }],
        links: [{ texto: 'Abrir', url: a.url.split('?')[0] }], rodape: 'n8n: vigia do que é público' });
    }
  } else {
    if (s.em_baixo) {
      avisos.push({ canal: '__CANAL__', nivel: 'ok', mencionar: false, teste: a.teste,
        titulo: `Voltou: ${a.nome}`,
        descricao: `Responde outra vez. Esteve em baixo cerca de ${dur(agora - new Date(s.desde))} (desde as ${hora(s.desde)}).`,
        campos: [{ nome: 'Último erro', valor: s.motivo || '?' }], rodape: 'n8n: vigia do que é público' });
    }
    s.falhas = 0; s.em_baixo = false; s.desde = null; s.motivo = null;
  }
  s.verificado = agora.toISOString();
  st.alvos[a.id] = s;
});
return avisos.map((x) => ({ json: { aviso: x } }));
"""


def wf_vigia():
    w = WF('autoVigiaUptime1', 'Vigia do que é público (uptime)', guardar_sucesso=False)   # 288 execucoes por dia
    agenda(w, 'De 5 em 5 minutos', {'field': 'minutes', 'minutesInterval': 5}, [0, -100])
    manual(w, [0, 100])
    code(w, 'Escolher o que testar agora', js(JS_ESCOLHER, ALVOS=json.dumps(ALVOS, ensure_ascii=False, indent=2)), [240, 0])
    w.no('Pedir a página', 'n8n-nodes-base.httpRequest', 4.2, {
        'url': '={{ $json.url }}', 'sendHeaders': True,
        'headerParameters': {'parameters': [{'name': 'User-Agent', 'value': 'Mozilla/5.0 (compatible; vigia-n8n/1.0)'},
                                            {'name': 'Cache-Control', 'value': 'no-cache'}]},
        'options': {'timeout': 20000, 'redirect': {'redirect': {'maxRedirects': 5}},
                    'response': {'response': {'fullResponse': True, 'neverError': True, 'responseFormat': 'text'}}}},
        [480, 0], onError='continueRegularOutput')
    code(w, 'Avaliar e decidir avisos', js(JS_AVALIAR, CANAL=CANAL_AVISOS, FUSO=FUSO), [720, 0])
    enviar_aviso(w, [960, 0])
    for a, b in (('De 5 em 5 minutos', 'Escolher o que testar agora'), ('Executar à mão', 'Escolher o que testar agora'),
                 ('Escolher o que testar agora', 'Pedir a página'), ('Pedir a página', 'Avaliar e decidir avisos'),
                 ('Avaliar e decidir avisos', 'Enviar aviso (Discord)')):
        w.liga(a, b)
    return w


# ============================================================== 3) lembretes do que caduca

HOSTS_TLS = ['example.com', 'n8n.example.com']
DOMINIO = 'example.com'

JS_PRAZOS = r"""
// Junta todos os prazos e avisa só se algum caducar nos próximos LIMITE dias (ou se não consegui verificar algo).
// Uma mensagem por dia, no máximo.
const LIMITE = 14;
const TESTE = false;   // em testes: LIMITE = 400 e TESTE = true mostra tudo o que é seguido
// Prazos teus que nenhuma API sabe: { nome, expira_em: 'AAAA-MM-DD', nota }
const FIXOS = [
  // { nome: 'Renovar o seguro do servidor', expira_em: '2027-03-01', nota: 'Exemplo: apaga ou muda.' },
];
const agora = new Date();
const dias = (iso) => (new Date(iso) - agora) / 86400000;
const data = (iso) => new Date(iso).toLocaleDateString('pt-PT', { timeZone: '__FUSO__' });
const itens = [], falhas = [];
const erroDe = (j) => j.error ? String(j.error.message || JSON.stringify(j.error)).slice(0, 200) : null;

const cert = $('Certificados TLS (sonda)').first().json;
if (erroDe(cert) || !Array.isArray(cert.certificados)) falhas.push(['Certificados TLS', erroDe(cert) || 'resposta inesperada da sonda']);
else for (const c of cert.certificados) {
  if (c.erro) falhas.push([`Certificado ${c.host}`, c.erro]);
  else itens.push({ nome: `Certificado TLS de ${c.host}`, expira_em: c.expira_em,
    nota: `Emissor: ${c.emissor}. Normalmente renova-se sozinho ~30 dias antes; se chegou aqui, a renovação automática falhou.` });
}
const ig = $('Tokens do Instagram (sonda)').first().json;
if (erroDe(ig) || !Array.isArray(ig.contas)) falhas.push(['Tokens do Instagram', erroDe(ig) || 'resposta inesperada da sonda']);
else for (const c of ig.contas) {
  if (c.ultimo_erro) falhas.push([`Token do Instagram @${c.conta}`, c.ultimo_erro]);
  if (c.expira_em) itens.push({ nome: `Token do Instagram @${c.conta}`, expira_em: c.expira_em,
    nota: (c.estimado ? 'Validade estimada (60 dias desde a última alteração da credencial). ' : '') + 'Renova-se sozinho à segunda às 10:00.' });
}
const tun = $('Túnel (sonda)').first().json;
if (tun.configurado !== false) {
  if (erroDe(tun) || tun.erro) falhas.push(['Túnel', erroDe(tun) || tun.erro]);
  else if (!tun.a_correr || !(tun.ligacoes > 0)) falhas.push(['Túnel', `estado ${tun.estado}, ligações prontas: ${tun.ligacoes ?? '?'}, reinícios: ${tun.reinicios}`]);
}
let rd = $('Domínio (RDAP)').first().json;
if (typeof rd.data === 'string') { try { rd = JSON.parse(rd.data); } catch (e) { /* fica como veio */ } }
const exp = (rd.events || []).find((e) => e.eventAction === 'expiration');
if (erroDe(rd) || !exp) falhas.push(['Domínio __DOMINIO__', erroDe(rd) || 'o RDAP não trouxe a data de expiração']);
else itens.push({ nome: 'Domínio __DOMINIO__', expira_em: exp.eventDate, nota: 'Renovar no registo do domínio. Sem renovação, tudo o que está nele para.' });
for (const f of FIXOS) itens.push({ ...f, expira_em: new Date(f.expira_em + 'T09:00:00Z').toISOString() });

const perto = itens.map((x) => ({ ...x, dias: dias(x.expira_em) })).filter((x) => x.dias <= LIMITE).sort((a, b) => a.dias - b.dias);
if (!perto.length && !falhas.length) return [];
const st = $getWorkflowStaticData('global');
const chave = agora.toISOString().slice(0, 10) + '|' + JSON.stringify([perto.map((x) => x.nome), falhas.map((f) => f[0])]);
if (st.ultimo === chave && !TESTE) return [];   // já avisado hoje com o mesmo conteúdo
st.ultimo = chave;
const urgente = perto.some((x) => x.dias <= 3);
const campos = perto.slice(0, 10).map((x) => ({
  nome: `${x.dias < 0 ? '⛔' : x.dias <= 3 ? '🔴' : '🟡'} ${x.nome}`,
  valor: `${x.dias < 0 ? 'Caducou a' : 'Caduca a'} **${data(x.expira_em)}** (${x.dias < 0 ? 'há ' + Math.ceil(-x.dias) : 'faltam ' + Math.floor(x.dias)} dias)\n${x.nota || ''}` }));
for (const [n, m] of falhas.slice(0, 5)) campos.push({ nome: `⚠️ Não consegui verificar: ${n}`, valor: m });
return [{ json: { aviso: {
  canal: '__CANAL__', nivel: urgente || falhas.length ? 'erro' : 'aviso', mencionar: !TESTE, teste: TESTE,
  titulo: perto.length ? `Prazos a caducar nos próximos ${LIMITE} dias: ${perto.length}` : 'Lembretes do que caduca: não consegui verificar tudo',
  descricao: 'Verificação diária. Só aviso quando falta pouco; amanhã volto a avisar se ainda estiver por tratar.',
  campos, rodape: 'n8n: lembretes do que caduca',
} } }];
"""


def wf_prazos():
    w = WF('autoPrazos000001', 'Lembretes do que caduca')
    agenda(w, 'Todos os dias às 09:30', {'field': 'days', 'daysInterval': 1, 'triggerAtHour': 9, 'triggerAtMinute': 30}, [0, -100])
    manual(w, [0, 100])
    sonda(w, 'Certificados TLS (sonda)', [240, 0], 'GET', '/certificados?hosts=' + ','.join(HOSTS_TLS))
    sonda(w, 'Tokens do Instagram (sonda)', [480, 0], 'GET', '/instagram/estado')
    sonda(w, 'Túnel (sonda)', [720, 0], 'GET', '/tunel')
    http(w, 'Domínio (RDAP)', 'GET', f'https://rdap.org/domain/{DOMINIO}', [960, 0], None, completo=False, nunca_erro=False,
         timeout=30000, cabecalhos={'Accept': 'application/rdap+json'}, alwaysOutputData=True, retryOnFail=True,
         maxTries=3, waitBetweenTries=5000)
    code(w, 'Juntar prazos e decidir', js(JS_PRAZOS, CANAL=CANAL_AVISOS, FUSO=FUSO, DOMINIO=DOMINIO), [1200, 0])
    enviar_aviso(w, [1440, 0])
    for a, b in (('Todos os dias às 09:30', 'Certificados TLS (sonda)'), ('Executar à mão', 'Certificados TLS (sonda)'),
                 ('Certificados TLS (sonda)', 'Tokens do Instagram (sonda)'), ('Tokens do Instagram (sonda)', 'Túnel (sonda)'),
                 ('Túnel (sonda)', 'Domínio (RDAP)'), ('Domínio (RDAP)', 'Juntar prazos e decidir'),
                 ('Juntar prazos e decidir', 'Enviar aviso (Discord)')):
        w.liga(a, b)
    return w


# ============================================================== 4) publicar no Instagram com aprovacao no Discord

ORIGEM_IG = 'n8n-instagram'
ATUALIZAR_URL = f"={SERVICO}/aprovacao/{{{{ $json.aid }}}}/atualizar"
ATUALIZAR_CORPO = '={{ JSON.stringify($json.corpo) }}'

JS_VALIDAR = r"""
// Valida o que a pasta mandou (imagem + legenda + hora). Nada aqui publica nem sai para fora.
const b = $json.body || {};
const motivos = [], avisos = [];
const nome = String(b.nome || 'imagem.jpg').replace(/[\\/]/g, '_').slice(0, 120);
const base = nome.replace(/\.(jpe?g|png)$/i, '');
const pubId = String(b.pub_id || '');
if (!/^[a-f0-9]{12,32}$/.test(pubId)) motivos.push('pedido sem identificador válido');

// ---- imagem: formato, tamanho e proporção (lidos do próprio ficheiro)
let buf = null, largura = 0, altura = 0, formato = '?';
try { buf = Buffer.from(String(b.imagem_b64 || ''), 'base64'); } catch (e) { buf = null; }
if (!buf || buf.length < 100) motivos.push('imagem vazia ou ilegível');
else {
  if (buf.length > 8 * 1024 * 1024) motivos.push(`a imagem tem ${(buf.length / 1048576).toFixed(1)} MB (máximo 8 MB)`);
  if (buf[0] === 0xFF && buf[1] === 0xD8) {
    formato = 'JPEG';
    let i = 2;
    while (i + 9 < buf.length) {
      if (buf[i] !== 0xFF) { i++; continue; }
      const m = buf[i + 1];
      if (m === 0xFF) { i++; continue; }
      if (m === 0xD8 || m === 0x01 || (m >= 0xD0 && m <= 0xD7)) { i += 2; continue; }
      const len = buf.readUInt16BE(i + 2);
      if (m >= 0xC0 && m <= 0xCF && m !== 0xC4 && m !== 0xC8 && m !== 0xCC) {
        altura = buf.readUInt16BE(i + 5); largura = buf.readUInt16BE(i + 7); break;
      }
      i += 2 + len;
    }
  } else if (buf.slice(0, 8).toString('hex') === '89504e470d0a1a0a') {
    formato = 'PNG'; largura = buf.readUInt32BE(16); altura = buf.readUInt32BE(20);
    motivos.push('chegou em PNG: o Instagram só aceita JPEG (o script da pasta converte sozinho; se chegou assim, a conversão falhou)');
  } else motivos.push('formato não suportado (só JPEG ou PNG)');
  if (formato !== '?' && (!largura || !altura)) motivos.push('não consegui ler as dimensões da imagem');
}
const proporcao = largura && altura ? largura / altura : 0;
if (proporcao) {
  if (proporcao < 0.8 - 0.005 || proporcao > 1.91 + 0.005)
    motivos.push(`proporção ${largura}×${altura} fora do que o Instagram aceita (entre 4:5 e 1,91:1)`);
  if (largura < 320) motivos.push(`largura ${largura} px: o mínimo é 320 px`);
  else if (largura < 1080) avisos.push(`largura ${largura} px (o Instagram recomenda 1080 px)`);
}

// ---- legenda
const legenda = String(b.legenda || '').replace(/\r\n/g, '\n').replace(/^﻿/, '').trim();
if (!legenda) motivos.push(`a legenda está vazia (${base}.txt sem texto)`);
if (legenda.length > 2200) motivos.push(`a legenda tem ${legenda.length} caracteres (máximo 2200)`);
const hashtags = legenda.match(/#[\p{L}\p{N}_]+/gu) || [];
if (hashtags.length > 30) motivos.push(`a legenda tem ${hashtags.length} hashtags (máximo 30)`);
const mencoes = legenda.match(/(^|\s)@[\w.]+/g) || [];
if (mencoes.length > 20) motivos.push(`a legenda tem ${mencoes.length} menções @ (máximo 20)`);

// ---- privacidade: o que nunca pode ir numa publicação. ACRESCENTA os teus nomes e domínios em NOMES.
const NOMES = [];   // ex.: ['o-meu-nome', 'o-meu-apelido']
const DOMINIO_MARCA = 'example.com';   // e-mails deste domínio são permitidos
const PRIVADO = [
  [new RegExp('[\\w.+-]+@(?!' + DOMINIO_MARCA.replace(/\./g, '\\.') + ')[\\w-]+\\.[\\w.]+', 'i'), 'um e-mail que não é da marca'],
  [/\b(?:\d{1,3}\.){3}\d{1,3}\b/, 'um endereço IP'],
  [/(?:\+?\d[ -]?){9,}/, 'um número de telefone'],
  [/ts\.net|tailscale|localhost|\.lan\b|\.local\b/i, 'um endereço da rede de casa'],
];
if (NOMES.length) PRIVADO.push([new RegExp(NOMES.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'i'), 'um nome pessoal']);
for (const [re, o] of PRIVADO) if (re.test(legenda)) motivos.push(`a legenda parece ter ${o}`);

// ---- hora marcada (opcional), na hora local do fuso do workflow
let quandoIso = null, quandoPt = null;
const qt = String(b.quando || '').trim();
if (qt) {
  const fmts = ['yyyy-MM-dd HH:mm', "yyyy-MM-dd'T'HH:mm", 'yyyy-MM-dd H:mm', 'dd/MM/yyyy HH:mm', 'd/M/yyyy H:mm',
                'dd-MM-yyyy HH:mm', 'yyyy-MM-dd HH:mm:ss', 'dd/MM/yyyy HH:mm:ss'];
  let dt = null;
  for (const f of fmts) { const d = DateTime.fromFormat(qt, f, { zone: '__FUSO__' }); if (d.isValid) { dt = d; break; } }
  if (!dt) motivos.push(`não percebi a hora em ${base}.quando.txt («${qt.slice(0, 40)}»): escreve por exemplo 2026-11-30 18:30`);
  else {
    const agora = DateTime.now().setZone('__FUSO__');
    if (dt < agora.plus({ minutes: 10 })) motivos.push(`a hora marcada (${dt.toFormat('dd/MM/yyyy HH:mm')}) já passou ou é daqui a menos de 10 min`);
    else if (dt > agora.plus({ days: 60 })) motivos.push(`a hora marcada (${dt.toFormat('dd/MM/yyyy HH:mm')}) é daqui a mais de 60 dias`);
    quandoIso = dt.toUTC().toISO();
    quandoPt = dt.setLocale('pt-PT').toFormat("cccc, dd/MM/yyyy 'às' HH:mm");
  }
}

return { json: {
  valido: motivos.length === 0, motivos, avisos, pub_id: pubId, nome, base, formato, largura, altura,
  proporcao: proporcao ? Number(proporcao.toFixed(3)) : 0, bytes: buf ? buf.length : 0,
  legenda, caracteres: legenda.length, hashtags: hashtags.length, quando_iso: quandoIso, quando_pt: quandoPt,
  teste: b.teste === true, convertido: b.convertido || null,
} };
"""

JS_MONTAR_APROVACAO = r"""
// Pedido de aprovação: pré-visualização, legenda e os botões. Só os aprovadores podem carregar.
const v = $json;
const b = $('Recebe da pasta (nova imagem)').item.json.body;
const aid = 'apr-ig-' + v.pub_id;
const kb = v.bytes > 1048576 ? (v.bytes / 1048576).toFixed(1).replace('.', ',') + ' MB' : Math.round(v.bytes / 1024) + ' KB';
const botoes = [{ id: 'publicar', rotulo: 'Publicar agora', emoji: '✅', estilo: 'sucesso' }];
if (v.quando_iso) botoes.push({ id: 'agendar', rotulo: 'Publicar à hora marcada', emoji: '🕒', estilo: 'primario' });
botoes.push({ id: 'editar', rotulo: 'Editar', emoji: '✏️', estilo: 'secundario',
  resposta: `✏️ Para editar: muda \`${v.base}.txt\` (legenda) ou a imagem \`${v.nome}\` na pasta e grava. Chega aqui uma aprovação nova; esta fica fechada.` });
botoes.push({ id: 'nao', rotulo: 'Não publicar', emoji: '❌', estilo: 'perigo' });
const campos = [
  { nome: 'Conta', valor: 'Instagram **@__CONTA__**', inline: true },
  { nome: 'Imagem', valor: `${v.nome} · ${v.largura}×${v.altura} · ${kb}` + (v.convertido ? ` · ${v.convertido}` : ''), inline: true },
  { nome: 'Legenda', valor: `${v.caracteres}/2200 caracteres · ${v.hashtags}/30 hashtags`, inline: true },
  { nome: 'Quando', valor: v.quando_iso ? `🕒 ${v.quando_pt} — se carregares em «Publicar à hora marcada»` : 'Sem hora marcada: «Publicar agora» publica logo.' },
];
if (v.avisos.length) campos.push({ nome: '⚠️ Avisos', valor: v.avisos.map((a) => '• ' + a).join('\n') });
return { json: { corpo: {
  id: aid, origem: '__ORIGEM__', canal: '__CANAL__', fase: 'pendente', mencionar: true, teste: v.teste,
  titulo: `📸 Publicar no Instagram @__CONTA__ — ${v.nome}`,
  descricao: v.legenda.length > 3900 ? v.legenda.slice(0, 3899) + '…' : v.legenda,
  campos, botoes, callback: '__CALLBACK__',
  contexto: { aid, pub_id: v.pub_id, nome: v.nome, base: v.base, legenda: v.legenda, quando_iso: v.quando_iso,
              quando_pt: v.quando_pt, teste: v.teste },
  rodape: 'n8n · Instagram', imagem_b64: b.imagem_b64,
} } };
"""

JS_MONTAR_INVALIDA = r"""
// A imagem não passa nas regras: aviso sem botões (nada é publicado). O script da pasta escreve o motivo ao lado do ficheiro.
const v = $json;
const b = $('Recebe da pasta (nova imagem)').item.json.body;
const temImagem = v.formato === 'JPEG' && v.bytes <= 8 * 1024 * 1024;
return { json: { corpo: {
  id: 'apr-ig-' + (v.pub_id || 'x'), origem: '__ORIGEM__', canal: '__CANAL__', fase: 'invalido', mencionar: true,
  teste: v.teste, cor: 'laranja', titulo: `⚠️ Não dá para publicar ${v.nome}`,
  descricao: v.motivos.map((m) => '• ' + m).join('\n') + `\n\nCorrige na pasta e grava: o script volta a mandar sozinho. O motivo também fica em \`${v.base}.erro.txt\`.`,
  campos: v.legenda ? [{ nome: 'Legenda recebida', valor: v.legenda.slice(0, 1000) }] : [],
  contexto: { pub_id: v.pub_id, nome: v.nome, base: v.base, motivos: v.motivos, teste: v.teste },
  resultado: '⚠️ Inválida: nada foi publicado.', rodape: 'n8n · Instagram',
  imagem_b64: temImagem ? b.imagem_b64 : undefined,
} } };
"""


def wf_entrada():
    w = WF('autoInstaEntrada', 'Instagram: entrada e aprovação')
    webhook(w, 'Recebe da pasta (nova imagem)', 'POST', 'publicar-entrada', [0, 0], CRED_PASTA)
    escolha(w, 'Que pedido?', '={{ ($json.body || {}).acao }}', ['retirar', 'erro'], [220, 0], resto='nova imagem')
    w.liga('Recebe da pasta (nova imagem)', 'Que pedido?')

    code(w, 'Validar imagem e legenda', js(JS_VALIDAR, FUSO=FUSO), [440, 120], modo='runOnceForEachItem')
    w.liga('Que pedido?', 'Validar imagem e legenda', 2)
    se(w, 'É válida?', '={{ $json.valido }}', [660, 120])
    w.liga('Validar imagem e legenda', 'É válida?')
    code(w, 'Montar aprovação', js(JS_MONTAR_APROVACAO, CONTA=IG_CONTA, ORIGEM=ORIGEM_IG, CANAL=CANAL_PUBLICAR,
                                   CALLBACK=f'{N8N_INTERNO}/publicar-decisao'), [880, 40], modo='runOnceForEachItem')
    w.liga('É válida?', 'Montar aprovação', 0)
    code(w, 'Montar aviso (inválida)', js(JS_MONTAR_INVALIDA, ORIGEM=ORIGEM_IG, CANAL=CANAL_PUBLICAR), [880, 220],
         modo='runOnceForEachItem')
    w.liga('É válida?', 'Montar aviso (inválida)', 1)
    http(w, 'Enviar ao Discord', 'POST', f'{SERVICO}/aprovacao', [1100, 120], CRED_SERVICO, corpo='={{ JSON.stringify($json.corpo) }}')
    w.liga('Montar aprovação', 'Enviar ao Discord')
    w.liga('Montar aviso (inválida)', 'Enviar ao Discord')
    code(w, 'Resposta à pasta', r"""
const v = $('Validar imagem e legenda').item.json;
const r = $json;
const ok = r.statusCode === 200 && r.body && r.body.id;
return { json: { ok: !!ok, estado: v.valido ? 'pendente' : 'invalido', motivos: v.motivos, avisos: v.avisos,
  aprovacao: ok ? r.body.id : null, erro: ok ? null : String(JSON.stringify(r.body || r.error || r).slice(0, 300)) } };
""", [1320, 120], modo='runOnceForEachItem')
    w.liga('Enviar ao Discord', 'Resposta à pasta')
    responder(w, 'Responder à pasta', [1540, 120], codigo='={{ $json.ok ? 200 : 502 }}')
    w.liga('Resposta à pasta', 'Responder à pasta')

    # retirar: o ficheiro mudou ou saiu da pasta antes da decisao
    code(w, 'Qual retirar?', r"""
const b = $json.body || {};
const pubId = String(b.pub_id || '');
if (!/^[a-f0-9]{12,32}$/.test(pubId)) return { json: { aid: 'apr-ig-invalido', motivo: 'x' } };
return { json: { aid: 'apr-ig-' + pubId, motivo: b.motivo === 'substituido' ? 'substituido' : 'retirado' } };
""", [440, -140], modo='runOnceForEachItem')
    w.liga('Que pedido?', 'Qual retirar?', 0)
    http(w, 'Ver aprovação', 'GET', f"={SERVICO}/aprovacao/{{{{ $json.aid }}}}", [660, -140], CRED_SERVICO)
    w.liga('Qual retirar?', 'Ver aprovação')
    code(w, 'Pode fechar?', r"""
// Só se fecha o que ainda está à espera. O que já foi publicado nunca é mexido.
const q = $('Qual retirar?').item.json;
const a = $json.statusCode === 200 ? $json.body : null;
const sub = q.motivo === 'substituido';
return { json: { fechar: !!a && a.estado === 'pendente', aid: q.aid, corpo: { estado: 'concluido', fase: q.motivo, apagar_imagem: true,
  resultado: sub ? '🔁 Substituída: o ficheiro mudou e chegou uma aprovação nova.' : '🗑️ Retirada: o ficheiro saiu da pasta. Nada foi publicado.' } } };
""", [880, -140], modo='runOnceForEachItem')
    w.liga('Ver aprovação', 'Pode fechar?')
    se(w, 'Fechar?', '={{ $json.fechar }}', [1100, -140])
    w.liga('Pode fechar?', 'Fechar?')
    http(w, 'Fechar aprovação antiga', 'POST', ATUALIZAR_URL, [1320, -200], CRED_SERVICO, corpo=ATUALIZAR_CORPO)
    w.liga('Fechar?', 'Fechar aprovação antiga', 0)
    responder(w, 'Responder à pasta (retirar)', [1540, -140], corpo='={{ JSON.stringify({ ok: true }) }}')
    w.liga('Fechar aprovação antiga', 'Responder à pasta (retirar)')
    w.liga('Fechar?', 'Responder à pasta (retirar)', 1)

    # o script da pasta falhou: aviso (o script so manda o mesmo erro uma vez por hora)
    code(w, 'Montar aviso (erro na pasta)', js(r"""
const b = $json.body || {};
return { json: { aviso: { canal: '__CANAL__', nivel: 'erro', mencionar: true,
  titulo: 'O script da pasta de publicar falhou',
  descricao: '```\n' + String(b.erro || '?').slice(0, 1500) + '\n```\nNada foi publicado. Vê o log do script.',
  rodape: 'n8n · Instagram' } } };
""", CANAL=CANAL_AVISOS), [440, -300], modo='runOnceForEachItem')
    w.liga('Que pedido?', 'Montar aviso (erro na pasta)', 1)
    http(w, 'Avisar erro da pasta', 'POST', f'{SERVICO}/aviso', [660, -300], CRED_SERVICO, corpo='={{ JSON.stringify($json.aviso) }}')
    w.liga('Montar aviso (erro na pasta)', 'Avisar erro da pasta')
    responder(w, 'Responder à pasta (erro)', [880, -300], corpo='={{ JSON.stringify({ ok: $json.statusCode === 200 }) }}')
    w.liga('Avisar erro da pasta', 'Responder à pasta (erro)')

    # estado para o script da pasta (o que foi publicado, recusado, ...)
    webhook(w, 'Pasta pergunta o estado', 'GET', 'publicar-estado', [0, 420], CRED_PASTA)
    http(w, 'Listar aprovações', 'GET',
         f"={SERVICO}/aprovacoes?origem={ORIGEM_IG}&desde={{{{ Math.floor(Date.now() / 1000) - 60 * 86400 }}}}",
         [220, 420], CRED_SERVICO)
    w.liga('Pasta pergunta o estado', 'Listar aprovações')
    code(w, 'Estado para a pasta', r"""
const r = $json;
const lista = Array.isArray(r.body) ? r.body : [];
return { json: { ok: r.statusCode === 200, publicacoes: lista.map((a) => ({
  aid: a.id, pub_id: (a.contexto || {}).pub_id || null, fase: a.fase, estado: a.estado,
  link: (a.contexto || {}).link || null, resultado: a.resultado, motivos: (a.contexto || {}).motivos || null })) } };
""", [440, 420], modo='runOnceForEachItem')
    w.liga('Listar aprovações', 'Estado para a pasta')
    responder(w, 'Responder estado à pasta', [660, 420], codigo='={{ $json.ok ? 200 : 502 }}')
    w.liga('Estado para a pasta', 'Responder estado à pasta')
    return w


def wf_publicacao():
    w = WF('autoInstaPublica', 'Instagram: decisão e publicação')
    webhook(w, 'Decisão (callback do serviço)', 'POST', 'publicar-decisao', [0, 0], CRED_SERVICO, resposta='onReceived')
    code(w, 'Qual aprovação?', js(r"""
const b = $json.body || {};
if (b.origem !== '__ORIGEM__' || !/^apr-ig-[a-f0-9]{12,32}$/.test(String(b.id || '')))
  throw new Error('decisão inválida recebida no webhook: ' + JSON.stringify(b).slice(0, 200));
return { json: { aid: b.id, fases: null } };
""", ORIGEM=ORIGEM_IG), [220, 0], modo='runOnceForEachItem')
    w.liga('Decisão (callback do serviço)', 'Qual aprovação?')

    agenda(w, 'A cada 5 min (agendadas)', {'field': 'minutes', 'minutesInterval': 5}, [0, 300])
    http(w, 'Listar agendadas', 'GET', f'{SERVICO}/aprovacoes?origem={ORIGEM_IG}&estado=pendente&fase=agendado', [220, 300], CRED_SERVICO)
    w.liga('A cada 5 min (agendadas)', 'Listar agendadas')
    code(w, 'Chegou a hora?', r"""
// Agendadas cuja hora já chegou (no máximo 3 por volta: o Instagram demora a processar).
const r = $input.first().json;
const lista = Array.isArray(r.body) ? r.body : [];
const agora = Date.now();
const prontas = lista.filter((a) => a.contexto && a.contexto.quando_iso && Date.parse(a.contexto.quando_iso) <= agora);
return prontas.slice(0, 3).map((a) => ({ json: { aid: a.id, fases: ['agendado'] } }));
""", [440, 300])
    w.liga('Listar agendadas', 'Chegou a hora?')

    http(w, 'Reservar (só uma vez)', 'POST', f"={SERVICO}/aprovacao/{{{{ $json.aid }}}}/reservar", [660, 120], CRED_SERVICO,
         corpo='={{ JSON.stringify({ fases: $json.fases }) }}', nunca_erro=False, on_error=None)
    w.liga('Qual aprovação?', 'Reservar (só uma vez)')
    w.liga('Chegou a hora?', 'Reservar (só uma vez)')
    code(w, 'O que fazer?', r"""
// O serviço só deixa reservar uma vez: cliques repetidos e a volta das agendadas não fazem nada a dobrar.
const r = $json.body || {};
const c = r.contexto || {};
let acao = 'nada';
if (r.ok && r.decisao == null) acao = r.fase === 'agendado' ? 'publicar' : 'nada';   // agendada: a hora chegou
else if (r.ok) acao = r.decisao;                                                     // botão carregado
return { json: { acao, aid: c.aid, contexto: c } };
""", [880, 120], modo='runOnceForEachItem')
    w.liga('Reservar (só uma vez)', 'O que fazer?')
    escolha(w, 'Que botão?', '={{ $json.acao }}', ['publicar', 'agendar', 'editar', 'nao'], [1100, 120])
    w.liga('O que fazer?', 'Que botão?')

    code(w, 'Agendar', r"""
const c = $json.contexto;
if (!c.quando_iso) return { json: { aid: c.aid, corpo: { estado: 'pendente', fase: 'erro', nota: '⚠️ Esta imagem não tem hora marcada.',
  botoes: [{ id: 'publicar', rotulo: 'Publicar agora', emoji: '✅', estilo: 'sucesso' }, { id: 'nao', rotulo: 'Não publicar', emoji: '❌', estilo: 'perigo' }] } } };
return { json: { aid: c.aid, corpo: { estado: 'pendente', fase: 'agendado', cor: 'azul',
  nota: `🕒 Agendado para ${c.quando_pt}. Publico sozinho a essa hora (até 5 min depois). Ainda podes publicar já ou cancelar.`,
  botoes: [{ id: 'publicar', rotulo: 'Publicar já', emoji: '✅', estilo: 'sucesso' }, { id: 'nao', rotulo: 'Cancelar (não publicar)', emoji: '❌', estilo: 'perigo' }] } } };
""", [1320, 160], modo='runOnceForEachItem')
    w.liga('Que botão?', 'Agendar', 1)
    code(w, 'Editar', r"""
const c = $json.contexto;
return { json: { aid: c.aid, corpo: { estado: 'concluido', fase: 'editar', apagar_imagem: true, cor: 'cinzento',
  resultado: `✏️ À espera da versão editada: muda \`${c.base}.txt\` (ou a imagem) na pasta e grava. Nada foi publicado.` } } };
""", [1320, 300], modo='runOnceForEachItem')
    w.liga('Que botão?', 'Editar', 2)
    code(w, 'Não publicar', r"""
const c = $json.contexto;
return { json: { aid: c.aid, corpo: { estado: 'concluido', fase: 'recusado', apagar_imagem: true, cor: 'vermelho',
  resultado: '❌ Não publicado. O script da pasta move os ficheiros para recusados (na volta seguinte).' } } };
""", [1320, 440], modo='runOnceForEachItem')
    w.liga('Que botão?', 'Não publicar', 3)

    # ---- publicar
    code(w, 'Preparar publicação', r"""
const c = $json.contexto;
if (!c || !c.aid || !c.legenda) throw new Error('aprovação sem contexto para publicar');
return { json: { aid: c.aid, legenda: c.legenda, nome: c.nome, teste: c.teste === true } };
""", [1320, -60], modo='runOnceForEachItem')
    w.liga('Que botão?', 'Preparar publicação', 0)
    http(w, 'Quota do Instagram', 'GET', f'{IG}/{IG_USER_ID}/content_publishing_limit?fields=config,quota_usage', [1540, -60], CRED_IG)
    w.liga('Preparar publicação', 'Quota do Instagram')
    http(w, 'Chave temporária da imagem', 'POST', f"={SERVICO}/aprovacao/{{{{ $('Preparar publicação').item.json.aid }}}}/chave",
         [1760, -60], CRED_SERVICO)
    w.liga('Quota do Instagram', 'Chave temporária da imagem')
    code(w, 'Pronto para criar?', js(r"""
// A imagem fica em privado no serviço; o Instagram vai buscá-la ao webhook público com uma chave de 15 min.
const p = $('Preparar publicação').item.json;
const q = $('Quota do Instagram').item.json;
const k = $json;
let erro = null;
const quota = q.statusCode === 200 && q.body && q.body.data && q.body.data[0];
if (!quota) erro = 'não consegui ler a quota do Instagram (token caducado?): ' + JSON.stringify(q.body || q.error || '').slice(0, 200);
else if ((quota.quota_usage || 0) >= ((quota.config || {}).quota_total || 50)) erro = `quota de publicações das últimas 24 h esgotada (${quota.quota_usage})`;
else if (k.statusCode !== 200 || !k.body || !k.body.chave) erro = 'não consegui preparar a imagem no serviço: ' + JSON.stringify(k.body || k.error || '').slice(0, 200);
return { json: { ...p, erro, image_url: erro ? null : '__PUBLICO__/publicar-imagem?c=' + encodeURIComponent(k.body.chave) } };
""", PUBLICO=N8N_PUBLICO), [1980, -60], modo='runOnceForEachItem')
    w.liga('Chave temporária da imagem', 'Pronto para criar?')
    se(w, 'Sem erro?', '={{ !$json.erro }}', [2200, -60])
    w.liga('Pronto para criar?', 'Sem erro?')
    http(w, 'Criar contentor (ainda não publica)', 'POST', f'{IG}/{IG_USER_ID}/media', [2420, -140], CRED_IG,
         corpo='={{ JSON.stringify({ image_url: $json.image_url, caption: $json.legenda }) }}', timeout=120000)
    w.liga('Sem erro?', 'Criar contentor (ainda não publica)', 0)
    w.no('Esperar o Instagram', 'n8n-nodes-base.wait', 1.1, {'resume': 'timeInterval', 'amount': 6, 'unit': 'seconds'},
         [2640, -140], webhookId=uid('espera-ig'))
    w.liga('Criar contentor (ainda não publica)', 'Esperar o Instagram')
    http(w, 'Estado do contentor', 'GET',
         f"={IG}/{{{{ $('Criar contentor (ainda não publica)').item.json.body.id || 'sem-id' }}}}?fields=status_code,status",
         [2860, -140], CRED_IG)
    w.liga('Esperar o Instagram', 'Estado do contentor')
    code(w, 'Contentor pronto?', r"""
const p = $('Pronto para criar?').item.json;
const criar = $('Criar contentor (ainda não publica)').item.json;
const s = $json;
const tentativa = $runIndex;   // cada volta pelo «Esperar» conta uma
let estado = 'erro', erro = null;
const id = criar.body && criar.body.id;
if (!id) erro = 'o Instagram recusou criar a publicação: ' + JSON.stringify(criar.body || criar.error || '').slice(0, 300);
else {
  const sc = s.body && s.body.status_code;
  if (sc === 'FINISHED') estado = 'pronto';
  else if ((sc === 'IN_PROGRESS' || !sc) && tentativa < 15) estado = 'esperar';
  else erro = `o Instagram não processou a imagem (${sc || 'sem resposta'}): ` + JSON.stringify((s.body && s.body.status) || s.body || '').slice(0, 200);
}
return { json: { ...p, estado: p.teste && estado === 'pronto' ? 'teste' : estado, erro, contentor: id || null } };
""", [3080, -140], modo='runOnceForEachItem')
    w.liga('Estado do contentor', 'Contentor pronto?')
    escolha(w, 'Continuar?', '={{ $json.estado }}', ['pronto', 'esperar', 'teste', 'erro'], [3300, -140])
    w.liga('Contentor pronto?', 'Continuar?')
    w.liga('Continuar?', 'Esperar o Instagram', 1)
    http(w, 'Publicar (media_publish)', 'POST', f'{IG}/{IG_USER_ID}/media_publish', [3520, -260], CRED_IG,
         corpo='={{ JSON.stringify({ creation_id: $json.contentor }) }}', timeout=120000)
    w.liga('Continuar?', 'Publicar (media_publish)', 0)
    http(w, 'Link da publicação', 'GET', f"={IG}/{{{{ ($json.body && $json.body.id) || 'sem-id' }}}}?fields=permalink,timestamp",
         [3740, -260], CRED_IG)
    w.liga('Publicar (media_publish)', 'Link da publicação')
    code(w, 'Resultado da publicação', js(r"""
const p = $('Contentor pronto?').item.json;
const pub = $('Publicar (media_publish)').item.json;
const media = pub.body && pub.body.id;
const hora = DateTime.now().setZone('__FUSO__').toFormat("dd/MM 'às' HH:mm");
if (!media) {
  return { json: { aid: p.aid, corpo: { estado: 'pendente', fase: 'erro', cor: 'laranja',
    nota: '⚠️ O Instagram recusou publicar: ' + JSON.stringify(pub.body || pub.error || '').slice(0, 300) +
          '\nConfirma no perfil que não saiu antes de tentares outra vez.',
    botoes: [{ id: 'publicar', rotulo: 'Tentar outra vez', emoji: '🔁', estilo: 'primario' }, { id: 'nao', rotulo: 'Não publicar', emoji: '❌', estilo: 'perigo' }] } } };
}
const link = ($json.body && $json.body.permalink) || 'https://www.instagram.com/';
return { json: { aid: p.aid, corpo: { estado: 'concluido', fase: 'publicado', apagar_imagem: true, cor: 'verde',
  resultado: `✅ Publicado a ${hora}: ${link}`, ligacoes: [{ rotulo: 'Ver no Instagram', url: link }],
  contexto: { link, media_id: media } } } };
""", FUSO=FUSO), [3960, -260], modo='runOnceForEachItem')
    w.liga('Link da publicação', 'Resultado da publicação')
    code(w, 'Teste: não publicar', r"""
// Modo [teste]: o contentor foi criado (prova que o Instagram conseguiu ir buscar a imagem), mas NÃO se chama o
// media_publish. Um contentor não publicado não aparece em lado nenhum e caduca sozinho em 24 h.
const p = $json;
return { json: { aid: p.aid, corpo: { estado: 'concluido', fase: 'teste_contentor', apagar_imagem: true, cor: 'cinzento',
  resultado: `🧪 Teste: o Instagram foi buscar a imagem e criou o contentor ${p.contentor}, mas NÃO foi publicado.` } } };
""", [3520, -100], modo='runOnceForEachItem')
    w.liga('Continuar?', 'Teste: não publicar', 2)
    code(w, 'Explicar erro (nada publicado)', r"""
const p = $json;
return { json: { aid: p.aid, corpo: { estado: 'pendente', fase: 'erro', cor: 'laranja',
  nota: '⚠️ Falhou antes de publicar (nada saiu no Instagram): ' + String(p.erro || 'erro desconhecido').slice(0, 600),
  botoes: [{ id: 'publicar', rotulo: 'Tentar outra vez', emoji: '🔁', estilo: 'primario' }, { id: 'nao', rotulo: 'Não publicar', emoji: '❌', estilo: 'perigo' }] } } };
""", [3520, 60], modo='runOnceForEachItem')
    w.liga('Continuar?', 'Explicar erro (nada publicado)', 3)
    w.liga('Sem erro?', 'Explicar erro (nada publicado)', 1)

    http(w, 'Atualizar aprovação', 'POST', ATUALIZAR_URL, [4180, 120], CRED_SERVICO, corpo=ATUALIZAR_CORPO,
         nunca_erro=False, on_error=None)
    for n in ('Agendar', 'Editar', 'Não publicar', 'Resultado da publicação', 'Teste: não publicar', 'Explicar erro (nada publicado)'):
        w.liga(n, 'Atualizar aprovação')

    # ---- o Instagram vem buscar a imagem (webhook publico; a chave e a unica autorizacao)
    webhook(w, 'Instagram vem buscar a imagem', 'GET', 'publicar-imagem', [0, 700])
    se(w, 'Chave com bom aspeto?', "={{ /^[A-Za-z0-9_-]{30,60}$/.test(String(($json.query || {}).c || '')) }}", [220, 700])
    w.liga('Instagram vem buscar a imagem', 'Chave com bom aspeto?')
    http(w, 'Ler imagem do serviço', 'GET', f"={SERVICO}/aprovacao-imagem/{{{{ $json.query.c }}}}", [440, 640], CRED_SERVICO,
         completo=False, nunca_erro=False, ficheiro=True, timeout=30000, on_error='continueErrorOutput')
    w.liga('Chave com bom aspeto?', 'Ler imagem do serviço', 0)
    w.no('Dar a imagem', 'n8n-nodes-base.respondToWebhook', 1.1, {
        'respondWith': 'binary', 'inputDataFieldName': 'data',
        'options': {'responseCode': 200, 'responseHeaders': {'entries': [
            {'name': 'Cache-Control', 'value': 'no-store'}, {'name': 'X-Robots-Tag', 'value': 'noindex'}]}}}, [660, 600])
    w.liga('Ler imagem do serviço', 'Dar a imagem', 0)
    responder(w, 'Não existe', [660, 780], corpo='={{ JSON.stringify({ erro: "nao existe" }) }}', codigo=404)
    w.liga('Ler imagem do serviço', 'Não existe', 1)
    w.liga('Chave com bom aspeto?', 'Não existe', 1)
    return w


# ============================================================== escrever

def gravar(caminho, w):
    destino = AQUI / caminho
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(w.json(), ensure_ascii=False, indent=1) + '\n', encoding='utf-8', newline='\n')
    print('escrito', caminho, len(w.nos), 'nós')


if __name__ == '__main__':
    gravar('avisar-erros.json', wf_erros())
    gravar('tokens-instagram/tokens-instagram.json', wf_tokens())
    gravar('vigia-uptime/vigia-uptime.json', wf_vigia())
    gravar('lembretes-prazos/lembretes-prazos.json', wf_prazos())
    gravar('publicar-instagram/entrada.json', wf_entrada())
    gravar('publicar-instagram/publicacao.json', wf_publicacao())

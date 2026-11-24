# Lembretes do que caduca ⏳

Todos os dias às 09:30 junta tudo o que tem prazo e **só avisa se algo caducar nos próximos 14 dias** (ou se não conseguiu verificar alguma coisa). No máximo uma mensagem por dia.

| O quê | De onde vem |
|---|---|
| Certificados TLS dos teus sites | [sonda](../sonda) `GET /certificados?hosts=…` (liga à porta 443 e lê a data) |
| Domínio | `https://rdap.org/domain/<o-teu-domínio>` (RDAP, o sucessor do WHOIS, em JSON) |
| Tokens do Instagram | sonda `GET /instagram/estado` (o que a [renovação](../tokens-instagram) guardou) |
| Túnel (ex.: `cloudflared`) | sonda `GET /tunel`: a correr, reinícios, ligações prontas. Opcional |
| Prazos teus | lista `FIXOS` no nó «Juntar prazos e decidir» (ex.: renovar um seguro, fim de um período de espera) |

Os certificados da Cloudflare, do GitHub Pages ou do Let's Encrypt renovam-se sozinhos cerca de 30 dias antes. Se algum chegar aos 14 dias, é porque a renovação automática falhou: é exatamente isso que queres saber.

## Requisitos

- A [sonda](../sonda) (para os certificados, tokens e túnel). Sem ela, o aviso diz «não consegui verificar».
- O [Discord aprovações](../../discord-aprovacoes).

## Configurar

| Onde | O quê |
|---|---|
| `gerar.py` → `HOSTS_TLS` | os teus sites (só o nome, sem `https://`) |
| `gerar.py` → `DOMINIO` | o teu domínio |
| nó «Juntar prazos e decidir» → `FIXOS` | `{ nome, expira_em: 'AAAA-MM-DD', nota }` |
| nó «Juntar prazos e decidir» → `LIMITE` | dias de antecedência (14) |

## Testar

No nó «Juntar prazos e decidir» muda `LIMITE` para `400` e `TESTE = true` → **Execute workflow** → mensagem `[teste]` com tudo o que é seguido. Repõe os valores.

## Desligar

Desliga o workflow no n8n.

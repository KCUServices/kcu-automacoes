# Workflows do n8n ⚙️

Modelos de workflows prontos a importar no [n8n](https://n8n.io/) (testados no n8n 2.x, *self-hosted* em Docker). Todos avisam pelo [Discord aprovações](../discord-aprovacoes): nada de webhooks do Discord espalhados por cada workflow.

| Pasta | O que faz | Quando corre |
|---|---|---|
| [`tokens-instagram`](tokens-instagram) | Renova os tokens do Instagram e grava o novo **na mesma credencial** do n8n (com a [sonda](sonda)) | segunda às 10:00 |
| [`vigia-uptime`](vigia-uptime) | Vê se os teus sites e APIs públicos respondem. 2 falhas seguidas → «Em baixo»; quando volta → «Voltou» | de 5 em 5 min |
| [`lembretes-prazos`](lembretes-prazos) | Certificados TLS, domínio (RDAP), tokens e prazos teus: avisa só se algo caducar em ≤ 14 dias | todos os dias às 09:30 |
| [`publicar-instagram`](publicar-instagram) | Pasta no PC → validação → aprovação no Discord → publicação no Instagram | quando pões uma imagem na pasta |
| [`avisar-erros.json`](avisar-erros.json) | *Error workflow* de todos os outros: avisa quando um falha (o mesmo erro só de 6 em 6 h) | quando um workflow falha |
| [`sonda`](sonda) | Serviço pequeno no anfitrião do Docker: faz o que o n8n não consegue sozinho | sempre ligado |

## Antes de importar

1. Abre o [`gerar.py`](gerar.py) e muda a secção **CONFIGURAÇÃO**: endereço do serviço de aprovações, da sonda, do teu n8n (interno e público), o id da conta do Instagram, os nomes dos canais e o fuso.
2. Corre `python gerar.py`: reescreve os `.json` de cada pasta com os teus valores.
3. Cria as credenciais no n8n (**Credentials → New → Header Auth**):

| Credencial (nome sugerido) | Name | Value | Usada por |
|---|---|---|---|
| Discord aprovações (X-Token) | `X-Token` | o `API_TOKEN` do serviço | todos (enviar avisos e aprovações; receber o callback) |
| Sonda (X-Token) | `X-Token` | o `SONDA_TOKEN` da sonda | tokens, prazos |
| Instagram (Authorization: Bearer) | `Authorization` | `Bearer <token IGAA…>` | tokens, publicar |
| Pasta de publicar (X-Token) | `X-Token` | um token teu, 32+ caracteres | publicar (webhooks que o PC chama) |

4. **Import from File** de cada `.json`. Os nós com credenciais aparecem com aviso: abre-os e escolhe a tua credencial.
5. Importa e **publica/ativa primeiro o `avisar-erros.json`**: no n8n 2.x um *Error workflow* inativo é ignorado. Os outros já apontam para ele (`errorWorkflow: autoAvisarErros1`).

## Formato dos avisos

Todos os workflows acabam num nó «Enviar aviso (Discord)» que faz `POST /aviso` com:

```json
{"canal": "alertas", "titulo": "…", "descricao": "…", "nivel": "erro|aviso|ok|info",
 "campos": [{"nome": "…", "valor": "…", "inline": false}], "links": [{"texto": "…", "url": "https://…"}],
 "mencionar": true, "teste": false, "rodape": "…"}
```

`teste: true` põe `[teste]` no título: usa-o sempre que estiveres a experimentar.

## Notas do n8n 2.x

- Importar ou ativar pelo CLI (`n8n import:workflow`, `n8n publish:workflow`) só vale depois de reiniciar o n8n. Pela interface (botão *Publish*/*Active*) vale logo.
- Correr um workflow pelo CLI guardando o estado como em produção: `docker exec -e N8N_RUNNERS_BROKER_PORT=5699 <contentor> n8n execute --id=<id>` (a porta extra evita o conflito com o n8n que já está a correr).
- O estado entre execuções (falhas seguidas, avisos já dados) fica nos *static data* de cada workflow. As execuções à mão **não** guardam esse estado.

## Desligar

Na interface: abre o workflow e desliga **Active/Published**. Nada mais muda.

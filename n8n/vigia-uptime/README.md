# Vigia do que é público 👀

Um monitor de *uptime* simples, dentro do n8n: de 5 em 5 minutos pede cada endereço da tua lista e confirma o **código 200** e um **texto conhecido** na resposta (ou a resposta exata). Não enche o canal:

- **1 falha:** nada (pode ser um soluço).
- **2 falhas seguidas:** aviso vermelho «**Em baixo:** o meu site», com menção, o que viu e desde quando.
- Pelo meio: nada.
- **Quando volta:** aviso verde «**Voltou:** o meu site», com quanto tempo esteve em baixo.

## Requisitos

- O [Discord aprovações](../../discord-aprovacoes) para os avisos.
- Nada mais: só pedidos HTTP.

## Configurar

No [`gerar.py`](../gerar.py), lista `ALVOS` (ou depois de importar, no nó «Escolher o que testar agora»):

```js
{ id: 'site', nome: 'O meu site', url: 'https://example.com/', texto: 'Example Domain', cada_min: 5 },
{ id: 'api', nome: 'API pública (saúde)', url: 'https://example.com/saude', exato: 'ok', cada_min: 5 },
```

| Campo | Para quê |
|---|---|
| `id` | único; é a chave do estado (falhas seguidas, desde quando) |
| `texto` | tem de aparecer na página (sem distinguir maiúsculas). Escolhe algo que só aparece quando está tudo bem |
| `exato` | em vez de `texto`: a resposta inteira tem de ser igual (bom para `/saude` que responde `ok`) |
| `cada_min` | de quantos em quantos minutos (5, 15, 60…). Útil para endereços que criam trabalho do outro lado |

As execuções com sucesso não ficam guardadas (seriam 288 por dia); as com erro ficam.

**Truque para webhooks:** para saber se um webhook teu está registado e ligado, sem saber o segredo, pede-o com um parâmetro errado de propósito e espera a resposta de recusa exata (ex.: o desafio de verificação da Meta com um `verify_token` errado devolve a tua mensagem de recusa; se o workflow estiver desligado, o n8n dá 404).

## Testar

No nó «Escolher o que testar agora», muda um `url` para uma página que não existe e `TESTE = true`. Como as execuções à mão não guardam o estado, testa pelo CLI (ver [README do n8n](../README.md#notas-do-n8n-2x)) duas vezes → «Em baixo» `[teste]`; repõe o `url` e corre outra vez → «Voltou» `[teste]`.

## Desligar

Desliga o workflow no n8n.

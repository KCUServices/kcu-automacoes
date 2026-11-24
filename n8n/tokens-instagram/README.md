# Renovar tokens do Instagram 🔑

Os tokens de longa duração da API do Instagram (*Instagram API with Instagram Login*) duram **60 dias**. Se não os renovares, os teus workflows (publicar, ler DMs, estatísticas) param de um dia para o outro. Este workflow renova-os sozinho todas as segundas e grava o token novo **na mesma credencial do n8n**, por isso nenhum outro workflow precisa de mudar.

```
segunda 10:00 → POST /instagram/renovar (sonda) → para cada credencial: verifica → renova se faltarem < 45 dias
              → grava na mesma credencial → aviso verde («renovado») ou vermelho com os passos para gerar à mão
```

## Requisitos

- A [sonda](../sonda) instalada no anfitrião do Docker, com os ids das tuas credenciais do Instagram em `sonda.json`.
- Cada credencial do Instagram é do tipo **Header Auth**: **Name** `Authorization`, **Value** `Bearer IGAA…`.
- O [Discord aprovações](../../discord-aprovacoes) para os avisos.

## Configurar

| Onde | O quê |
|---|---|
| `gerar.py` → `SONDA` | endereço da sonda visto de dentro do contentor do n8n |
| `gerar.py` → `CANAL_AVISOS` | canal dos avisos |
| nó «Opções» | `teste: true` (só verifica) ou `forcar: true` (renova já, se o token tiver mais de 24 h) |

Quando avisa: **falha** (logo, com menção e os passos para gerar um token à mão), **faltam ≤ 10 dias**, ou **houve renovação** (verde, sem menção).

Se trocares o token à mão, a sonda nota que mudou (pela impressão sha256), assume 60 dias a partir dessa alteração («validade estimada») e renova-o na segunda seguinte, passadas 24 h, para ficar com a data certa.

## Testar

Nó «Opções» → `teste: true` → **Execute workflow** → mensagem verde `[teste] Tokens do Instagram verificados (sem renovar)`. Volta a pôr `teste: false`.

## Desligar

Desliga o workflow no n8n. Os tokens continuam válidos até ao fim dos 60 dias; depois tens de os gerar à mão.

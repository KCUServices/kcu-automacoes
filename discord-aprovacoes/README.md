# Discord aprovações 🔘

Um serviço pequeno em Python que põe **avisos e pedidos de aprovação com botões** num canal do Discord e devolve a decisão a quem pediu. Só as pessoas da tua lista podem carregar nos botões; para os outros aparece «Só os aprovadores podem decidir isto» e nada muda.

É a peça central das outras pastas: o [n8n](../n8n) pede aprovação antes de publicar no Instagram, as [melhorias com IA](../melhorias-com-ia) pedem aprovação antes de juntar um pull request, e tudo o que for aviso (site em baixo, token a caducar) passa por aqui.

```
programa teu ──POST /aprovacao──► serviço ──► mensagem com botões em #aprovacoes
                                               │ clique de um aprovador
                       callback (empurrar) ◄───┤ ou
     GET /aprovacoes?estado=decidido (puxar) ◄─┘
programa teu ──POST /aprovacao/<id>/reservar──► (só uma vez) ──► faz o trabalho ──► POST /atualizar
```

## O que precisas

- Um Linux com systemd e Python 3.10+ (uma VM pequena, um contentor LXC no Proxmox, um Raspberry Pi).
- Um bot do Discord no teu servidor (ver as duas opções abaixo).
- Os programas que vão usar a API têm de estar na mesma rede (a API **não** é para pôr na Internet).

## Duas formas de ligar o bot

### Opção A — um bot só para isto (a mais simples)

1. [Discord Developer Portal](https://discord.com/developers/applications) → **New Application** → **Bot** → **Reset Token** e copia o token.
2. Não precisas de nenhum *Privileged Gateway Intent*: o serviço liga-se com `Intents.none()`.
3. **OAuth2 → URL Generator**: escopo `bot`, permissões *View Channels*, *Send Messages*, *Embed Links*, *Attach Files*, *Read Message History*. Abre o link e adiciona o bot ao servidor.
4. Dá-lhe acesso só aos canais que vai usar (ex.: `#alertas` e `#aprovacoes`, privados).

### Opção B — uma segunda sessão de um bot que já tens

Se já tens um bot a correr (um assistente, um bot de música…) e não queres mais um na lista de membros, podes usar o **mesmo token**. O Discord aceita várias ligações ao gateway com o mesmo token e entrega os cliques (interações) **a todas**. O truque para não se atropelarem:

- este serviço liga-se com **`Intents.none()`**: não recebe mensagens, presenças nem conteúdo, por isso nunca responde a conversas e quase não gasta nada;
- só trata cliques cujo `custom_id` começa pelo **prefixo** (`PREFIXO_BOTOES`, por omissão `apr:`) e **não responde** a mais nenhum;
- o teu outro bot tem de fazer o mesmo ao contrário: ignorar os cliques que não são dele. Bots em discord.py que só usam `View`s registadas já fazem isso. Se o teu tiver um `on_interaction` que responde a tudo, muda-o para ignorar o prefixo `apr:` (ou usa a opção A).

Cuidado: cada clique só pode ter **uma** resposta. Se os dois lados responderem ao mesmo clique, um deles recebe erro. E se trocares o token no outro bot, tens de o trocar aqui também.

## Instalar

```sh
# no servidor, como root, com esta pasta copiada para lá
sh instalar.sh                       # 1.ª vez: cria /etc/discord-aprovacoes/discord-aprovacoes.env
nano /etc/discord-aprovacoes/discord-aprovacoes.env    # preenche o token, os aprovadores e os canais
sh instalar.sh                       # 2.ª vez: arranca o serviço
systemctl status discord-aprovacoes
```

O `instalar.sh` cria o utilizador `aprovacoes`, um `venv` em `/opt/discord-aprovacoes`, gera o `API_TOKEN` (nunca o mostra) e instala o [`discord-aprovacoes.service`](discord-aprovacoes.service).

## Configuração (`.env`)

| Variável | Para quê | Exemplo |
|---|---|---|
| `DISCORD_BOT_TOKEN` | token do bot (opção A ou B) | — |
| `APROVADORES` | IDs de utilizador que podem carregar nos botões | `123…,456…` |
| `CANAL_<NOME>_ID` | um por canal; o nome é o que usas no pedido (`"canal": "alertas"`) | `CANAL_ALERTAS_ID=…` |
| `AVISO_CANAIS` | canais que aceitam `POST /aviso` | `alertas` |
| `PREFIXO_BOTOES` | prefixo do `custom_id` (só estes cliques são tratados) | `apr` |
| `API_ESCUTA` | IP:porta da API (o IP da máquina na rede de casa) | `10.0.0.30:8790` |
| `API_ORIGENS` | IPs ou redes que podem chamar a API | `10.0.0.20,10.0.0.0/28` |
| `API_TOKEN` | token do cabeçalho `X-Token` (32+ caracteres) | gerado |
| `CALLBACK_PREFIXO` | só se aceitam callbacks para URLs que começam assim; vazio = só modo puxar | `http://10.0.0.20:5678/webhook/` |
| `CALLBACK_TOKEN` | token enviado nos callbacks (vazio = o `API_TOKEN`) | — |

Todos os exemplos de IP são inventados: põe os da tua rede.

## A API

Todos os pedidos (menos `/saude`) precisam do cabeçalho `X-Token` **e** de vir de um IP de `API_ORIGENS`. Caso contrário: `403`.

| Pedido | O que faz |
|---|---|
| `POST /aviso` | aviso simples, com cor por nível (`erro`, `aviso`, `ok`, `info`). Formato em [`aviso.py`](aviso.py) e [`exemplos/aviso.json`](exemplos/aviso.json) |
| `POST /aprovacao` | pedido com botões (até 5), campos, ligações e imagem opcional (`imagem_b64`, JPEG/PNG até 10 MB). Idempotente pelo `id`. Sem botões = aviso que fica logo concluído. Exemplo em [`exemplos/aprovacao.json`](exemplos/aprovacao.json) |
| `GET /aprovacao/<id>` | estado atual |
| `GET /aprovacoes?origem=…&estado=…&fase=…&desde=<epoch>` | lista (é assim que funciona o modo puxar) |
| `POST /aprovacao/<id>/reservar` | passa a «a executar» **uma única vez**; um 2.º pedido devolve `ok: false`. É isto que impede publicar duas vezes com dois cliques |
| `POST /aprovacao/<id>/atualizar` | `estado` (`pendente` volta a mostrar botões, `a_executar`, `concluido`), `nota`, `resultado`, `cor`, `fase`, `botoes`, `ligacoes`, `campos_extra`, `contexto`, `apagar_imagem` |
| `POST /aprovacao/<id>/chave` | chave aleatória de 15 min para servir a imagem (ver [publicar no Instagram](../n8n/publicar-instagram)) |
| `GET /aprovacao-imagem/<chave>` | os bytes da imagem (também com token) |

**Empurrar ou puxar.** Se o pedido trouxer `callback`, o bot chama esse URL com `{id, origem, decisao, simulado, contexto}` assim que alguém carrega (e repete de minuto a minuto, até 30 vezes, se o outro lado estiver em baixo). Sem `callback`, quem pediu pergunta de vez em quando `GET /aprovacoes?origem=…&estado=decidido`. O modo puxar serve para um PC que não tem endereço fixo nem portas abertas.

## Testar

```sh
cd /opt/discord-aprovacoes
sudo -u aprovacoes venv/bin/python ferramenta.py aviso-teste alertas          # aviso [teste] no canal
sudo -u aprovacoes venv/bin/python ferramenta.py aprovacao-teste aprovacoes   # aprovação [teste] com 2 botões
sudo -u aprovacoes venv/bin/python ferramenta.py listar
sudo -u aprovacoes venv/bin/python ferramenta.py simular apr-teste-xxxxxxxx sim   # o mesmo código do botão
```

Depois carrega num botão a sério (com a tua conta e com outra que não esteja em `APROVADORES`, para veres a recusa). Para testar a API a partir de outra máquina: [`exemplos/testar.sh`](exemplos/testar.sh).

Logs: `/var/log/discord-aprovacoes/bot.log` e `journalctl -u discord-aprovacoes`. Estado: `/var/lib/discord-aprovacoes/estado.db` (SQLite).

## Desligar

```sh
systemctl disable --now discord-aprovacoes
```

Os botões deixam de responder (o Discord mostra «Esta interação falhou») e a API fecha. Nada é apagado: voltar a ligar retoma as aprovações pendentes. Para remover de vez: apaga `/opt/discord-aprovacoes`, `/etc/discord-aprovacoes`, `/var/lib/discord-aprovacoes`, o `.service` e o utilizador `aprovacoes`.

# Sonda do n8n 🔌

Um serviço pequeno (Python, só biblioteca padrão) que corre **no anfitrião do Docker** onde está o teu n8n e faz o que o n8n não consegue fazer sozinho:

- **renovar os tokens do Instagram e gravá-los na mesma credencial do n8n** (usado por [tokens-instagram](../tokens-instagram));
- ler a **validade dos certificados TLS** dos teus sites e o **estado do túnel** (usado por [lembretes-prazos](../lembretes-prazos)).

O n8n continua a ser quem decide quando corre e quem avisa. A sonda só responde.

## Porquê uma sonda

Um workflow do n8n **não consegue alterar as suas próprias credenciais**. As alternativas eram piores:

- a **API pública do n8n** obrigava a criar uma chave de API com poder sobre tudo e a guardá-la como credencial; e não devolve o valor das credenciais, por isso o token teria de passar pelos dados das execuções;
- o **CLI do n8n dentro do contentor** (`n8n export:credentials --decrypted` + `n8n import:credentials`) é o mecanismo oficial: o n8n cifra com a sua própria chave, a credencial mantém o **mesmo id** (os workflows não notam nada), o dono e as partilhas, e o token nunca aparece no n8n, nos logs nem no Discord.

O que a sonda faz em cada credencial do Instagram:

1. lê a credencial decifrada (ficheiro temporário dentro do contentor, apagado logo a seguir);
2. confirma que o token funciona (`GET graph.instagram.com/v23.0/me`);
3. renova **só se** a validade não for conhecida ou faltarem menos de 45 dias, e se o token tiver pelo menos 24 h (regra da Meta): `GET /refresh_access_token?grant_type=ig_refresh_token`;
4. confirma que o token novo funciona, guarda uma **cópia cifrada** da credencial antiga (`/var/lib/n8n-sonda/copias/`, as 10 últimas), grava o token novo e volta a ler para confirmar;
5. guarda só metadados (`/var/lib/n8n-sonda/instagram.json`): validade, conta, e uma impressão sha256 curta para saber se alguém trocou o token à mão.

## Instalar

No anfitrião do Docker (Linux, como root):

```sh
sh instalar.sh
nano /etc/n8n-sonda/sonda.json      # nome do contentor do n8n, ids das credenciais do Instagram, contentor do túnel
systemctl restart n8n-sonda
```

| Ficheiro / variável | Para quê |
|---|---|
| `sonda.json` → `n8n_container` | nome do contentor do n8n (`docker ps`) |
| `sonda.json` → `instagram` | `[{"id": "<id da credencial>", "conta": "<@>"}]`. O id está no URL da credencial no n8n. A credencial é do tipo *Header Auth* com **Name** `Authorization` e **Value** `Bearer <token>` |
| `sonda.json` → `tunel_container` | opcional: contentor do `cloudflared` (vazio = não verifica) |
| `sonda.env` → `SONDA_ESCUTA` | IP:porta onde escuta: o *gateway* da rede Docker do n8n (por omissão `172.17.0.1`, a `docker0`; vê com `docker network inspect <rede>`) |
| `sonda.env` → `SONDA_ORIGENS` | rede que pode chamar (a rede Docker do n8n) |
| `sonda.env` → `SONDA_TOKEN` | gerado pelo `instalar.sh`; põe o mesmo valor numa credencial *Header Auth* do n8n com **Name** `X-Token` |

Se mudares o IP, muda também a constante `SONDA` no [`gerar.py`](../gerar.py) e volta a gerar os workflows.

## Testar

```sh
systemctl status n8n-sonda
tail /var/log/n8n-sonda/sonda.log
docker exec <contentor-do-n8n> wget -qO- http://172.17.0.1:8791/saude     # {"ok": true}
```

No n8n: workflow **Renovar tokens do Instagram** → nó «Opções» → `teste: true` → *Execute workflow* (só verifica, não renova).

## Desligar

`systemctl disable --now n8n-sonda`. Os workflows passam a avisar «não consegui verificar». Para voltar ao token antigo: copia uma das cópias de `/var/lib/n8n-sonda/copias/` para dentro do contentor (`docker cp`) e corre `docker exec <contentor> n8n import:credentials --input=<ficheiro>`; depois apaga o ficheiro do contentor.

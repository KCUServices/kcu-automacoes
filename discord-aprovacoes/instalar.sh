#!/bin/sh
# Instala ou atualiza o Discord aprovacoes num Linux com systemd (Debian/Ubuntu, VM ou contentor LXC).
# Corre como root a partir desta pasta:  sh instalar.sh
# Idempotente: volta a correr para atualizar o codigo. Nunca reescreve o ficheiro de ambiente se ja existir.
set -e
AQUI=$(cd "$(dirname "$0")" && pwd)
AMB=/etc/discord-aprovacoes/discord-aprovacoes.env

command -v python3 >/dev/null || { echo "falta o python3"; exit 1; }
python3 -c 'import venv' 2>/dev/null || { echo "falta o python3-venv (apt install python3-venv)"; exit 1; }

# utilizador proprio, sem login
id aprovacoes >/dev/null 2>&1 || useradd --system --home-dir /var/lib/discord-aprovacoes --shell /usr/sbin/nologin aprovacoes
install -d -o aprovacoes -g aprovacoes -m 700 /var/lib/discord-aprovacoes /var/log/discord-aprovacoes
install -d -o root -g root -m 755 /opt/discord-aprovacoes /etc/discord-aprovacoes

# codigo + dependencias
install -o root -g root -m 644 "$AQUI"/comum.py "$AQUI"/aprovacao.py "$AQUI"/aviso.py "$AQUI"/bot.py "$AQUI"/ferramenta.py /opt/discord-aprovacoes/
[ -d /opt/discord-aprovacoes/venv ] || python3 -m venv /opt/discord-aprovacoes/venv
/opt/discord-aprovacoes/venv/bin/pip install -q --disable-pip-version-check -r "$AQUI"/requirements.txt

# ficheiro de ambiente: so na primeira vez; o token da API e gerado aqui e nunca e mostrado
if [ ! -f "$AMB" ]; then
  umask 077
  sed "s/^API_TOKEN=.*/API_TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(32))')/" "$AQUI"/.env.exemplo > "$AMB"
  echo "Criei $AMB: preenche DISCORD_BOT_TOKEN, APROVADORES e os CANAL_*_ID e corre outra vez este script."
fi
chown aprovacoes:aprovacoes "$AMB"
chmod 600 "$AMB"

install -o root -g root -m 644 "$AQUI"/discord-aprovacoes.service /etc/systemd/system/discord-aprovacoes.service
systemctl daemon-reload
if grep -q '^DISCORD_BOT_TOKEN=.\+' "$AMB" && grep -q '^APROVADORES=.\+' "$AMB"; then
  systemctl enable discord-aprovacoes >/dev/null 2>&1
  systemctl restart discord-aprovacoes
  echo "instalado e a correr: systemctl status discord-aprovacoes"
else
  echo "instalado, mas ainda nao arrancado (falta configuracao em $AMB)"
fi

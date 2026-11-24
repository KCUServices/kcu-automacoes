#!/bin/sh
# Instala ou atualiza a sonda do n8n no anfitriao do Docker. Corre como root a partir desta pasta:  sh instalar.sh
# Idempotente. Nao mexe no n8n: so usa o CLI dele (dentro do contentor) para ler e gravar as credenciais do Instagram.
set -e
AQUI=$(cd "$(dirname "$0")" && pwd)
command -v docker >/dev/null || { echo "falta o docker"; exit 1; }
install -d -o root -g root -m 755 /opt/n8n-sonda
install -d -o root -g root -m 700 /etc/n8n-sonda /var/lib/n8n-sonda /var/lib/n8n-sonda/copias /var/log/n8n-sonda
install -o root -g root -m 644 "$AQUI"/sonda.py /opt/n8n-sonda/sonda.py
[ -f /etc/n8n-sonda/sonda.json ] || install -o root -g root -m 600 "$AQUI"/sonda.exemplo.json /etc/n8n-sonda/sonda.json

# IP do anfitriao na rede do Docker onde esta o n8n (por omissao a docker0). Muda SONDA_ESCUTA se o n8n estiver
# noutra rede (docker network inspect <rede> -> Gateway).
AMB=/etc/n8n-sonda/sonda.env
if [ ! -f "$AMB" ]; then
  umask 077
  { echo "SONDA_TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    echo 'SONDA_ESCUTA=172.17.0.1:8791'
    echo 'SONDA_ORIGENS=172.17.0.0/16'; } > "$AMB"
  echo "Criei $AMB (com um token novo). Copia o SONDA_TOKEN para a credencial do n8n:"
  echo "  grep ^SONDA_TOKEN= $AMB | cut -d= -f2-"
fi
chmod 600 "$AMB"
install -o root -g root -m 644 "$AQUI"/n8n-sonda.service /etc/systemd/system/n8n-sonda.service
systemctl daemon-reload
systemctl enable n8n-sonda >/dev/null 2>&1
systemctl restart n8n-sonda
echo "instalado: systemctl status n8n-sonda"

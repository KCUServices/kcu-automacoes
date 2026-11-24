#!/bin/sh
# Testa a API local com curl. Corre num IP que esteja em API_ORIGENS.
#   API=http://127.0.0.1:8790 TOKEN=... sh exemplos/testar.sh
# O token nunca e escrito no ecra; le-o de uma variavel de ambiente.
set -e
API=${API:-http://127.0.0.1:8790}
[ -n "$TOKEN" ] || { echo "define TOKEN (o API_TOKEN do ficheiro de ambiente)"; exit 1; }
AQUI=$(cd "$(dirname "$0")" && pwd)

echo "1) saude"
curl -fsS "$API/saude"; echo

echo "2) token errado -> tem de dar 403"
curl -s -o /dev/null -w '%{http_code}\n' -H 'X-Token: errado' "$API/aprovacoes?origem=exemplo"

echo "3) aviso"
curl -fsS -H "X-Token: $TOKEN" -H 'Content-Type: application/json' --data @"$AQUI/aviso.json" "$API/aviso"; echo

echo "4) aprovacao (modo puxar: sem callback)"
curl -fsS -H "X-Token: $TOKEN" -H 'Content-Type: application/json' --data @"$AQUI/aprovacao.json" "$API/aprovacao"; echo

echo "5) carrega num botao no Discord (ou: ferramenta.py simular apr-exemplo-0001 aprovar) e depois:"
echo "   curl -H \"X-Token: \$TOKEN\" '$API/aprovacoes?origem=exemplo&estado=decidido'"
echo "   curl -H \"X-Token: \$TOKEN\" -X POST '$API/aprovacao/apr-exemplo-0001/reservar'"
echo "   curl -H \"X-Token: \$TOKEN\" -H 'Content-Type: application/json' -X POST \\"
echo "        --data '{\"estado\":\"concluido\",\"cor\":\"verde\",\"resultado\":\"Feito.\"}' '$API/aprovacao/apr-exemplo-0001/atualizar'"

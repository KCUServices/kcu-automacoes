# O que mudou

Cada versão fica ligada ao episódio em que foi mostrada. As ideias da comunidade levam o nome de quem as deu.

## [ep00] Primeira versão pública: 2026-11-24

- **Discord aprovações:** avisos e pedidos de aprovação com botões, só para os aprovadores da lista; API local com token e lista de IPs; modo empurrar (callback) e modo puxar; reserva de uso único; imagem com chave de 15 min; pode usar um bot só para isto ou uma segunda sessão (`Intents.none()`) de um bot que já tens.
- **Melhorias com IA:** issue ou ideia → análise com o Claude Code sem interface (`claude -p`) num git worktree, com ferramentas limitadas e a tua subscrição → PR + CI → Aprovar/Recusar no Discord → *squash merge* ou fecho com comentário.
- **Recetor de webhooks:** Cloudflare Worker que verifica webhooks no formato Standard Webhooks e os guarda numa fila; script de casa que os vai buscar, volta a verificar e entrega. 12 testes do Worker e testes das assinaturas do lado de casa.
- **n8n:** renovar tokens do Instagram (com a sonda), vigia de uptime, lembretes do que caduca (TLS, domínio, tokens), publicar no Instagram com aprovação no Discord e aviso de erros dos workflows.

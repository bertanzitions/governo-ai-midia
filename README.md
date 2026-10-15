# governo.ai — mídia e publicação

Repositório público usado só na hora de publicar no Instagram do @governo.ai.

1. A esteira envia as imagens do post e um `pub/<lote>/job.json`.
2. O GitHub Actions (`.github/workflows/publicar.yml`) publica pelo Instagram com o segredo `IG_TOKEN`.
3. O resultado vai para `resultados/<lote>.json` e as imagens são apagadas.

Configuração (Settings → Secrets and variables → Actions):
- Secret `IG_TOKEN`: token do Instagram (vale 60 dias).
- Variable `IG_TOKEN_GERADO_EM`: data em que o token foi gerado (AAAA-MM-DD).

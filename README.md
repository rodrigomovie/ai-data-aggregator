# AI Data Aggregator

Pipeline modular em Python para agregação de dados (Web Scraping + Consumo de APIs REST), acompanhado de um site estático **AI-first** (otimizado tanto para consumo direto por Agentes de IA quanto para leitura humana) e automação diária via **GitHub Actions**.

## Arquitetura do Projeto

```text
├── .github/
│   └── workflows/
│       └── update_data.yml   # Cron job diário (00:00 UTC) + commit automático
├── config/
│   └── sources.json          # Cadastro centralizado de URLs web e endpoints de APIs
├── public/
│   ├── data/
│   │   └── data.json         # Output JSON estruturado para consumo de Agentes de IA e Frontend
│   ├── llms.txt              # Output em Markdown puro (padrão llms.txt) para LLMs
│   ├── index.html            # Interface semântica com alternância Cards / Markdown / JSON
│   ├── style.css             # Estilização limpa, responsiva e com suporte a Dark/Light mode
│   └── app.js                # Fetch de data.json, filtros e visualização
├── src/
│   ├── __init__.py
│   ├── extractor.py          # Extração web com requests + BeautifulSoup (seletores CSS ou texto limpo)
│   ├── api_client.py         # Cliente de API com paginação, autenticação via env vars e nested queries
│   └── main.py               # Orquestrador que lê config/sources.json e gera data.json + llms.txt
└── requirements.txt          # Dependências Python
```

## Por que este site é amigável para Agentes de IA?

1. **JSON Estruturado (`/data/data.json`):** Schema padronizado e previsível com metadados de coleta (`generated_at_utc`, contagem de itens, status por fonte).
2. **Padrão `/llms.txt`:** Arquivo em texto/Markdown puro gerado automaticamente em `public/llms.txt`, permitindo que agentes e LLMs consumam todo o conteúdo com o menor custo de tokens possível, sem precisar processar tags HTML.
3. **Descoberta Automática (`<link rel="alternate">` + JSON Embutido):** O `index.html` declara os endpoints de dados no `<head>` e também injeta o payload em `<script id="ai-agent-structured-data" type="application/json">`.

## Como adicionar novas fontes

Edite o arquivo [`config/sources.json`](config/sources.json):

- **`web_sources`**: Adicione páginas para extração via `BeautifulSoup`.
  - Se informar `container_selector` e `fields`, o extrator coleta múltiplos itens estruturados.
  - Se deixar `extraction_rules` vazio (`{}`), o extrator remove menus/scripts e extrai o texto principal limpo da página automaticamente.
- **`api_sources`**: Adicione endpoints de APIs REST.
  - Suporta autenticação via variável de ambiente (`bearer`, `header`, `query`).
  - Suporta paginação (`page`, `offset`, `cursor`, `next_url`).
  - Suporta `nested_queries` para chamadas sequenciais/aninhadas.

## Execução Local

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate | Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m src.main --config config/sources.json
```

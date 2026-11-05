"""
Orquestrador principal do Pipeline de Agregação de Dados.
Lê config/sources.json (ou YAML), executa extrações web e consumo de APIs,
e gera outputs otimizados para Agentes de IA tanto em `public/` quanto na raiz (`llms.txt`).
"""

import argparse
import datetime
import html
import json
import logging
from pathlib import Path
from typing import Any, Dict, List
import yaml

from src.extractor import WebExtractor
from src.api_client import APIClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("aggregator")


def load_config(config_path: Path) -> Dict[str, Any]:
    """Carrega o arquivo de configuração em formato JSON ou YAML."""
    if not config_path.exists():
        raise FileNotFoundError(f"Arquivo de configuração não encontrado: {config_path}")

    content = config_path.read_text(encoding="utf-8")
    if config_path.suffix.lower() in (".yaml", ".yml"):
        return yaml.safe_load(content)
    return json.loads(content)


def load_previous_sources(output_json_path: Path) -> Dict[str, Dict[str, Any]]:
    """Carrega os resultados anteriores por ID de fonte para resiliência contra bloqueios temporários."""
    if not output_json_path.exists():
        return {}
    try:
        data = json.loads(output_json_path.read_text(encoding="utf-8"))
        return {
            src["id"]: src
            for src in data.get("sources", [])
            if isinstance(src, dict) and src.get("id") and src.get("items")
        }
    except Exception:  # pylint: disable=broad-except
        return {}


def generate_llms_txt(aggregated_data: Dict[str, Any]) -> str:
    """
    Gera uma representação em Markdown puro (padrão llms.txt) para leitura direta
    e altamente eficiente de tokens por Agentes de IA.
    """
    meta = aggregated_data.get("metadata", {})
    lines: List[str] = [
        f"# {meta.get('project_name', 'AI Data Aggregator')}",
        f"> {meta.get('description', 'Dados consolidados para consumo por Agentes de IA.')}",
        "",
        f"- **Última atualização (UTC):** `{meta.get('generated_at_utc', '')}`",
        f"- **Total de fontes processadas:** `{meta.get('total_sources', 0)}`",
        f"- **Total de registros extraídos:** `{meta.get('total_items', 0)}`",
        "- **Endpoint JSON Estruturado:** `/public/data/data.json`",
        "",
        "---",
        "",
    ]

    for source in aggregated_data.get("sources", []):
        lines.append(f"## [{source.get('type', 'source').upper()}] {source.get('name')}")
        lines.append(f"- **ID:** `{source.get('id')}`")
        lines.append(f"- **Categoria:** `{source.get('category')}`")
        lines.append(f"- **Origem:** {source.get('source_url')}")
        lines.append(f"- **Status:** `{source.get('status')}` ({source.get('items_count', 0)} itens)")
        if source.get("note"):
            lines.append(f"- **Nota:** {source.get('note')}")
        if source.get("error"):
            lines.append(f"- **Erro:** `{source.get('error')}`")
        lines.append("")

        for idx, item in enumerate(source.get("items", []), start=1):
            title = item.get("title") or item.get("name") or f"Item #{idx}"
            url = item.get("link") or item.get("url")
            header_str = f"### {idx}. [{title}]({url})" if url else f"### {idx}. {title}"
            lines.append(header_str)

            for key, value in item.items():
                if key in ("title", "name", "link", "url") or value is None or value == "":
                    continue
                if isinstance(value, (dict, list)):
                    lines.append(f"- **{key}:** `{json.dumps(value, ensure_ascii=False)}`")
                else:
                    lines.append(f"- **{key}:** {value}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def generate_static_index_html(aggregated_data: Dict[str, Any]) -> str:
    """
    Gera um index.html 100% estático, somente texto e altamente indexável
    (sem dependência de JavaScript) para que agentes como Microsoft Copilot,
    Bing, ChatGPT, Perplexity e crawlers leiam todo o conteúdo diretamente no HTML.
    """
    meta = aggregated_data.get("metadata", {})
    project_name = html.escape(str(meta.get("project_name", "AI Data Aggregator")))
    description = html.escape(
        str(meta.get("description", "Dados consolidados para consumo por Agentes de IA."))
    )
    generated_at = html.escape(str(meta.get("generated_at_utc", "")))
    total_sources = int(meta.get("total_sources", 0))
    total_items = int(meta.get("total_items", 0))

    # Monta Schema.org DataFeed (JSON-LD) para indexadores
    feed_elements: List[Dict[str, Any]] = []
    for source in aggregated_data.get("sources", []):
        src_name = source.get("name", "")
        for item in source.get("items", []):
            title = item.get("title") or item.get("name") or ""
            url = item.get("link") or item.get("url") or ""
            summary = item.get("summary") or item.get("description") or ""
            pub_date = item.get("published_at") or item.get("date") or ""
            if title:
                entry: Dict[str, Any] = {
                    "@type": "DataFeedItem",
                    "item": {
                        "@type": "Article",
                        "headline": title,
                        "description": summary,
                        "publisher": {"@type": "Organization", "name": src_name},
                    },
                }
                if url:
                    entry["item"]["url"] = url
                if pub_date:
                    entry["dateCreated"] = pub_date
                feed_elements.append(entry)

    json_ld = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "DataFeed",
            "name": meta.get("project_name", "AI Data Aggregator"),
            "description": meta.get("description", ""),
            "dateModified": meta.get("generated_at_utc", ""),
            "dataFeedElement": feed_elements[:100],
        },
        ensure_ascii=False,
    )

    out: List[str] = [
        "<!DOCTYPE html>",
        '<html lang="pt-BR">',
        "<head>",
        '  <meta charset="UTF-8" />',
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0" />',
        '  <meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large, max-video-preview:-1" />',
        f"  <title>{project_name}</title>",
        f'  <meta name="description" content="{description}" />',
        '  <link rel="canonical" href="https://rodrigomovie.github.io/ai-data-aggregator/" />',
        '  <link rel="alternate" type="text/markdown" href="llms.txt" title="LLM Plain-Text Feed (llms.txt)" />',
        '  <link rel="alternate" type="application/json" href="public/data/data.json" title="Structured JSON Data Feed" />',
        f'  <script type="application/ld+json">{json_ld}</script>',
        "  <style>",
        "    body { max-width: 920px; margin: 2rem auto; padding: 0 1.25rem; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; line-height: 1.6; color: #111111; background: #ffffff; }",
        "    header, section, footer { margin-bottom: 2rem; }",
        "    h1 { font-size: 1.5rem; border-bottom: 2px solid #111; padding-bottom: 0.5rem; }",
        "    h2 { font-size: 1.25rem; margin-top: 2rem; border-bottom: 1px solid #ccc; padding-bottom: 0.3rem; }",
        "    h3 { font-size: 1.05rem; margin: 1.25rem 0 0.35rem 0; }",
        "    article { margin-bottom: 1.25rem; padding-bottom: 0.75rem; border-bottom: 1px dashed #ddd; }",
        "    ul { margin: 0.35rem 0; padding-left: 1.4rem; }",
        "    a { color: #0044aa; text-decoration: underline; word-break: break-word; }",
        "    .meta-box { background: #f6f8fa; border: 1px solid #d0d7de; padding: 0.85rem 1rem; }",
        "  </style>",
        "</head>",
        "<body>",
        "  <header>",
        f"    <h1>{project_name}</h1>",
        f"    <p>{description}</p>",
        '    <div class="meta-box">',
        "      <ul>",
        f"        <li><strong>Última atualização (UTC):</strong> <time datetime=\"{generated_at}\">{generated_at}</time></li>",
        f"        <li><strong>Total de fontes processadas:</strong> {total_sources}</li>",
        f"        <li><strong>Total de registros extraídos:</strong> {total_items}</li>",
        '        <li><strong>Versão Visual Interativa (Cards e Filtros):</strong> <a href="public/">https://rodrigomovie.github.io/ai-data-aggregator/public/</a></li>',
        '        <li><strong>Arquivo Markdown Puro (llms.txt):</strong> <a href="llms.txt">https://rodrigomovie.github.io/ai-data-aggregator/llms.txt</a></li>',
        '        <li><strong>Arquivo JSON Estruturado (data.json):</strong> <a href="public/data/data.json">https://rodrigomovie.github.io/ai-data-aggregator/public/data/data.json</a></li>',
        "      </ul>",
        "    </div>",
        "  </header>",
        "  <main>",
    ]

    for source in aggregated_data.get("sources", []):
        src_type = html.escape(str(source.get("type", "source")).upper())
        src_name = html.escape(str(source.get("name", "")))
        src_id = html.escape(str(source.get("id", "")))
        src_cat = html.escape(str(source.get("category", "")))
        src_url = html.escape(str(source.get("source_url", "")))
        src_status = html.escape(str(source.get("status", "")))
        src_count = int(source.get("items_count", 0))

        out.append(f'    <section id="source-{src_id}">')
        out.append(f"      <h2>[{src_type}] {src_name}</h2>")
        out.append("      <ul>")
        out.append(f"        <li><strong>ID da Fonte:</strong> {src_id}</li>")
        out.append(f"        <li><strong>Categoria:</strong> {src_cat}</li>")
        if src_url:
            out.append(
                f'        <li><strong>URL de Origem:</strong> <a href="{src_url}">{src_url}</a></li>'
            )
        out.append(f"        <li><strong>Status:</strong> {src_status} ({src_count} itens)</li>")
        if source.get("note"):
            out.append(
                f"        <li><strong>Nota:</strong> {html.escape(str(source.get('note')))}</li>"
            )
        out.append("      </ul>")

        for idx, item in enumerate(source.get("items", []), start=1):
            raw_title = str(item.get("title") or item.get("name") or f"Item #{idx}")
            title = html.escape(raw_title)
            raw_url = str(item.get("link") or item.get("url") or "")
            url = html.escape(raw_url)

            out.append("      <article>")
            if url:
                out.append(f'        <h3>{idx}. <a href="{url}">{title}</a></h3>')
            else:
                out.append(f"        <h3>{idx}. {title}</h3>")

            out.append("        <ul>")
            if url:
                out.append(f'          <li><strong>Link:</strong> <a href="{url}">{url}</a></li>')

            for key, value in item.items():
                if key in ("title", "name", "link", "url") or value is None or value == "":
                    continue
                k_esc = html.escape(str(key))
                if isinstance(value, (dict, list)):
                    v_str = html.escape(json.dumps(value, ensure_ascii=False))
                else:
                    v_str = html.escape(str(value))
                out.append(f"          <li><strong>{k_esc}:</strong> {v_str}</li>")

            out.append("        </ul>")
            out.append("      </article>")

        out.append("    </section>")

    out.extend(
        [
            "  </main>",
            "  <footer>",
            "    <p>Documento HTML estático e indexável gerado automaticamente pelo pipeline Python para leitura direta por Agentes de IA, Microsoft Copilot e buscadores.</p>",
            "  </footer>",
            "</body>",
            "</html>",
            "",
        ]
    )
    return "\n".join(out)


def run_pipeline(config_path: Path, base_dir: Path) -> Dict[str, Any]:
    """Executa o pipeline completo de agregação e salva os arquivos de saída."""
    config = load_config(config_path)
    settings = config.get("settings", {})
    meta_cfg = config.get("metadata", {})

    timeout = settings.get("timeout_seconds", 15)
    max_retries = settings.get("max_retries", 2)
    user_agent = settings.get("user_agent", "AIDataAggregator/1.0")

    output_json_rel = meta_cfg.get("output_json", "public/data/data.json")
    output_json_path = base_dir / output_json_rel
    previous_sources = load_previous_sources(output_json_path)

    web_extractor = WebExtractor(timeout=timeout, max_retries=max_retries, user_agent=user_agent)
    api_client = APIClient(timeout=timeout, max_retries=max_retries, user_agent=user_agent)

    results: List[Dict[str, Any]] = []

    def append_with_cache_fallback(res: Dict[str, Any]) -> None:
        src_id = res.get("id", "")
        if res.get("items_count", 0) == 0 and src_id in previous_sources:
            cached = previous_sources[src_id]
            logger.warning(
                "Fonte '%s' retornou 0 itens na execução atual; preservando %d itens do cache anterior.",
                src_id,
                cached.get("items_count", 0),
            )
            cached_copy = dict(cached)
            cached_copy["note"] = (
                f"Dados preservados da coleta anterior (motivo na execução atual: {res.get('error', 'indisponível')})"
            )
            results.append(cached_copy)
        else:
            results.append(res)

    for web_src in config.get("web_sources", []):
        if not web_src.get("enabled", True):
            logger.info("Fonte web '%s' desativada. Pulando.", web_src.get("id"))
            continue
        append_with_cache_fallback(web_extractor.fetch_and_extract(web_src))

    for api_src in config.get("api_sources", []):
        if not api_src.get("enabled", True):
            logger.info("Fonte de API '%s' desativada. Pulando.", api_src.get("id"))
            continue
        append_with_cache_fallback(api_client.fetch_api_source(api_src))

    total_items = sum(r.get("items_count", 0) for r in results)
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    aggregated_payload: Dict[str, Any] = {
        "schema_version": "1.0",
        "metadata": {
            "project_name": meta_cfg.get("project_name", "AI Data Aggregator"),
            "description": meta_cfg.get("description", ""),
            "generated_at_utc": now_utc,
            "total_sources": len(results),
            "successful_sources": sum(1 for r in results if r.get("status") == "success"),
            "total_items": total_items,
        },
        "sources": results,
    }

    json_text = json.dumps(aggregated_payload, indent=2, ensure_ascii=False) + "\n"
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json_text, encoding="utf-8")
    logger.info("Arquivo JSON salvo com sucesso em: %s", output_json_path)

    llms_text = generate_llms_txt(aggregated_payload)
    output_md_rel = meta_cfg.get("output_markdown", "public/llms.txt")
    output_md_path = base_dir / output_md_rel
    output_md_path.parent.mkdir(parents=True, exist_ok=True)
    output_md_path.write_text(llms_text, encoding="utf-8")

    # Espelha llms.txt na raiz para acesso direto em /llms.txt no GitHub Pages
    root_llms_path = base_dir / "llms.txt"
    root_llms_path.write_text(llms_text, encoding="utf-8")
    logger.info("Arquivos llms.txt salvos em: %s e %s", output_md_path, root_llms_path)

    # Gera o index.html 100% estático e indexável na raiz para leitura por Copilot e Agentes de IA
    static_index_html = generate_static_index_html(aggregated_payload)
    root_index_path = base_dir / "index.html"
    root_index_path.write_text(static_index_html, encoding="utf-8")
    logger.info("Página estática indexável salva em: %s", root_index_path)

    return aggregated_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Executa o pipeline de agregação de dados.")
    parser.add_argument(
        "--config",
        type=str,
        default="config/sources.json",
        help="Caminho para o arquivo de configuração (JSON ou YAML).",
    )
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent.parent
    config_path = base_dir / args.config
    run_pipeline(config_path=config_path, base_dir=base_dir)


if __name__ == "__main__":
    main()

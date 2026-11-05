"""
Orquestrador principal do Pipeline de Agregação de Dados.
Lê config/sources.json (ou YAML), executa extrações web e consumo de APIs,
e gera outputs otimizados para Agentes de IA tanto em `public/` quanto na raiz (`llms.txt`).
"""

import argparse
import datetime
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

    root_llms_path = base_dir / "llms.txt"
    root_llms_path.write_text(llms_text, encoding="utf-8")
    logger.info("Arquivos llms.txt salvos em: %s e %s", output_md_path, root_llms_path)

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

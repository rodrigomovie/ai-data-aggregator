"""
Módulo de Extração Web (Web Scraper) modular usando requests e BeautifulSoup.
"""

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class WebExtractor:
    """Extrator modular para páginas HTML e feeds web configurados via JSON/YAML."""

    def __init__(self, timeout: int = 20, max_retries: int = 3, user_agent: str = "AIDataAggregator/1.0"):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        })

        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def fetch_and_extract(self, source_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executa a requisição para uma fonte web e extrai os dados conforme as regras.
        """
        source_id = source_config.get("id", "unknown_web_source")
        name = source_config.get("name", source_id)
        url = source_config.get("url")
        category = source_config.get("category", "general")
        rules = source_config.get("extraction_rules", {})

        if not url:
            logger.warning("Fonte web '%s' ignorada: URL não informada.", source_id)
            return self._build_result(source_id, name, url, category, "error", [], "URL ausente")

        logger.info("Extraindo fonte web: %s (%s)", name, url)

        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            html_content = response.text
        except requests.RequestException as exc:
            logger.error("Erro ao acessar '%s' (%s): %s", name, url, exc)
            return self._build_result(source_id, name, url, category, "error", [], str(exc))

        try:
            items = self._parse_html(html_content, base_url=url, rules=rules)
            return self._build_result(source_id, name, url, category, "success", items)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Erro ao processar HTML de '%s': %s", name, exc)
            return self._build_result(source_id, name, url, category, "error", [], str(exc))

    def _parse_html(self, html_content: str, base_url: str, rules: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Analisa o HTML usando BeautifulSoup de acordo com seletores CSS ou extrai texto limpo."""
        soup = BeautifulSoup(html_content, "html.parser")

        container_selector = rules.get("container_selector")
        fields_config = rules.get("fields", {})
        max_items = rules.get("max_items", 50)

        extracted_items: List[Dict[str, Any]] = []

        # Modo 1: Extração estruturada de múltiplos itens via container_selector
        if container_selector:
            containers = soup.select(container_selector)
            for container in containers[:max_items]:
                item_data: Dict[str, Any] = {}
                if fields_config:
                    for field_name, field_rule in fields_config.items():
                        value = self._extract_field(container, field_rule, base_url)
                        item_data[field_name] = value
                else:
                    item_data["text"] = container.get_text(separator=" ", strip=True)

                if any(item_data.values()):
                    extracted_items.append(item_data)
            return extracted_items

        # Modo 2: Extração de página inteira (Clean Text para Agentes de IA)
        for tag in soup(["script", "style", "noscript", "svg", "footer", "nav"]):
            tag.decompose()

        title = soup.title.get_text(strip=True) if soup.title else base_url
        main_node = soup.find("main") or soup.find("article") or soup.body or soup
        headings = [h.get_text(strip=True) for h in main_node.find_all(["h1", "h2", "h3"]) if h.get_text(strip=True)]
        paragraphs = [p.get_text(" ", strip=True) for p in main_node.find_all("p") if p.get_text(strip=True)]
        full_text = "\n\n".join(paragraphs) if paragraphs else main_node.get_text(separator="\n", strip=True)

        extracted_items.append({
            "title": title,
            "url": base_url,
            "headings": headings[:25],
            "content": full_text[:10000],
        })
        return extracted_items

    def _extract_field(self, element: Any, field_rule: Dict[str, Any], base_url: str) -> Optional[str]:
        """Extrai um campo específico de um elemento BeautifulSoup."""
        selector = field_rule.get("selector")
        attribute = field_rule.get("attribute", "text")

        target = element.select_one(selector) if selector else element
        if not target:
            return None

        if attribute == "text":
            return target.get_text(separator=" ", strip=True)

        raw_val = target.get(attribute)
        if raw_val and attribute in ("href", "src") and isinstance(raw_val, str):
            return urljoin(base_url, raw_val)
        return str(raw_val) if raw_val is not None else None

    @staticmethod
    def _build_result(
        source_id: str,
        name: str,
        url: Optional[str],
        category: str,
        status: str,
        items: List[Dict[str, Any]],
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "id": source_id,
            "name": name,
            "type": "web",
            "category": category,
            "source_url": url,
            "status": status,
            "items_count": len(items),
            "items": items,
        }
        if error_message:
            result["error"] = error_message
        return result

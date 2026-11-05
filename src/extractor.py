"""
Módulo de Extração Web e Feeds RSS/Atom (WebExtractor) usando requests e BeautifulSoup.
Inclui:
- Extração estruturada por seletores CSS (HTML e XML/RSS)
- Extração automática de JSON embutido em páginas modernas (Web Components / Elementor / Next.js)
- Extração de texto limpo (Clean Text) para consumo por Agentes de IA
- Detecção automática de bloqueios Anti-Bot (WAF/Captcha) com fallback configurável
"""

import html
import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class WebExtractor:
    """Extrator modular para páginas HTML, portais SPA/WebComponents e feeds RSS/XML."""

    def __init__(self, timeout: int = 25, max_retries: int = 3, user_agent: str = "AIDataAggregator/1.0"):
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
        Executa a requisição para uma fonte web/RSS e extrai os dados conforme as regras.
        Caso detecte bloqueio WAF/Captcha ou falha de conexão e haja `fallback_url`,
        utiliza automaticamente a fonte de contingência.
        """
        source_id = source_config.get("id", "unknown_web_source")
        name = source_config.get("name", source_id)
        url = source_config.get("url")
        category = source_config.get("category", "general")
        rules = source_config.get("extraction_rules", {})
        fallback_cfg = source_config.get("fallback")

        if not url:
            logger.warning("Fonte web '%s' ignorada: URL não informada.", source_id)
            return self._build_result(source_id, name, url, category, "error", [], "URL ausente")

        logger.info("Extraindo fonte web: %s (%s)", name, url)

        items, error_msg = self._try_fetch_and_parse(url, rules)

        # Se falhou ou retornou vazio (por ex. WAF/Captcha ou renderização 100% client-side) e há fallback
        if (error_msg or not items) and fallback_cfg and fallback_cfg.get("url"):
            fb_url = fallback_cfg["url"]
            fb_rules = fallback_cfg.get("extraction_rules", rules)
            logger.info(
                "Acionando fallback para '%s' (%s) devido a: %s",
                name,
                fb_url,
                error_msg or "0 itens na URL primária",
            )
            fb_items, fb_error = self._try_fetch_and_parse(fb_url, fb_rules)
            if fb_items:
                return self._build_result(
                    source_id,
                    name,
                    url,
                    category,
                    "success",
                    fb_items,
                    note=f"Coletado via endpoint/feed de fallback ({fb_url})",
                )
            if fb_error:
                error_msg = f"{error_msg} | Fallback error: {fb_error}"

        if error_msg and not items:
            return self._build_result(source_id, name, url, category, "error", [], error_msg)

        return self._build_result(source_id, name, url, category, "success", items)

    def _try_fetch_and_parse(
        self, url: str, rules: Dict[str, Any]
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """Faz download do conteúdo e direciona para o parser apropriado (RSS, Embedded JSON ou HTML)."""
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            if response.encoding and response.encoding.lower() == "iso-8859-1":
                raw_text = response.content.decode("utf-8", errors="replace")
            else:
                raw_text = response.text
        except requests.RequestException as exc:
            logger.error("Erro HTTP ao acessar '%s': %s", url, exc)
            return [], str(exc)

        if self._is_bot_challenge(raw_text):
            msg = "Bloqueio WAF/Captcha detectado (ShieldSquare / Radware / Cloudflare Challenge)."
            logger.warning("%s URL: %s", msg, url)
            return [], msg

        mode = rules.get("mode", "auto").lower()
        try:
            if mode == "rss" or self._looks_like_rss(raw_text, url):
                return self._parse_rss(raw_text, rules), None
            if mode == "embedded_json":
                return self._parse_embedded_json(raw_text, base_url=url, rules=rules), None
            return self._parse_html(raw_text, base_url=url, rules=rules), None
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Erro ao processar conteúdo de '%s': %s", url, exc)
            return [], str(exc)

    @staticmethod
    def _is_bot_challenge(content: str) -> bool:
        """Detecta páginas de desafio anti-bot conhecidas."""
        head_sample = content[:4000].lower()
        markers = [
            "<title>shieldsquare captcha</title>",
            "window.ssjsinternal",
            "cdn.perfdrive.com/aperture",
            "<title>just a moment...</title>",
        ]
        return any(m in head_sample for m in markers)

    @staticmethod
    def _looks_like_rss(content: str, url: str) -> bool:
        """Verifica se o documento é um feed RSS/Atom XML."""
        sample = content[:600].lstrip().lower()
        return (
            sample.startswith("<?xml")
            or "<rss" in sample
            or "<feed" in sample
            or "/rss/" in url.lower()
            or url.lower().endswith(".xml")
        )

    def _parse_rss(self, xml_content: str, rules: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Faz parsing de feeds RSS 2.0 ou Atom, limpando tags HTML das descrições."""
        max_items = rules.get("max_items", 20)
        try:
            soup = BeautifulSoup(xml_content, "xml")
        except Exception:  # pylint: disable=broad-except
            soup = BeautifulSoup(xml_content, "html.parser")

        entries = soup.find_all("item") or soup.find_all("entry")
        items: List[Dict[str, Any]] = []

        for entry in entries[:max_items]:
            title_tag = entry.find("title")
            link_tag = entry.find("link")
            pub_tag = entry.find("pubDate") or entry.find("published") or entry.find("updated")
            desc_tag = (
                entry.find("content:encoded")
                or entry.find("encoded")
                or entry.find("description")
                or entry.find("summary")
            )

            title = self._clean_html_text(title_tag.get_text()) if title_tag else ""
            link = ""
            if link_tag:
                link = link_tag.get("href") or link_tag.get_text(strip=True)
                if not link and link_tag.next_sibling:
                    link = str(link_tag.next_sibling).strip()

            pub_date = pub_tag.get_text(strip=True) if pub_tag else None
            summary = self._clean_html_text(desc_tag.get_text()) if desc_tag else None
            if summary and len(summary) > 600:
                summary = summary[:597].rstrip() + "..."

            item_obj: Dict[str, Any] = {"title": title, "url": link}
            if pub_date:
                item_obj["published_at"] = pub_date
            if summary:
                item_obj["summary"] = summary

            if title or link:
                items.append(item_obj)

        return items

    def _parse_embedded_json(
        self, html_content: str, base_url: str, rules: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Extrai objetos JSON ricos embutidos em scripts de páginas construídas com Web Components /
        Elementor (como o portal `bb.com.br/site/setor-publico/`), consolidando títulos, descrições e links.
        """
        max_items = rules.get("max_items", 30)
        extracted: List[Dict[str, Any]] = []
        seen_keys: set[str] = set()

        def add_candidate(title: Optional[str], desc: Optional[str], link: Optional[str], section: str) -> None:
            clean_title = self._clean_html_text(title or "")
            clean_desc = self._clean_html_text(desc or "")
            clean_link = urljoin(base_url, link.strip()) if link and link.strip() else base_url

            if not clean_title and not clean_desc:
                return
            if not clean_title:
                clean_title = clean_desc[:80]
            if "cookies" in clean_title.lower():
                return

            dedup_key = f"{clean_title.lower()}|{clean_link.lower()}"
            if dedup_key in seen_keys:
                return
            seen_keys.add(dedup_key)

            item: Dict[str, Any] = {
                "title": clean_title,
                "url": clean_link,
                "section": section,
            }
            if clean_desc and clean_desc != clean_title:
                item["description"] = clean_desc
            extracted.append(item)

        array_patterns = [
            (r"let\s+banners\s*=\s*(\[\{.*?\}\]);", "Destaques"),
            (r"let\s+cards_on_multiples_banners\s*=\s*(\[\{.*?\}\]);", "Destaques"),
            (r"menusJsonStr\s*=\s*'(\[\{.*?\}\])';", "Segmentos Setor Público"),
            (r"cardsJsonStr\s*=\s*'(\[\{.*?\}\])';", "Soluções em Destaque"),
            (r"itemsGridJsonStr\s*=\s*'(\[\{.*?\}\])';", "Vantagens e Soluções"),
            (r"dataContentItemsJsonStr\s*=\s*'(\[\{.*?\}\])';", "Soluções por Tema"),
        ]

        for pattern, section_name in array_patterns:
            for match in re.finditer(pattern, html_content, flags=re.DOTALL):
                raw_json = match.group(1)
                try:
                    parsed_list = json.loads(raw_json)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed_list, list):
                    continue
                for obj in parsed_list:
                    if not isinstance(obj, dict):
                        continue
                    title = obj.get("title") or obj.get("dataTitle") or obj.get("titleIntro") or obj.get("label")
                    desc = obj.get("description") or obj.get("desc")
                    link = (
                        obj.get("link")
                        or obj.get("cta_link")
                        or obj.get("linkButton")
                        or obj.get("url")
                    )
                    add_candidate(title, desc, link, section_name)

        card_block_pattern = re.compile(
            r'const\s+links\s*=\s*(\[\{.*?\}\]);.*?const\s+description\s*=\s*"([^"]*)";',
             flags=re.DOTALL,
        )
        for match in card_block_pattern.finditer(html_content):
            links_raw, desc_raw = match.group(1), match.group(2)
            try:
                links_list = json.loads(links_raw)
                desc_decoded = json.loads(f'"{desc_raw}"')
            except json.JSONDecodeError:
                continue
            if links_list and isinstance(links_list[0], dict):
                link_url = links_list[0].get("url") or ""
                slug_title = link_url.rstrip("/").split("/")[-1].replace("-", " ").title() if link_url else "Solução Setor Público"
                add_candidate(slug_title, desc_decoded, link_url, "Serviços e Soluções")

        if not extracted:
            return self._parse_html(html_content, base_url=base_url, rules=rules)

        return extracted[:max_items]

    def _parse_html(self, html_content: str, base_url: str, rules: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Analisa o HTML usando BeautifulSoup de acordo com seletores CSS ou extrai texto limpo."""
        soup = BeautifulSoup(html_content, "html.parser")

        container_selector = rules.get("container_selector")
        fields_config = rules.get("fields", {})
        max_items = rules.get("max_items", 50)

        extracted_items: List[Dict[str, Any]] = []

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

        for tag in soup(["script", "style", "noscript", "svg", "footer", "nav"]):
            tag.decompose()

        title = soup.title.get_text(strip=True) if soup.title else base_url
        meta_desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find(
            "meta", attrs={"property": "og:description"}
        )
        meta_desc = meta_desc_tag.get("content", "").strip() if meta_desc_tag else ""

        main_node = soup.find("main") or soup.find("article") or soup.body or soup
        headings = [h.get_text(strip=True) for h in main_node.find_all(["h1", "h2", "h3"]) if h.get_text(strip=True)]
        paragraphs = [p.get_text(" ", strip=True) for p in main_node.find_all("p") if p.get_text(strip=True)]
        full_text = "\n\n".join(paragraphs) if paragraphs else main_node.get_text(separator="\n", strip=True)

        extracted_items.append({
            "title": title,
            "url": base_url,
            "description": meta_desc,
            "headings": headings[:25],
            "content": full_text[:10000] if full_text else meta_desc,
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
            return self._clean_html_text(target.get_text(separator=" ", strip=True))

        raw_val = target.get(attribute)
        if raw_val and attribute in ("href", "src") and isinstance(raw_val, str):
            return urljoin(base_url, raw_val)
        return str(raw_val) if raw_val is not None else None

    @staticmethod
    def _clean_html_text(raw_text: str) -> str:
        """Remove tags HTML residuais, decodifica entidades HTML e normaliza espaços."""
        if not raw_text:
            return ""
        unescaped = html.unescape(raw_text)
        if "<" in unescaped and ">" in unescaped:
            unescaped = BeautifulSoup(unescaped, "html.parser").get_text(separator=" ", strip=True)
        return re.sub(r"\s+", " ", unescaped).strip()

    @staticmethod
    def _build_result(
        source_id: str,
        name: str,
        url: Optional[str],
        category: str,
        status: str,
        items: List[Dict[str, Any]],
        error_message: Optional[str] = None,
        note: Optional[str] = None,
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
        if note:
            result["note"] = note
        if error_message:
            result["error"] = error_message
        return result

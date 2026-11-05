"""
Módulo de Consumo de APIs (APIClient) com suporte a:
- Autenticação via variáveis de ambiente (Bearer, API Key Header, Query Param, Basic)
- Paginação automática (page-based, offset-based e cursor/next_url)
- Múltiplas queries sequenciais ou aninhadas (nested queries) para consolidar resultados
- Pós-processamento opcional (limpeza de HTML em campos de texto e prefixo de URLs relativas)
"""

import html
import logging
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class APIClient:
    """Cliente de API configurável e resiliente para agregação de dados."""

    def __init__(self, timeout: int = 25, max_retries: int = 3, user_agent: str = "AIDataAggregator/1.0"):
        self.timeout = timeout
        self.user_agent = user_agent
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        })

        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def fetch_api_source(self, api_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Consome um endpoint de API com suporte a paginação, mapeamento de campos
        e queries aninhadas/sequenciais.
        """
        source_id = api_config.get("id", "unknown_api_source")
        name = api_config.get("name", source_id)
        category = api_config.get("category", "api_data")
        base_url = api_config.get("base_url", "").rstrip("/")
        endpoint = api_config.get("endpoint", "")
        full_url = f"{base_url}/{endpoint.lstrip('/')}" if endpoint else base_url
        origin_page_url = api_config.get("origin_url", full_url)

        if not full_url:
            return self._build_result(source_id, name, origin_page_url, category, "error", [], "URL base/endpoint ausente")

        logger.info("Consumindo API: %s (%s)", name, full_url)

        try:
            headers, auth_params = self._prepare_auth(api_config.get("auth", {}))
            if api_config.get("headers"):
                headers.update(api_config["headers"])

            raw_items = self._fetch_paginated(
                url=full_url,
                method=api_config.get("method", "GET").upper(),
                headers=headers,
                base_params={**api_config.get("params", {}), **auth_params},
                body=api_config.get("body"),
                pagination_cfg=api_config.get("pagination", {}),
            )

            nested_queries = api_config.get("nested_queries", [])
            if nested_queries and raw_items:
                raw_items = self._execute_nested_queries(
                    parent_items=raw_items,
                    nested_configs=nested_queries,
                    base_url=base_url,
                    headers=headers,
                    auth_params=auth_params,
                )

            field_mapping = api_config.get("field_mapping")
            url_prefix = api_config.get("url_prefix")
            strip_html_fields = set(api_config.get("strip_html_fields", []))

            processed_items = [
                self._transform_item(
                    item,
                    mapping=field_mapping,
                    url_prefix=url_prefix,
                    strip_html_fields=strip_html_fields,
                )
                for item in raw_items
            ]

            return self._build_result(source_id, name, origin_page_url, category, "success", processed_items)

        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Erro ao consumir API '%s': %s", name, exc)
            return self._build_result(source_id, name, origin_page_url, category, "error", [], str(exc))

    def _prepare_auth(self, auth_cfg: Dict[str, Any]) -> tuple[Dict[str, str], Dict[str, str]]:
        """Prepara cabeçalhos ou parâmetros de autenticação via variáveis de ambiente."""
        headers: Dict[str, str] = {}
        params: Dict[str, str] = {}

        if not auth_cfg:
            return headers, params

        auth_type = auth_cfg.get("type", "none").lower()
        env_var = auth_cfg.get("env_var")
        is_optional = auth_cfg.get("optional", False)

        if auth_type == "none" or not env_var:
            return headers, params

        token = os.getenv(env_var)
        if not token:
            if is_optional:
                logger.info("Variável de ambiente '%s' não definida (autenticação opcional ignorada).", env_var)
                return headers, params
            raise ValueError(f"Variável de ambiente obrigatória '{env_var}' não encontrada para autenticação.")

        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "header":
            header_name = auth_cfg.get("header_name", "X-API-Key")
            prefix = auth_cfg.get("prefix", "")
            headers[header_name] = f"{prefix}{token}".strip()
        elif auth_type == "query":
            param_name = auth_cfg.get("param_name", "api_key")
            params[param_name] = token

        return headers, params

    def _fetch_paginated(
        self,
        url: str,
        method: str,
        headers: Dict[str, str],
        base_params: Dict[str, Any],
        body: Optional[Dict[str, Any]],
        pagination_cfg: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Realiza requisições suportando paginação (page, offset ou cursor)."""
        pagination_enabled = pagination_cfg.get("enabled", False)
        strategy = pagination_cfg.get("strategy", "page")
        max_pages = pagination_cfg.get("max_pages", 1) if pagination_enabled else 1
        results_path = pagination_cfg.get("results_path")

        collected: List[Dict[str, Any]] = []
        current_page = pagination_cfg.get("start_page", 1)
        per_page = pagination_cfg.get("per_page", 20)
        offset = 0
        next_cursor: Optional[str] = None
        next_url: Optional[str] = url

        for page_idx in range(max_pages):
            params = dict(base_params)

            if pagination_enabled:
                if strategy == "page":
                    page_param = pagination_cfg.get("page_param", "page")
                    per_page_param = pagination_cfg.get("per_page_param", "per_page")
                    params[page_param] = current_page + page_idx
                    if per_page_param:
                        params[per_page_param] = per_page
                elif strategy == "offset":
                    offset_param = pagination_cfg.get("offset_param", "offset")
                    limit_param = pagination_cfg.get("limit_param", "limit")
                    params[offset_param] = offset
                    params[limit_param] = per_page
                elif strategy == "cursor" and next_cursor:
                    cursor_param = pagination_cfg.get("cursor_param", "cursor")
                    params[cursor_param] = next_cursor

            target_url = next_url if (strategy == "next_url" and next_url) else url
            response = self.session.request(
                method=method,
                url=target_url,
                headers=headers,
                params=params if strategy != "next_url" or page_idx == 0 else None,
                json=body,
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()

            page_items = self._extract_by_path(payload, results_path) if results_path else payload
            if isinstance(page_items, dict):
                page_items = [page_items]
            elif not isinstance(page_items, list):
                page_items = []

            if not page_items:
                break

            collected.extend(page_items)

            if not pagination_enabled:
                break

            if strategy == "offset":
                offset += len(page_items)
                if len(page_items) < per_page:
                    break
            elif strategy == "cursor":
                cursor_path = pagination_cfg.get("next_cursor_path", "next_cursor")
                next_cursor = self._extract_by_path(payload, cursor_path)
                if not next_cursor:
                    break
            elif strategy == "next_url":
                next_url_path = pagination_cfg.get("next_url_path", "next")
                next_url = self._extract_by_path(payload, next_url_path)
                if not next_url:
                    break

        return collected

    def _execute_nested_queries(
        self,
        parent_items: List[Dict[str, Any]],
        nested_configs: List[Dict[str, Any]],
        base_url: str,
        headers: Dict[str, str],
        auth_params: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """Executa queries sequenciais/aninhadas para enriquecer cada item pai."""
        for nested_cfg in nested_configs:
            target_field = nested_cfg.get("target_field", "nested_details")
            endpoint_template = nested_cfg.get("endpoint_template", "")
            results_path = nested_cfg.get("results_path")
            max_parent_items = nested_cfg.get("max_parent_items", 10)

            if not endpoint_template:
                continue

            for item in parent_items[:max_parent_items]:
                try:
                    formatted_endpoint = endpoint_template.format(**item)
                    nested_url = (
                        formatted_endpoint
                        if formatted_endpoint.startswith("http")
                        else f"{base_url}/{formatted_endpoint.lstrip('/')}"
                    )
                    resp = self.session.get(
                        nested_url,
                        headers=headers,
                        params={**nested_cfg.get("params", {}), **auth_params},
                        timeout=self.timeout,
                    )
                    resp.raise_for_status()
                    nested_payload = resp.json()
                    item[target_field] = (
                        self._extract_by_path(nested_payload, results_path)
                        if results_path
                        else nested_payload
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    logger.warning("Falha na query aninhada '%s': %s", target_field, exc)
                    item[target_field] = None

        return parent_items

    def _transform_item(
        self,
        item: Dict[str, Any],
        mapping: Optional[Dict[str, str]],
        url_prefix: Optional[str],
        strip_html_fields: set[str],
    ) -> Dict[str, Any]:
        """Mapeia chaves, resolve URLs relativas e remove HTML de campos textuais."""
        if mapping:
            mapped: Dict[str, Any] = {}
            for clean_key, source_path in mapping.items():
                mapped[clean_key] = self._extract_by_path(item, source_path)
        else:
            mapped = dict(item)

        if url_prefix:
            for url_key in ("url", "link"):
                val = mapped.get(url_key)
                if isinstance(val, str) and val and not val.startswith("http"):
                    mapped[url_key] = urljoin(url_prefix.rstrip("/") + "/", val.lstrip("/"))

        for key, val in list(mapped.items()):
            if isinstance(val, str) and (key in strip_html_fields or key in ("title", "subtitle", "summary", "content")):
                cleaned = self._clean_html_text(val)
                if key == "content" and len(cleaned) > 2500:
                    cleaned = cleaned[:2497].rstrip() + "..."
                mapped[key] = cleaned

        return mapped

    @staticmethod
    def _clean_html_text(raw_text: str) -> str:
        """Remove comentários/tags HTML (como blocos do WordPress), decodifica entidades e normaliza espaços."""
        if not raw_text:
            return ""
        unescaped = html.unescape(raw_text)
        if "<" in unescaped and ">" in unescaped:
            soup = BeautifulSoup(unescaped, "html.parser")
            for fig in soup.find_all("figcaption"):
                fig.decompose()
            unescaped = soup.get_text(separator=" ", strip=True)
        return re.sub(r"\s+", " ", unescaped).strip()

    @staticmethod
    def _extract_by_path(data: Any, path: Optional[str]) -> Any:
        """Navega em dicionários aninhados usando notação de ponto (ex: 'data.results.items')."""
        if not path or data is None:
            return data
        current = data
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit():
                idx = int(part)
                current = current[idx] if 0 <= idx < len(current) else None
            else:
                return None
        return current

    @staticmethod
    def _build_result(
        source_id: str,
        name: str,
        url: str,
        category: str,
        status: str,
        items: List[Dict[str, Any]],
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "id": source_id,
            "name": name,
            "type": "api",
            "category": category,
            "source_url": url,
            "status": status,
            "items_count": len(items),
            "items": items,
        }
        if error_message:
            result["error"] = error_message
        return result

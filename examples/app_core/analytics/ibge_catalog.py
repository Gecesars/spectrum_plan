from __future__ import annotations

import json
import logging
import math
import re
import ssl
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Tuple

import requests
from requests import Session
from requests.adapters import HTTPAdapter

from app_core.integrations import ibge as ibge_api

LOGGER = logging.getLogger(__name__)

SIDRA_BASE_URL = "https://servicodados.ibge.gov.br/api/v3/agregados"
LOCALIDADE_BASE_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"

# --- Constantes das tabelas SIDRA utilizadas ---
SIDRA_POPULATION_TABLE = "6579"
SIDRA_POPULATION_VARIABLE = "9324"

SIDRA_INCOME_TABLE = "7531"
SIDRA_INCOME_VARIABLE = "10824"
SIDRA_INCOME_CLASSIFICATION = "1019"
SIDRA_INCOME_CATEGORY_TOTAL = "49243"


class _SidraTLSAdapter(HTTPAdapter):
    """Adaptador HTTP que habilita cifras legadas, conforme recomendado pelo IBGE."""

    LEGACY_FLAG = getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x00040)

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        ctx.options |= self.LEGACY_FLAG
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        ctx.options |= self.LEGACY_FLAG
        kwargs["ssl_context"] = ctx
        return super().proxy_manager_for(*args, **kwargs)


def _create_sidra_session() -> Session:
    session = requests.Session()
    session.mount("https://", _SidraTLSAdapter())
    session.headers.update({"User-Agent": "ATXCoverage/1.0 (+https://atxcoverage)"})
    return session


def _chunked(iterable: Iterable[str], size: int = 50) -> Iterable[List[str]]:
    chunk: List[str] = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _parse_numeric_value(raw: str) -> Optional[float]:
    if raw in (None, "", "...", "-", ".."):
        return None
    try:
        sanitized = raw.replace(".", "").replace(",", ".")
        return float(sanitized)
    except (ValueError, AttributeError):
        return None


def _extract_latest_entry(series: Dict[str, str]) -> Optional[Tuple[int, float]]:
    if not series:
        return None
    for period in sorted(series.keys(), reverse=True):
        value = _parse_numeric_value(series[period])
        if value is not None:
            try:
                return int(period), value
            except ValueError:
                continue
    return None


@lru_cache(maxsize=4096)
def get_municipality_metadata(code: str) -> Optional[Dict[str, str]]:
    try:
        resp = requests.get(f"{LOCALIDADE_BASE_URL}/{code}", timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        LOGGER.warning("ibge.metadata.municipio_failed", extra={"code": code, "error": str(exc)})
        return None

    uf_info = payload.get("microrregiao", {}).get("mesorregiao", {}).get("UF") or {}
    return {
        "ibge_code": str(payload.get("id")),
        "municipality": payload.get("nome"),
        "state": uf_info.get("sigla"),
        "state_id": str(uf_info.get("id")) if uf_info.get("id") is not None else None,
    }


def fetch_population_estimates(
    municipality_codes: Iterable[str],
    session: Optional[Session] = None,
) -> Dict[str, Dict[str, Optional[float]]]:
    """Retorna população estimada (tabela 6579) para os municípios fornecidos."""

    codes = [str(code) for code in dict.fromkeys(municipality_codes) if code]
    if not codes:
        return {}

    session = session or _create_sidra_session()
    results: Dict[str, Dict[str, Optional[float]]] = {}

    for chunk in _chunked(codes, size=40):
        codes_param = ",".join(chunk)
        url = (
            f"{SIDRA_BASE_URL}/{SIDRA_POPULATION_TABLE}/periodos/all/variaveis/"
            f"{SIDRA_POPULATION_VARIABLE}?localidades=N6[{codes_param}]"
        )
        try:
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as exc:
            LOGGER.warning(
                "ibge.population.fetch_failed",
                extra={"codes": codes_param, "error": str(exc)},
            )
            continue

        if not payload:
            continue

        for result in payload[0].get("resultados", []):
            for serie in result.get("series", []):
                loc = serie.get("localidade", {})
                loc_id = str(loc.get("id"))
                latest = _extract_latest_entry(serie.get("serie", {}))
                if not latest:
                    continue
                year, value = latest
                results[loc_id] = {"year": year, "value": value}

    return results


def fetch_income_per_capita_by_state(
    state_codes: Iterable[str],
    session: Optional[Session] = None,
) -> Dict[str, Dict[str, Optional[float]]]:
    """Busca renda per capita (tabela 7531) para os estados informados."""

    codes = [str(code) for code in dict.fromkeys(state_codes) if code]
    if not codes:
        return {}

    session = session or _create_sidra_session()
    results: Dict[str, Dict[str, Optional[float]]] = {}

    for chunk in _chunked(codes, size=25):
        codes_param = ",".join(chunk)
        url = (
            f"{SIDRA_BASE_URL}/{SIDRA_INCOME_TABLE}/periodos/all/variaveis/"
            f"{SIDRA_INCOME_VARIABLE}?localidades=N3[{codes_param}]"
            f"&classificacao={SIDRA_INCOME_CLASSIFICATION}"
            f"[{SIDRA_INCOME_CATEGORY_TOTAL}]"
        )
        try:
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as exc:
            LOGGER.warning(
                "ibge.income.fetch_failed",
                extra={"codes": codes_param, "error": str(exc)},
            )
            continue

        if not payload:
            continue

        for result in payload[0].get("resultados", []):
            for serie in result.get("series", []):
                loc = serie.get("localidade", {})
                loc_id = str(loc.get("id"))
                latest = _extract_latest_entry(serie.get("serie", {}))
                if not latest:
                    continue
                year, value = latest
                results[loc_id] = {"year": year, "value": value}

    return results


def _get_sidra_metadata(table_id: str, session: Optional[Session] = None) -> Dict[str, object]:
    """
    Recupera os metadados de uma tabela SIDRA.
    """
    session = session or _create_sidra_session()
    resp = session.get(f"{SIDRA_BASE_URL}/{table_id}/metadados", timeout=30)
    resp.raise_for_status()
    return resp.json()


def _parse_simple_age(name: str) -> Optional[int]:
    """
    Extrai a idade (anos completos) de uma categoria textual.
    Retorna None quando a categoria não representa idade simples.
    """
    normalized = name.strip().lower()
    if "anos" not in normalized:
        return None
    if " a " in normalized:
        return None
    if "menos de" in normalized:
        return None
    match = re.match(r"(\\d+)", normalized)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def discover_population_age_sex_classifications(
    session: Optional[Session] = None,
) -> Dict[str, object]:
    """
    Descobre as classificações de Sexo e Idade (anos simples) da tabela 6579.
    """
    metadata = _get_sidra_metadata(SIDRA_POPULATION_TABLE, session=session)
    classifications: List[Dict[str, object]] = metadata.get("classificacoes") or []
    sidra_session = session or _create_sidra_session()
    if not classifications:
        resp = sidra_session.get(
            f"{SIDRA_BASE_URL}/{SIDRA_POPULATION_TABLE}/classificacoes",
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            classifications = payload
        elif isinstance(payload, dict):
            classifications = payload.get("classificacoes") or []
        else:
            classifications = []

    sex_class = None
    for cls in classifications:
        if "sexo" in (cls.get("nome") or "").lower():
            sex_class = cls
            break
    if not sex_class:
        raise RuntimeError("Classificação 'Sexo' não encontrada na tabela 6579.")

    sex_categories = {
        cat.get("nome"): str(cat.get("id"))
        for cat in sex_class.get("categorias", [])
        if cat.get("nome")
    }

    age_class = None
    age_categories: List[Dict[str, object]] = []
    for cls in classifications:
        if "idade" not in (cls.get("nome") or "").lower():
            continue
        candidates: List[Dict[str, object]] = []
        for cat in cls.get("categorias", []):
            name = cat.get("nome") or ""
            age_value = _parse_simple_age(name)
            if age_value is None:
                continue
            candidates.append(
                {"id": str(cat.get("id")), "name": name, "age": age_value}
            )
        if candidates:
            age_class = cls
            age_categories = candidates
            break

    if not age_class:
        raise RuntimeError(
            "Classificação de idade em anos simples não encontrada na tabela 6579."
        )

    age_categories.sort(key=lambda item: item["age"])

    return {
        "sex": {"id": str(sex_class.get("id")), "categories": sex_categories},
        "age": {"id": str(age_class.get("id")), "categories": age_categories},
    }


def get_population_by_sex_for_min_age(
    municipality_code: str,
    min_age: int = 18,
    session: Optional[Session] = None,
) -> Dict[str, object]:
    """
    Consulta a população residente (tabela 6579) de um município
    filtrando pessoas com idade mínima informada, agrupadas por sexo.
    """
    if min_age < 0:
        raise ValueError("Idade mínima deve ser não negativa.")

    session = session or _create_sidra_session()
    class_info = discover_population_age_sex_classifications(session=session)

    sex_info = class_info["sex"]
    age_info = class_info["age"]

    sex_categories = {name.lower(): cid for name, cid in sex_info["categories"].items()}
    try:
        male_id = sex_categories["homens"]
        female_id = sex_categories["mulheres"]
    except KeyError as exc:
        raise RuntimeError("Categorias 'Homens' e 'Mulheres' não disponíveis na tabela 6579.") from exc

    age_category_ids = [
        cat["id"]
        for cat in age_info["categories"]
        if cat["age"] >= min_age
    ]
    if not age_category_ids:
        raise ValueError(f"Nenhuma categoria de idade >= {min_age} anos encontrada.")

    url = (
        f"{SIDRA_BASE_URL}/{SIDRA_POPULATION_TABLE}/periodos/last%201/variaveis/"
        f"{SIDRA_POPULATION_VARIABLE}?localidades=N6[{municipality_code}]"
        f"&classificacao={age_info['id']}[{','.join(age_category_ids)}]"
        f"&classificacao={sex_info['id']}[{male_id},{female_id}]"
    )

    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    if not payload:
        raise RuntimeError("Resposta vazia da API SIDRA para a consulta de população.")

    period_map: Dict[str, Dict[str, float]] = {}
    resultados = payload[0].get("resultados", []) if isinstance(payload, list) else []

    for resultado in resultados:
        for serie in resultado.get("series", []):
            classificacoes = {
                entry.get("id"): entry for entry in serie.get("classificacoes", [])
            }
            sex_entry = classificacoes.get(sex_info["id"])
            if not sex_entry:
                continue
            sex_label = sex_entry.get("categoria", {}).get("nome")
            if not sex_label:
                continue
            for period, raw_value in (serie.get("serie") or {}).items():
                value = _parse_numeric_value(raw_value)
                if value is None:
                    continue
                period_totals = period_map.setdefault(period, {})
                period_totals[sex_label] = period_totals.get(sex_label, 0.0) + value

    if not period_map:
        raise RuntimeError("Nenhum dado de população retornado para o município informado.")

    def _period_key(period: str) -> int:
        digits = "".join(ch for ch in period if ch.isdigit())
        if digits:
            try:
                return int(digits)
            except ValueError:
                return -1
        return -1

    latest_period = max(period_map.keys(), key=_period_key)
    sex_totals = period_map[latest_period]

    expected_labels = {"Homens", "Mulheres"}
    if not expected_labels.issubset(set(sex_totals.keys())):
        raise RuntimeError("Resposta não contém ambos os sexos solicitados.")

    return {
        "municipality": str(municipality_code),
        "period": latest_period,
        "sex_totals": sex_totals,
        "age_category_count": len(age_category_ids),
        "age_category_ids": age_category_ids,
    }


def get_or_resolve_municipality(
    municipality_name: str,
    state_hint: Optional[str],
) -> Optional[str]:
    """Resolve nome de município e UF em um código IBGE."""

    if not municipality_name:
        return None
    try:
        return ibge_api.resolve_municipality_code(municipality_name, state_hint)
    except Exception as exc:  # pragma: no cover - proteção adicional
        LOGGER.warning(
            "ibge.resolve_municipio_failed",
            extra={"name": municipality_name, "state": state_hint, "error": str(exc)},
        )
        return None


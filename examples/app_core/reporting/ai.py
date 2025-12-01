from __future__ import annotations

import base64
import json
import re
from typing import Any, Dict, List

import google.generativeai as genai
from flask import current_app


class AIUnavailable(RuntimeError):
    """Raised when the Gemini client cannot be used."""


class AISummaryError(RuntimeError):
    """Raised when the summary request fails for any reason."""


ANALYSIS_PROMPT = """Você é um engenheiro especialista em radiodifusão TV e FM.
Não invente dados: se algo não existir, explique a limitação explicitamente e escreva "Dados indisponíveis".
Nunca retorne informações ambiguas ou incompletas.
Nunca mencione que é um modelo ou utilize os termos "IA" ou "inteligência artificial".
Responda EXCLUSIVAMENTE em JSON válido no formato:
{{
  "overview": "...",
  "coverage": "...",
  "profile": "...",
  "pattern_horizontal": "...",
  "pattern_vertical": "...",
  "recommendations": ["...", "..."],
  "conclusion": "...",
  "link_analyses": [
    {{"label": "...", "analysis": "..."}}
  ]
}}

Restrições Negativas (CRÍTICO):
- NÃO mencione "Petrolina" ou "Pernambuco" a menos que "Petrolina" esteja explicitamente escrito no campo "Região" abaixo.
- NÃO invente cidades ou estados. Use apenas o que está em "Região".
- NÃO use dados de exemplos de treinamento.

Contexto:
- Projeto: {project_name} ({project_slug})
- Serviço: {service} / Classe {service_class}
- Região: {location} (Esta é a localização OFICIAL do projeto. Ignore qualquer outra menção geográfica que contradiga isso.)
- Engine: {engine}
- Clima: {climate}
- Notas do projeto: {project_notes}
- Pico a pico horizontal (máximo - mínimo do diagrama): {horizontal_peak_to_peak_db} dB
- Apenas os dois principais receptores foram fornecidos para análise automática; os demais permanecem descritos no relatório.
- Receptores avaliados (resumo):
{link_summary}
- Receptores detalhados (JSON):
{link_payload}
  * Nota: O campo 'erp_dbm' em cada receptor representa a ERP DIRECIONAL (na direção daquele receptor), considerando o diagrama de irradiação da antena. É esperado que seja diferente da ERP máxima se a antena for direcional.
- Potência do Transmissor (Entrada): {tx_power_w} W
- Ganho da Antena (Entrada): {antenna_gain_dbd} dBd (equivalente a {antenna_gain_dbi} dBi)
- Perdas (Entrada): {losses_db} dB

Parâmetros principais:
- Raio planejado: {radius_km} km
- Frequência: {frequency_mhz} MHz
- Polarização: {polarization}

Requisitos:
- overview: Inicie a string OBRIGATORIAME com o histórico de cálculo da ERP MÁXIMA, formatado exatamente assim (substitua X, Y, Z, A, B pelos valores calculados com precisão):
  "Cálculo ERP Máxima: [P_tx: 10*log10({tx_power_w}) = X dBW] + [G_ant: {antenna_gain_dbd} dBd (={antenna_gain_dbi} dBi)] - [Perdas: {losses_db} dB] = [ERP Máx: Z dBW / A dBm / B kW]."
  Este valor "A" (em dBm) é a ERP máxima de referência.
  Após o cálculo, insira um caractere de nova linha (use `\\n`) e continue com o resumo executivo (até 7 frases) usando a ERP calculada.
- coverage: Análise da mancha/cobertura, validando a cobertura contra a ERP calculada (Z dBW) e o {radius_km}.
- profile: Análise do perfil de enlace.
- pattern_horizontal: Análise da imagem do diagrama horizontal. Utilize sempre o valor pico a pico ({horizontal_peak_to_peak_db} dB) para classificar (ex.: <6 dB = omni, 6–12 dB = quasi‑omni, >12 dB = direcional). Se a imagem contrariar o valor, explique a divergência. Comente se esta direcionalidade está alinhada às {project_notes}.
- pattern_vertical: comentários sobre tilt/elevação/ nulos e lobulos.
- recommendations: lista com, no mínimo, 3 recomendações objetivas.
- conclusion: parecer final considerando as notas do projeto, a ERP calculada (Z dBW / A dBm), e a direcionalidade (conforme 'pattern_horizontal').
- link_analyses: para cada receptor listado no JSON {link_payload} (mesmo se faltarem dados):
  1. Forneça uma análise específica (distância, campo, potência).
  2. Verifique se os níveis de sinal são coerentes com a ERP DIRECIONAL para aquele receptor (campo 'erp_dbm' no JSON do receptor). NÃO compare com a ERP máxima do sistema, pois a antena pode atenuar o sinal naquela direção.
  3. **Crucial:** Compare o campo/potência do receptor (listado em {link_payload}) com qualquer valor de campo/potência estimado na análise do 'profile' (se disponível para esse receptor) e aponte explicitamente qualquer discrepância (ex: "Campo no perfil é 70.8 dBµV/m, mas no receptor é 63.3 dBµV/m").
- Utilize 25 dBµV/m como limiar mínimo de cobertura aceitável; destaque quando um receptor ficar abaixo desse valor e proponha correção.
- Caso não haja perfil/imagem para um receptor, explicite "Dados indisponíveis" mas mantenha a entrada no array.
- Ao comentar os diagramas horizontal/vertical, cite se o sistema é omni ou direcional com base na análise visual da imagem.
- Utilize o resumo de receptores para avaliar cada enlace no campo profile/conclusion, apontando discrepâncias de campo/potência.
Caso alguma imagem não esteja disponível, informe explicitamente que a análise ficou limitada.
As imagens (mancha, perfil e diagramas) serão fornecidas como anexos inline; use-as nos campos indicados.
"""


_JSON_BLOCK_RE = re.compile(r"(\{.*\}|\[.*\])", re.S)


VALIDATION_PROMPT = """Você é um auditor técnico. Verifique se os textos do relatório estão coerentes com os dados oficiais.
Sempre explique o motivo da inconsistência usando valores objetivos (ERP, HAAT, distâncias, campo mínimo, etc.).

Projeto: {project_name} ({project_slug})
Serviço / Classe: {service} / {service_class}
Localização: {location}
Raio alvo: {radius_km} km
ERP (dBm): {erp_dbm}
HAAT médio: {haat_average_m} m
Notas: {project_notes}

JSON de métricas:
{metrics_json}

Resumo de receptores:
{link_summary}

JSON de receptores:
{links_json}

Seções atuais do relatório (JSON):
{sections_json}

Analise cada campo e responda EXCLUSIVAMENTE com uma lista JSON:
[
  {{
    "field": "coverage",
    "issue": "Descrição clara do problema",
    "severity": "error" | "warning",
    "corrected_text": "Texto substituto válido (sem citar IA)",
    "evidence": "Trecho explicando quais números justificam a correção"
  }}
]
Se não houver problemas, retorne [].
"""


def _extract_json_text(raw_text: str) -> str:
    raw_text = (raw_text or "").strip()
    if not raw_text:
        raise AISummaryError("Resposta vazia do modelo Gemini.")
    fence_match = re.search(r"```json\s*(\{.*?\}|\[.*?\])\s*```", raw_text, re.S | re.I)
    if fence_match:
        return fence_match.group(1)
    match = _JSON_BLOCK_RE.search(raw_text)
    if match:
        return match.group(0)
    return raw_text


def build_ai_summary(
    project,
    snapshot: Dict[str, Any],
    metrics: Dict[str, Any],
    images: Dict[str, bytes] | None = None,
    links_payload: list[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    api_key = current_app.config.get("GEMINI_API_KEY")
    if not api_key:
        raise AIUnavailable("GEMINI_API_KEY não configurada.")
    model_name = current_app.config.get("GEMINI_MODEL", "gemini-2.5-flash")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)

    links_json = json.dumps(links_payload or [], ensure_ascii=False, indent=2)

    prompt = ANALYSIS_PROMPT.format(
        project_name=project.name,
        project_slug=project.slug,
        service=metrics.get("service") or (project.settings or {}).get("serviceType") or "FM",
        service_class=metrics.get("service_class") or (project.settings or {}).get("serviceClass") or "—",
        location=metrics.get("location") or snapshot.get("tx_location_name") or "—",
        erp_dbm=metrics.get("erp_dbm") or "—",
        radius_km=metrics.get("radius_km") or "—",
        frequency_mhz=metrics.get("frequency_mhz") or "—",
        polarization=metrics.get("polarization") or "—",
        engine=snapshot.get("engine") or "—",
        climate=metrics.get("climate") or "Não informado",
        project_notes=metrics.get("project_notes") or "Sem notas registradas.",
        horizontal_peak_to_peak_db=metrics.get("horizontal_peak_to_peak_db") or "—",
       # horizontal_peak_to_peak_db=metrics.get("horizontal_peak_to_peak_db") or "—",
        link_summary=metrics.get("link_summary") or "Nenhum receptor cadastrado.",
        link_payload=links_json,
        # Elas leem do dict 'metrics' e passam para o prompt
        tx_power_w=metrics.get("tx_power_w") or "0",
        antenna_gain_dbi=metrics.get("antenna_gain_dbi") or "0",
        antenna_gain_dbd=metrics.get("antenna_gain_dbd") or "0",
        losses_db=metrics.get("losses_db") or "0"
    )

    parts: list[dict[str, Any]] = [{"text": prompt}]
    if images:
        for label, blob in images.items():
            if not blob:
                continue
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": base64.b64encode(blob).decode('utf-8'),
                }
            })
            parts.append({
                "text": f"A imagem acima representa '{label}'. Considere-a ao preencher os campos apropriados."
            })

    response = model.generate_content(parts)
    text = _extract_json_text(getattr(response, "text", ""))

    try:
        summary = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AISummaryError("Formato de resposta inválido do modelo.") from exc

    required = [
        "overview",
        "coverage",
        "profile",
        "pattern_horizontal",
        "pattern_vertical",
        "recommendations",
        "conclusion",
        "link_analyses",
    ]
    for key in required:
        if key not in summary:
            raise AISummaryError(f"Campo '{key}' ausente na resposta do modelo.")
    recs = summary.get("recommendations")
    if isinstance(recs, str):
        recs = [item.strip() for item in recs.split("\n") if item.strip()]
    if not isinstance(recs, list):
        raise AISummaryError("Campo 'recommendations' deveria ser uma lista.")
    summary["recommendations"] = recs if recs else []
    link_analyses = summary.get("link_analyses") or []
    if isinstance(link_analyses, dict):
        link_analyses = [link_analyses]
    normalized_links = []
    if isinstance(link_analyses, list):
        for item in link_analyses:
            if not isinstance(item, dict):
                continue
            label = item.get("label")
            analysis = item.get("analysis") or item.get("texto") or item.get("comentario")
            if not label or not analysis:
                continue
            normalized_links.append({"label": str(label), "analysis": str(analysis)})
    summary["link_analyses"] = normalized_links
    return summary


def validate_ai_sections(
    project,
    snapshot: Dict[str, Any],
    metrics: Dict[str, Any],
    ai_sections: Dict[str, Any],
    link_summary: str,
    links_payload: list[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    api_key = current_app.config.get("GEMINI_API_KEY")
    if not api_key:
        raise AIUnavailable("GEMINI_API_KEY não configurada.")
    model_name = current_app.config.get("GEMINI_MODEL", "gemini-2.5-flash")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)

    metrics_payload = {
        "service": metrics.get("service"),
        "service_class": metrics.get("service_class"),
        "erp_dbm": metrics.get("erp_dbm"),
        "haat_average_m": metrics.get("haat_average_m"),
        "radius_km": metrics.get("radius_km"),
        "frequency_mhz": metrics.get("frequency_mhz"),
        "polarization": metrics.get("polarization"),
        "losses_db": metrics.get("losses_db"),
        "tx_power_w": metrics.get("tx_power_w"),
        "location": metrics.get("location") or snapshot.get("tx_location_name"),
    }
    metrics_json = json.dumps(metrics_payload, ensure_ascii=False, indent=2)
    sections_json = json.dumps(ai_sections or {}, ensure_ascii=False, indent=2)
    links_json = json.dumps(links_payload or [], ensure_ascii=False, indent=2)

    prompt = VALIDATION_PROMPT.format(
        project_name=project.name,
        project_slug=project.slug,
        service=metrics.get("service") or (project.settings or {}).get("serviceType") or "FM",
        service_class=metrics.get("service_class") or (project.settings or {}).get("serviceClass") or "—",
        location=metrics.get("location") or snapshot.get("tx_location_name") or "—",
        radius_km=metrics.get("radius_km") or snapshot.get("requested_radius_km") or snapshot.get("radius") or "—",
        erp_dbm=metrics.get("erp_dbm") or "—",
        haat_average_m=metrics.get("haat_average_m") or "—",
        project_notes=metrics.get("project_notes") or "Sem notas registradas.",
        metrics_json=metrics_json,
        link_summary=link_summary or "Nenhum receptor cadastrado.",
        links_json=links_json,
        sections_json=sections_json,
    )

    response = model.generate_content([{"text": prompt}])
    text = _extract_json_text(getattr(response, "text", ""))
    try:
        issues = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AISummaryError("Formato de resposta inválido do modelo.") from exc

    if isinstance(issues, dict):
        issues = [issues]
    normalized: List[Dict[str, Any]] = []
    if isinstance(issues, list):
        for item in issues:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or "").strip()
            if not field:
                continue
            normalized.append({
                "field": field,
                "issue": item.get("issue") or item.get("mensagem") or item.get("detalhe") or "Inconsistência não detalhada.",
                "severity": item.get("severity") or "warning",
                "corrected_text": item.get("corrected_text") or item.get("correcao"),
                "evidence": item.get("evidence") or item.get("justificativa") or item.get("explicacao"),
            })
    return normalized

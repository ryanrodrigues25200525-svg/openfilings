"""Parse and render BMV's JSON serialization of Mexican IFRS XBRL filings."""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import TYPE_CHECKING, Any

from openfilings.exceptions import ExtractionError, FinancialsUnavailableError
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES

if TYPE_CHECKING:
    from openfilings.xbrl.parser import ParsedXbrl, XbrlContext, XbrlFact

_MAX_ARCHIVE_FILES = 10
_MAX_FACTS = 100_000
_MAX_CONTEXTS = 20_000
_WHITESPACE = re.compile(r"\s+")
_HTML_TAG = re.compile(r"<[^>]+>")


def bmv_json_to_markdown(archive_bytes: bytes) -> str:
    """Render BMV presentation roles and facts as navigable Markdown."""

    payload = load_bmv_json_archive(archive_bytes)
    taxonomy = _mapping(payload.get("Taxonomia"))
    concepts = _mapping(taxonomy.get("ConceptosPorId"))
    facts = _mapping(payload.get("HechosPorId"))
    facts_by_concept = _mapping(payload.get("HechosPorIdConcepto"))
    contexts = _mapping(payload.get("ContextosPorId"))
    units = _mapping(payload.get("UnidadesPorId"))

    lines = [
        f"# {_document_title(payload)}",
        "",
        "Source format: BMV IFRS XBRL JSON",
        "",
    ]
    rendered_fact_ids: set[str] = set()
    roles = taxonomy.get("RolesPresentacion")
    for role in roles if isinstance(roles, list) else []:
        if not isinstance(role, dict):
            continue
        role_lines = _render_role(
            role,
            concepts=concepts,
            facts=facts,
            facts_by_concept=facts_by_concept,
            contexts=contexts,
            units=units,
            rendered_fact_ids=rendered_fact_ids,
        )
        if role_lines:
            lines.extend(role_lines)

    remaining = [
        fact_id
        for fact_id, fact in facts.items()
        if fact_id not in rendered_fact_ids and isinstance(fact, dict)
    ]
    if remaining:
        lines.extend(
            _render_fact_group(
                "Other reported facts",
                remaining,
                facts=facts,
                concepts=concepts,
                contexts=contexts,
                units=units,
                rendered_fact_ids=rendered_fact_ids,
            )
        )
    return "\n".join(lines).strip() + "\n"


def parse_bmv_json_xbrl(archive_bytes: bytes) -> ParsedXbrl:
    """Convert BMV JSON facts into OpenFilings' compact XBRL representation."""

    from openfilings.xbrl.parser import ParsedXbrl

    payload = load_bmv_json_archive(archive_bytes)
    raw_contexts = _mapping(payload.get("ContextosPorId"))
    raw_units = _mapping(payload.get("UnidadesPorId"))
    raw_facts = _mapping(payload.get("HechosPorId"))
    if len(raw_contexts) > _MAX_CONTEXTS:
        raise FinancialsUnavailableError("BMV filing contains too many contexts.")
    if len(raw_facts) > _MAX_FACTS:
        raise FinancialsUnavailableError("BMV filing contains too many facts.")

    contexts = {
        context_id: context
        for context_id, value in raw_contexts.items()
        if (context := _context_from_json(context_id, value)) is not None
    }
    units = {
        unit_id: label
        for unit_id, value in raw_units.items()
        if (label := _unit_label(value))
    }
    taxonomy = _mapping(payload.get("Taxonomia"))
    prefixes = _mapping(taxonomy.get("mapaPrefijos"))
    facts = tuple(
        fact
        for value in raw_facts.values()
        if (fact := _fact_from_json(value, prefixes)) is not None
    )
    if not any(fact.numeric and fact.value is not None for fact in facts):
        raise FinancialsUnavailableError(
            "The BMV filing contains no usable numeric facts."
        )
    return ParsedXbrl(
        contexts=contexts,
        units=units,
        facts=facts,
        taxonomy_namespaces=tuple(sorted(str(key) for key in prefixes)),
    )


def load_bmv_json_archive(archive_bytes: bytes) -> dict[str, Any]:
    """Load the single bounded JSON document from a BMV filing archive."""

    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if len(members) > _MAX_ARCHIVE_FILES:
                raise ExtractionError("The BMV archive contains too many files.")
            if sum(member.file_size for member in members) > MAX_TAGGED_DOCUMENT_BYTES:
                raise ExtractionError("The BMV archive is too large when expanded.")
            json_members = [
                member
                for member in members
                if member.filename.casefold().endswith(".json")
                and ".." not in member.filename
            ]
            if len(json_members) != 1:
                raise ExtractionError(
                    "The BMV archive must contain exactly one JSON report."
                )
            payload = json.loads(archive.read(json_members[0]))
    except ExtractionError:
        raise
    except (
        json.JSONDecodeError,
        OSError,
        UnicodeDecodeError,
        zipfile.BadZipFile,
    ) as exc:
        raise ExtractionError(f"Could not read the BMV filing archive: {exc}") from exc
    if not isinstance(payload, dict):
        raise ExtractionError("The BMV archive contains an invalid JSON report.")
    return payload


def _render_role(
    role: dict[str, Any],
    *,
    concepts: dict[str, Any],
    facts: dict[str, Any],
    facts_by_concept: dict[str, Any],
    contexts: dict[str, Any],
    units: dict[str, Any],
    rendered_fact_ids: set[str],
) -> list[str]:
    fact_ids: list[str] = []
    structures = role.get("Estructuras")
    for concept_id in _presentation_concepts(
        structures if isinstance(structures, list) else []
    ):
        ids = facts_by_concept.get(concept_id)
        if isinstance(ids, list):
            fact_ids.extend(str(value) for value in ids)
    title = str(role.get("Nombre") or role.get("Uri") or "BMV disclosure")
    return _render_fact_group(
        title,
        fact_ids,
        facts=facts,
        concepts=concepts,
        contexts=contexts,
        units=units,
        rendered_fact_ids=rendered_fact_ids,
    )


def _render_fact_group(
    title: str,
    fact_ids: list[str],
    *,
    facts: dict[str, Any],
    concepts: dict[str, Any],
    contexts: dict[str, Any],
    units: dict[str, Any],
    rendered_fact_ids: set[str],
) -> list[str]:
    numeric_rows: list[str] = []
    narratives: list[tuple[str, str]] = []
    for fact_id in fact_ids:
        if fact_id in rendered_fact_ids:
            continue
        fact = facts.get(fact_id)
        if not isinstance(fact, dict) or fact.get("EsValorNil") is True:
            continue
        rendered_fact_ids.add(fact_id)
        concept_id = str(fact.get("IdConcepto") or "")
        label = _concept_label(concepts.get(concept_id), fact)
        raw_value = str(fact.get("Valor") or "").strip()
        if not raw_value:
            continue
        if fact.get("EsNumerico") is True:
            context = contexts.get(str(fact.get("IdContexto") or ""))
            period, dimensions = _context_labels(context)
            unit = _unit_label(units.get(str(fact.get("IdUnidad") or "")))
            numeric_rows.append(
                "| "
                + " | ".join(
                    _escape_table(value)
                    for value in (label, period, raw_value, unit, dimensions)
                )
                + " |"
            )
        else:
            text = _markdown_text(raw_value)
            if text and text.casefold() not in {"true", "false"}:
                narratives.append((label, text))

    if not numeric_rows and not narratives:
        return []
    lines = [f"## {_markdown_text(title)}", ""]
    if numeric_rows:
        lines.extend(
            [
                "| Line item | Period | Value | Unit | Dimensions |",
                "|---|---|---:|---|---|",
                *numeric_rows,
                "",
            ]
        )
    for label, text in narratives:
        lines.extend([f"### {label}", "", text, ""])
    return lines


def _presentation_concepts(structures: list[Any]) -> list[str]:
    concepts: list[str] = []
    stack = list(reversed(structures))
    while stack:
        structure = stack.pop()
        if not isinstance(structure, dict):
            continue
        concept_id = str(structure.get("IdConcepto") or "").strip()
        if concept_id:
            concepts.append(concept_id)
        children = structure.get("SubEstructuras")
        if isinstance(children, list):
            stack.extend(reversed(children))
    return concepts


def _context_from_json(context_id: str, value: Any) -> XbrlContext | None:
    from openfilings.xbrl.parser import XbrlContext

    if not isinstance(value, dict):
        return None
    period = _mapping(value.get("Periodo"))
    instant = _date_value(period.get("FechaInstante"))
    start = _date_value(period.get("FechaInicio"))
    end = _date_value(period.get("FechaFin"))
    dimensions: list[tuple[str, str]] = []
    raw_dimensions = value.get("ValoresDimension")
    for dimension in raw_dimensions if isinstance(raw_dimensions, list) else []:
        if not isinstance(dimension, dict):
            continue
        axis = str(dimension.get("IdDimension") or "").strip()
        member = str(
            dimension.get("IdItemMiembro")
            or _markdown_text(str(dimension.get("ElementoMiembroTipificado") or ""))
        ).strip()
        if axis and member:
            dimensions.append((axis, member))
    if not instant and not (start and end):
        return None
    return XbrlContext(
        id=context_id,
        start_date=start,
        end_date=end,
        instant=instant,
        dimensions=tuple(sorted(dimensions)),
    )


def _fact_from_json(value: Any, prefixes: dict[str, Any]) -> XbrlFact | None:
    from openfilings.xbrl.parser import XbrlFact

    if not isinstance(value, dict):
        return None
    context_ref = str(value.get("IdContexto") or "").strip()
    local_name = str(value.get("NombreConcepto") or "").strip()
    namespace = str(value.get("EspacioNombres") or "").strip()
    if not context_ref or not local_name:
        return None
    prefix = str(prefixes.get(namespace) or "").strip()
    concept = f"{prefix}:{local_name}" if prefix else local_name
    raw_value = str(value.get("Valor") or "").strip()
    numeric = value.get("EsNumerico") is True
    number = (
        _decimal_value(raw_value) if numeric and not value.get("EsValorNil") else None
    )
    return XbrlFact(
        concept=concept,
        context_ref=context_ref,
        unit_ref=str(value.get("IdUnidad") or "").strip() or None,
        decimals=str(value.get("Decimales") or "").strip() or None,
        value=number,
        raw_value=raw_value,
        numeric=numeric,
    )


def _concept_label(value: Any, fact: dict[str, Any]) -> str:
    if isinstance(value, dict):
        labels = _mapping(value.get("Etiquetas"))
        for language in ("es", "en"):
            by_role = _mapping(labels.get(language))
            for role in (
                "http://www.xbrl.org/2003/role/label",
                "http://www.xbrl.org/2003/role/terseLabel",
            ):
                label = _mapping(by_role.get(role)).get("Valor")
                if label:
                    return _markdown_text(str(label))
    return _markdown_text(
        str(fact.get("NombreConcepto") or fact.get("IdConcepto") or "Reported fact")
    )


def _context_labels(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        return "", ""
    period = _mapping(value.get("Periodo"))
    instant = _date_value(period.get("FechaInstante"))
    start = _date_value(period.get("FechaInicio"))
    end = _date_value(period.get("FechaFin"))
    period_label = instant.isoformat() if instant else ""
    if start and end:
        period_label = f"{start.isoformat()} to {end.isoformat()}"
    dimension_labels: list[str] = []
    raw_dimensions = value.get("ValoresDimension")
    for dimension in raw_dimensions if isinstance(raw_dimensions, list) else []:
        if not isinstance(dimension, dict):
            continue
        axis = str(dimension.get("IdDimension") or "").strip()
        member = str(
            dimension.get("IdItemMiembro")
            or _markdown_text(str(dimension.get("ElementoMiembroTipificado") or ""))
        ).strip()
        if axis and member:
            dimension_labels.append(f"{axis}={member}")
    return period_label, "; ".join(dimension_labels)


def _unit_label(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    numerator = _measure_labels(value.get("MedidasNumerador"))
    denominator = _measure_labels(value.get("MedidasDenominador"))
    if numerator or denominator:
        return f"{numerator} / {denominator}".strip(" /")
    return _measure_labels(value.get("Medidas"))


def _measure_labels(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    labels = []
    for measure in value:
        if not isinstance(measure, dict):
            continue
        label = str(measure.get("Etiqueta") or measure.get("Nombre") or "").strip()
        if label:
            labels.append(label)
    return " * ".join(labels)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _document_title(payload: dict[str, Any]) -> str:
    entities = _mapping(payload.get("EntidadesPorId"))
    entity = next(
        (
            str(value.get("Id") or "").strip()
            for value in entities.values()
            if isinstance(value, dict) and value.get("Id")
        ),
        "BMV issuer",
    )
    contexts = _mapping(payload.get("ContextosPorId"))
    dates = []
    for value in contexts.values():
        if not isinstance(value, dict):
            continue
        period = _mapping(value.get("Periodo"))
        dates.extend(
            candidate
            for candidate in (
                _date_value(period.get("FechaInstante")),
                _date_value(period.get("FechaFin")),
            )
            if candidate is not None
        )
    suffix = f" — {max(dates).isoformat()}" if dates else ""
    return f"{_markdown_text(entity)} BMV IFRS filing{suffix}"


def _date_value(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _decimal_value(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def _markdown_text(value: str) -> str:
    return _WHITESPACE.sub(" ", unescape(_HTML_TAG.sub(" ", value))).strip()


def _escape_table(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ")

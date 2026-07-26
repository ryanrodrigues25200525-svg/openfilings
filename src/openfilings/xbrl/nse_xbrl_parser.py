"""Parser for NSE's "Integrated Filing - Financials" XBRL instance documents.

Since April 2025, SEBI Regulation 33 financial results are filed exclusively
in XBRL (the ``in-capmkt``/``IFIndAs`` taxonomy) - PDF submission for
financial results was discontinued. This is a plain XBRL 2003 instance
document (facts are namespaced elements with a ``contextRef``), not inline
XBRL embedded in HTML, so it needs its own parser - but it reuses the same
``ParsedXbrl``/``XbrlContext``/``XbrlFact`` shapes (and therefore the same
downstream statement-building and concept mapping) as every other tagged
market, since the taxonomy's concept names (``Assets``, ``CurrentAssets``,
``Revenue``, ...) already match the standard IFRS concepts recognized there.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation

from openfilings.exceptions import FinancialsUnavailableError
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES
from openfilings.xbrl.parser import ParsedXbrl, XbrlContext, XbrlFact

_XBRLI_NS = "http://www.xbrl.org/2003/instance"
_XBRLDI_NS = "http://xbrl.org/2006/xbrldi"
_XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
_MAX_FACTS = 100_000
_MAX_CONTEXTS = 20_000
_MAX_UNITS = 20_000


def parse_nse_xbrl_instance(data: bytes) -> ParsedXbrl:
    if len(data) > MAX_TAGGED_DOCUMENT_BYTES:
        raise FinancialsUnavailableError(
            "The NSE Integrated Filing XBRL document exceeds the size limit."
        )
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise FinancialsUnavailableError(
            "The NSE Integrated Filing XBRL document contains unsupported "
            "XML declarations."
        )

    contexts: dict[str, XbrlContext] = {}
    units: dict[str, str] = {}
    facts: list[XbrlFact] = []
    namespaces: set[str] = set()
    try:
        events = ET.iterparse(io.BytesIO(data), events=("end",))
        for _, element in events:
            namespace, _, local = element.tag.removeprefix("{").partition("}")
            if namespace == _XBRLI_NS:
                if local == "context":
                    context = _parse_context(element)
                    if context is not None:
                        if len(contexts) >= _MAX_CONTEXTS:
                            raise FinancialsUnavailableError(
                                "The NSE Integrated Filing XBRL document has "
                                "too many contexts."
                            )
                        contexts[context.id] = context
                    element.clear()
                elif local == "unit":
                    unit_id = element.get("id")
                    measure = element.find(f"{{{_XBRLI_NS}}}measure")
                    if unit_id and measure is not None and measure.text:
                        if len(units) >= _MAX_UNITS:
                            raise FinancialsUnavailableError(
                                "The NSE Integrated Filing XBRL document has "
                                "too many units."
                            )
                        units[unit_id] = measure.text.rsplit(":", 1)[-1]
                    element.clear()
                continue
            if namespace and local:
                namespaces.add(namespace)
                fact = _parse_fact(element, local)
                if fact is not None:
                    if len(facts) >= _MAX_FACTS:
                        raise FinancialsUnavailableError(
                            "The NSE Integrated Filing XBRL document has too "
                            "many facts."
                        )
                    facts.append(fact)
    except ET.ParseError as exc:
        raise FinancialsUnavailableError(
            f"The NSE Integrated Filing XBRL document could not be parsed: {exc}"
        ) from exc

    return ParsedXbrl(
        contexts=contexts,
        units=units,
        facts=tuple(facts),
        taxonomy_namespaces=tuple(sorted(namespaces)),
    )


def _parse_context(element: ET.Element) -> XbrlContext | None:
    context_id = element.get("id")
    if not context_id:
        return None
    period = element.find(f"{{{_XBRLI_NS}}}period")
    start_date = end_date = instant = None
    if period is not None:
        start_element = period.find(f"{{{_XBRLI_NS}}}startDate")
        end_element = period.find(f"{{{_XBRLI_NS}}}endDate")
        instant_element = period.find(f"{{{_XBRLI_NS}}}instant")
        if start_element is not None:
            start_date = _parse_date(start_element.text)
        if end_element is not None:
            end_date = _parse_date(end_element.text)
        if instant_element is not None:
            instant = _parse_date(instant_element.text)
    dimensions = tuple(
        (member.get("dimension", ""), (member.text or "").strip())
        for member in element.iter(f"{{{_XBRLDI_NS}}}explicitMember")
        if member.get("dimension")
    )
    return XbrlContext(
        id=context_id,
        start_date=start_date,
        end_date=end_date,
        instant=instant,
        dimensions=dimensions,
    )


def _parse_fact(element: ET.Element, local_name: str) -> XbrlFact | None:
    context_ref = element.get("contextRef")
    if not context_ref:
        return None
    raw_value = (element.text or "").strip()
    is_nil = element.get(_XSI_NIL, "").casefold() == "true"
    numeric = element.get("decimals") is not None or element.get("unitRef") is not None
    value = None if is_nil or not raw_value else _parse_numeric(raw_value)
    return XbrlFact(
        concept=local_name,
        context_ref=context_ref,
        unit_ref=element.get("unitRef"),
        decimals=element.get("decimals"),
        value=value,
        raw_value=raw_value,
        numeric=numeric and value is not None,
    )


def _parse_numeric(raw_value: str) -> Decimal | None:
    cleaned = raw_value.replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None

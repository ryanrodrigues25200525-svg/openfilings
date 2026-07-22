"""Source adapters."""

from openfilings.adapters.cvm import CvmClient
from openfilings.adapters.edinet import EdinetClient
from openfilings.adapters.esef import (
    DENMARK,
    ENABLED_ESEF_MARKETS,
    FINLAND,
    FRANCE,
    ITALY,
    NETHERLANDS,
    SPAIN,
    SWEDEN,
    EsefClient,
    EsefMarket,
)
from openfilings.adapters.hkex import HkexClient
from openfilings.adapters.sgx import SgxClient
from openfilings.adapters.twse import TwseClient

__all__ = [
    "DENMARK",
    "ENABLED_ESEF_MARKETS",
    "FINLAND",
    "FRANCE",
    "ITALY",
    "NETHERLANDS",
    "SPAIN",
    "SWEDEN",
    "CvmClient",
    "EdinetClient",
    "EsefClient",
    "EsefMarket",
    "HkexClient",
    "SgxClient",
    "TwseClient",
]

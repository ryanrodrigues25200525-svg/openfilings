"""Source adapters."""

from openfilings.adapters.bmv import BmvClient
from openfilings.adapters.cninfo import CninfoClient
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
from openfilings.adapters.nse import NseClient
from openfilings.adapters.sedar import SedarClient
from openfilings.adapters.sfc import SfcClient
from openfilings.adapters.sgx import SgxClient
from openfilings.adapters.smv import SmvClient
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
    "BmvClient",
    "CninfoClient",
    "CvmClient",
    "EdinetClient",
    "EsefClient",
    "EsefMarket",
    "HkexClient",
    "NseClient",
    "SedarClient",
    "SfcClient",
    "SgxClient",
    "SmvClient",
    "TwseClient",
]

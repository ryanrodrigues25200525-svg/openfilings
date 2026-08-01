"""Installed-package version, isolated from openfilings/__init__.py.

Adapters are imported transitively by the top-level package (via
service.py), so they cannot import from openfilings itself without risking
a circular import. This leaf module has no further internal imports, so
both __init__.py and every adapter's User-Agent header can depend on it
safely.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("openfilings")
except PackageNotFoundError:
    # Running from a source checkout without an installed distribution.
    __version__ = "0+unknown"

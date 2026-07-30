"""Search matching across the diacritics real registered names actually use."""

from __future__ import annotations

from openfilings.adapters._common import match_text, normalize_text, ranked_matches


def _records() -> list[tuple[tuple[str, ...], str]]:
    return [
        (("ØRSTED A/S",), "orsted"),
        (("JERÓNIMO MARTINS SGPS SA",), "jeronimo"),
        (("POWSZECHNA KASA OSZCZĘDNOŚCI BANK POLSKI",), "pko"),
        (("TÜRKİYE İŞ BANKASI A.Ş.",), "isbank"),
        (("UNRELATED HOLDINGS PLC",), "unrelated"),
    ]


def test_ascii_query_matches_names_with_combining_accents() -> None:
    """NFKD folds these, so they worked before; guard against regression."""

    assert ranked_matches("Jeronimo", _records(), limit=3) == ["jeronimo"]
    assert ranked_matches("Oszczednosci", _records(), limit=3) == ["pko"]


def test_ascii_query_matches_letters_nfkd_cannot_decompose() -> None:
    """Slashed O and dotless i are independent letters, not base + mark.

    NFKD leaves them intact, so a plain ASCII query could never substring-
    match the registered name. Confirmed live on filings.xbrl.org: "Orsted"
    returned nothing while ØRSTED A/S was present in the Danish index.
    """

    assert ranked_matches("Orsted", _records(), limit=3) == ["orsted"]
    assert ranked_matches("Turkiye Is Bankasi", _records(), limit=3) == ["isbank"]


def test_match_text_folds_beyond_normalize_text() -> None:
    assert normalize_text("ØRSTED") == "ørsted"
    assert match_text("ØRSTED") == "orsted"
    assert match_text("Œuvre æther Straße") == "oeuvre aether strasse"


def test_unrelated_names_still_do_not_match() -> None:
    assert ranked_matches("Orsted", [(("UNRELATED HOLDINGS PLC",), "x")], limit=3) == []

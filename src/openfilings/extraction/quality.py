"""Explainable quality scoring for extracted filing text."""

from __future__ import annotations

from openfilings.models import ExtractionQuality, QualityStatus


def assess_markdown(
    markdown: str,
    *,
    page_count: int | None = None,
) -> ExtractionQuality:
    """Score extracted content using deterministic, source-independent signals."""

    visible = "".join(character for character in markdown if not character.isspace())
    character_count = len(visible)
    characters_per_page = (
        character_count / page_count if page_count and page_count > 0 else None
    )
    alphanumeric_count = sum(character.isalnum() for character in visible)
    replacement_count = visible.count("\ufffd")
    alphanumeric_ratio = (
        alphanumeric_count / character_count if character_count else 0.0
    )
    replacement_ratio = replacement_count / character_count if character_count else 0.0
    nonempty_lines = [line for line in markdown.splitlines() if line.strip()]

    score = 100
    warnings: list[str] = []
    if character_count == 0:
        score = 0
        warnings.append("no_text")
    elif character_count < 80:
        score -= 60
        warnings.append("very_little_text")
    elif character_count < 300:
        score -= 25
        warnings.append("little_text")

    if characters_per_page is not None:
        if characters_per_page < 40:
            score -= 55
            warnings.append("very_low_text_per_page")
        elif characters_per_page < 120:
            score -= 25
            warnings.append("low_text_per_page")

    if character_count and alphanumeric_ratio < 0.35:
        score -= 30
        warnings.append("low_alphanumeric_ratio")
    elif character_count and alphanumeric_ratio < 0.5:
        score -= 15
        warnings.append("mixed_symbol_content")

    if replacement_ratio > 0.01:
        score -= 30
        warnings.append("encoding_replacement_characters")
    elif replacement_ratio > 0.001:
        score -= 10
        warnings.append("minor_encoding_noise")

    if character_count > 500 and len(nonempty_lines) <= 2:
        score -= 20
        warnings.append("collapsed_line_structure")

    score = min(max(score, 0), 100)
    status: QualityStatus
    if score >= 70:
        status = "good"
    elif score >= 35:
        status = "degraded"
    else:
        status = "unusable"

    return ExtractionQuality(
        score=score,
        status=status,
        character_count=character_count,
        page_count=page_count,
        characters_per_page=characters_per_page,
        alphanumeric_ratio=alphanumeric_ratio,
        replacement_character_ratio=replacement_ratio,
        warnings=tuple(warnings),
    )


def add_quality_warning(quality: ExtractionQuality, warning: str) -> ExtractionQuality:
    if warning in quality.warnings:
        return quality
    return quality.model_copy(update={"warnings": (*quality.warnings, warning)})

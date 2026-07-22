"""Optional page-at-a-time Tesseract OCR for scanned PDF filings."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable

from openfilings.exceptions import ExtractionError

CommandRunner = Callable[[list[str], bytes, float], subprocess.CompletedProcess[bytes]]


def tesseract_available(executable: str = "tesseract") -> bool:
    return shutil.which(executable) is not None


def ocr_pdf_to_markdown(
    pdf_bytes: bytes,
    *,
    language: str = "eng",
    dpi: int = 200,
    max_pages: int = 250,
    executable: str = "tesseract",
    page_timeout_seconds: float = 120.0,
    command_runner: CommandRunner | None = None,
) -> str:
    """Render and OCR one page at a time, keeping peak memory bounded."""

    resolved_executable = shutil.which(executable)
    if resolved_executable is None:
        raise ExtractionError(
            f"Tesseract executable '{executable}' is not installed or not on PATH."
        )
    if not pdf_bytes.startswith(b"%PDF"):
        raise ExtractionError("The OCR source document is not a PDF.")

    try:
        import pymupdf

        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ExtractionError(f"Could not open PDF for OCR: {exc}") from exc

    runner = command_runner or _run_command
    try:
        if document.page_count > max_pages:
            raise ExtractionError(
                f"OCR page limit exceeded: {document.page_count} pages, "
                f"maximum {max_pages}."
            )

        pages: list[str] = []
        for index, page in enumerate(document):
            pixmap = page.get_pixmap(
                dpi=dpi,
                colorspace=pymupdf.csRGB,
                alpha=False,
            )
            image_bytes = pixmap.tobytes("png")
            command = [
                resolved_executable,
                "-",
                "-",
                "-l",
                language,
                "--psm",
                "3",
                "quiet",
            ]
            try:
                completed = runner(command, image_bytes, page_timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                raise ExtractionError(
                    f"Tesseract timed out on page {index + 1}."
                ) from exc

            if completed.returncode != 0:
                detail = completed.stderr.decode("utf-8", errors="replace").strip()
                raise ExtractionError(
                    f"Tesseract failed on page {index + 1}: "
                    f"{detail[:300] or 'unknown error'}"
                )
            text = completed.stdout.decode("utf-8", errors="replace").strip()
            if text:
                pages.append(f"## Page {index + 1}\n\n{text}")
    finally:
        document.close()

    if not pages:
        raise ExtractionError("Tesseract produced no text for the PDF.")
    return "\n\n".join(pages).strip() + "\n"


def _run_command(
    command: list[str], image_bytes: bytes, timeout_seconds: float
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        input=image_bytes,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )

"""PDF page rendering and auxiliary embedded text extraction."""

from __future__ import annotations

from io import BytesIO
from typing import Any


def render_pdf(pdf_bytes: bytes, dpi: int = 200) -> list[dict[str, Any]]:
    """Render every PDF page to PNG and return page-local metadata.

    ``pypdfium2`` is required for consistent rasterization.  The input is kept
    in memory and is never modified.
    """

    if not isinstance(pdf_bytes, (bytes, bytearray, memoryview)):
        raise TypeError("pdf_bytes must be bytes")
    if not isinstance(dpi, int) or dpi <= 0:
        raise ValueError("dpi must be a positive integer")
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise ImportError("render_pdf requires pypdfium2") from exc
    return _render_pdfium(bytes(pdf_bytes), dpi, pdfium)


def _render_pdfium(pdf_bytes: bytes, dpi: int, pdfium: Any) -> list[dict[str, Any]]:
    document = pdfium.PdfDocument(pdf_bytes)
    pages: list[dict[str, Any]] = []
    scale = dpi / 72.0
    try:
        for index in range(len(document)):
            page = document[index]
            rotation = int(page.get_rotation() or 0)
            # PDFium already applies the page's intrinsic /Rotate value.  A
            # second render rotation would rotate pages twice.
            bitmap = page.render(scale=scale, rotation=0)
            image = bitmap.to_pil()
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            text_page = page.get_textpage()
            text = text_page.get_text_bounded() or ""
            pages.append(
                {
                    "page": index + 1,
                    "width": int(bitmap.width),
                    "height": int(bitmap.height),
                    "dpi": dpi,
                    "rotation": rotation,
                    "image": buffer.getvalue(),
                    "embedded_text": text,
                }
            )
            text_page.close()
            bitmap.close()
            page.close()
    finally:
        document.close()
    return pages

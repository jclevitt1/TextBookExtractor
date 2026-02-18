"""PDF to image conversion with page-range support."""
import base64
import io
from pathlib import Path

from pdf2image import convert_from_path
from PIL import Image
from rich.console import Console

console = Console()

# Claude API max dimension for multi-image requests
MAX_DIMENSION = 1568


def get_page_count(pdf_path: str) -> int:
    """Get the number of pages in a PDF."""
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


def extract_text(pdf_path: str, start_page: int, end_page: int) -> list[str]:
    """Extract text from PDF pages [start, end] inclusive (0-indexed).

    Returns a list of strings, one per page.
    """
    import pdfplumber
    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for i in range(start_page, min(end_page + 1, len(pdf.pages))):
            text = pdf.pages[i].extract_text() or ""
            pages_text.append(text)
    return pages_text


def render_pages(pdf_path: str, start: int, end: int, dpi: int = 200) -> list[str]:
    """Render PDF pages [start, end] inclusive (0-indexed) as base64 PNGs.

    Returns list of base64-encoded PNG strings.
    """
    page_count = get_page_count(pdf_path)
    start = max(0, start)
    end = min(end, page_count - 1)

    if start > end:
        return []

    console.print(
        f"  [dim]Rendering pages {start}-{end} ({end - start + 1} pages) at {dpi} DPI...[/dim]"
    )

    # pdf2image uses 1-indexed pages
    images = convert_from_path(
        pdf_path,
        dpi=dpi,
        first_page=start + 1,
        last_page=end + 1,
    )

    return [_image_to_base64(img) for img in images]


def _resize_if_needed(img: Image.Image) -> Image.Image:
    """Resize image so neither dimension exceeds MAX_DIMENSION."""
    w, h = img.size
    if w <= MAX_DIMENSION and h <= MAX_DIMENSION:
        return img
    scale = min(MAX_DIMENSION / w, MAX_DIMENSION / h)
    new_w = int(w * scale)
    new_h = int(h * scale)
    return img.resize((new_w, new_h), Image.LANCZOS)


def _image_to_base64(img: Image.Image) -> str:
    img = _resize_if_needed(img)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return base64.standard_b64encode(buffer.read()).decode("utf-8")

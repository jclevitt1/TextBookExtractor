"""Worker 2: TOC Raw Extract — read TOC pages, produce flat entry list.

Text-first with vision fallback:
1. Try pdfplumber text extraction
2. If text is clean, send text + one validation image to Claude
3. If text is garbage, fall back to full vision
"""
import re

from rich.console import Console

from ..utils import s3, pdf, claude as claude_mod, json_repair
from .. import prompts
from . import register_worker
from .base import BaseWorker

console = Console()

# Minimum ratio of alphanumeric chars for text to be considered "clean"
MIN_ALNUM_RATIO = 0.3
# Minimum text length per page to be considered non-empty
MIN_TEXT_PER_PAGE = 50


def _text_is_clean(pages_text: list[str]) -> bool:
    """Heuristic: is the extracted text readable or garbage?"""
    if not pages_text:
        return False

    all_text = " ".join(pages_text)
    if len(all_text.strip()) < MIN_TEXT_PER_PAGE:
        return False

    # Check alphanumeric ratio
    alnum = sum(1 for c in all_text if c.isalnum())
    total = len(all_text)
    if total == 0 or alnum / total < MIN_ALNUM_RATIO:
        return False

    # TOC should contain some numbers (page numbers)
    has_numbers = bool(re.search(r'\d{1,4}', all_text))
    if not has_numbers:
        return False

    # Check that most pages have content
    non_empty = sum(1 for t in pages_text if len(t.strip()) > MIN_TEXT_PER_PAGE)
    if non_empty < len(pages_text) * 0.5:
        return False

    return True


@register_worker
class W2TOCRaw(BaseWorker):
    """Extract flat TOC entries from the textbook's TOC pages."""

    worker_name = "w2_toc_raw"

    def execute(self, event: dict) -> dict:
        textbook_s3_uri = event["textbook_s3_uri"]
        toc_start = event["toc_start_page"]
        toc_end = event["toc_end_page"]
        output_prefix = event["output_s3_prefix"]

        console.print(f"[bold blue]W2: TOC Raw Extract[/bold blue] — pages {toc_start}-{toc_end}")

        # Get PDF locally
        local_path = s3.ensure_local_pdf(textbook_s3_uri)

        # Try text-first mode
        pages_text = pdf.extract_text(local_path, toc_start, toc_end)

        client = claude_mod.get_client(self.settings, self.worker_name)

        if _text_is_clean(pages_text):
            console.print("  [green]Text extraction clean — using text-first mode[/green]")
            result = self._extract_text_mode(client, local_path, pages_text, toc_start, toc_end)
        else:
            console.print("  [yellow]Text extraction poor — falling back to vision mode[/yellow]")
            result = self._extract_vision_mode(client, local_path, toc_start, toc_end)

        # Validate we got entries
        entries = result.get("entries", [])
        if not entries:
            raise ValueError("W2 produced zero TOC entries")

        console.print(f"  [green]Extracted {len(entries)} TOC entries[/green]")

        # Write to S3
        output_uri = f"{output_prefix.rstrip('/')}/toc_raw.json"
        s3.write_json(result, output_uri)

        return {"toc_raw_uri": output_uri, "entry_count": len(entries)}

    def _extract_text_mode(
        self, client: claude_mod.ClaudeClient, local_path: str,
        pages_text: list[str], toc_start: int, toc_end: int,
    ) -> dict:
        """Mode 1: send extracted text + one validation image."""
        toc_text = "\n\n".join(
            f"--- Page {toc_start + i} ---\n{text}"
            for i, text in enumerate(pages_text)
        )

        # Render one page as validation image (pick the first TOC page)
        images = pdf.render_pages(local_path, toc_start, toc_start, dpi=self.settings.image_dpi)

        user_content = client.build_image_content(
            images=images,
            prompt=prompts.W2_USER_PROMPT_TEXT.format(toc_text=toc_text),
            page_labels=[f"PDF Page {toc_start} (validation image)"],
        )

        response = client.call(
            system=prompts.W2_SYSTEM_PROMPT,
            user_content=user_content,
        )

        return self._parse_response(client, response)

    def _extract_vision_mode(
        self, client: claude_mod.ClaudeClient, local_path: str,
        toc_start: int, toc_end: int,
    ) -> dict:
        """Mode 2: send all TOC pages as images."""
        images = pdf.render_pages(local_path, toc_start, toc_end, dpi=self.settings.image_dpi)
        page_labels = [f"PDF Page {toc_start + i}" for i in range(len(images))]

        user_content = client.build_image_content(
            images=images,
            prompt=prompts.W2_USER_PROMPT_VISION.format(
                start_page=toc_start,
                end_page=toc_end,
            ),
            page_labels=page_labels,
        )

        response = client.call(
            system=prompts.W2_SYSTEM_PROMPT,
            user_content=user_content,
        )

        return self._parse_response(client, response)

    def _parse_response(self, client: claude_mod.ClaudeClient, response: str, attempts: int = 2) -> dict:
        """Parse and validate the response, with correction retries."""
        for attempt in range(attempts + 1):
            data = json_repair.extract_json(response)

            if data is None:
                if attempt < attempts:
                    console.print("  [yellow]No JSON found, requesting correction...[/yellow]")
                    response = client.call(
                        system=prompts.W2_SYSTEM_PROMPT,
                        user_content=prompts.W2_CORRECTION_PROMPT.format(
                            error="Could not find valid JSON in response",
                            response=response[:2000],
                        ),
                    )
                    continue
                raise ValueError("Could not extract JSON from W2 response")

            # Basic validation
            if "entries" not in data or not isinstance(data["entries"], list):
                if attempt < attempts:
                    console.print("  [yellow]Missing entries array, requesting correction...[/yellow]")
                    response = client.call(
                        system=prompts.W2_SYSTEM_PROMPT,
                        user_content=prompts.W2_CORRECTION_PROMPT.format(
                            error="Response must contain an 'entries' array",
                            response=response[:2000],
                        ),
                    )
                    continue
                raise ValueError("W2 response missing 'entries' array")

            # Validate entry shape (page is optional)
            for i, entry in enumerate(data["entries"]):
                if "title" not in entry or "level" not in entry:
                    if attempt < attempts:
                        console.print(f"  [yellow]Entry {i} missing fields, requesting correction...[/yellow]")
                        response = client.call(
                            system=prompts.W2_SYSTEM_PROMPT,
                            user_content=prompts.W2_CORRECTION_PROMPT.format(
                                error=f"Entry {i} must have 'title' and 'level' fields. Got: {entry}",
                                response=response[:2000],
                            ),
                        )
                        break
                    raise ValueError(f"Entry {i} missing required fields: {entry}")
            else:
                # All entries valid
                return data

        raise ValueError("W2 response validation failed after all correction attempts")

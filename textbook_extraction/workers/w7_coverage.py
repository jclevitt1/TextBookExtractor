"""Worker 7: Coverage Cleanup — detect and extract uncovered pages.

Two phases:
1. Programmatic gap detection per top-level chapter
2. LLM extraction for each gap (same approach as W5)
"""
from rich.console import Console

from ..utils import s3, pdf, claude as claude_mod, json_repair
from .. import prompts
from . import register_worker
from .base import BaseWorker

console = Console()


def _collect_covered_pages(extraction_units: list[dict], chapter_key: str) -> set[int]:
    """Collect all PDF pages covered by W5 extractions under a given chapter."""
    covered = set()
    for unit in extraction_units:
        # Check if this unit belongs to the chapter (first path entry)
        if not unit.get("path"):
            continue
        if unit["path"][0]["key"] != chapter_key:
            continue
        start, end = unit["pdf_page_range"]
        covered.update(range(start, end + 1))
    return covered


def _group_consecutive(pages: list[int]) -> list[list[int]]:
    """Group sorted page numbers into consecutive ranges.

    Returns list of [start, end] pairs.
    """
    if not pages:
        return []

    pages = sorted(pages)
    ranges = []
    start = pages[0]
    prev = pages[0]

    for p in pages[1:]:
        if p == prev + 1:
            prev = p
        else:
            ranges.append([start, prev])
            start = p
            prev = p
    ranges.append([start, prev])

    return ranges


def _get_top_level_chapters(toc_data: dict, page_1_offset: int) -> list[dict]:
    """Extract top-level chapter info with PDF page ranges."""
    toc = toc_data["toc"]
    chapters = []
    top_keys = [k for k in toc if k not in ("title", "page_range", "content_uri")]

    for key in top_keys:
        node = toc[key]
        printed_range = node["page_range"]
        chapters.append({
            "key": key,
            "title": node["title"],
            "printed_page_range": printed_range,
            "chapter_page_range": [
                printed_range[0] + page_1_offset,
                printed_range[1] + page_1_offset,
            ],
        })

    return chapters


@register_worker
class W7Coverage(BaseWorker):
    """Detect page coverage gaps and extract missing content."""

    worker_name = "w7_coverage"

    def execute(self, event: dict) -> dict:
        textbook_s3_uri = event["textbook_s3_uri"]
        toc_structured_uri = event["toc_structured_uri"]
        extraction_manifest_uri = event["extraction_manifest_uri"]
        page_1_offset = event["page_1_offset"]
        page_count = event["page_count"]
        output_prefix = event["output_s3_prefix"]
        text_only_mode = event.get("text_only_mode", False)

        mode_label = "TEXT" if text_only_mode else "VISION"
        console.print(f"[bold blue]W7: Coverage Cleanup ({mode_label})[/bold blue]")

        # Load data
        toc_data = s3.read_json(toc_structured_uri)
        manifest = s3.read_json(extraction_manifest_uri)
        extraction_units = manifest["extraction_units"]

        # Get top-level chapters
        chapters = _get_top_level_chapters(toc_data, page_1_offset)
        console.print(f"  Checking coverage for {len(chapters)} top-level entries...")

        # Phase 1: Programmatic gap detection
        report_chapters = []
        all_gaps = []  # (chapter_info, gap_ranges) for extraction

        for ch in chapters:
            ch_key = ch["key"]
            ch_range = ch["chapter_page_range"]

            # All pages that should be covered
            full_pages = set(range(ch_range[0], ch_range[1] + 1))

            # Pages actually covered by W5
            covered = _collect_covered_pages(extraction_units, ch_key)

            # Gap pages
            gap_pages = sorted(full_pages - covered)
            gap_ranges = _group_consecutive(gap_pages)

            # Build report entry
            gaps = []
            for i, gap_range in enumerate(gap_ranges, 1):
                label = f"additional section {i}"
                gaps.append({
                    "pdf_page_range": gap_range,
                    "label": label,
                })

            fully_covered = len(gap_pages) == 0
            report_chapters.append({
                "key": ch_key,
                "title": ch["title"],
                "chapter_page_range": ch_range,
                "covered_pages": sorted(covered),
                "gaps": gaps,
                "fully_covered": fully_covered,
            })

            if not fully_covered:
                all_gaps.append((ch, gaps))
                console.print(
                    f"  [yellow]{ch_key}: {len(gap_pages)} uncovered pages "
                    f"in {len(gap_ranges)} gap(s)[/yellow]"
                )
            else:
                console.print(f"  [green]{ch_key}: fully covered[/green]")

        # Write coverage report
        coverage_report = {"chapters": report_chapters}
        report_uri = f"{output_prefix.rstrip('/')}/coverage_report.json"
        s3.write_json(coverage_report, report_uri)

        # Phase 2: Extract content from gaps
        total_gaps = sum(len(gaps) for _, gaps in all_gaps)
        gap_results = []

        if total_gaps > 0:
            console.print(f"\n  Extracting content from {total_gaps} gap(s)...")
            local_path = s3.ensure_local_pdf(textbook_s3_uri)
            client = claude_mod.get_client(self.settings, self.worker_name)

            for ch, gaps in all_gaps:
                for gap in gaps:
                    result = self._extract_gap(
                        client, local_path, ch, gap, output_prefix, toc_data, text_only_mode,
                    )
                    gap_results.append(result)

            # Write updated TOC with additional sections
            s3.write_json(toc_data, toc_structured_uri)
            console.print(f"  [green]Updated toc_structured.json with {total_gaps} additional section(s)[/green]")
        else:
            console.print("  [green]No gaps found — full coverage![/green]")

        return {
            "coverage_report_uri": report_uri,
            "total_gaps": total_gaps,
            "gap_results": gap_results,
        }

    def _extract_gap(
        self,
        client: claude_mod.ClaudeClient,
        local_path: str,
        chapter: dict,
        gap: dict,
        output_prefix: str,
        toc_data: dict,
        text_only_mode: bool = False,
    ) -> dict:
        """Extract content from one gap range using LLM."""
        gap_range = gap["pdf_page_range"]
        label = gap["label"]
        ch_key = chapter["key"]
        ch_title = chapter["title"]

        mode_label = "TEXT" if text_only_mode else "VISION"
        console.print(
            f"    Extracting {ch_key}/{label} ({mode_label}) "
            f"(PDF pages {gap_range[0]}-{gap_range[1]})..."
        )

        # Call Claude with text or vision mode
        if text_only_mode:
            # TEXT MODE: Extract text from PDF pages
            pages_text = pdf.extract_text(local_path, gap_range[0], gap_range[1])

            # Build text-based prompt
            text_content = ""
            for i, page_text in enumerate(pages_text):
                page_num = gap_range[0] + i
                text_content += f"\n--- PDF Page {page_num} ---\n{page_text}\n"

            user_content = prompts.W7_USER_PROMPT.format(
                chapter_title=ch_title,
                start_page=gap_range[0],
                end_page=gap_range[1],
            ) + text_content

        else:
            # VISION MODE: Render pages as images
            images = pdf.render_pages(
                local_path, gap_range[0], gap_range[1], dpi=self.settings.image_dpi,
            )
            page_labels = [f"PDF Page {gap_range[0] + i}" for i in range(len(images))]

            user_content = client.build_image_content(
                images=images,
                prompt=prompts.W7_USER_PROMPT.format(
                    chapter_title=ch_title,
                    start_page=gap_range[0],
                    end_page=gap_range[1],
                ),
                page_labels=page_labels,
            )

        response = client.call(
            system=prompts.W7_SYSTEM_PROMPT,
            user_content=user_content,
        )

        content = json_repair.extract_json(response)
        if content is None:
            raise ValueError(f"Could not extract JSON for gap {ch_key}/{label}")

        if "title" not in content:
            content["title"] = f"Additional Content (pages {gap_range[0]}-{gap_range[1]})"

        # Write content.json
        output_path = f"{ch_key}/{label}/"
        content_uri = f"{output_prefix.rstrip('/')}/{output_path}content.json"
        s3.write_json(content, content_uri)

        # Enrich TOC with additional section
        page_1_offset = gap_range[0] - chapter["printed_page_range"][0]
        printed_gap = [
            gap_range[0] - (chapter["chapter_page_range"][0] - chapter["printed_page_range"][0]),
            gap_range[1] - (chapter["chapter_page_range"][0] - chapter["printed_page_range"][0]),
        ]

        toc_data["toc"][ch_key][label] = {
            "title": content["title"],
            "page_range": printed_gap,
            "content_uri": content_uri,
        }

        console.print(f"    [green]Extracted: {content['title']}[/green]")

        return {
            "chapter_key": ch_key,
            "label": label,
            "content_uri": content_uri,
            "title": content["title"],
        }

"""Worker 4: Granularity Extraction — programmatic tree walk to leaf level.

No LLM. Walks toc_structured.json to produce the fan-out manifest
of extraction units with computed page ranges and output paths.
"""
from rich.console import Console

from ..utils import s3
from . import register_worker
from .base import BaseWorker

console = Console()


def _get_child_keys(node: dict) -> list[str]:
    """Get child keys of a node in insertion order.

    Children are any key that isn't 'title', 'page_range', or 'content_uri'.
    Preserves dict insertion order (Python 3.7+).
    """
    return [k for k in node if k not in ("title", "page_range", "content_uri")]


def _walk_to_leaves(
    toc: dict,
    level_names: list[str],
    max_depth: int,
    page_1_offset: int,
    last_printed_page: int,
) -> list[dict]:
    """Walk the TOC tree and collect extraction units at leaf level.

    Args:
        toc: The top-level toc dict from toc_structured.json.
        level_names: Named levels (max 3).
        max_depth: Extraction depth (len(level_names)).
        page_1_offset: PDF pages before printed page 1.
        last_printed_page: Last printed page in the book.

    Returns:
        List of extraction unit dicts.
    """
    units: list[dict] = []

    def _recurse(node: dict, key: str, depth: int, breadcrumb: list[dict], siblings_after: list[dict]):
        """Recurse into the tree. siblings_after = remaining siblings at this level."""
        level_name = level_names[depth] if depth < len(level_names) else f"level_{depth}"

        current_crumb = {
            "level": level_name,
            "key": key,
            "title": node["title"],
        }
        path = breadcrumb + [current_crumb]

        children = _get_child_keys(node)

        # At leaf level (or no children) — this is an extraction unit
        if depth >= max_depth - 1 or not children:
            printed_start = node["page_range"][0]

            # End page: next sibling's start - 1, or parent boundary
            printed_end = _compute_end_page(node, siblings_after, last_printed_page)

            output_path = "/".join(c["key"] for c in path) + "/"

            units.append({
                "title": node["title"],
                "printed_page_range": [printed_start, printed_end],
                "pdf_page_range": [
                    printed_start + page_1_offset,
                    printed_end + page_1_offset,
                ],
                "output_path": output_path,
                "path": path,
            })
            return

        # Not at leaf — recurse into children
        child_nodes = [(k, node[k]) for k in children]
        for i, (child_key, child_node) in enumerate(child_nodes):
            remaining = [cn for _, cn in child_nodes[i + 1:]]
            _recurse(child_node, child_key, depth + 1, path, remaining)

    # Walk top-level entries
    top_keys = _get_child_keys(toc)
    top_nodes = [(k, toc[k]) for k in top_keys]

    for i, (key, node) in enumerate(top_nodes):
        remaining = [n for _, n in top_nodes[i + 1:]]
        _recurse(node, key, 0, [], remaining)

    return units


def _compute_end_page(node: dict, siblings_after: list[dict], last_printed_page: int) -> int:
    """Compute the end page for a node.

    End page = (next sibling's start page - 1).
    If no next sibling, use own page_range[1] (set by W3), or fall back to last page.
    """
    # If there's a next sibling, end just before it starts
    if siblings_after:
        next_start = siblings_after[0]["page_range"][0]
        return next_start - 1

    # No next sibling — use own page_range end (W3 computed this)
    return node["page_range"][1]


@register_worker
class W4Granularity(BaseWorker):
    """Walk TOC tree to leaf level, produce extraction manifest."""

    worker_name = "w4_granularity"

    def execute(self, event: dict) -> dict:
        toc_structured_uri = event["toc_structured_uri"]
        page_1_offset = event["page_1_offset"]
        page_count = event["page_count"]
        output_prefix = event["output_s3_prefix"]

        console.print("[bold blue]W4: Granularity Extraction[/bold blue] (programmatic)")

        # Read structured TOC
        toc_data = s3.read_json(toc_structured_uri)
        structure = toc_data["structure"]
        level_names = structure["level_names"]
        depth = min(len(level_names), 3)
        toc = toc_data["toc"]

        # Last printed page = total PDF pages - offset
        last_printed_page = page_count - page_1_offset - 1

        console.print(
            f"  Depth: {depth} levels ({', '.join(level_names[:depth])}), "
            f"offset: {page_1_offset}, last printed page: {last_printed_page}"
        )

        # Walk tree
        units = _walk_to_leaves(toc, level_names, depth, page_1_offset, last_printed_page)

        console.print(f"  [green]{len(units)} extraction units at leaf level ({level_names[depth - 1]})[/green]")

        # Build manifest
        manifest = {
            "granularity": {
                "depth": depth,
                "level_names": level_names[:depth],
                "leaf_level": level_names[depth - 1],
                "leaf_count": len(units),
            },
            "extraction_units": units,
        }

        # Write to S3
        manifest_uri = f"{output_prefix.rstrip('/')}/extraction_manifest.json"
        s3.write_json(manifest, manifest_uri)

        # Return manifest + extraction_units for Step Functions Map state
        return {
            "extraction_manifest_uri": manifest_uri,
            "extraction_units": units,
        }

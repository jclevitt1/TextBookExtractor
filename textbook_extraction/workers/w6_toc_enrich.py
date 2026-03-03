"""Worker 6: TOC Enrich — write content_uri into toc_structured.json leaf nodes.

No-LLM worker. Reads W5 extraction results and merges each section's
content_uri back into the corresponding toc_structured.json leaf node.
"""
from rich.console import Console

from ..utils import s3
from . import register_worker
from .base import BaseWorker

console = Console()


def _enrich_toc_node(node: dict, uri_map: dict, path_parts: list[str] | None = None) -> int:
    """Recursively walk TOC tree and set content_uri on leaf nodes.

    Returns number of nodes enriched.
    """
    if path_parts is None:
        path_parts = []

    enriched = 0

    for key, value in node.items():
        if key in ("title", "page_range", "content_uri"):
            continue
        if not isinstance(value, dict):
            continue

        current_path = path_parts + [key]

        # Leaf = has no child dicts besides title/page_range/content_uri
        child_keys = [k for k in value if k not in ("title", "page_range", "content_uri")]
        is_leaf = not any(isinstance(value.get(k), dict) for k in child_keys)

        if is_leaf:
            output_path = "/".join(current_path) + "/"
            if output_path in uri_map:
                value["content_uri"] = uri_map[output_path]
                enriched += 1
        else:
            enriched += _enrich_toc_node(value, uri_map, current_path)

    return enriched


def _discover_key_patterns(toc: dict, level_names: list[str]) -> dict:
    """Walk TOC tree and discover actual key patterns at each hierarchy level.

    Returns dict mapping level_name -> example key (e.g. {"chapter": "chapter 1", "section": "section 1.1"}).
    """
    patterns = {}

    def walk(node, depth=0):
        if depth >= len(level_names):
            return
        level = level_names[depth]
        for key, value in node.items():
            if key in ("title", "page_range", "content_uri"):
                continue
            if not isinstance(value, dict):
                continue
            if level not in patterns:
                patterns[level] = key  # first example at this level
            walk(value, depth + 1)

    walk(toc)
    return patterns


def _build_reader_instructions(toc_data: dict) -> dict:
    """Generate reader_instructions block from TOC structure metadata.

    Reads structure.depth and structure.level_names to produce navigation
    guidance for any downstream agent consuming the TOC.
    """
    structure = toc_data.get("structure", {})
    depth = structure.get("depth", 0)
    level_names = structure.get("level_names", [])

    if not level_names or depth == 0:
        return {}

    toc = toc_data.get("toc", {})
    key_examples = _discover_key_patterns(toc, level_names)

    leaf_level = level_names[-1]

    # Build key_format_by_level from actual examples
    key_format = {}
    for name in level_names:
        example = key_examples.get(name)
        if example:
            key_format[name] = example
        else:
            key_format[name] = f"{name} N"

    # Build example full path from discovered keys
    example_path = "/".join(key_format.get(name, f"{name} N") for name in level_names)

    return {
        "hierarchy": f"Nested dictionary with {depth} levels: {' → '.join(level_names)}",
        "level_names": level_names,
        "leaf_level": leaf_level,
        "key_examples": key_format,
        "content_location": (
            f"Extracted content (content.json with exercises, definitions, examples) "
            f"exists ONLY at {leaf_level}-level leaf nodes. Parent levels ({', '.join(level_names[:-1])}) "
            f"are organizational groupings with NO content files."
        ),
        "path_format": (
            f"Always construct paths down to the {leaf_level} level. "
            f"Example: \"{example_path}\". "
            f"Use the EXACT key names from the TOC (e.g., \"{key_format.get(level_names[0], level_names[0] + ' 1')}\", "
            f"not just \"{level_names[0]} 1\" if the TOC says otherwise)."
        ),
        "important": (
            f"NEVER return a path that stops at {', '.join(level_names[:-1])} level. "
            f"Always include the specific {leaf_level}. "
            f"If the student says 'Section 1.1', determine WHICH {leaf_level} under that section is relevant."
        ),
    }


def _count_leaves(node: dict) -> int:
    """Count leaf nodes in TOC tree."""
    count = 0
    for key, value in node.items():
        if key in ("title", "page_range", "content_uri"):
            continue
        if not isinstance(value, dict):
            continue
        child_keys = [k for k in value if k not in ("title", "page_range", "content_uri")]
        is_leaf = not any(isinstance(value.get(k), dict) for k in child_keys)
        if is_leaf:
            count += 1
        else:
            count += _count_leaves(value)
    return count


@register_worker
class W6TocEnrich(BaseWorker):
    """Merge W5 content_uri values into toc_structured.json leaf nodes."""

    worker_name = "w6_toc_enrich"

    def execute(self, event: dict) -> dict:
        toc_structured_uri = event["toc_structured_uri"]
        w5_results = event["w5_results"]

        console.print("[bold blue]W6: TOC Enrich[/bold blue]")

        # Read current TOC
        toc_data = s3.read_json(toc_structured_uri)

        # Build uri_map: output_path -> content_uri from W5 results
        uri_map = {}
        for result in w5_results:
            if result.get("content_uri") and result.get("output_path"):
                uri_map[result["output_path"]] = result["content_uri"]

        console.print(f"  W5 produced {len(uri_map)} content URIs")

        # Walk TOC tree and enrich leaf nodes
        toc = toc_data.get("toc", {})
        enriched = _enrich_toc_node(toc, uri_map)

        total_leaves = _count_leaves(toc)
        skipped = total_leaves - enriched

        console.print(f"  [green]Enriched {enriched}/{total_leaves} leaf nodes with content_uri[/green]")
        if skipped > 0:
            console.print(f"  [yellow]{skipped} leaf nodes had no matching W5 result[/yellow]")

        # Generate reader_instructions from TOC structure metadata
        reader_instructions = _build_reader_instructions(toc_data)
        if reader_instructions:
            toc_data["reader_instructions"] = reader_instructions
            console.print(f"  [green]Added reader_instructions ({reader_instructions['leaf_level']}-level leaf navigation)[/green]")
        else:
            console.print("  [yellow]No structure metadata found — skipping reader_instructions[/yellow]")

        # Write enriched TOC back to S3
        s3.write_json(toc_data, toc_structured_uri)
        console.print(f"  [green]Updated {toc_structured_uri}[/green]")

        return {
            "toc_structured_uri": toc_structured_uri,
            "sections_enriched": enriched,
            "sections_total": total_leaves,
            "sections_skipped": skipped,
        }

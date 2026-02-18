"""Worker 3: TOC Structure — organize flat entries into nested hierarchy.

Takes toc_raw.json (flat entries with title/page/level) and produces
toc_structured.json with self-describing keys and page_range at every node.
"""
import json
import re

from rich.console import Console

from ..utils import s3, claude as claude_mod, json_repair
from .. import prompts
from . import register_worker
from .base import BaseWorker

console = Console()


def _validate_node(node: dict, level_names: list[str], path: str = "") -> list[str]:
    """Recursively validate that every node has title and page_range.

    Returns a list of error messages (empty = valid).
    """
    errors = []

    if "title" not in node:
        errors.append(f"{path}: missing 'title'")
    if "page_range" not in node:
        errors.append(f"{path}: missing 'page_range'")
    elif not isinstance(node["page_range"], list) or len(node["page_range"]) != 2:
        errors.append(f"{path}: 'page_range' must be [start, end], got {node.get('page_range')}")

    # Check children — any key that isn't 'title' or 'page_range' or 'content_uri'
    # should be a child node (a dict with its own title/page_range)
    for key, value in node.items():
        if key in ("title", "page_range", "content_uri"):
            continue
        if isinstance(value, dict):
            child_errors = _validate_node(value, level_names, path=f"{path} > {key}")
            errors.extend(child_errors)

    return errors


def _validate_structured_toc(data: dict) -> list[str]:
    """Validate the full structured TOC. Returns list of error messages."""
    errors = []

    if "structure" not in data:
        errors.append("Missing 'structure'")
        return errors

    structure = data["structure"]
    if "depth" not in structure:
        errors.append("Missing 'structure.depth'")
    if "level_names" not in structure or not isinstance(structure.get("level_names"), list):
        errors.append("Missing or invalid 'structure.level_names'")
    elif len(structure["level_names"]) > 3:
        errors.append(f"level_names must have at most 3 entries, got {len(structure['level_names'])}")

    if "toc" not in data:
        errors.append("Missing 'toc'")
        return errors

    toc = data["toc"]
    if not isinstance(toc, dict) or len(toc) == 0:
        errors.append("'toc' must be a non-empty object")
        return errors

    level_names = structure.get("level_names", [])

    for key, node in toc.items():
        if not isinstance(node, dict):
            errors.append(f"toc['{key}'] must be an object")
            continue
        node_errors = _validate_node(node, level_names, path=key)
        errors.extend(node_errors)

    return errors


@register_worker
class W3TOCStructure(BaseWorker):
    """Organize flat TOC entries into a nested hierarchy with self-describing keys."""

    worker_name = "w3_toc_structure"

    def execute(self, event: dict) -> dict:
        toc_raw_uri = event["toc_raw_uri"]
        output_prefix = event["output_s3_prefix"]

        console.print(f"[bold blue]W3: TOC Structure[/bold blue]")

        # Read raw TOC from S3
        toc_raw = s3.read_json(toc_raw_uri)
        entries = toc_raw.get("entries", [])
        console.print(f"  Structuring {len(entries)} flat entries...")

        # Send to Claude for structuring
        client = claude_mod.ClaudeClient(self.settings)
        entries_json = json.dumps(entries, indent=2)

        response = client.call(
            system=prompts.W3_SYSTEM_PROMPT,
            user_content=prompts.W3_USER_PROMPT.format(entries_json=entries_json),
        )

        # Parse and validate
        result = self._parse_and_validate(client, response)

        structure = result["structure"]
        depth = structure["depth"]
        level_names = structure["level_names"]
        top_level_count = len(result["toc"])

        console.print(
            f"  [green]Structured TOC: {depth} levels ({', '.join(level_names)}), "
            f"{top_level_count} top-level entries[/green]"
        )

        # Write to S3
        output_uri = f"{output_prefix.rstrip('/')}/toc_structured.json"
        s3.write_json(result, output_uri)

        return {
            "toc_structured_uri": output_uri,
            "depth": depth,
            "level_names": level_names,
            "top_level_count": top_level_count,
        }

    def _parse_and_validate(self, client: claude_mod.ClaudeClient, response: str, attempts: int = 2) -> dict:
        """Parse, validate structure, retry on errors."""
        for attempt in range(attempts + 1):
            data = json_repair.extract_json(response)

            if data is None:
                if attempt < attempts:
                    console.print("  [yellow]No JSON found, requesting correction...[/yellow]")
                    response = client.call(
                        system=prompts.W3_SYSTEM_PROMPT,
                        user_content=prompts.W2_CORRECTION_PROMPT.format(
                            error="Could not find valid JSON in response",
                            response=response[:3000],
                        ),
                    )
                    continue
                raise ValueError("Could not extract JSON from W3 response")

            errors = _validate_structured_toc(data)
            if not errors:
                return data

            if attempt < attempts:
                error_msg = "; ".join(errors[:5])
                console.print(f"  [yellow]Validation errors ({len(errors)}), requesting correction...[/yellow]")
                response = client.call(
                    system=prompts.W3_SYSTEM_PROMPT,
                    user_content=prompts.W2_CORRECTION_PROMPT.format(
                        error=error_msg,
                        response=response[:3000],
                    ),
                )
                continue

            raise ValueError(f"W3 validation failed: {'; '.join(errors[:5])}")

        raise ValueError("W3 response validation failed after all correction attempts")

"""GetSchemaUnion — compute union of field names from first N W5 extractions.

This is called between W5's first batch and the remaining parallel batch
to establish a consistent schema for the rest of the extraction.
"""
from rich.console import Console

from ..utils import s3
from . import register_worker
from .base import BaseWorker

console = Console()

# Fields to exclude from the schema union (they're always present or are metadata)
EXCLUDE_FIELDS = {"title", "likely_hw_exercise_attributes", "most_likely_hw_exercise_attribute"}


@register_worker
class GetSchemaUnion(BaseWorker):
    """Compute union of all field names from W5 results."""

    worker_name = "get_schema_union"

    def execute(self, event: dict) -> dict:
        """
        Input:
          w5_results: array of W5 result objects, each with content_uri

        Output:
          accumulated_attributes: array of field name strings (union of all fields)
        """
        w5_results = event.get("w5_results", [])

        console.print(f"[bold blue]GetSchemaUnion[/bold blue] — processing {len(w5_results)} sections")

        all_attrs = set()

        for result in w5_results:
            if result.get("skipped"):
                continue

            content_uri = result.get("content_uri")
            if not content_uri:
                continue

            # Read the content.json
            try:
                content = s3.read_json(content_uri)
                # Extract field names, excluding metadata fields
                attrs = set(content.keys()) - EXCLUDE_FIELDS
                all_attrs.update(attrs)
                console.print(f"  Section '{result.get('title', '?')}': {sorted(attrs)}")
            except Exception as e:
                console.print(f"  [yellow]Failed to read {content_uri}: {e}[/yellow]")
                continue

        accumulated = sorted(all_attrs)
        console.print(f"  [green]Schema union: {accumulated}[/green]")

        return {"accumulated_attributes": accumulated}

"""Split Manifest Worker — splits extraction units for hybrid parallel processing.

Splits the extraction_units array into:
- first_two: first 2 units (for sequential schema establishment)
- remaining: units 2+ (for parallel processing with accumulated schema)
"""
from . import register_worker
from .base import BaseWorker


@register_worker
class SplitManifest(BaseWorker):
    """Split extraction units into first_two and remaining for hybrid parallel processing."""

    worker_name = "split_manifest"

    def execute(self, event: dict) -> dict:
        extraction_units = event.get("extraction_units", [])
        preview_mode = event.get("preview_mode", False)
        preview_section_count = event.get("preview_section_count", 1)
        total = len(extraction_units)

        if total == 0:
            return {
                "first_two": [],
                "remaining": [],
                "total_units": 0,
                "preview_mode": preview_mode,
            }

        if preview_mode:
            # Preview mode: only process first N sections, no parallel fan-out
            first_two = extraction_units[:min(preview_section_count, total)]
            remaining = []
        else:
            # Full mode: first 2 sequential (for schema), rest parallel
            first_two = extraction_units[:2] if total >= 2 else extraction_units
            remaining = extraction_units[2:] if total > 2 else []

        return {
            "first_two": first_two,
            "remaining": remaining,
            "total_units": total,
            "preview_mode": preview_mode,
        }

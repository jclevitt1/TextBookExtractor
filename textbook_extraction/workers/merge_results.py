"""Merge Results Worker — concatenates first_two_results and remaining_results into w5.

Simple utility worker that combines the results from sequential and parallel W5 execution
into a single array for downstream workers.
"""
from . import register_worker
from .base import BaseWorker


@register_worker
class MergeResults(BaseWorker):
    """Merge first_two_results and remaining_results into w5 array."""

    worker_name = "merge_results"

    def execute(self, event: dict) -> dict:
        first_two = event.get("first_two_results", [])
        remaining = event.get("remaining_results", [])

        # Concatenate arrays
        w5 = first_two + remaining

        return {"w5": w5}

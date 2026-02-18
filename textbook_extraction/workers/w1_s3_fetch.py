"""Worker 1: S3 Fetch — download textbook PDF, return metadata."""
import os

from rich.console import Console

from ..utils import s3, pdf
from . import register_worker
from .base import BaseWorker

console = Console()


@register_worker
class W1S3Fetch(BaseWorker):
    """Download textbook PDF from S3, verify it exists, return page count and size."""

    worker_name = "w1_s3_fetch"

    def execute(self, event: dict) -> dict:
        textbook_s3_uri = event["textbook_s3_uri"]
        console.print(f"[bold blue]W1: S3 Fetch[/bold blue] — {textbook_s3_uri}")

        # Validate the object exists and get size
        head = s3.head_object(textbook_s3_uri)
        file_size_bytes = head["ContentLength"]
        console.print(f"  Found PDF: {file_size_bytes / 1024 / 1024:.1f} MB")

        # Download to local /tmp
        local_path = s3.ensure_local_pdf(textbook_s3_uri)

        # Get page count
        page_count = pdf.get_page_count(local_path)
        console.print(f"  [green]PDF has {page_count} pages[/green]")

        return {
            "local_path": local_path,
            "page_count": page_count,
            "file_size_bytes": file_size_bytes,
        }

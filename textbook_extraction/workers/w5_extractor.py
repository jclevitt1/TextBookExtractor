"""Worker 5: Granular Extractor — extract content from one leaf-level section.

Each invocation processes ONE extraction unit (typically 2-10 pages).
Fan-out is handled by the pipeline runner or Step Functions Map state.
"""
from rich.console import Console

from ..utils import s3, pdf, claude as claude_mod, json_repair
from .. import prompts
from . import register_worker
from .base import BaseWorker

console = Console()


@register_worker
class W5Extractor(BaseWorker):
    """Extract structured content from one leaf-level textbook section."""

    worker_name = "w5_extractor"

    def execute(self, event: dict) -> dict:
        textbook_s3_uri = event["textbook_s3_uri"]
        output_prefix = event["output_s3_prefix"]
        unit = event["unit"]

        title = unit["title"]
        pdf_range = unit["pdf_page_range"]
        output_path = unit["output_path"]
        path = unit["path"]

        console.print(f"[bold blue]W5: Granular Extractor[/bold blue] — {title}")
        console.print(f"  PDF pages {pdf_range[0]}-{pdf_range[1]}, output: {output_path}")

        # Get PDF locally
        local_path = s3.ensure_local_pdf(textbook_s3_uri)

        # Render the pages for this section
        images = pdf.render_pages(local_path, pdf_range[0], pdf_range[1], dpi=self.settings.image_dpi)

        if not images:
            console.print("  [yellow]No pages to render — skipping[/yellow]")
            return {"content_uri": None, "skipped": True}

        page_labels = [f"PDF Page {pdf_range[0] + i}" for i in range(len(images))]

        # Build path description for the prompt
        path_description = " > ".join(f"{p['key']}: {p['title']}" for p in path)

        # Call Claude
        client = claude_mod.ClaudeClient(self.settings)
        user_content = client.build_image_content(
            images=images,
            prompt=prompts.W5_USER_PROMPT.format(
                title=title,
                path_description=path_description,
                start_page=pdf_range[0],
                end_page=pdf_range[1],
            ),
            page_labels=page_labels,
        )

        response = client.call(
            system=prompts.W5_SYSTEM_PROMPT,
            user_content=user_content,
        )

        # Parse response
        content = self._parse_response(client, response)

        # Ensure title is set
        if "title" not in content:
            content["title"] = title

        # Write content.json to S3
        content_uri = f"{output_prefix.rstrip('/')}/{output_path}content.json"
        s3.write_json(content, content_uri)

        console.print(f"  [green]Extracted: {list(content.keys())}[/green]")

        return {"content_uri": content_uri, "title": title, "output_path": output_path}

    def _parse_response(self, client: claude_mod.ClaudeClient, response: str, attempts: int = 2) -> dict:
        """Parse and validate — only requirement is that it's valid JSON with a title."""
        for attempt in range(attempts + 1):
            data = json_repair.extract_json(response)

            if data is None:
                if attempt < attempts:
                    console.print("  [yellow]No JSON found, requesting correction...[/yellow]")
                    response = client.call(
                        system=prompts.W5_SYSTEM_PROMPT,
                        user_content=f"Your previous response was not valid JSON. "
                        f"Here's what you said (truncated):\n{response[:2000]}\n\n"
                        f"Please respond with ONLY valid JSON.",
                    )
                    continue
                raise ValueError("Could not extract JSON from W5 response")

            if not isinstance(data, dict):
                if attempt < attempts:
                    console.print("  [yellow]Response is not a JSON object, requesting correction...[/yellow]")
                    response = client.call(
                        system=prompts.W5_SYSTEM_PROMPT,
                        user_content=f"Your response must be a JSON object (not an array). "
                        f"Please respond with ONLY valid JSON.",
                    )
                    continue
                raise ValueError("W5 response is not a JSON object")

            return data

        raise ValueError("W5 response parsing failed after all attempts")

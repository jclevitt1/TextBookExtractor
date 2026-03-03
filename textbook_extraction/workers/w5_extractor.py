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
        previous_attrs = event.get("previous_attributes")
        text_only_mode = event.get("text_only_mode", False)

        title = unit["title"]
        pdf_range = unit["pdf_page_range"]
        output_path = unit["output_path"]
        path = unit["path"]

        mode_label = "TEXT" if text_only_mode else "VISION"
        console.print(f"[bold blue]W5: Granular Extractor ({mode_label})[/bold blue] — {title}")
        console.print(f"  PDF pages {pdf_range[0]}-{pdf_range[1]}, output: {output_path}")
        if previous_attrs:
            console.print(f"  [dim]Using schema from previous sections: {', '.join(previous_attrs)}[/dim]")

        # Get PDF locally
        local_path = s3.ensure_local_pdf(textbook_s3_uri)

        # Build path description for the prompt
        path_description = " > ".join(f"{p['key']}: {p['title']}" for p in path)

        # Build consistency guidance
        if previous_attrs:
            consistency_guidance = (
                f"CONSISTENCY NOTE: Previous sections in this textbook used these fields: "
                f"{', '.join(previous_attrs)}.\n"
                f"If this section has similar content types, use the same field names (strongly recommended for consistency). "
                f"But if this section has unique content not seen before, add new fields as needed."
            )
        else:
            consistency_guidance = ""

        # Call Claude with text or vision mode
        client = claude_mod.get_client(self.settings, self.worker_name)

        if text_only_mode:
            # TEXT MODE: Extract text from PDF pages
            pages_text = pdf.extract_text(local_path, pdf_range[0], pdf_range[1])

            if not pages_text or all(not t.strip() for t in pages_text):
                console.print("  [yellow]No text extracted — skipping[/yellow]")
                return {"content_uri": None, "skipped": True}

            # Build text-based prompt
            text_content = ""
            for i, page_text in enumerate(pages_text):
                page_num = pdf_range[0] + i
                text_content += f"\n--- PDF Page {page_num} ---\n{page_text}\n"

            user_content = prompts.W5_USER_PROMPT.format(
                title=title,
                path_description=path_description,
                start_page=pdf_range[0],
                end_page=pdf_range[1],
                consistency_guidance=consistency_guidance,
            ) + text_content

        else:
            # VISION MODE: Render pages as images
            images = pdf.render_pages(local_path, pdf_range[0], pdf_range[1], dpi=self.settings.image_dpi)

            if not images:
                console.print("  [yellow]No pages to render — skipping[/yellow]")
                return {"content_uri": None, "skipped": True}

            page_labels = [f"PDF Page {pdf_range[0] + i}" for i in range(len(images))]

            user_content = client.build_image_content(
                images=images,
                prompt=prompts.W5_USER_PROMPT.format(
                    title=title,
                    path_description=path_description,
                    start_page=pdf_range[0],
                    end_page=pdf_range[1],
                    consistency_guidance=consistency_guidance,
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
        """Parse and validate — requires valid JSON dict with HW identification fields."""
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

            # Validate HW identification fields are present (can be null)
            if "likely_hw_exercise_attributes" not in data:
                if attempt < attempts:
                    console.print("  [yellow]Missing likely_hw_exercise_attributes, requesting correction...[/yellow]")
                    response = client.call(
                        system=prompts.W5_SYSTEM_PROMPT,
                        user_content=f"Your response is missing the required 'likely_hw_exercise_attributes' field. "
                        f"Please add both 'likely_hw_exercise_attributes' and 'most_likely_hw_exercise_attribute' "
                        f"at the top level of your JSON (set to null if no homework content).",
                    )
                    continue
                console.print("  [yellow]Warning: missing likely_hw_exercise_attributes, setting to null[/yellow]")
                data["likely_hw_exercise_attributes"] = None

            if "most_likely_hw_exercise_attribute" not in data:
                if attempt < attempts:
                    console.print("  [yellow]Missing most_likely_hw_exercise_attribute, requesting correction...[/yellow]")
                    response = client.call(
                        system=prompts.W5_SYSTEM_PROMPT,
                        user_content=f"Your response is missing the required 'most_likely_hw_exercise_attribute' field. "
                        f"Please add both 'likely_hw_exercise_attributes' and 'most_likely_hw_exercise_attribute' "
                        f"at the top level of your JSON (set to null if no homework content).",
                    )
                    continue
                console.print("  [yellow]Warning: missing most_likely_hw_exercise_attribute, setting to null[/yellow]")
                data["most_likely_hw_exercise_attribute"] = None

            return data

        raise ValueError("W5 response parsing failed after all attempts")

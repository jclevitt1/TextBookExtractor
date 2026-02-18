"""Worker 7: Section Keys Generator — generate self-describing metadata for all content.

Batch LLM pass over all content.json files (W5 + W6) to produce
section_keys metadata and normalize inconsistent field names.
"""
import json

from rich.console import Console

from ..utils import s3, claude as claude_mod, json_repair
from .. import prompts
from . import register_worker
from .base import BaseWorker

console = Console()


def _list_content_files(s3_client, bucket: str, prefix: str) -> list[str]:
    """List all content.json files under the output prefix."""
    import boto3

    paginator = s3_client.get_paginator("list_objects_v2")
    content_uris = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("/content.json"):
                content_uris.append(f"s3://{bucket}/{obj['Key']}")

    return sorted(content_uris)


@register_worker
class W7SectionKeys(BaseWorker):
    """Generate section_keys metadata for all extracted content."""

    worker_name = "w7_section_keys"

    def execute(self, event: dict) -> dict:
        output_prefix = event["output_s3_prefix"]

        console.print("[bold blue]W7: Section Keys Generator[/bold blue]")

        # Find all content.json files
        bucket, prefix = s3.parse_s3_uri(output_prefix)
        s3_client = s3._get_client()
        content_uris = _list_content_files(s3_client, bucket, prefix)

        if not content_uris:
            console.print("  [yellow]No content.json files found — nothing to do[/yellow]")
            return {"sections_processed": 0}

        console.print(f"  Found {len(content_uris)} content files")

        # Read all content files
        sections = []
        for uri in content_uris:
            content = s3.read_json(uri)
            sections.append({
                "content_uri": uri,
                "content": content,
            })

        # Build summary for Claude — send field names + first element samples,
        # not full content (too large for many sections)
        sections_summary = self._build_summary(sections)

        # Call Claude
        client = claude_mod.ClaudeClient(self.settings)
        response = client.call(
            system=prompts.W7_SYSTEM_PROMPT,
            user_content=prompts.W7_USER_PROMPT.format(
                sections_json=json.dumps(sections_summary, indent=2, ensure_ascii=False),
            ),
        )

        result = self._parse_response(client, response)

        # Apply section_keys to content files and handle normalizations
        normalizations = result.get("normalizations", [])
        if normalizations:
            console.print(f"  [yellow]Normalizations: {len(normalizations)}[/yellow]")
            for norm in normalizations:
                console.print(f"    {norm['from']} -> {norm['to']}: {norm.get('reason', '')}")

        sections_output = result.get("sections", [])

        # Build lookup by content_uri
        keys_by_uri = {s["content_uri"]: s["section_keys"] for s in sections_output}

        # Update each content.json with section_keys + apply normalizations
        updated = 0
        for section in sections:
            uri = section["content_uri"]
            content = section["content"]

            # Apply normalizations
            for norm in normalizations:
                old_name = norm["from"]
                new_name = norm["to"]
                if old_name in content and old_name != new_name:
                    content[new_name] = content.pop(old_name)

            # Add section_keys
            if uri in keys_by_uri:
                content["section_keys"] = keys_by_uri[uri]
                s3.write_json(content, uri)
                updated += 1

        console.print(f"  [green]Updated {updated}/{len(content_uris)} content files with section_keys[/green]")

        return {
            "sections_processed": updated,
            "normalizations": normalizations,
        }

    def _build_summary(self, sections: list[dict]) -> list[dict]:
        """Build a compact summary of each section for the LLM.

        For each section, include:
        - content_uri
        - field names with types and first-element samples for lists
        """
        summaries = []

        for section in sections:
            uri = section["content_uri"]
            content = section["content"]

            field_summary = {}
            for key, value in content.items():
                if key == "section_keys":
                    continue  # skip if already present from a re-run
                if isinstance(value, str):
                    field_summary[key] = {"type": "str", "sample": value[:200]}
                elif isinstance(value, bool):
                    field_summary[key] = {"type": "bool", "value": value}
                elif isinstance(value, (int, float)):
                    field_summary[key] = {"type": type(value).__name__, "value": value}
                elif isinstance(value, list):
                    info = {"type": "list", "length": len(value)}
                    if value:
                        first = value[0]
                        if isinstance(first, dict):
                            info["element_type"] = "json"
                            info["sample_element"] = first
                        else:
                            info["element_type"] = type(first).__name__
                            info["sample_element"] = first
                    summaries_entry = info
                    field_summary[key] = summaries_entry
                elif isinstance(value, dict):
                    field_summary[key] = {"type": "json", "keys": list(value.keys())[:10]}
                else:
                    field_summary[key] = {"type": type(value).__name__}

            summaries.append({
                "content_uri": uri,
                "fields": field_summary,
            })

        return summaries

    def _parse_response(self, client: claude_mod.ClaudeClient, response: str, attempts: int = 2) -> dict:
        """Parse and validate the section keys response."""
        for attempt in range(attempts + 1):
            data = json_repair.extract_json(response)

            if data is None:
                if attempt < attempts:
                    console.print("  [yellow]No JSON found, requesting correction...[/yellow]")
                    response = client.call(
                        system=prompts.W7_SYSTEM_PROMPT,
                        user_content=f"Your previous response was not valid JSON. "
                        f"Here's what you said (truncated):\n{response[:2000]}\n\n"
                        f"Please respond with ONLY valid JSON.",
                    )
                    continue
                raise ValueError("Could not extract JSON from W7 response")

            if "sections" not in data:
                if attempt < attempts:
                    console.print("  [yellow]Missing 'sections' array, requesting correction...[/yellow]")
                    response = client.call(
                        system=prompts.W7_SYSTEM_PROMPT,
                        user_content=f"Your response must contain a 'sections' array. "
                        f"Please respond with ONLY valid JSON.",
                    )
                    continue
                raise ValueError("W7 response missing 'sections'")

            return data

        raise ValueError("W7 response parsing failed after all attempts")

"""Worker 8: Section Keys Generator — generate self-describing metadata for all content.

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
class W8SectionKeys(BaseWorker):
    """Generate section_keys metadata for all extracted content."""

    worker_name = "w8_section_keys"

    def execute(self, event: dict) -> dict:
        output_prefix = event["output_s3_prefix"]

        console.print("[bold blue]W8: Section Keys Generator[/bold blue]")

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
        client = claude_mod.get_client(self.settings, self.worker_name)
        response = client.call(
            system=prompts.W8_SYSTEM_PROMPT,
            user_content=prompts.W8_USER_PROMPT.format(
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

        # Generate homework template
        hw_template_uri = self._generate_homework_template(sections, output_prefix)
        console.print(f"  [green]Generated homework template: {hw_template_uri}[/green]")

        return {
            "sections_processed": updated,
            "normalizations": normalizations,
            "homework_template_uri": hw_template_uri,
        }

    def _generate_homework_template(self, sections: list[dict], output_prefix: str) -> str:
        """Generate homework template by aggregating exercise types across all sections."""
        from collections import Counter

        console.print("\n[bold blue]Generating homework template...[/bold blue]")

        # Get TOC structure to determine hierarchy
        toc_uri = f"{output_prefix.rstrip('/')}/toc_structured.json"
        try:
            toc_data = s3.read_json(toc_uri)
            level_names = toc_data.get("level_names", ["chapter", "section", "topic"])
            console.print(f"  Hierarchy levels: {level_names}")
        except Exception as e:
            console.print(f"  [yellow]Could not read TOC, using default hierarchy: {e}[/yellow]")
            level_names = ["chapter", "section", "topic"]

        # Build hierarchy field list (capitalize first letter for display)
        hierarchy_fields = []
        for i, level in enumerate(level_names):
            hierarchy_fields.append({
                "label": f"{level.capitalize()}:",
                "level": i,
                "required": i < 2,  # First two levels required, rest optional
            })

        # Aggregate exercise types
        most_likely_counts = Counter()
        all_exercise_types = set()
        sections_with_hw = 0

        for section_data in sections:
            content = section_data["content"]

            # Count most_likely
            most_likely = content.get("most_likely_hw_exercise_attribute")
            if most_likely:
                most_likely_counts[most_likely] += 1

            # Collect all likely types
            likely_types = content.get("likely_hw_exercise_attributes")
            if likely_types:
                all_exercise_types.update(likely_types)
                sections_with_hw += 1

        # Find the most common exercise type
        if most_likely_counts:
            most_common_field, most_common_count = most_likely_counts.most_common(1)[0]
            console.print(f"  Most common exercise type: '{most_common_field}' (appears in {most_common_count} sections)")
        else:
            most_common_field = None
            most_common_count = 0
            console.print("  [yellow]No exercise types found[/yellow]")

        # Build default exercise type
        if most_common_field:
            default_exercise_type = {
                "field_name": most_common_field,
                "display_label": self._field_to_display_label(most_common_field),
                "appears_in_sections": most_common_count,
                "is_most_common": True,
            }
        else:
            default_exercise_type = None

        # Build additional exercise types (exclude the most common)
        additional_types = []
        for field_name in sorted(all_exercise_types):
            if field_name != most_common_field:
                # Count how many sections have this type
                count = sum(
                    1 for s in sections
                    if field_name in s["content"].get("likely_hw_exercise_attributes", [])
                )
                additional_types.append({
                    "field_name": field_name,
                    "display_label": self._field_to_display_label(field_name),
                    "appears_in_sections": count,
                })

        # Sort by frequency
        additional_types.sort(key=lambda x: x["appears_in_sections"], reverse=True)

        console.print(f"  Additional exercise types: {[t['field_name'] for t in additional_types]}")

        # Build template
        template = {
            "hierarchy": hierarchy_fields,
            "default_exercise_type": default_exercise_type,
            "additional_exercise_types": additional_types,
            "total_sections_with_hw": sections_with_hw,
            "total_sections": len(sections),
        }

        # Write to top level of output prefix
        template_uri = f"{output_prefix.rstrip('/')}/homework_template.json"
        s3.write_json(template, template_uri)

        return template_uri

    def _field_to_display_label(self, field_name: str) -> str:
        """Convert field_name to display label (e.g., 'review_questions' -> 'Review Questions:')."""
        # Replace underscores with spaces, capitalize each word, add colon
        words = field_name.replace("_", " ").split()
        return " ".join(word.capitalize() for word in words) + ":"

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
                        system=prompts.W8_SYSTEM_PROMPT,
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
                        system=prompts.W8_SYSTEM_PROMPT,
                        user_content=f"Your response must contain a 'sections' array. "
                        f"Please respond with ONLY valid JSON.",
                    )
                    continue
                raise ValueError("W7 response missing 'sections'")

            return data

        raise ValueError("W7 response parsing failed after all attempts")

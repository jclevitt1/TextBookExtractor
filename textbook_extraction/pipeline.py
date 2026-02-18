"""Local pipeline runner — chains workers sequentially with state passing.

Mirrors the Step Functions state machine but runs locally, with controls
for stopping early (--through) and limiting fan-out (--limit).
"""
import json
from pathlib import Path

from rich.console import Console

from .config import Settings
from .workers import get_worker

console = Console()

PIPELINE_ORDER = [
    "w1_s3_fetch",
    "w2_toc_raw",
    "w3_toc_structure",
    "w4_granularity",
    "w5_extractor",
    "w6_coverage",
    "w7_section_keys",
]

# Short aliases so you can do --through w4 instead of --through w4_granularity
WORKER_ALIASES = {
    "w1": "w1_s3_fetch",
    "w2": "w2_toc_raw",
    "w3": "w3_toc_structure",
    "w4": "w4_granularity",
    "w5": "w5_extractor",
    "w6": "w6_coverage",
    "w7": "w7_section_keys",
}


def resolve_worker_name(name: str) -> str:
    """Resolve a short alias (w4) or full name (w4_granularity)."""
    return WORKER_ALIASES.get(name, name)


def build_worker_event(worker_name: str, state: dict) -> dict:
    """Extract the parameters a worker needs from the accumulated pipeline state.

    Mirrors the Step Functions ASL Parameters blocks exactly.
    """
    if worker_name == "w1_s3_fetch":
        return {
            "worker": "w1_s3_fetch",
            "textbook_s3_uri": state["textbook_s3_uri"],
            "output_s3_prefix": state["output_s3_prefix"],
        }

    elif worker_name == "w2_toc_raw":
        return {
            "worker": "w2_toc_raw",
            "textbook_s3_uri": state["textbook_s3_uri"],
            "toc_start_page": state["toc_start_page"],
            "toc_end_page": state["toc_end_page"],
            "output_s3_prefix": state["output_s3_prefix"],
        }

    elif worker_name == "w3_toc_structure":
        return {
            "worker": "w3_toc_structure",
            "toc_raw_uri": state["w2"]["toc_raw_uri"],
            "output_s3_prefix": state["output_s3_prefix"],
        }

    elif worker_name == "w4_granularity":
        return {
            "worker": "w4_granularity",
            "toc_structured_uri": state["w3"]["toc_structured_uri"],
            "page_1_offset": state["page_1_offset"],
            "page_count": state["w1"]["page_count"],
            "output_s3_prefix": state["output_s3_prefix"],
        }

    # W5 is handled specially (fan-out) — see run_pipeline()

    elif worker_name == "w6_coverage":
        return {
            "worker": "w6_coverage",
            "textbook_s3_uri": state["textbook_s3_uri"],
            "toc_structured_uri": state["w3"]["toc_structured_uri"],
            "extraction_manifest_uri": state["w4"]["extraction_manifest_uri"],
            "page_1_offset": state["page_1_offset"],
            "page_count": state["w1"]["page_count"],
            "output_s3_prefix": state["output_s3_prefix"],
        }

    elif worker_name == "w7_section_keys":
        return {
            "worker": "w7_section_keys",
            "output_s3_prefix": state["output_s3_prefix"],
        }

    else:
        raise ValueError(f"Unknown worker: {worker_name}")


def run_pipeline(
    pipeline_input: dict,
    settings: Settings,
    *,
    through: str | None = None,
    limit: int | None = None,
    state_dir: str | None = None,
) -> dict:
    """Run the pipeline locally, chaining workers sequentially.

    Args:
        pipeline_input: Initial pipeline input dict.
        settings: App settings.
        through: Stop after this worker (e.g. "w4" or "w4_granularity").
        limit: For W5, only process this many extraction units.
        state_dir: If set, write intermediate state JSON after each worker.

    Returns:
        The accumulated state dict after the last worker that ran.
    """
    stop_after = resolve_worker_name(through) if through else None
    if stop_after and stop_after not in PIPELINE_ORDER:
        raise ValueError(f"Unknown worker: {stop_after}. Options: {PIPELINE_ORDER}")

    state = dict(pipeline_input)

    if state_dir:
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        _save_state(state, state_dir, "00_input")

    for worker_name in PIPELINE_ORDER:
        console.print(f"\n[bold]{'=' * 60}[/bold]")

        if worker_name == "w5_extractor":
            _run_w5_fanout(state, settings, limit=limit)
        else:
            event = build_worker_event(worker_name, state)
            worker = get_worker(worker_name)(settings)
            result = worker.execute(event)

            # Store result under the worker's short key (w1, w2, etc.)
            short_key = worker_name.split("_")[0]
            state[short_key] = result

        if state_dir:
            _save_state(state, state_dir, worker_name)

        if worker_name == stop_after:
            console.print(f"\n[yellow]Stopped after {worker_name} (--through)[/yellow]")
            break

    return state


def _run_w5_fanout(state: dict, settings: Settings, limit: int | None = None):
    """Run W5 for each extraction unit, sequentially.

    In Step Functions this is a Map state with MaxConcurrency=10.
    Locally we run sequentially for predictability and cost control.
    """
    units = state["w4"]["extraction_units"]
    total = len(units)

    if limit:
        units = units[:limit]
        console.print(
            f"[bold blue]W5: Granular Extractor[/bold blue] — "
            f"processing {len(units)}/{total} units (--limit {limit})"
        )
    else:
        console.print(
            f"[bold blue]W5: Granular Extractor[/bold blue] — "
            f"processing all {total} units"
        )

    results = []
    worker = get_worker("w5_extractor")(settings)

    for i, unit in enumerate(units):
        console.print(f"\n  [dim]--- Unit {i + 1}/{len(units)}: {unit.get('title', '?')} ---[/dim]")
        event = {
            "worker": "w5_extractor",
            "textbook_s3_uri": state["textbook_s3_uri"],
            "output_s3_prefix": state["output_s3_prefix"],
            "unit": unit,
        }
        result = worker.execute(event)
        results.append(result)

    state["w5"] = results


def _save_state(state: dict, state_dir: str, label: str):
    """Write the accumulated state to a JSON file."""
    path = Path(state_dir) / f"state_after_{label}.json"
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    console.print(f"  [dim]State saved: {path}[/dim]")

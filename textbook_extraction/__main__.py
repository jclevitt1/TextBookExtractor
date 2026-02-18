"""CLI entry point: python -m textbook_extraction"""
import json
import sys

from dotenv import load_dotenv

load_dotenv()

import click
from rich.console import Console

from .config import Settings
from .workers import get_worker, WORKER_REGISTRY

console = Console()


@click.group()
def cli():
    """Textbook Extraction v2 — structured data extraction pipeline."""
    pass


@cli.command()
@click.argument("worker_name")
@click.option("--input", "-i", "input_path", required=True, help="Path to input JSON file")
@click.option("--output", "-o", "output_path", default=None, help="Path to write output JSON (default: stdout)")
def run_worker(worker_name, input_path, output_path):
    """Run a single worker with the given input JSON.

    Examples:

        python -m textbook_extraction run-worker w1_s3_fetch -i w1_input.json

        python -m textbook_extraction run-worker w4_granularity -i w4_input.json -o manifest.json
    """
    _import_workers()

    with open(input_path) as f:
        event = json.load(f)

    event["worker"] = worker_name

    settings = Settings()
    worker_cls = get_worker(worker_name)
    worker = worker_cls(settings)

    console.print(f"[bold blue]Running worker: {worker_name}[/bold blue]")
    result = worker.execute(event)

    output = json.dumps(result, indent=2)
    if output_path:
        with open(output_path, "w") as f:
            f.write(output)
        console.print(f"[green]Output written to {output_path}[/green]")
    else:
        console.print(output)


@cli.command()
@click.option("--input", "-i", "input_path", required=True, help="Path to pipeline input JSON")
@click.option("--through", "-t", default=None, help="Stop after this worker (e.g. w4, w2_toc_raw)")
@click.option("--limit", "-l", type=int, default=None, help="W5 fan-out: only process N units")
@click.option("--output", "-o", "output_path", default=None, help="Write final state JSON to file")
@click.option("--state-dir", "-s", default=None, help="Dir to save intermediate state after each worker")
def run_pipeline(input_path, through, limit, output_path, state_dir):
    """Run the full pipeline (or partial with --through).

    Examples:

        # Run W1-W4, inspect manifest before spending on extraction
        python -m textbook_extraction run-pipeline -i input.json --through w4

        # Run full pipeline but only extract 3 sections (test quality)
        python -m textbook_extraction run-pipeline -i input.json --through w5 --limit 3

        # Full pipeline, save intermediate state for debugging
        python -m textbook_extraction run-pipeline -i input.json -s ./debug_state/
    """
    _import_workers()

    from .pipeline import run_pipeline as _run

    with open(input_path) as f:
        pipeline_input = json.load(f)

    settings = Settings()

    state = _run(
        pipeline_input,
        settings,
        through=through,
        limit=limit,
        state_dir=state_dir,
    )

    if output_path:
        with open(output_path, "w") as f:
            json.dump(state, f, indent=2, default=str)
        console.print(f"\n[green]Final state written to {output_path}[/green]")


@cli.command()
def list_workers():
    """List all registered workers."""
    _import_workers()
    for name in sorted(WORKER_REGISTRY.keys()):
        console.print(f"  {name}")


def _import_workers():
    """Import all worker modules to trigger registration."""
    from .workers import w1_s3_fetch  # noqa: F401
    from .workers import w2_toc_raw  # noqa: F401


if __name__ == "__main__":
    cli()

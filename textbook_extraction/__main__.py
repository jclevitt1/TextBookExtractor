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
    """Run a single worker with the given input JSON."""
    # Import all workers
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
def list_workers():
    """List all registered workers."""
    _import_workers()
    for name in sorted(WORKER_REGISTRY.keys()):
        console.print(f"  {name}")


def _import_workers():
    """Import all worker modules to trigger registration."""
    from .workers import w1_s3_fetch  # noqa: F401


if __name__ == "__main__":
    cli()

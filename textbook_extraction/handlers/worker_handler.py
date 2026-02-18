"""Lambda handler that dispatches to the appropriate worker.

Step Functions invokes this Lambda for every state. The `worker` field
in the event determines which worker class handles it.
"""
import json
import traceback

from ..config import Settings
from ..workers import get_worker

# Force-import all worker modules so they register themselves.
# Workers are added here as they're implemented.
from ..workers import w1_s3_fetch  # noqa: F401
from ..workers import w2_toc_raw  # noqa: F401


def handler(event, context):
    """AWS Lambda entry point.

    Event must contain a "worker" key with the worker name.
    Remaining keys are passed as the worker's input.
    """
    worker_name = event.get("worker")
    if not worker_name:
        return {"error": "Missing 'worker' key in event"}

    try:
        settings = Settings()
        worker_cls = get_worker(worker_name)
        worker = worker_cls(settings)
        result = worker.execute(event)
        return result

    except Exception as e:
        traceback.print_exc()
        return {
            "error": str(e),
            "worker": worker_name,
            "error_type": type(e).__name__,
        }

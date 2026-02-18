from .base import BaseWorker

WORKER_REGISTRY: dict[str, type[BaseWorker]] = {}


def register_worker(cls: type[BaseWorker]) -> type[BaseWorker]:
    """Decorator to register a worker class by its worker_name."""
    WORKER_REGISTRY[cls.worker_name] = cls
    return cls


def get_worker(name: str) -> type[BaseWorker]:
    if name not in WORKER_REGISTRY:
        raise ValueError(f"Unknown worker: {name}. Available: {list(WORKER_REGISTRY.keys())}")
    return WORKER_REGISTRY[name]

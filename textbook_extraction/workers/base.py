"""Base worker class for the extraction pipeline."""
from abc import ABC, abstractmethod

from ..config import Settings


class BaseWorker(ABC):
    """Abstract base for all pipeline workers.

    Each worker:
    - Has a unique worker_name (used for dispatch)
    - Takes a dict event, returns a dict result
    - Is independently testable
    """

    worker_name: str  # must be set by subclass (class variable)

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    def execute(self, event: dict) -> dict:
        """Run this worker.

        Args:
            event: Worker-specific input (from Step Functions state or CLI).

        Returns:
            Worker-specific output dict (merged into Step Functions state).
        """
        ...

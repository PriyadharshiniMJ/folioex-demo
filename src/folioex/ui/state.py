"""Process-wide UI state, in memory only (single user): finished datasets and the processing job."""

from folioex.dataset import Dataset
from folioex.jobs.runner import JobRegistry
from folioex.session import SessionRegistry

REGISTRY: SessionRegistry[Dataset] = SessionRegistry()
JOBS: JobRegistry[Dataset] = JobRegistry()

"""
Dependency-injection wiring for agent modules.  Owned by A1.

Routes obtain service instances via ``Depends(get_*)``.  Each getter
instantiates the module's class so A1 never edits another agent's code.
When agent owners replace the stub with a real implementation the same
protocol / base class is honoured and these factories remain unchanged.
"""

from __future__ import annotations

from functools import lru_cache

from app.browser.observer import BrowserObserver
from app.execution.executor import BrowserExecutor
from app.execution.planner import WorkflowPlanner
from app.execution.recovery import RecoveryPolicy
from app.execution.verifier import CheckpointVerifier
from app.learning.learner import WorkflowLearner
from app.memory.repository import InMemoryWorkflowRepository, WorkflowRepository
from app.voice.adapter import TextFallbackAdapter

# --- Singletons (one per process) ------------------------------------------


@lru_cache(maxsize=1)
def get_workflow_repository() -> WorkflowRepository:  # type: ignore[type-arg]
    """A2 will swap this for a SQLite-backed implementation."""
    return InMemoryWorkflowRepository()


@lru_cache(maxsize=1)
def get_browser_observer() -> BrowserObserver:
    return BrowserObserver()


@lru_cache(maxsize=1)
def get_workflow_learner() -> WorkflowLearner:
    return WorkflowLearner()


@lru_cache(maxsize=1)
def get_workflow_planner() -> WorkflowPlanner:
    return WorkflowPlanner()


@lru_cache(maxsize=1)
def get_browser_executor() -> BrowserExecutor:
    return BrowserExecutor()


@lru_cache(maxsize=1)
def get_checkpoint_verifier() -> CheckpointVerifier:
    return CheckpointVerifier()


@lru_cache(maxsize=1)
def get_recovery_policy() -> RecoveryPolicy:
    return RecoveryPolicy()


@lru_cache(maxsize=1)
def get_voice_adapter() -> TextFallbackAdapter:
    """A3 will swap this for the Gnani adapter when available."""
    return TextFallbackAdapter()

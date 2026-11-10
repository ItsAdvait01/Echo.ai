"""
Dependency-injection wiring for agent modules.  Owned by A1.

Routes obtain service instances via ``Depends(get_*)``.  Each getter
instantiates the module's class so A1 never edits another agent's code.
When agent owners replace the stub with a real implementation the same
protocol / base class is honoured and these factories remain unchanged.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.browser.adapter import ExecutionBrowserAdapter
from app.browser.observer import BrowserObserver
from app.execution.executor import BrowserExecutor
from app.execution.planner import WorkflowPlanner
from app.execution.recovery import RecoveryPolicy
from app.execution.verifier import CheckpointVerifier
from app.learning.learner import WorkflowLearner
from app.memory.factory import get_repository
from app.memory.repository import WorkflowRepository
from app.voice.adapter import TextFallbackAdapter

logger = logging.getLogger(__name__)

# --- Singletons (one per process) ------------------------------------------


def get_workflow_repository() -> WorkflowRepository:  # type: ignore[type-arg]
    """A2's process-wide repository: durable SQLite unless configured otherwise.

    ``ECHO_MEMORY_BACKEND=memory`` selects the temporary adapter. A broken store
    raises instead of silently pretending data was saved.
    """
    return get_repository()


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
def get_execution_adapter() -> ExecutionBrowserAdapter:
    """U1's controlled-browser adapter used by U3 to perform actions.

    Executor and adapter are separate objects so U3 stays free of browser
    control while still performing real browser actions.
    """
    return ExecutionBrowserAdapter()


@lru_cache(maxsize=1)
def get_browser_executor() -> BrowserExecutor:
    return BrowserExecutor(perform_action=get_execution_adapter().perform)


def shutdown_browser_resources() -> None:
    """Release controlled browsers on application shutdown."""
    observer = get_browser_observer()
    try:
        if observer.is_active:
            observer.stop(reason="app_shutdown")
    except Exception:
        logger.warning("could not stop the observation browser on shutdown", exc_info=True)

    adapter = get_execution_adapter()
    try:
        adapter.close()
    except Exception:
        logger.warning("could not close the execution browser on shutdown", exc_info=True)


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

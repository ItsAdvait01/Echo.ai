"""SQLite + SQLAlchemy implementation of the workflow memory repository.

Owned by A2. One file-backed database, three tables, no extra services. Step
definitions are stored as JSON snapshots because the shared `Workflow` contract
owns their shape; workflow and run state live in real columns.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from app.memory.errors import (
    MemoryValidationError,
    RunNotFoundError,
    UnapprovedWorkflowError,
    WorkflowNotFoundError,
)
from app.memory.models import (
    ExecutionProgress,
    VerifiedCheckpoint,
    from_iso,
    to_iso,
    utcnow,
)
from app.memory.repository import TERMINAL_RUN_STATES
from app.memory.validation import require_verification, validate_runnable
from app.schemas.contracts import AgentState, ExecutionRun, Workflow, WorkflowStep

WORKFLOW_TABLE = "memory_workflows"
RUN_TABLE = "memory_execution_runs"
CHECKPOINT_TABLE = "memory_verified_checkpoints"


class _Base(DeclarativeBase):
    pass


class _WorkflowRow(_Base):
    __tablename__ = WORKFLOW_TABLE

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    trigger_phrases: Mapped[list] = mapped_column(JSON, default=list)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    uncertainty_notes: Mapped[list] = mapped_column(JSON, default=list)


class _RunRow(_Base):
    __tablename__ = RUN_TABLE

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        String(64), ForeignKey(f"{WORKFLOW_TABLE}.id"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    last_verified_step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ended_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class _CheckpointRow(_Base):
    __tablename__ = CHECKPOINT_TABLE
    __table_args__ = (
        UniqueConstraint("run_id", "step_order", name="uq_checkpoint_run_step"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey(f"{RUN_TABLE}.id"), nullable=False, index=True
    )
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    verified_at: Mapped[str] = mapped_column(String(40), nullable=False)
    verification: Mapped[dict] = mapped_column(JSON, default=dict)


def _workflow_from_row(row: _WorkflowRow) -> Workflow:
    return Workflow(
        id=row.id,
        name=row.name,
        description=row.description or "",
        trigger_phrases=list(row.trigger_phrases or []),
        steps=[WorkflowStep.model_validate(item) for item in (row.steps or [])],
        uncertainty_notes=list(row.uncertainty_notes or []),
        approved=bool(row.approved),
        created_at=from_iso(row.created_at),
    )


def _run_from_row(row: _RunRow) -> ExecutionRun:
    return ExecutionRun(
        id=row.id,
        workflow_id=row.workflow_id,
        state=AgentState(row.state),
        last_verified_step=row.last_verified_step,
        started_at=from_iso(row.started_at) if row.started_at else None,
        ended_at=from_iso(row.ended_at) if row.ended_at else None,
    )


def _checkpoint_from_row(row: _CheckpointRow) -> VerifiedCheckpoint:
    return VerifiedCheckpoint(
        run_id=row.run_id,
        workflow_id=row.workflow_id,
        step_order=row.step_order,
        verified_at=from_iso(row.verified_at),
        verification=dict(row.verification or {}),
    )


class SQLiteWorkflowRepository:
    """File-backed repository. Safe to share across FastAPI worker threads."""

    def __init__(self, db_path: str | Path = "echo.db", *, echo: bool = False) -> None:
        self.db_path = str(db_path)
        self._engine = self._create_engine(self.db_path, echo=echo)
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)
        _Base.metadata.create_all(self._engine)

    @property
    def is_durable(self) -> bool:
        return True

    @staticmethod
    def _create_engine(db_path: str, *, echo: bool) -> Engine:
        if db_path == ":memory:":
            engine = create_engine(
                "sqlite://",
                echo=echo,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        else:
            path = Path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(
                f"sqlite:///{path}",
                echo=echo,
                connect_args={"check_same_thread": False, "timeout": 30},
            )

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        return engine

    def close(self) -> None:
        self._engine.dispose()

    # --- workflow draft and approval lifecycle -------------------------
    def save_draft(self, workflow: Workflow) -> Workflow:
        if workflow.approved:
            raise UnapprovedWorkflowError(
                workflow.id, "save_draft only accepts unapproved drafts; use save_approved()."
            )
        draft = workflow.model_copy(deep=True, update={"approved": False})
        with self._session_factory() as session:
            session.merge(self._to_row(draft))
            session.commit()
        return draft

    def approve(self, workflow_id: str) -> Workflow:
        with self._session_factory() as session:
            row = session.get(_WorkflowRow, workflow_id)
            if row is None:
                raise WorkflowNotFoundError(workflow_id)
            workflow = _workflow_from_row(row)
            if not workflow.approved:
                approved = workflow.model_copy(update={"approved": True})
                validate_runnable(approved)
                row.approved = True
                session.commit()
                workflow = approved
            return workflow

    def reject(self, workflow_id: str) -> Workflow:
        with self._session_factory() as session:
            row = session.get(_WorkflowRow, workflow_id)
            if row is None:
                raise WorkflowNotFoundError(workflow_id)
            row.approved = False
            session.commit()
            return _workflow_from_row(row)

    def save_approved(self, workflow: Workflow) -> Workflow:
        validate_runnable(workflow)
        approved = workflow.model_copy(deep=True, update={"approved": True})
        with self._session_factory() as session:
            session.merge(self._to_row(approved))
            session.commit()
        return approved

    # --- retrieval ------------------------------------------------------
    def get(self, workflow_id: str) -> Workflow | None:
        with self._session_factory() as session:
            row = session.get(_WorkflowRow, workflow_id)
            if row is None or not row.approved:
                return None
            return _workflow_from_row(row)

    def get_draft(self, workflow_id: str) -> Workflow | None:
        with self._session_factory() as session:
            row = session.get(_WorkflowRow, workflow_id)
            return None if row is None else _workflow_from_row(row)

    def list_approved(self) -> list[Workflow]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(_WorkflowRow)
                .where(_WorkflowRow.approved.is_(True))
                .order_by(_WorkflowRow.created_at)
            ).all()
            return [_workflow_from_row(row) for row in rows]

    def list_all(self) -> list[Workflow]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(_WorkflowRow).order_by(_WorkflowRow.created_at)
            ).all()
            return [_workflow_from_row(row) for row in rows]

    # --- execution runs and verified checkpoints ------------------------
    def create_run(self, workflow_id: str, *, run_id: str | None = None) -> ExecutionRun:
        with self._session_factory() as session:
            workflow_row = session.get(_WorkflowRow, workflow_id)
            if workflow_row is None:
                raise WorkflowNotFoundError(workflow_id)
            if not workflow_row.approved:
                raise UnapprovedWorkflowError(
                    workflow_id, "Only approved workflows can be executed."
                )
            new_id = run_id or uuid.uuid4().hex
            if session.get(_RunRow, new_id) is not None:
                raise MemoryValidationError(f"Execution run {new_id!r} already exists.")
            run = ExecutionRun(
                id=new_id,
                workflow_id=workflow_id,
                state=AgentState.EXECUTING,
                last_verified_step=0,
                started_at=utcnow(),
            )
            session.add(
                _RunRow(
                    id=run.id,
                    workflow_id=run.workflow_id,
                    state=run.state.value,
                    last_verified_step=0,
                    started_at=to_iso(run.started_at),
                    ended_at=None,
                )
            )
            session.commit()
            return run

    def get_run(self, run_id: str) -> ExecutionRun | None:
        with self._session_factory() as session:
            row = session.get(_RunRow, run_id)
            return None if row is None else _run_from_row(row)

    def list_runs(self, workflow_id: str | None = None) -> list[ExecutionRun]:
        query = select(_RunRow).order_by(_RunRow.started_at.desc())
        if workflow_id is not None:
            query = query.where(_RunRow.workflow_id == workflow_id)
        with self._session_factory() as session:
            rows = session.scalars(query).all()
            return [_run_from_row(row) for row in rows]

    def set_run_state(
        self, run_id: str, state: AgentState, *, ended_at: Any | None = None
    ) -> ExecutionRun:
        with self._session_factory() as session:
            row = session.get(_RunRow, run_id)
            if row is None:
                raise RunNotFoundError(run_id)
            row.state = state.value
            if ended_at is not None:
                row.ended_at = to_iso(ended_at)
            elif state in TERMINAL_RUN_STATES and row.ended_at is None:
                row.ended_at = to_iso(utcnow())
            session.commit()
            return _run_from_row(row)

    def record_verified_progress(
        self,
        run_id: str,
        *,
        step_order: int,
        verified: bool,
        verification: dict[str, Any] | None = None,
    ) -> ExecutionProgress:
        evidence = require_verification(
            step_order=step_order, verified=verified, verification=verification
        )
        with self._session_factory() as session:
            run_row = session.get(_RunRow, run_id)
            if run_row is None:
                raise RunNotFoundError(run_id)
            workflow_row = session.get(_WorkflowRow, run_row.workflow_id)
            if workflow_row is None:
                raise WorkflowNotFoundError(run_row.workflow_id)
            workflow = _workflow_from_row(workflow_row)
            if all(step.order != step_order for step in workflow.steps):
                raise MemoryValidationError(
                    f"Workflow {workflow.id!r} has no step with order {step_order}."
                )

            existing = session.scalars(
                select(_CheckpointRow).where(
                    _CheckpointRow.run_id == run_id,
                    _CheckpointRow.step_order == step_order,
                )
            ).first()
            if existing is None:
                session.add(
                    _CheckpointRow(
                        run_id=run_id,
                        workflow_id=workflow.id,
                        step_order=step_order,
                        verified_at=to_iso(utcnow()),
                        verification=evidence,
                    )
                )
            run_row.last_verified_step = max(run_row.last_verified_step, step_order)
            session.commit()
            return self._progress(session, run_id)

    def get_progress(self, run_id: str) -> ExecutionProgress:
        with self._session_factory() as session:
            return self._progress(session, run_id)

    def get_latest_progress(self, workflow_id: str) -> ExecutionProgress | None:
        with self._session_factory() as session:
            run_row = session.scalars(
                select(_RunRow)
                .where(_RunRow.workflow_id == workflow_id)
                .order_by(_RunRow.started_at.desc())
            ).first()
            if run_row is None:
                return None
            return self._progress(session, run_row.id)

    # --- internals ------------------------------------------------------
    @staticmethod
    def _to_row(workflow: Workflow) -> _WorkflowRow:
        return _WorkflowRow(
            id=workflow.id,
            name=workflow.name,
            description=workflow.description,
            approved=workflow.approved,
            created_at=to_iso(workflow.created_at),
            trigger_phrases=list(workflow.trigger_phrases),
            steps=[step.model_dump(mode="json") for step in workflow.steps],
            uncertainty_notes=list(workflow.uncertainty_notes),
        )

    @staticmethod
    def _progress(session: Any, run_id: str) -> ExecutionProgress:
        run_row = session.get(_RunRow, run_id)
        if run_row is None:
            raise RunNotFoundError(run_id)
        checkpoints = session.scalars(
            select(_CheckpointRow)
            .where(_CheckpointRow.run_id == run_id)
            .order_by(_CheckpointRow.step_order)
        ).all()
        return ExecutionProgress(
            run=_run_from_row(run_row),
            verified_checkpoints=[_checkpoint_from_row(row) for row in checkpoints],
        )

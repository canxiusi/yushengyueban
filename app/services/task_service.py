"""
Task lifecycle service: create, execute, and manage agent tasks.

Responsible for:
- Task persistence (DB writes)
- Sandbox lifecycle (create → execute → destroy)
- Concurrency control via semaphore
- Event emission for WebSocket subscribers
"""

import asyncio
import logging
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy import select, update
from ulid import ULID

from app.agents.cloud_agent import run_agent
from app.config import settings
from app.core.database import TaskRecord, async_session
from app.schemas.task import TaskEvent, TaskResponse, TaskStatus

if settings.sandbox_mode == "docker":
    from app.sandbox.docker_sandbox import sandbox as sandbox
else:
    from app.sandbox.local_sandbox import local_sandbox as sandbox

logger = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)
_event_subscribers: dict[str, list[Callable]] = {}


def subscribe(task_id: str, callback: Callable):
    _event_subscribers.setdefault(task_id, []).append(callback)


def unsubscribe(task_id: str, callback: Callable):
    if task_id in _event_subscribers:
        _event_subscribers[task_id] = [c for c in _event_subscribers[task_id] if c != callback]


async def _emit_event(task_id: str, event: TaskEvent):
    for callback in _event_subscribers.get(task_id, []):
        try:
            await callback(event)
        except Exception:
            pass


async def create_task(prompt: str, context: Optional[dict] = None, timeout: Optional[int] = None) -> str:
    task_id = str(ULID())
    record = TaskRecord(
        task_id=task_id,
        status=TaskStatus.PENDING,
        prompt=prompt,
        context=context,
        timeout=timeout or settings.task_timeout,
    )
    async with async_session() as session:
        session.add(record)
        await session.commit()
    return task_id


async def get_task(task_id: str) -> Optional[TaskResponse]:
    async with async_session() as session:
        result = await session.execute(select(TaskRecord).where(TaskRecord.task_id == task_id))
        record = result.scalar_one_or_none()
        if not record:
            return None
        return TaskResponse(
            task_id=record.task_id,
            status=record.status,
            prompt=record.prompt,
            created_at=record.created_at,
            started_at=record.started_at,
            completed_at=record.completed_at,
            result=record.result,
            error=record.error,
            steps=record.steps or [],
        )


async def list_tasks(limit: int = 20, offset: int = 0) -> list[TaskResponse]:
    async with async_session() as session:
        result = await session.execute(
            select(TaskRecord).order_by(TaskRecord.created_at.desc()).limit(limit).offset(offset)
        )
        records = result.scalars().all()
        return [
            TaskResponse(
                task_id=r.task_id,
                status=r.status,
                prompt=r.prompt,
                created_at=r.created_at,
                started_at=r.started_at,
                completed_at=r.completed_at,
                result=r.result,
                error=r.error,
                steps=r.steps or [],
            )
            for r in records
        ]


async def execute_task(task_id: str):
    """Main task execution pipeline: sandbox → agent → cleanup."""
    async with _semaphore:
        sandbox_id = None
        try:
            async with async_session() as session:
                await session.execute(
                    update(TaskRecord)
                    .where(TaskRecord.task_id == task_id)
                    .values(status=TaskStatus.RUNNING, started_at=datetime.utcnow())
                )
                await session.commit()

            await _emit_event(task_id, TaskEvent(
                task_id=task_id, event_type="step",
                data={"message": "Creating sandbox environment..."}
            ))

            sandbox_id = await sandbox.create_sandbox(task_id)

            async with async_session() as session:
                await session.execute(
                    update(TaskRecord).where(TaskRecord.task_id == task_id).values(sandbox_id=sandbox_id)
                )
                await session.commit()

            await _emit_event(task_id, TaskEvent(
                task_id=task_id, event_type="step",
                data={"message": "Sandbox ready. Starting agent execution..."}
            ))

            task_record = None
            async with async_session() as session:
                result = await session.execute(select(TaskRecord).where(TaskRecord.task_id == task_id))
                task_record = result.scalar_one()

            agent_result = await asyncio.wait_for(
                run_agent(
                    task_id=task_id,
                    prompt=task_record.prompt,
                    sandbox_id=sandbox_id,
                ),
                timeout=task_record.timeout,
            )

            status = TaskStatus.COMPLETED if agent_result["status"] == "completed" else TaskStatus.FAILED
            async with async_session() as session:
                await session.execute(
                    update(TaskRecord)
                    .where(TaskRecord.task_id == task_id)
                    .values(
                        status=status,
                        result=agent_result.get("result"),
                        error=agent_result.get("error"),
                        steps=agent_result.get("steps", []),
                        completed_at=datetime.utcnow(),
                    )
                )
                await session.commit()

            await _emit_event(task_id, TaskEvent(
                task_id=task_id, event_type="done",
                data={"status": status.value, "result": agent_result.get("result")}
            ))

        except asyncio.TimeoutError:
            async with async_session() as session:
                await session.execute(
                    update(TaskRecord)
                    .where(TaskRecord.task_id == task_id)
                    .values(
                        status=TaskStatus.FAILED,
                        error="Task execution timed out",
                        completed_at=datetime.utcnow(),
                    )
                )
                await session.commit()
            await _emit_event(task_id, TaskEvent(
                task_id=task_id, event_type="error",
                data={"error": "Task execution timed out"}
            ))

        except Exception as e:
            logger.exception(f"Task {task_id} failed with unexpected error")
            async with async_session() as session:
                await session.execute(
                    update(TaskRecord)
                    .where(TaskRecord.task_id == task_id)
                    .values(
                        status=TaskStatus.FAILED,
                        error=str(e),
                        completed_at=datetime.utcnow(),
                    )
                )
                await session.commit()
            await _emit_event(task_id, TaskEvent(
                task_id=task_id, event_type="error",
                data={"error": str(e)}
            ))

        finally:
            if sandbox_id:
                await sandbox.destroy_sandbox(sandbox_id)


async def cancel_task(task_id: str) -> bool:
    async with async_session() as session:
        result = await session.execute(select(TaskRecord).where(TaskRecord.task_id == task_id))
        record = result.scalar_one_or_none()
        if not record:
            return False
        if record.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return False
        if record.sandbox_id:
            await sandbox.destroy_sandbox(record.sandbox_id)
        await session.execute(
            update(TaskRecord)
            .where(TaskRecord.task_id == task_id)
            .values(status=TaskStatus.CANCELLED, completed_at=datetime.utcnow())
        )
        await session.commit()
    return True

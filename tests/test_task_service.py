from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.task import TaskStatus
from app.services.task_service import (cancel_task, create_task, execute_task,
                                       get_task)


@pytest.fixture(autouse=True)
async def setup_db():
    from app.core.database import init_db
    await init_db()


class TestCreateTask:
    @pytest.mark.asyncio
    async def test_create_returns_ulid(self):
        task_id = await create_task(prompt="test task")
        assert len(task_id) == 26
        assert task_id.isalnum()

    @pytest.mark.asyncio
    async def test_create_with_context(self):
        task_id = await create_task(prompt="test", context={"key": "val"})
        task = await get_task(task_id)
        assert task.status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_create_with_timeout(self):
        task_id = await create_task(prompt="test", timeout=120)
        assert task_id is not None


class TestGetTask:
    @pytest.mark.asyncio
    async def test_get_existing_task(self):
        task_id = await create_task(prompt="find me")
        task = await get_task(task_id)
        assert task is not None
        assert task.prompt == "find me"
        assert task.status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_nonexistent_task(self):
        task = await get_task("NONEXISTENT_ID_12345678")
        assert task is None


class TestCancelTask:
    @pytest.mark.asyncio
    async def test_cancel_pending_task(self):
        task_id = await create_task(prompt="cancel me")
        success = await cancel_task(task_id)
        assert success is True
        task = await get_task(task_id)
        assert task.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self):
        success = await cancel_task("NOPE_000000000000000000")
        assert success is False


class TestExecuteTask:
    @pytest.mark.asyncio
    @patch("app.services.task_service.sandbox")
    @patch("app.services.task_service.run_agent")
    async def test_execute_success(self, mock_run_agent, mock_sandbox):
        mock_sandbox.create_sandbox = AsyncMock(return_value="sandbox-123")
        mock_sandbox.destroy_sandbox = AsyncMock()
        mock_run_agent.return_value = {
            "status": "completed",
            "result": "Done!",
            "steps": [{"step_id": 1, "action": "tool_call"}],
        }

        task_id = await create_task(prompt="do something")
        await execute_task(task_id)

        task = await get_task(task_id)
        assert task.status == TaskStatus.COMPLETED
        assert task.result == "Done!"
        assert len(task.steps) == 1
        mock_sandbox.destroy_sandbox.assert_called_once_with("sandbox-123")

    @pytest.mark.asyncio
    @patch("app.services.task_service.sandbox")
    @patch("app.services.task_service.run_agent")
    async def test_execute_agent_failure(self, mock_run_agent, mock_sandbox):
        mock_sandbox.create_sandbox = AsyncMock(return_value="sandbox-456")
        mock_sandbox.destroy_sandbox = AsyncMock()
        mock_run_agent.return_value = {
            "status": "failed",
            "error": "LLM refused",
            "steps": [],
        }

        task_id = await create_task(prompt="fail task")
        await execute_task(task_id)

        task = await get_task(task_id)
        assert task.status == TaskStatus.FAILED
        assert "LLM refused" in task.error

    @pytest.mark.asyncio
    @patch("app.services.task_service.sandbox")
    async def test_execute_sandbox_creation_fails(self, mock_sandbox):
        mock_sandbox.create_sandbox = AsyncMock(side_effect=Exception("Docker not running"))
        mock_sandbox.destroy_sandbox = AsyncMock()

        task_id = await create_task(prompt="no docker")
        await execute_task(task_id)

        task = await get_task(task_id)
        assert task.status == TaskStatus.FAILED
        assert "Docker not running" in task.error

    @pytest.mark.asyncio
    @patch("app.services.task_service.sandbox")
    @patch("app.services.task_service.run_agent")
    async def test_execute_timeout(self, mock_run_agent, mock_sandbox):
        import asyncio
        mock_sandbox.create_sandbox = AsyncMock(return_value="sandbox-789")
        mock_sandbox.destroy_sandbox = AsyncMock()

        async def slow_agent(*args, **kwargs):
            await asyncio.sleep(100)

        mock_run_agent.side_effect = slow_agent

        task_id = await create_task(prompt="slow task", timeout=30)

        # Override timeout in DB to 1 second for test
        from sqlalchemy import update

        from app.core.database import TaskRecord, async_session
        async with async_session() as session:
            await session.execute(
                update(TaskRecord).where(TaskRecord.task_id == task_id).values(timeout=1)
            )
            await session.commit()

        await execute_task(task_id)

        task = await get_task(task_id)
        assert task.status == TaskStatus.FAILED
        assert "timed out" in task.error.lower()

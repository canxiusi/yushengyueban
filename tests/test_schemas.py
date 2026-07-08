from datetime import datetime

import pytest

from app.schemas.task import TaskCreate, TaskEvent, TaskResponse, TaskStatus


class TestTaskCreate:
    def test_valid_prompt(self):
        task = TaskCreate(prompt="写一个 hello world")
        assert task.prompt == "写一个 hello world"
        assert task.context is None
        assert task.timeout is None

    def test_with_context(self):
        task = TaskCreate(prompt="分析代码", context={"repo": "test"})
        assert task.context == {"repo": "test"}

    def test_with_timeout(self):
        task = TaskCreate(prompt="长任务", timeout=120)
        assert task.timeout == 120

    def test_empty_prompt_rejected(self):
        with pytest.raises(Exception):
            TaskCreate(prompt="")

    def test_timeout_too_small(self):
        with pytest.raises(Exception):
            TaskCreate(prompt="test", timeout=10)

    def test_timeout_too_large(self):
        with pytest.raises(Exception):
            TaskCreate(prompt="test", timeout=7200)


class TestTaskResponse:
    def test_minimal_response(self):
        resp = TaskResponse(
            task_id="01ABC",
            status=TaskStatus.PENDING,
            prompt="test",
            created_at=datetime(2026, 7, 8),
        )
        assert resp.task_id == "01ABC"
        assert resp.status == TaskStatus.PENDING
        assert resp.result is None
        assert resp.steps == []

    def test_completed_response(self):
        resp = TaskResponse(
            task_id="01ABC",
            status=TaskStatus.COMPLETED,
            prompt="test",
            created_at=datetime(2026, 7, 8),
            started_at=datetime(2026, 7, 8),
            completed_at=datetime(2026, 7, 8),
            result="Done",
            steps=[{"step_id": 1, "action": "tool_call"}],
        )
        assert resp.result == "Done"
        assert len(resp.steps) == 1


class TestTaskEvent:
    def test_step_event(self):
        event = TaskEvent(
            task_id="01ABC",
            event_type="step",
            data={"message": "Creating sandbox..."},
        )
        assert event.event_type == "step"
        assert event.timestamp is not None

    def test_done_event(self):
        event = TaskEvent(
            task_id="01ABC",
            event_type="done",
            data={"status": "completed", "result": "OK"},
        )
        assert event.event_type == "done"


class TestTaskStatus:
    def test_all_statuses(self):
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.CANCELLED == "cancelled"

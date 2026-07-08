from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskCreate(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=10000)
    context: Optional[dict] = None
    timeout: Optional[int] = Field(None, ge=30, le=3600)


class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    prompt: str
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[str] = None
    error: Optional[str] = None
    steps: list[dict] = []


class TaskEvent(BaseModel):
    task_id: str
    event_type: Literal["step", "tool_call", "tool_result", "thinking", "error", "done"]
    data: dict
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AgentStep(BaseModel):
    step_id: int
    action: str
    tool_name: Optional[str] = None
    tool_input: Optional[dict] = None
    tool_output: Optional[str] = None
    thought: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

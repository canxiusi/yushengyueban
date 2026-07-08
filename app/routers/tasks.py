"""REST API routes for task management."""

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.schemas.task import TaskCreate, TaskResponse
from app.services import task_service

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("", response_model=dict, status_code=201)
async def create_task(body: TaskCreate, background_tasks: BackgroundTasks):
    """Submit a new agent task. Returns task_id immediately, execution happens async."""
    task_id = await task_service.create_task(
        prompt=body.prompt,
        context=body.context,
        timeout=body.timeout,
    )
    background_tasks.add_task(task_service.execute_task, task_id)
    return {"task_id": task_id, "status": "pending"}


@router.get("", response_model=list[TaskResponse])
async def list_tasks(limit: int = 20, offset: int = 0):
    """List all tasks with pagination."""
    return await task_service.list_tasks(limit=limit, offset=offset)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    """Get task details by ID."""
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/{task_id}/cancel", response_model=dict)
async def cancel_task(task_id: str):
    """Cancel a running task and destroy its sandbox."""
    success = await task_service.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Task cannot be cancelled")
    return {"task_id": task_id, "status": "cancelled"}

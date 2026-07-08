"""WebSocket endpoint for real-time task execution streaming."""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.schemas.task import TaskEvent
from app.services.task_service import subscribe, unsubscribe

router = APIRouter()


@router.websocket("/ws/tasks/{task_id}")
async def task_stream(websocket: WebSocket, task_id: str):
    """
    Stream real-time events for a task execution.

    Events sent:
    - step: Agent progress updates
    - tool_call: Tool invocation details
    - tool_result: Tool execution results
    - thinking: Agent reasoning
    - error: Error occurred
    - done: Task completed
    """
    await websocket.accept()

    queue: asyncio.Queue[TaskEvent] = asyncio.Queue()

    async def on_event(event: TaskEvent):
        await queue.put(event)

    subscribe(task_id, on_event)

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=60)
                await websocket.send_json({
                    "event_type": event.event_type,
                    "data": event.data,
                    "timestamp": event.timestamp.isoformat(),
                })
                if event.event_type == "done":
                    break
            except asyncio.TimeoutError:
                await websocket.send_json({"event_type": "ping", "data": {}})
    except WebSocketDisconnect:
        pass
    finally:
        unsubscribe(task_id, on_event)

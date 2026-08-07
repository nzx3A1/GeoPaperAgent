from collections.abc import AsyncIterator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.schemas.task import TaskProgressEvent, TaskRead, TaskStatus

router = APIRouter()


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(task_id: str) -> TaskRead:
    return TaskRead(
        id=task_id,
        type="unassigned",
        status=TaskStatus.PENDING,
        progress=0,
        message="Task persistence is not configured yet",
    )


@router.get("/{task_id}/events", response_class=EventSourceResponse)
async def task_events(task_id: str) -> EventSourceResponse:
    async def events() -> AsyncIterator[dict[str, str]]:
        event = TaskProgressEvent(
            task_id=task_id,
            status=TaskStatus.PENDING,
            progress=0,
            message="Task event stream is ready",
        )
        yield {"event": "progress", "data": event.model_dump_json(by_alias=True)}

    return EventSourceResponse(events())

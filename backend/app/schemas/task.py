from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskRead(BaseModel):
    id: str
    type: str
    status: TaskStatus
    progress: int
    message: str | None = None


class TaskProgressEvent(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    task_id: str
    status: TaskStatus
    progress: int
    message: str | None = None

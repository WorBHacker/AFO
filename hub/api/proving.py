"""/v1/proving - the proving ground and the models' exam (stage 12, D45): the tasks, a try of one with a model, exams
of chosen tasks on chosen models with their table.

Reading is open to every client; trying, starting, stopping and deleting are the app's (a run lets a model write and
run commands in its copy without asking).
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import proving
from ..auth import APP_CLIENT, Client
from ..proving import ProvingError
from .deps import require_client

router = APIRouter(prefix="/v1")


class Try(BaseModel):
    task_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)


class Exam(BaseModel):
    model_ids: list[str] = Field(min_length=1)
    task_ids: list[str] = Field(default_factory=list)  # none named: every task
    judge: bool = False  # the model of «Проверка цели» checks the goals too


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may run the proving ground")


async def _call(coro) -> Any:
    try:
        return await coro
    except ProvingError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.get("/proving/tasks")
async def list_tasks(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """The proving ground's tasks, the easy ones first."""
    return {"tasks": await asyncio.to_thread(proving.tasks)}


@router.post("/proving/try", status_code=201)
async def try_task(body: Try, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """One task for one model on a fresh copy: its chat (with the goal) is ready to watch."""
    _app_only(client)
    return {"run": await _call(request.app.state.proving.try_task(body.task_id, body.model_id, by=client.name))}


@router.get("/proving/runs")
async def list_runs(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """The tries («Попробовать»), the newest first."""
    return {"runs": await request.app.state.db.acall(lambda conn: proving.runs_of(conn, None))}


@router.delete("/proving/runs/{run_id}", status_code=204)
async def delete_run(run_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> None:
    """A try gone, with its copy, chat and project."""
    _app_only(client)
    await _call(request.app.state.proving.delete_run(run_id))


@router.get("/proving/exams")
async def list_exams(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """The exams, the newest first, each with its runs and its table per model."""
    return {"exams": await request.app.state.db.acall(proving.list_exams)}


@router.post("/proving/exams", status_code=201)
async def start_exam(body: Exam, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """Chosen tasks on chosen models, one run after another, each on its own copy."""
    _app_only(client)
    ground = request.app.state.proving
    return {"exam": await _call(ground.start_exam(body.model_ids, body.task_ids, judge=body.judge, by=client.name))}


@router.get("/proving/exams/{exam_id}")
async def get_exam(exam_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """An exam with its runs and its table per model."""
    return {"exam": await _call(request.app.state.db.acall(lambda conn: proving.get_exam(conn, exam_id)))}


@router.post("/proving/exams/{exam_id}/stop", status_code=202)
async def stop_exam(exam_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """The run going now ends; those waiting are skipped."""
    _app_only(client)
    return {"exam": await _call(request.app.state.proving.stop_exam(exam_id))}


@router.delete("/proving/exams/{exam_id}", status_code=204)
async def delete_exam(exam_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> None:
    """An exam gone, with its runs' copies, chats and projects."""
    _app_only(client)
    await _call(request.app.state.proving.delete_exam(exam_id))

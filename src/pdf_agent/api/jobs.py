"""Jobs API - create, query, cancel jobs and stream events."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from pdf_agent.db import get_session
from pdf_agent.schemas.job import JobCreateRequest, JobListResponse, JobResponse, JobStepResponse
from pdf_agent.services import FileService
from pdf_agent.services.job_service import JobService
from pdf_agent.worker.tasks import execute_job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _job_to_response(job) -> JobResponse:
    return JobResponse(
        id=job.id,
        status=job.status.value,
        mode=job.mode.value,
        instruction=job.instruction,
        plan_json=job.plan_json,
        progress=job.progress,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
        result_path=job.result_path,
        result_type=job.result_type,
        steps=[
            JobStepResponse(
                id=s.id,
                idx=s.idx,
                tool_name=s.tool_name,
                params_json=s.params_json,
                status=s.status.value,
                started_at=s.started_at,
                ended_at=s.ended_at,
                log_text=s.log_text,
            )
            for s in job.steps
        ],
    )


@router.post("", response_model=JobResponse)
async def create_job(
    req: JobCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    """Create a new job from a plan."""
    file_svc = FileService(session)
    job_svc = JobService(session)

    # Resolve input file paths
    input_paths: list[str] = []
    for fid in req.file_ids:
        path = await file_svc.get_path(fid)
        input_paths.append(str(path))

    # Create job record
    job = await job_svc.create(
        plan=req.plan,
        file_ids=req.file_ids,
        mode=req.mode,
        instruction=req.instruction,
    )

    # Dispatch to Celery worker
    execute_job.delay(str(job.id), input_paths)

    return _job_to_response(job)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> JobListResponse:
    """List all jobs."""
    job_svc = JobService(session)
    jobs, total = await job_svc.list_jobs(limit=limit, offset=offset)
    return JobListResponse(items=[_job_to_response(j) for j in jobs], total=total)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    """Get job status and details."""
    job_svc = JobService(session)
    job = await job_svc.get(job_id)
    return _job_to_response(job)


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    """Cancel a running or pending job."""
    job_svc = JobService(session)
    job = await job_svc.cancel(job_id)
    return _job_to_response(job)


@router.get("/{job_id}/result")
async def download_result(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Download the result file of a completed job."""
    job_svc = JobService(session)
    job = await job_svc.get(job_id)
    if not job.result_path:
        raise HTTPException(status_code=404, detail="No result available")

    result_path = Path(job.result_path)
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="Result file not found on disk")

    return FileResponse(result_path, filename=result_path.name)


@router.get("/{job_id}/events")
async def job_events(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """SSE endpoint for real-time job progress updates."""
    import asyncio
    from sse_starlette.sse import EventSourceResponse

    job_svc = JobService(session)

    async def event_generator():
        while True:
            job = await job_svc.get(job_id)
            yield {
                "event": "progress",
                "data": f'{{"progress": {job.progress}, "status": "{job.status.value}"}}',
            }
            if job.status.value in ("SUCCESS", "FAILED", "CANCELED"):
                yield {
                    "event": "done",
                    "data": f'{{"status": "{job.status.value}"}}',
                }
                break
            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())

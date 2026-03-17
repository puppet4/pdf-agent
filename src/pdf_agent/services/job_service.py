"""Job service - create and manage jobs."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pdf_agent.core import ErrorCode, PDFAgentError
from pdf_agent.db.models import Job, JobMode, JobStatus, JobStep, StepStatus
from pdf_agent.schemas.plan import PlanSchema


class JobService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        plan: PlanSchema,
        file_ids: list[uuid.UUID],
        mode: str = "FORM",
        instruction: str | None = None,
    ) -> Job:
        job_id = uuid.uuid4()

        job = Job(
            id=job_id,
            status=JobStatus.PENDING,
            mode=JobMode(mode),
            instruction=instruction,
            plan_json=plan.model_dump(),
            progress=0,
        )
        self.session.add(job)

        # Create steps from plan
        for idx, step in enumerate(plan.steps):
            job_step = JobStep(
                job_id=job_id,
                idx=idx,
                tool_name=step.tool,
                params_json=step.params,
                status=StepStatus.PENDING,
            )
            self.session.add(job_step)

        await self.session.commit()
        return await self.get(job_id)

    async def get(self, job_id: uuid.UUID) -> Job:
        result = await self.session.execute(
            select(Job).where(Job.id == job_id).options(selectinload(Job.steps))
        )
        job = result.scalar_one_or_none()
        if not job:
            raise PDFAgentError(ErrorCode.JOB_NOT_FOUND, f"Job {job_id} not found")
        return job

    async def list_jobs(self, limit: int = 50, offset: int = 0) -> tuple[list[Job], int]:
        count_result = await self.session.execute(select(Job))
        total = len(count_result.scalars().all())

        result = await self.session.execute(
            select(Job)
            .options(selectinload(Job.steps))
            .order_by(Job.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all()), total

    async def update_status(self, job_id: uuid.UUID, status: JobStatus, **kwargs) -> Job:
        job = await self.get(job_id)
        job.status = status
        for k, v in kwargs.items():
            if hasattr(job, k):
                setattr(job, k, v)
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def cancel(self, job_id: uuid.UUID) -> Job:
        job = await self.get(job_id)
        if job.status in (JobStatus.SUCCESS, JobStatus.FAILED):
            raise PDFAgentError(ErrorCode.INVALID_PARAMS, "Cannot cancel a finished job")
        return await self.update_status(job_id, JobStatus.CANCELED)

    async def update_step(
        self,
        step_id: uuid.UUID,
        status: StepStatus,
        log_text: str | None = None,
        output_path: str | None = None,
    ) -> None:
        result = await self.session.execute(select(JobStep).where(JobStep.id == step_id))
        step = result.scalar_one_or_none()
        if not step:
            return
        step.status = status
        if status == StepStatus.RUNNING:
            step.started_at = datetime.now(timezone.utc)
        if status in (StepStatus.SUCCESS, StepStatus.FAILED):
            step.ended_at = datetime.now(timezone.utc)
        if log_text is not None:
            step.log_text = log_text
        if output_path is not None:
            step.output_path = output_path
        await self.session.commit()

"""Worker orchestrator - executes job plans step by step."""
from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from pdf_agent.config import settings
from pdf_agent.core import ErrorCode
from pdf_agent.db.models import Job, JobStatus, JobStep, StepStatus
from pdf_agent.storage import storage
from pdf_agent.tools.registry import registry

logger = logging.getLogger(__name__)


class Orchestrator:
    """Executes a job's plan step by step (runs in Celery worker, sync context)."""

    def __init__(self, db_session: Session) -> None:
        self.db = db_session

    def execute_job(self, job_id: uuid.UUID, input_file_paths: list[str]) -> None:
        job = self.db.get(Job, job_id)
        if not job:
            logger.error("Job %s not found", job_id)
            return
        if job.status == JobStatus.CANCELED:
            logger.info("Job %s already canceled, skipping", job_id)
            return

        # Mark running
        job.status = JobStatus.RUNNING
        self.db.commit()

        workdir = storage.create_job_workdir(job_id)
        output_dir = storage.create_job_output_dir(job_id)

        steps = (
            self.db.execute(
                select(JobStep).where(JobStep.job_id == job_id).order_by(JobStep.idx)
            )
            .scalars()
            .all()
        )

        current_inputs = [Path(p) for p in input_file_paths]
        total_steps = len(steps)

        try:
            for i, step in enumerate(steps):
                # Check cancellation
                self.db.refresh(job)
                if job.status == JobStatus.CANCELED:
                    logger.info("Job %s canceled during execution", job_id)
                    return

                tool = registry.get(step.tool_name)
                if not tool:
                    self._fail_step(step, ErrorCode.ENGINE_NOT_INSTALLED, f"Tool '{step.tool_name}' not found")
                    self._fail_job(job, ErrorCode.ENGINE_NOT_INSTALLED, f"Tool '{step.tool_name}' not found")
                    return

                # Mark step running
                step.status = StepStatus.RUNNING
                self.db.commit()

                step_workdir = workdir / f"step_{i}"
                step_workdir.mkdir(parents=True, exist_ok=True)

                try:
                    result = tool.run(
                        inputs=current_inputs,
                        params=step.params_json,
                        workdir=step_workdir,
                    )
                except Exception as e:
                    self._fail_step(step, ErrorCode.ENGINE_EXEC_FAILED, str(e))
                    self._fail_job(job, ErrorCode.ENGINE_EXEC_FAILED, str(e))
                    return

                # Mark step success
                step.status = StepStatus.SUCCESS
                step.log_text = result.log
                if result.output_files:
                    step.output_path = str(result.output_files[0])
                self.db.commit()

                # Pipe output to next step's input
                current_inputs = result.output_files

                # Update progress
                job.progress = int((i + 1) / total_steps * 100)
                self.db.commit()

            # Copy final outputs to output dir
            final_outputs = []
            for f in current_inputs:
                dest = output_dir / f.name
                shutil.copy2(f, dest)
                final_outputs.append(dest)

            # Mark job success
            job.status = JobStatus.SUCCESS
            job.progress = 100
            if final_outputs:
                job.result_path = str(final_outputs[0])
                job.result_type = "pdf" if final_outputs[0].suffix == ".pdf" else "zip"
            self.db.commit()
            logger.info("Job %s completed successfully", job_id)

        except Exception as e:
            logger.exception("Unexpected error in job %s", job_id)
            self._fail_job(job, ErrorCode.ENGINE_EXEC_FAILED, str(e))

    def _fail_step(self, step: JobStep, code: str, message: str) -> None:
        step.status = StepStatus.FAILED
        step.log_text = f"[{code}] {message}"
        self.db.commit()

    def _fail_job(self, job: Job, code: str, message: str) -> None:
        job.status = JobStatus.FAILED
        job.error_code = code
        job.error_message = message
        self.db.commit()

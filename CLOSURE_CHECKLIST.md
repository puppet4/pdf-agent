# Closure Checklist

## Current Status

- Runtime architecture is frozen on `FastAPI + LangChain/LangGraph + local storage`.
- Product surface is conversation-first and no longer exposes legacy execution management HTTP entrypoints.
- Reduced smoke-oriented suite is green: `16 passed`.

## Recommended Commit Split

### 1. Runtime / Conversation Backbone

Scope:
- Remove legacy queue/worker/runtime layers
- Keep the chat-first conversation flow stable
- Align health, cleanup, and storage behavior to the simplified single-process model

Primary files:
- `src/pdf_agent/external_commands.py`
- `src/pdf_agent/agent/tools_adapter.py`
- `src/pdf_agent/api/files.py`
- `src/pdf_agent/api/metrics.py`
- `src/pdf_agent/services/__init__.py`
- `src/pdf_agent/db/models.py`
- `src/pdf_agent/main.py`
- `src/pdf_agent/api/router.py`
- `src/pdf_agent/api/agent.py`
- `src/pdf_agent/config.py`
- `src/pdf_agent/storage/__init__.py`
- `src/pdf_agent/db/models.py`
- `src/pdf_agent/api/health.py`

Also include:
- deletion of legacy paths such as `src/pdf_agent/api/jobs.py`, `src/pdf_agent/api/tools.py`, `src/pdf_agent/api/workflows.py`, `src/pdf_agent/api/executions.py`, `src/pdf_agent/execution_queue.py`, `src/pdf_agent/worker.py`, `src/pdf_agent/static/app.js`, `src/pdf_agent/static/tools.html`, `src/pdf_agent/webhook.py`

### 2. Tool Runtime Alignment

Scope:
- Move built-in tools onto the shared command runner
- Close remaining execution cancellation gaps
- Keep tool behavior aligned to manifest-driven execution

Primary files:
- `src/pdf_agent/tools/_builtins/compress.py`
- `src/pdf_agent/tools/_builtins/pdf_to_word.py`
- `src/pdf_agent/tools/_builtins/pdf_to_office.py`
- `src/pdf_agent/tools/_builtins/pdf_to_html.py`
- `src/pdf_agent/tools/_builtins/pdf_to_pdfa.py`
- `src/pdf_agent/tools/_builtins/compare.py`
- `src/pdf_agent/tools/_builtins/deskew.py`
- `src/pdf_agent/tools/_builtins/auto_rotate.py`
- `src/pdf_agent/tools/_builtins/linearize.py`
- `src/pdf_agent/tools/_builtins/repair.py`
- `src/pdf_agent/tools/_builtins/flatten.py`
- `src/pdf_agent/tools/_builtins/pages_to_zip.py`
- `src/pdf_agent/tools/_builtins/pdf_to_images.py`
- `src/pdf_agent/tools/_builtins/remove_blank_pages.py`
- `src/pdf_agent/tools/_builtins/redact.py`
- `src/pdf_agent/tools/_builtins/validate.py`
- `src/pdf_agent/tools/_builtins/tile_pages.py`
- `src/pdf_agent/tools/_builtins/nup.py`

Optional to include in the same commit if desired:
- capability backfill files already in the worktree, such as `add_blank_pages.py`, `booklet.py`, `extract_attachments.py`, `extract_images.py`, `office_to_pdf.py`

### 3. Frontend / Docs / Smoke Acceptance

Scope:
- Collapse the product surface into conversation-first PDF editing
- Remove legacy manual tool/workflow/execution HTTP entrypoints
- Freeze the design doc on the final target architecture
- Keep only smoke-oriented tests plus key runtime acceptance coverage

Primary files:
- `src/pdf_agent/static/react-app.js`
- `src/pdf_agent/static/index.html`
- `PDF-Agent系统设计.md`
- `tests/test_smoke_core.py`
- `tests/test_smoke_tools.py`

Also include:
- deletion of the old detailed regression tests now intentionally removed from the suite

## Pre-Commit Checks

Run before any commit:

```bash
git status --short
python -m py_compile src/pdf_agent/api/router.py src/pdf_agent/api/agent.py src/pdf_agent/main.py tests/test_smoke_core.py
PYTHONPATH=src .venv/bin/python -m pytest tests -q
```

Expected current result:
- `PYTHONPATH=src .venv/bin/python -m pytest tests -q` -> `16 passed`

## Git Risks To Check Manually

- `git status --short` currently shows historical `AD` entries for `src/pdf_agent/api/jobs.py` and `tests/test_jobs_api.py`.
- Those files do not exist in the working tree anymore.
- Before committing, review the index carefully so these legacy paths land as clean deletions rather than confusing staged add/delete artifacts.

## Manual Acceptance Checklist

- Upload a PDF from the frontend
- Send one natural-language request from the main chat flow
- Send one follow-up instruction in the same conversation
- Start a long-running execution and cancel it
- Verify conversation output, result download, output download, and inline error display

## Freeze Rules

- Do not reintroduce `Job/Step/Artifact`
- Do not add a second planner or executor path
- Do not add new raw `subprocess.run(...)` calls to active runtime paths
- Do not expand the test suite back into large implementation-coupled regression coverage

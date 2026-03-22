# Commit Plan

## Current Index Findings

`git diff --cached --name-status` currently shows:

```text
A  src/pdf_agent/api/jobs.py
A  tests/test_jobs_api.py
A  tests/test_smoke_core.py
A  tests/test_smoke_tools.py
```

This is inconsistent with the current working tree because:

- `src/pdf_agent/api/jobs.py` does not exist anymore
- `tests/test_jobs_api.py` does not exist anymore
- `tests/test_smoke_core.py` and `tests/test_smoke_tools.py` have further unstaged modifications

So the index still contains historical staged state and should be normalized before any real commit split.

## Recommended Index Cleanup

Run these first if you want a clean commit boundary:

```bash
git restore --staged src/pdf_agent/api/jobs.py tests/test_jobs_api.py tests/test_smoke_core.py tests/test_smoke_tools.py
```

After that, re-check:

```bash
git status --short
git diff --cached --name-status
```

Expected outcome:
- no stale staged adds for deleted `jobs` files
- smoke tests return to plain working-tree modifications

## Recommended Commit Sequence

### Commit 1

Message:

```text
refactor: collapse legacy runtime infrastructure into chat-first conversation flow
```

Suggested paths:

```bash
git add \
  src/pdf_agent/external_commands.py \
  src/pdf_agent/api/router.py \
  src/pdf_agent/api/files.py \
  src/pdf_agent/api/metrics.py \
  src/pdf_agent/api/agent.py \
  src/pdf_agent/main.py \
  src/pdf_agent/config.py \
  src/pdf_agent/db/models.py \
  src/pdf_agent/storage/__init__.py \
  src/pdf_agent/services/__init__.py \
  src/pdf_agent/agent/tools_adapter.py \
  src/pdf_agent/agent/graph.py \
  src/pdf_agent/api/health.py \
  src/pdf_agent/api/middleware.py \
  src/pdf_agent/core/__init__.py \
  src/pdf_agent/static/index.html \
  src/pdf_agent/static/react-app.js \
  src/pdf_agent/api/jobs.py \
  src/pdf_agent/api/tools.py \
  src/pdf_agent/api/workflows.py \
  src/pdf_agent/api/executions.py \
  src/pdf_agent/execution_queue.py \
  src/pdf_agent/worker.py \
  src/pdf_agent/static/app.js \
  src/pdf_agent/static/tools.html \
  src/pdf_agent/webhook.py
```

Notes:
- include deletions for `jobs.py`, `tools.py`, `workflows.py`, `executions.py`, `execution_queue.py`, `worker.py`, `app.js`, `tools.html`, `webhook.py`
- this commit should establish the simplified conversation-first runtime

### Commit 2

Message:

```text
feat: align builtin tools with shared conversation-run command runner
```

Suggested paths:

```bash
git add \
  src/pdf_agent/tools/_builtins/__init__.py \
  src/pdf_agent/tools/_builtins/auto_rotate.py \
  src/pdf_agent/tools/_builtins/compare.py \
  src/pdf_agent/tools/_builtins/compress.py \
  src/pdf_agent/tools/_builtins/deskew.py \
  src/pdf_agent/tools/_builtins/flatten.py \
  src/pdf_agent/tools/_builtins/form_fill.py \
  src/pdf_agent/tools/_builtins/linearize.py \
  src/pdf_agent/tools/_builtins/merge.py \
  src/pdf_agent/tools/_builtins/metadata_info.py \
  src/pdf_agent/tools/_builtins/nup.py \
  src/pdf_agent/tools/_builtins/ocr.py \
  src/pdf_agent/tools/_builtins/pages_to_zip.py \
  src/pdf_agent/tools/_builtins/pdf_to_html.py \
  src/pdf_agent/tools/_builtins/pdf_to_images.py \
  src/pdf_agent/tools/_builtins/pdf_to_markdown.py \
  src/pdf_agent/tools/_builtins/pdf_to_office.py \
  src/pdf_agent/tools/_builtins/pdf_to_pdfa.py \
  src/pdf_agent/tools/_builtins/pdf_to_text.py \
  src/pdf_agent/tools/_builtins/pdf_to_word.py \
  src/pdf_agent/tools/_builtins/remove_blank_pages.py \
  src/pdf_agent/tools/_builtins/repair.py \
  src/pdf_agent/tools/_builtins/signature.py \
  src/pdf_agent/tools/_builtins/signature_info.py \
  src/pdf_agent/tools/_builtins/split.py \
  src/pdf_agent/tools/_builtins/tile_pages.py \
  src/pdf_agent/tools/_builtins/validate.py \
  src/pdf_agent/tools/_builtins/add_blank_pages.py \
  src/pdf_agent/tools/_builtins/booklet.py \
  src/pdf_agent/tools/_builtins/extract_attachments.py \
  src/pdf_agent/tools/_builtins/extract_images.py \
  src/pdf_agent/tools/_builtins/office_to_pdf.py \
  src/pdf_agent/tools/_builtins/redact.py
```

Notes:
- this commit carries tool capability alignment and subprocess unification

### Commit 3

Message:

```text
test: freeze conversation-first architecture docs and smoke acceptance suite
```

Suggested paths:

```bash
git add \
  PDF-Agent系统设计.md \
  CLOSURE_CHECKLIST.md \
  task_plan.md \
  findings.md \
  progress.md \
  tests/test_smoke_core.py \
  tests/test_smoke_tools.py \
  tests/test_agent_api.py \
  tests/test_agent_graph.py \
  tests/test_config.py \
  tests/test_e2e.py \
  tests/test_files_api.py \
  tests/test_jobs_api.py \
  tests/test_main.py \
  tests/test_migrations.py \
  tests/test_page_range.py \
  tests/test_registry.py \
  tests/test_scripts.py \
  tests/test_static_frontend.py \
  tests/test_storage.py \
  tests/test_tools_adapter.py \
  tests/test_tools_api.py \
  tests/test_tools_integration.py
```

Notes:
- include deleted detailed tests here so the test-suite simplification lands in one place
- this is also where the final architecture freeze is documented

## Validation Before Each Commit

Use at least:

```bash
python -m py_compile src/pdf_agent/api/router.py src/pdf_agent/api/agent.py src/pdf_agent/main.py tests/test_smoke_core.py
PYTHONPATH=src .venv/bin/python -m pytest tests -q
```

Current expected result:

```text
28 passed
```

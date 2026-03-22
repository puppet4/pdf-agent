# Release Acceptance Checklist

## Goal

Verify the current chat-first PDF Agent is ready to use as a stable baseline.

## Environment

- start from a clean working tree
- use the current project-local git identity
- ensure dependencies are installed

## 0. Baseline Check

Run:

```bash
git status --short
PYTHONPATH=src .venv/bin/python -m pytest tests -q
```

Expected:

- `git status --short` shows no output
- tests pass with the current smoke count

## 1. Start Services

If running locally:

```bash
uvicorn pdf_agent.main:app --host 127.0.0.1 --port 8000
```

If validating compose:

```bash
docker compose up --build
```

Expected:

- API starts successfully
- no immediate runtime or import errors
- `/healthz` returns healthy or at least structured status output

## 2. Upload Flow

From the frontend:

- upload one valid PDF
- upload multiple PDFs

Expected:

- files appear in the file list
- no broken thumbnail / page preview behavior
- status text updates normally

## 3. Conversation Flow

- select one or more files
- enter a natural-language instruction in the main chat composer
- send the message

Expected:

- a conversation is created or reused
- chat stream renders normally
- processing steps appear inline when the system performs PDF operations
- resulting files can be downloaded from the conversation result area

## 4. Follow-Up Conversation Flow

- after the first result is produced, send a follow-up instruction in the same conversation

Expected:

- the existing conversation continues instead of creating a disconnected new flow
- prior context remains usable
- new result files appear in the same conversation result area

## 5. Error Surface

Trigger one known validation error, for example:

- send a request without selecting any input file in a new conversation
- or provide obviously invalid natural-language constraints

Expected:

- frontend shows readable error feedback
- chat-first layout remains usable after the error

## 6. Metrics / Health

Check:

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/metrics | grep pdf_agent_conversation_runs_total
```

Expected:

- health endpoint responds
- metrics include `pdf_agent_conversation_runs_total`
- metrics do not include retired legacy counters

## 7. Release Decision

Release is acceptable if:

- baseline tests pass
- upload / conversation flows all work
- downloads work
- no visible legacy queue/platform terminology remains in the active UI
- no visible legacy manual-operation style product navigation remains in the active UI
- the main landing surface is chat-first
- legacy manual-operation HTTP entrypoints are gone from the active service surface
- no blocking runtime error appears in logs

## Notes

- if compose validation fails, check the API container logs first
- if history looks wrong again, confirm local git identity before making new commits

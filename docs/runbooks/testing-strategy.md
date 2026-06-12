# Testing Strategy

The default test flow is a functional automation gate. It should protect the main product workflows without making every developer run coverage-chasing or machine-dependent tests.

## Daily Functional Gate

Run:

```bash
bash scripts/quality_gate.sh
```

This uses `PDF_AGENT_TEST_PROFILE=functional` by default:

- Runs Ruff lint.
- Runs backend tests except `external_tools` and `coverage_edges`.
- Enforces 90% backend coverage as a regression guard, not as the primary goal.
- Runs the frontend lint and production build.
- Runs the frontend Playwright smoke test when local browser/server permissions allow it.

## Coverage Profile

Run:

```bash
PDF_AGENT_TEST_PROFILE=coverage bash scripts/quality_gate.sh
```

This includes `coverage_edges` tests and enforces 99% coverage. Use it when intentionally checking coverage health or before cutting a release candidate.

## Release Profile

Run:

```bash
PDF_AGENT_TEST_PROFILE=release bash scripts/quality_gate.sh
```

This turns on the heavier checks:

- Ruff format check.
- Coverage-edge tests.
- External tool tests.
- Frontend smoke.
- Migration rollback verification.
- Full browser E2E matrix.

The release profile assumes local dependencies such as browsers, database services, and external PDF engines are installed.

## What Should Not Be Committed

Do not commit one-off debugging scripts, `.coverage`, `htmlcov/`, Playwright reports, test results, generated browser fixtures, screenshots, videos, or temporary output files. Keep reusable automated tests and runbooks in the repository.

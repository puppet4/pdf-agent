#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_PROFILE="${PDF_AGENT_TEST_PROFILE:-functional}"

case "$TEST_PROFILE" in
  functional)
    DEFAULT_COVERAGE_FAIL_UNDER=90
    DEFAULT_RUN_BROWSER_E2E=0
    DEFAULT_RUN_COVERAGE_EDGES=0
    DEFAULT_RUN_EXTERNAL_TOOLS=0
    DEFAULT_RUN_FRONTEND_SMOKE=1
    DEFAULT_RUN_FORMAT_CHECK=0
    DEFAULT_RUN_MIGRATIONS=0
    ;;
  coverage)
    DEFAULT_COVERAGE_FAIL_UNDER=99
    DEFAULT_RUN_BROWSER_E2E=0
    DEFAULT_RUN_COVERAGE_EDGES=1
    DEFAULT_RUN_EXTERNAL_TOOLS=0
    DEFAULT_RUN_FRONTEND_SMOKE=0
    DEFAULT_RUN_FORMAT_CHECK=0
    DEFAULT_RUN_MIGRATIONS=0
    ;;
  release)
    DEFAULT_COVERAGE_FAIL_UNDER=99
    DEFAULT_RUN_BROWSER_E2E=1
    DEFAULT_RUN_COVERAGE_EDGES=1
    DEFAULT_RUN_EXTERNAL_TOOLS=1
    DEFAULT_RUN_FRONTEND_SMOKE=1
    DEFAULT_RUN_FORMAT_CHECK=1
    DEFAULT_RUN_MIGRATIONS=1
    ;;
  *)
    echo "Unknown PDF_AGENT_TEST_PROFILE: ${TEST_PROFILE}" >&2
    echo "Use one of: functional, coverage, release" >&2
    exit 2
    ;;
esac

COVERAGE_FAIL_UNDER="${PDF_AGENT_COVERAGE_FAIL_UNDER:-$DEFAULT_COVERAGE_FAIL_UNDER}"
RUN_MIGRATIONS="${PDF_AGENT_RUN_MIGRATIONS:-$DEFAULT_RUN_MIGRATIONS}"
RUN_BROWSER_E2E="${PDF_AGENT_RUN_BROWSER_E2E:-$DEFAULT_RUN_BROWSER_E2E}"
RUN_FRONTEND_SMOKE="${PDF_AGENT_RUN_FRONTEND_SMOKE:-$DEFAULT_RUN_FRONTEND_SMOKE}"
RUN_FORMAT_CHECK="${PDF_AGENT_RUN_FORMAT_CHECK:-$DEFAULT_RUN_FORMAT_CHECK}"
RUN_EXTERNAL_TOOLS="${PDF_AGENT_RUN_EXTERNAL_TOOLS:-$DEFAULT_RUN_EXTERNAL_TOOLS}"
RUN_COVERAGE_EDGES="${PDF_AGENT_RUN_COVERAGE_EDGES:-$DEFAULT_RUN_COVERAGE_EDGES}"
PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"
RUFF="${RUFF:-$ROOT_DIR/.venv/bin/ruff}"

echo "=== PDF Agent Quality Gate ==="
echo "test profile: ${TEST_PROFILE}"
echo "coverage threshold: ${COVERAGE_FAIL_UNDER}%"
echo "run format check: ${RUN_FORMAT_CHECK}"
echo "run external tool tests: ${RUN_EXTERNAL_TOOLS}"
echo "run coverage-edge tests: ${RUN_COVERAGE_EDGES}"
echo "run frontend smoke: ${RUN_FRONTEND_SMOKE}"
echo "run migrations: ${RUN_MIGRATIONS}"
echo "run browser e2e: ${RUN_BROWSER_E2E}"
echo

cd "$ROOT_DIR"

if [[ ! -x "$PYTHON" ]]; then
  echo "Python not found: $PYTHON" >&2
  exit 1
fi

if [[ ! -x "$RUFF" ]]; then
  echo "Ruff not found: $RUFF" >&2
  exit 1
fi

echo "[1/5] Ruff lint"
"$RUFF" check src tests

echo "[2/5] Ruff format check"
if [[ "$RUN_FORMAT_CHECK" == "1" ]]; then
  "$RUFF" format --check src tests
else
  echo "Skipping Ruff format check. Set PDF_AGENT_RUN_FORMAT_CHECK=1 to enable."
fi

echo "[3/5] Backend tests with coverage"
if ! "$PYTHON" -c "import coverage" >/dev/null 2>&1; then
  echo "coverage is not installed in $PYTHON" >&2
  echo "Install dev dependencies first, for example: uv pip install --python \"$PYTHON\" '.[dev]'" >&2
  exit 1
fi
PYTEST_ARGS=(tests -q)
PYTEST_MARK_EXPR="not external_tools and not coverage_edges"
if [[ "$RUN_EXTERNAL_TOOLS" == "1" && "$RUN_COVERAGE_EDGES" == "1" ]]; then
  PYTEST_MARK_EXPR=""
elif [[ "$RUN_EXTERNAL_TOOLS" == "1" ]]; then
  PYTEST_MARK_EXPR="not coverage_edges"
elif [[ "$RUN_COVERAGE_EDGES" == "1" ]]; then
  PYTEST_MARK_EXPR="not external_tools"
fi
if [[ -n "$PYTEST_MARK_EXPR" ]]; then
  PYTEST_ARGS+=("-m" "$PYTEST_MARK_EXPR")
fi
if [[ "$RUN_COVERAGE_EDGES" != "1" ]]; then
  echo "Skipping coverage-edge branch tests. Set PDF_AGENT_TEST_PROFILE=coverage or PDF_AGENT_RUN_COVERAGE_EDGES=1 to enable."
fi
"$PYTHON" -m coverage run --source=pdf_agent -m pytest "${PYTEST_ARGS[@]}"
"$PYTHON" -m coverage report --fail-under="$COVERAGE_FAIL_UNDER" -m

echo "[4/5] Frontend lint and build"
(
  cd "$ROOT_DIR/frontend"
  npm run lint
  npm run build
)

echo "[5/5] Optional release checks"
if [[ "$RUN_FRONTEND_SMOKE" == "1" ]]; then
  if [[ -f "$ROOT_DIR/qa/browser-e2e/package.json" ]]; then
    (
      cd "$ROOT_DIR/qa/browser-e2e"
      PDF_AGENT_E2E_START_WEB_SERVER=1 \
      PDF_AGENT_E2E_USE_SYSTEM_CHROME="${PDF_AGENT_E2E_USE_SYSTEM_CHROME:-1}" \
      npm run test:frontend-smoke
    )
  else
    echo "Skipping frontend browser smoke because qa/browser-e2e is not present in this checkout."
  fi
else
  echo "Skipping frontend browser smoke. Set PDF_AGENT_RUN_FRONTEND_SMOKE=1 to enable."
fi

if [[ "$RUN_MIGRATIONS" == "1" ]]; then
  bash "$ROOT_DIR/scripts/verify_migrations.sh"
else
  echo "Skipping migration rollback check. Set PDF_AGENT_RUN_MIGRATIONS=1 to enable."
fi

if [[ "$RUN_BROWSER_E2E" == "1" ]]; then
  bash "$ROOT_DIR/scripts/qa_local.sh"
else
  echo "Skipping browser E2E matrix. Set PDF_AGENT_RUN_BROWSER_E2E=1 to enable."
fi

echo
echo "Quality gate completed."

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required but not installed." >&2
  exit 1
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Virtualenv Python not found at $VENV_PYTHON" >&2
  exit 1
fi

brew update

for formula in qpdf ghostscript poppler tesseract ocrmypdf; do
  if ! brew list --formula "$formula" >/dev/null 2>&1; then
    brew install "$formula"
  fi
done

if ! brew list --cask libreoffice >/dev/null 2>&1; then
  brew install --cask libreoffice
fi

mkdir -p "$HOME/.local/bin"

if [[ -x "/Applications/LibreOffice.app/Contents/MacOS/soffice" ]]; then
  ln -sf "/Applications/LibreOffice.app/Contents/MacOS/soffice" "$HOME/.local/bin/soffice"
  ln -sf "/Applications/LibreOffice.app/Contents/MacOS/soffice" "$HOME/.local/bin/libreoffice"
fi

uv pip install --python "$VENV_PYTHON" \
  "qrcode[pil]" \
  "python-barcode[images]" \
  "python-docx" \
  "openpyxl" \
  "python-pptx" \
  "pdfminer.six"

echo "Installed PDF test dependencies."

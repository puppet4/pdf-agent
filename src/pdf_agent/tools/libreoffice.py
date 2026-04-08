"""Helpers for invoking LibreOffice with an isolated user profile."""
from __future__ import annotations

import shutil
from pathlib import Path

from pdf_agent.config import settings
from pdf_agent.core import ToolError
from pdf_agent.external_commands import run_command


def build_libreoffice_command(
    lo_bin: str,
    *,
    convert_to: str,
    input_path: Path,
    outdir: Path,
    profile_dir: Path,
) -> list[str]:
    profile_dir.mkdir(parents=True, exist_ok=True)
    return [
        lo_bin,
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        "--headless",
        "--convert-to",
        convert_to,
        "--outdir",
        str(outdir),
        str(input_path),
    ]


def run_libreoffice_conversion(
    lo_bin: str,
    *,
    convert_to: str,
    input_path: Path,
    outdir: Path,
    profile_dir: Path,
    timeout: int | None = None,
) -> tuple[bool, str | None]:
    """Run LibreOffice conversion and return whether it completed successfully."""
    try:
        run_command(
            build_libreoffice_command(
                lo_bin,
                convert_to=convert_to,
                input_path=input_path,
                outdir=outdir,
                profile_dir=profile_dir,
            ),
            timeout=timeout or settings.libreoffice_timeout_sec,
        )
    except ToolError as exc:
        return False, str(exc)
    return True, None


def run_libreoffice_conversion_to_output(
    lo_bin: str,
    *,
    convert_to: str,
    input_path: Path,
    output_path: Path,
    outdir: Path,
    profile_dir: Path,
    timeout: int | None = None,
) -> tuple[bool, str | None]:
    """Run LibreOffice and normalize its default output name into the requested path."""
    success, failure_reason = run_libreoffice_conversion(
        lo_bin,
        convert_to=convert_to,
        input_path=input_path,
        outdir=outdir,
        profile_dir=profile_dir,
        timeout=timeout,
    )
    if not success:
        return False, failure_reason

    lo_output_path = outdir / f"{input_path.stem}{output_path.suffix}"
    if lo_output_path.exists():
        if lo_output_path != output_path:
            shutil.move(str(lo_output_path), str(output_path))
        return True, None

    if output_path.exists():
        return True, None

    return False, f"LibreOffice did not produce a {output_path.suffix} file"

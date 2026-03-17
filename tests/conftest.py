"""Shared test fixtures."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pikepdf
import pytest
from PIL import Image


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    """Create a simple 5-page PDF for testing."""
    pdf_path = tmp_path / "sample.pdf"
    pdf = pikepdf.Pdf.new()
    for i in range(5):
        page = pikepdf.Page(pikepdf.Dictionary(
            Type=pikepdf.Name.Page,
            MediaBox=[0, 0, 612, 792],
            Contents=pdf.make_stream(f"BT /F1 12 Tf 100 700 Td (Page {i + 1}) Tj ET".encode()),
            Resources=pikepdf.Dictionary(
                Font=pikepdf.Dictionary(
                    F1=pikepdf.Dictionary(
                        Type=pikepdf.Name.Font,
                        Subtype=pikepdf.Name.Type1,
                        BaseFont=pikepdf.Name.Helvetica,
                    )
                )
            ),
        ))
        pdf.pages.append(page)
    pdf.save(pdf_path)
    return pdf_path


@pytest.fixture()
def encrypted_pdf(tmp_path: Path, sample_pdf: Path) -> Path:
    """Create a password-protected PDF."""
    enc_path = tmp_path / "encrypted.pdf"
    with pikepdf.open(sample_pdf) as pdf:
        pdf.save(
            enc_path,
            encryption=pikepdf.Encryption(user="userpass", owner="ownerpass", R=6),
        )
    return enc_path


@pytest.fixture()
def sample_images(tmp_path: Path) -> list[Path]:
    """Create sample PNG images for testing."""
    paths = []
    for i in range(3):
        img_path = tmp_path / f"img_{i + 1}.png"
        img = Image.new("RGB", (200, 300), color=(50 * i, 100, 200))
        img.save(img_path)
        paths.append(img_path)
    return paths


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    """Provide a clean working directory."""
    wd = tmp_path / "workdir"
    wd.mkdir()
    return wd

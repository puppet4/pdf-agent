"""Smoke tests for representative built-in tools."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.tools._builtins.add_blank_pages import AddBlankPagesTool
from pdf_agent.tools._builtins.encrypt import EncryptTool
from pdf_agent.tools._builtins.merge import MergeTool
from pdf_agent.tools._builtins.metadata_info import MetadataInfoTool
from pdf_agent.tools._builtins.split import SplitTool


class TestRepresentativeToolsSmoke:
    def test_merge_supports_interleave_mode(self, sample_pdf: Path, workdir: Path):
        sample_copy = workdir / "first.pdf"
        second = workdir / "second.pdf"
        pdf_bytes = sample_pdf.read_bytes()
        sample_copy.write_bytes(pdf_bytes)
        second.write_bytes(pdf_bytes)
        merge_dir = workdir / "merge"
        merge_dir.mkdir(parents=True, exist_ok=True)

        result = MergeTool().run([sample_copy, second], {"mode": "interleave"}, merge_dir)

        assert result.output_files[0].exists()
        with pikepdf.open(result.output_files[0]) as pdf:
            assert len(pdf.pages) == 10

    def test_merge_supports_insert_mode(self, sample_pdf: Path, workdir: Path):
        first = workdir / "first.pdf"
        second = workdir / "second.pdf"
        data = sample_pdf.read_bytes()
        first.write_bytes(data)
        second.write_bytes(data)
        merge_dir = workdir / "merge_insert"
        merge_dir.mkdir(parents=True, exist_ok=True)

        result = MergeTool().run([first, second], {"mode": "insert", "insert_position": 2}, merge_dir)

        with pikepdf.open(result.output_files[0]) as pdf:
            assert len(pdf.pages) == 10

    def test_split_supports_chunk_mode(self, sample_pdf: Path, workdir: Path):
        split_dir = workdir / "split"
        split_dir.mkdir(parents=True, exist_ok=True)
        result = SplitTool().run([sample_pdf], {"mode": "chunk", "chunk_size": 2}, split_dir)

        assert len(result.output_files) == 3
        assert all(path.exists() for path in result.output_files)

    def test_split_supports_bookmark_mode(self, sample_pdf: Path, workdir: Path):
        bookmarked = workdir / "bookmarked.pdf"
        with pikepdf.open(sample_pdf) as pdf:
            with pdf.open_outline() as outline:
                outline.root.append(pikepdf.OutlineItem("PartA", 0))
                outline.root.append(pikepdf.OutlineItem("PartB", 2))
            pdf.save(bookmarked)

        split_dir = workdir / "split_bookmark"
        split_dir.mkdir(parents=True, exist_ok=True)
        result = SplitTool().run([bookmarked], {"mode": "bookmark"}, split_dir)

        assert len(result.output_files) == 2

    def test_add_blank_pages_inserts_pages(self, sample_pdf: Path, workdir: Path):
        blank_dir = workdir / "blank"
        blank_dir.mkdir(parents=True, exist_ok=True)
        result = AddBlankPagesTool().run([sample_pdf], {"page_range": "1,3", "count": 1}, blank_dir)

        with pikepdf.open(result.output_files[0]) as pdf:
            assert len(pdf.pages) == 7

    def test_encrypt_produces_password_protected_pdf(self, sample_pdf: Path, workdir: Path):
        encrypt_dir = workdir / "encrypt"
        encrypt_dir.mkdir(parents=True, exist_ok=True)
        result = EncryptTool().run(
            [sample_pdf],
            {"owner_password": "owner-pass", "user_password": "user-pass"},
            encrypt_dir,
        )

        assert result.output_files[0].exists()
        with pikepdf.open(result.output_files[0], password="user-pass") as pdf:
            assert len(pdf.pages) == 5

    def test_metadata_info_reports_basic_stats(self, sample_pdf: Path, workdir: Path):
        meta_dir = workdir / "meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        result = MetadataInfoTool().run([sample_pdf], {}, meta_dir)

        assert result.output_files[0].exists()
        assert result.meta["page_count"] == 5
        assert result.meta["has_text_layer"] is True
        assert len(result.meta["pages"]) == 5

"""Tests for page range parser."""
import pytest

from pdf_agent.core.page_range import parse_page_range
from pdf_agent.core import PDFAgentError


class TestParsePageRange:
    def test_all(self):
        assert parse_page_range("all", 5) == [0, 1, 2, 3, 4]

    def test_single_page(self):
        assert parse_page_range("3", 10) == [2]

    def test_range(self):
        assert parse_page_range("1-3", 10) == [0, 1, 2]

    def test_mixed(self):
        assert parse_page_range("1-3,5,7-9", 10) == [0, 1, 2, 4, 6, 7, 8]

    def test_odd(self):
        assert parse_page_range("odd", 6) == [0, 2, 4]

    def test_even(self):
        assert parse_page_range("even", 6) == [1, 3, 5]

    def test_last(self):
        assert parse_page_range("last", 5) == [4]

    def test_last_range(self):
        assert parse_page_range("last-2-last", 10) == [7, 8, 9]

    def test_out_of_range(self):
        with pytest.raises(PDFAgentError):
            parse_page_range("11", 10)

    def test_empty(self):
        with pytest.raises(PDFAgentError):
            parse_page_range("", 10)

    def test_invalid_token(self):
        with pytest.raises(PDFAgentError):
            parse_page_range("abc", 10)

import pytest

from app.utils.passengers import document_query, fold_text, normalize_document


class TestNormalize:
    @pytest.mark.parametrize("raw", ["V-12.345.678", " v 12 345 678 ", "V12345678", "v_12/345.678"])
    def test_document_ignores_noise(self, raw):
        assert normalize_document(raw) == "V12345678"

    def test_name_ignores_accents_case_and_spaces(self):
        assert fold_text("  MARÍA   José ") == "maria jose"

    def test_eñe_loses_its_tilde_like_any_accent(self):
        assert fold_text("Peña") == "pena"


class TestDocumentQuery:
    def test_needs_a_digit(self):
        assert document_query("ana") is None

    def test_needs_three_characters(self):
        assert document_query("V1") is None
        assert document_query("V12") == "V12"

    def test_full_document(self):
        assert document_query("V-12.345.678") == "V12345678"

    @pytest.mark.parametrize("raw", ["", "   ", "%", "_", ".*", "%%%"])
    def test_wildcards_are_not_documents(self, raw):
        assert document_query(raw) is None

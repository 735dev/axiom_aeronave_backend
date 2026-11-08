import pytest

from app.utils.csv_export import build_csv, cell, neutralize_formula


class TestCell:
    def test_wraps_in_quotes(self):
        assert cell("hola") == '"hola"'

    def test_doubles_inner_quotes(self):
        assert cell('dijo "sí"') == '"dijo ""sí"""'

    def test_keeps_separator_and_newlines_inside(self):
        assert cell("a;b\nc") == '"a;b\nc"'

    @pytest.mark.parametrize("text", ["=SUM(A1)", "+1", "-1+2", "@cmd", "\t=1", "\r=1"])
    def test_formulas_are_neutralized(self, text):
        assert cell(text).startswith("\"'")

    def test_equals_in_the_middle_is_left_alone(self):
        assert cell("a=b") == '"a=b"'

    def test_numbers_use_a_decimal_comma_and_no_guard(self):
        assert cell(1850.5) == '"1850,50"'
        assert cell(-5) == '"-5,00"'

    def test_none_is_empty(self):
        assert cell(None) == '""'

    def test_neutralize_leaves_plain_text(self):
        assert neutralize_formula("Combustible") == "Combustible"

    def test_a_hyperlink_injection_is_defanged(self):
        out = cell('=HYPERLINK("http://x")')
        assert out == '"\'=HYPERLINK(""http://x"")"'

    def test_a_dde_payload_is_defanged(self):
        assert cell("@SUM(1+1)*cmd|' /C calc'!A0").startswith("\"'@")


class TestBuild:
    def test_starts_with_bom_and_uses_crlf(self):
        text = build_csv(["a", "b"], [["1", "2"]])
        assert text.startswith("﻿") and text.endswith("\r\n")
        assert text[1:].split("\r\n") == ['"a";"b"', '"1";"2"', ""]

    def test_empty_body_keeps_the_header(self):
        assert build_csv(["a"], []).count("\r\n") == 1

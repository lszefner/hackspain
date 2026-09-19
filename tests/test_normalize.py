"""Tests for normalize.py — the matching contract every source passes through."""
import unittest
from decimal import Decimal

from rules_ingestion import normalize as N


class TestText(unittest.TestCase):
    def test_trailing_and_collapsed_whitespace(self):
        self.assertEqual(N.norm_text("Ofimática Cieza S.L.  "), "Ofimática Cieza S.L.")
        self.assertEqual(N.norm_text("  a   b\tc \n"), "a b c")

    def test_blank_and_none(self):
        self.assertIsNone(N.norm_text(None))
        self.assertIsNone(N.norm_text("   "))


class TestIBAN(unittest.TestCase):
    def test_strip_and_upper(self):
        self.assertEqual(N.norm_iban("ES21 0049 1500 0512 3456 7890"),
                         "ES2100491500051234567890")
        self.assertEqual(N.norm_iban("es21 0049"), "ES210049")

    def test_checksum_valid_and_invalid(self):
        self.assertTrue(N.iban_is_valid("GB82 WEST 1234 5698 7654 32"))
        self.assertFalse(N.iban_is_valid("GB82 WEST 1234 5698 7654 33"))

    def test_none(self):
        self.assertFalse(N.iban_is_valid(None))


class TestNIF(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(N.norm_nif(" b-46.102.331 "), "B46102331")

    def test_dni_control(self):
        self.assertTrue(N.nif_control_ok("12345678Z"))
        self.assertFalse(N.nif_control_ok("12345678A"))

    def test_wellformed_vs_control(self):
        # Synthetic master CIFs are well-formed but fail the real control digit;
        # rules must key on master membership, not this. Documented behaviour.
        self.assertTrue(N.nif_looks_wellformed("A41220987"))
        self.assertFalse(N.nif_control_ok("A41220987"))


class TestImporte(unittest.TestCase):
    def test_es_format(self):
        self.assertEqual(N.norm_importe("12.874,40"), Decimal("12874.40"))
        self.assertEqual(N.norm_importe("1.234,56 EUR"), Decimal("1234.56"))
        self.assertEqual(N.norm_importe("0,00"), Decimal("0.00"))

    def test_en_format_and_floats(self):
        self.assertEqual(N.norm_importe("12,874.40"), Decimal("12874.40"))
        self.assertEqual(N.norm_importe(1234.5), Decimal("1234.5"))
        self.assertEqual(N.norm_importe("9221.75"), Decimal("9221.75"))

    def test_garbage(self):
        self.assertIsNone(N.norm_importe(""))
        self.assertIsNone(N.norm_importe("N/A"))

    def test_tolerance(self):
        self.assertTrue(N.amounts_equal("100,00", "100,005"))
        self.assertTrue(N.amounts_equal("100,00", "100,01"))
        self.assertFalse(N.amounts_equal("100,00", "100,02"))
        self.assertFalse(N.amounts_equal(None, "100,00"))


class TestFecha(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(N.norm_fecha("16/07/2026"), "2026-07-16")
        self.assertEqual(N.norm_fecha("2026-07-16"), "2026-07-16")

    def test_future(self):
        self.assertTrue(N.is_future("2030-01-01", today="2026-09-19"))
        self.assertFalse(N.is_future("2020-01-01", today="2026-09-19"))

    def test_payment_terms(self):
        self.assertEqual(N.payment_terms_days("60 dias"), 60)
        self.assertEqual(N.payment_terms_days("30 días"), 30)
        self.assertIsNone(N.payment_terms_days(None))


if __name__ == "__main__":
    unittest.main()

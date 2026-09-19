"""Tests for loader.py — sheet whitelisting, dedup, schema discovery, norma."""
import os
import unittest

from rules_ingestion import loader

_CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "rules_ingestion", "sources.yaml")


class TestLoader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = loader.load(_CFG)

    def test_master_counts(self):
        # 11 distinct vendors (P001..P011); duplicate P007 collapsed.
        self.assertEqual(len(self.result.lookups["proveedores"]), 11)
        self.assertEqual(len(self.result.lookups["pedidos"]), 516)

    def test_dedup_p007_warning(self):
        self.assertTrue(any("duplicate key" in w for w in self.result.warnings))

    def test_normalization_applied(self):
        p001 = self.result.lookups["proveedores"]["P001"]
        self.assertEqual(p001["iban"], "ES2100491500051234567890")  # spaces stripped
        p003 = self.result.lookups["proveedores"]["P003"]
        self.assertEqual(p003["razon_social"], "Ofimática Cieza S.L.")  # trailing ws gone

    def test_pedido_importe_is_decimal(self):
        from decimal import Decimal
        po1 = self.result.lookups["pedidos"]["PO-2026-0001"]
        self.assertIsInstance(po1["importe_total"], Decimal)
        self.assertEqual(po1["fecha_pedido"], "2026-01-31")

    def test_junk_sheets_ignored(self):
        for junk in ("NO_TOCAR", "MACROS_ROTAS", "notas_alberto"):
            self.assertNotIn(junk, self.result.lookups)
            self.assertFalse(any(f"'{junk}'" in w and "junk" in w
                                 for w in self.result.warnings),
                             msg=f"{junk} should be explicitly ignored, not flagged")

    def test_norma_lines_extracted(self):
        self.assertEqual(len(self.result.norma_lines), 6)
        cells = [l["cell"] for l in self.result.norma_lines]
        self.assertEqual(cells, ["A2", "A3", "A4", "A5", "A6", "A7"])

    def test_norma_lines_marked_declared(self):
        self.assertTrue(all(l["declared"] for l in self.result.norma_lines))

    def test_schema_discovery(self):
        self.assertEqual(self.result.schema["proveedores"]["missing_columns"], [])
        self.assertEqual(self.result.ruleset_version, "v3")


class TestScan(unittest.TestCase):
    def test_text_candidate_filter(self):
        self.assertTrue(loader.is_text_candidate("El NIF debe existir en el maestro de proveedores"))
        self.assertFalse(loader.is_text_candidate("PO-2026-0001"))   # code, no spaces
        self.assertFalse(loader.is_text_candidate("123456"))          # digits
        self.assertFalse(loader.is_text_candidate(None))


if __name__ == "__main__":
    unittest.main()

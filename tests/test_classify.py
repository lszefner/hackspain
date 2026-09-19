"""Tests for classify.py — the offline lexical tier (Noul + Choice) and routing.

These run WITHOUT network or keys (prefer_jev=False / LexicalJEV directly) so the
suite is hermetic. The live JEV path is exercised via `make scan` / `make rules`.
"""
import unittest

from rules_ingestion.classify import LexicalJEV, Noul, Choice, classify_lines, IS_RULE_LO

# The actual v3 norm sentences (Norma_Pagos_v3, column A).
NORM = {
    "VENDOR": "1. Pagar solo si el NIF esta en el maestro y el IBAN de la factura coincide con el maestro.",
    "AMOUNT_pedido": "2. El pedido debe existir, pertenecer al proveedor y el importe de la factura debe ser igual al del pedido (tolerancia 0,01 EUR).",
    "AMOUNT_iva": "3. El IVA debe estar bien calculado y el total debe ser base + IVA, con la misma tolerancia de 0,01 EUR.",
    "DATES": "4. La fecha debe ser valida y no futura.",
    "DUPLICATES": "5. Estado ERP del pedido: PENDIENTE. Nunca pagar dos veces el mismo pedido.",
    "MISSING": "6. Cualquier anomalia que un humano deba ver: ESCALAR con motivo. Ante duda razonable, escalar antes que pagar.",
}

# Real junk from notas_alberto / pendiente_revisar (should NOT be rules).
JUNK = [
    "los de Guadaira siempre llaman los viernes",
    "cafe 3a planta roto desde 2023",
    "preguntar a Sonia lo del IVA reducido (aplica??)",
    "mirar cuando haya hueco",
]


class TestNoul(unittest.TestCase):
    def test_real_rules_score_high(self):
        for text in NORM.values():
            self.assertGreaterEqual(Noul.is_rule(text), 0.8, msg=text)

    def test_junk_scores_low(self):
        for text in JUNK:
            self.assertLess(Noul.is_rule(text), 0.5, msg=text)


class TestChoice(unittest.TestCase):
    def setUp(self):
        self.choice = Choice()

    def test_mappings(self):
        cases = {
            NORM["VENDOR"]: "VENDOR",
            NORM["AMOUNT_pedido"]: "AMOUNT",
            NORM["AMOUNT_iva"]: "AMOUNT",
            NORM["DATES"]: "DATES",
            NORM["DUPLICATES"]: "DUPLICATES",
            NORM["MISSING"]: "MISSING",
        }
        for text, expected in cases.items():
            label, conf, _ = self.choice.maps_to(text)
            self.assertEqual(label, expected, msg=f"{text}\n got {label} @ {conf}")
            self.assertGreaterEqual(conf, 0.6, msg=text)

    def test_token_free_junk_maps_to_none(self):
        # Choice is a *lexical* router; it is only meaningful AFTER Noul gates.
        # Lines with no domain token map to NONE.
        for text in ["los de Guadaira siempre llaman los viernes",
                     "cafe 3a planta roto desde 2023",
                     "mirar cuando haya hueco"]:
            label, _, _ = self.choice.maps_to(text)
            self.assertEqual(label, "NONE", msg=text)

    def test_domain_token_junk_is_gated_by_noul_not_choice(self):
        # This note contains 'IVA' so Choice alone fires AMOUNT@high — that is
        # correct lexical behaviour. Safety comes from Noul rejecting it, so the
        # full JEV must NOT activate it.
        note = "preguntar a Sonia lo del IVA reducido (aplica??)"
        label, conf, _ = self.choice.maps_to(note)
        self.assertEqual(label, "AMOUNT")            # Choice fires on 'iva'
        self.assertLess(Noul.is_rule(note), IS_RULE_LO)  # Noul is the gate
        self.assertNotEqual(LexicalJEV().classify(note).route, "ACTIVATE")


class TestRouting(unittest.TestCase):
    def test_all_norm_lines_activate_offline(self):
        jev = LexicalJEV()
        for text in NORM.values():
            res = jev.classify(text)
            self.assertEqual(res.route, "ACTIVATE", msg=f"{text} -> {res.route}")
            self.assertEqual(res.method, "lexical")

    def test_junk_not_a_rule(self):
        jev = LexicalJEV()
        for text in JUNK:
            res = jev.classify(text)
            self.assertIn(res.route, ("NOT_A_RULE", "NEW_RULE"), msg=text)
            self.assertNotEqual(res.route, "ACTIVATE")


class TestLLMDegradation(unittest.TestCase):
    def test_fallback_disabled_degrades_safely(self):
        # An intentionally ambiguous, rule-ish line with no clear canonical match.
        lines = [{"text": "Revisar condiciones especiales del contrato marco", "cell": "A9", "row": 9}]
        out = classify_lines(lines, prefer_jev=False, use_llm=False)
        _, res = out[0]
        # Must never silently ACTIVATE on an unresolved line.
        self.assertNotEqual(res.route, "ACTIVATE")


if __name__ == "__main__":
    unittest.main()

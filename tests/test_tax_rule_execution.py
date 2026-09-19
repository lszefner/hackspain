from __future__ import annotations

import copy
from decimal import localcontext
from pathlib import Path

import pytest
import yaml

from ingestion.contracts import canonical_bytes, digest
from rules_ingestion import decision_context as dc
from rules_ingestion.execution_context import validate_execution_context
from rules_ingestion.execution_rules import _nif_checksum
from tests.test_rule_execution import fixtures, history, rule, run, structured


@pytest.mark.parametrize('value', [
    '12345678Z', 'X5253868R', 'B64717838', 'J99216582', 'J9921658B',
    'M1234567L', 'K1234567L', 'L1234567L', 'ES B-58378431', 'b-64.717.838',
])
def test_nif_checksum_supported_identifiers(value):
    assert _nif_checksum(value)


@pytest.mark.parametrize('value', ['12345678A', 'B64717839', 'M1234567A', '', 'B123', '１２３４５６７８Z', 'DE123456789'])
def test_nif_checksum_invalid_identifiers(value):
    assert not _nif_checksum(value)


def identity(nif='B64717838'):
    invoice = fixtures.make_invoice()
    invoice['supplier']['tax_id'] = nif
    snapshots = fixtures.make_snapshots(
        supplier_records=[fixtures.supplier_record(nif=nif, active=True)],
        order_records=[{'pedido': 'PO-1', 'proveedor_id': 'P042', 'nif': nif,
                        'importe_total': '121.00', 'currency': 'EUR'}])
    return invoice, snapshots


@pytest.mark.parametrize('on_fail', ['ESCALAR', 'NO_PAGAR'])
@pytest.mark.parametrize('nif,valid', [('B64717838', True), ('B64717839', False)])
def test_nif_rule_honors_policy_and_records_evidence(on_fail, nif, valid):
    invoice, snapshots = identity(nif)
    _, result = run([rule('VENDOR', {'check_nif_control_digit': True}, on_fail=on_fail)],
                    invoice=invoice, snapshots=snapshots)
    output = result['rule_results'][0]
    assert output['status'] == ('PASS' if valid else 'VIOLATED')
    assert result['preliminary_decision'] == ('PAGAR' if valid else on_fail)
    assert output['complete']
    checksum = next(trace for trace in output['trace'] if trace['operation'] == 'spanish_tax_id_checksum')
    assert checksum['operands'] == [nif]
    assert {'field': 'invoice.supplier_tax_id'} in checksum['input_refs']
    assert {'source': 'invoice', 'pointer': '/supplier/tax_id'} in output['evidence_refs']
    if not valid:
        assert 'NIF_CONTROL_MISMATCH' in output['reason_codes']


def test_checksum_flag_does_not_override_membership_or_disabled_policy():
    _, disabled = run([rule('VENDOR', {'check_nif_control_digit': False})])
    assert disabled['preliminary_decision'] == 'PAGAR'
    invoice, snapshots = identity()
    snapshots['suppliers'] = fixtures.snap('supplier_master', {'records': []}, fixtures.SUPPLIER_SCOPES)
    _, result = run([rule('VENDOR', {'check_nif_control_digit': True})], invoice=invoice, snapshots=snapshots)
    assert result['preliminary_decision'] != 'PAGAR'
    assert not result['completeness']['evaluation_complete']


@pytest.mark.parametrize('mode', ['missing', 'unlinked', 'bad_reference'])
def test_nif_cannot_fail_or_pass_on_unusable_evidence(mode):
    invoice, snapshots = identity('B64717839')
    if mode == 'missing':
        invoice['supplier']['tax_id'] = None
    outcome = fixtures.make_outcome(invoice)
    if mode == 'unlinked':
        outcome['evidence'].pop('/supplier/tax_id')
    elif mode == 'bad_reference':
        outcome['evidence']['/supplier/tax_id'] = [{'page': 1, 'reference_ids': ['not-present']}]
    _, result = run([rule('VENDOR', {'check_nif_control_digit': True}, on_fail='NO_PAGAR')],
                    outcome=outcome, snapshots=snapshots)
    checksum = next(trace for trace in result['rule_results'][0]['trace'] if trace['operation'] == 'spanish_tax_id_checksum')
    assert checksum['verdict'] == 'BLOCKED'
    assert 'NIF_CONTROL_MISMATCH' not in result['rule_results'][0]['reason_codes']
    assert not result['completeness']['evaluation_complete']


def vat_rule(**params):
    return rule('AMOUNT', {'check_iva': True, 'check_line_items_sum': False,
                           'check_total_is_base_plus_iva': False, 'check_matches_pedido': False, **params})


def vat_invoice(base='100.00', rate='21', amount='21.00', currency='EUR'):
    invoice = fixtures.make_invoice(currency=currency)
    invoice['totals']['taxable_base'] = base
    invoice['taxes'] = [{'label': 'IVA', 'rate_percent': rate, 'amount': amount}]
    return invoice


@pytest.mark.parametrize('rate,amount,status', [
    ('21', '21', 'PASS'), ('10', '10', 'PASS'), ('0', '0', 'PASS'),
    ('21', '21.01', 'PASS'), ('21', '21.0101', 'VIOLATED'), ('21', '20', 'VIOLATED'),
    ('-1', '-1', 'VIOLATED'), ('101', '101', 'VIOLATED'),
])
def test_vat_rate_arithmetic_and_tolerance(rate, amount, status):
    _, result = run([vat_rule()], invoice=vat_invoice(rate=rate, amount=amount))
    output = result['rule_results'][0]
    assert output['status'] == status
    assert output['complete']
    assert 'UNIMPLEMENTED_FLAG' not in output['reason_codes']
    if rate in ('-1', '101'):
        assert 'VAT_RATE_INVALID' in output['reason_codes']
    elif status == 'VIOLATED':
        assert 'VAT_AMOUNT_MISMATCH' in output['reason_codes']


def test_vat_half_up_rounding_ignores_ambient_decimal_precision():
    with localcontext() as context:
        context.prec = 2
        _, result = run([vat_rule(tolerance_eur='0')], invoice=vat_invoice(base='0.05', rate='10', amount='0.01'))
    output = result['rule_results'][0]
    assert output['status'] == 'PASS'
    trace = next(item for item in output['trace'] if item['operation'] == 'vat_absolute_difference_lte')
    assert trace['computed_value'] == '0.00'
    assert trace['tolerance'] == '0'
    assert 'expected 0.01 EUR' in trace['explanation']
    assert {'source': 'invoice', 'pointer': '/taxes/0/rate_percent'} in output['evidence_refs']
    assert {item['field'] for item in output['inputs']} >= {
        'invoice.taxable_base', 'invoice.taxes.0.rate_percent', 'invoice.taxes.0.amount', 'invoice.currency'}


@pytest.mark.parametrize('values', [{'base': None}, {'rate': None}, {'amount': None}, {'currency': None}, {'currency': 'USD'}])
def test_vat_missing_values_and_currency_block(values):
    _, result = run([vat_rule()], invoice=vat_invoice(**values))
    assert result['rule_results'][0]['status'] == 'BLOCKED'
    assert not result['rule_results'][0]['complete']


@pytest.mark.parametrize('mode', ['unlinked', 'bad_reference', 'uncertain_issue', 'uncertain_block'])
def test_vat_rate_requires_usable_evidence(mode):
    invoice = vat_invoice(rate='10', amount='21')
    if mode == 'uncertain_issue':
        invoice['issues'] = [{'field': '/taxes/0/rate_percent', 'kind': 'ambiguous',
                              'raw_text': '10 or 21', 'candidates': ['10', '21']}]
    outcome = fixtures.make_outcome(invoice)
    if mode == 'unlinked':
        outcome['evidence'].pop('/taxes/0/rate_percent')
    elif mode == 'bad_reference':
        outcome['evidence']['/taxes/0/rate_percent'] = [{'page': 1, 'reference_ids': ['not-present']}]
    elif mode == 'uncertain_block':
        outcome['reading']['pages'][0]['blocks'][0]['uncertainties'] = [
            {'kind': 'uncertain', 'raw_text': '10 or 21', 'description': 'Uncertain tax rate'}]
    _, result = run([vat_rule()], outcome=outcome)
    assert result['rule_results'][0]['status'] == 'BLOCKED'
    assert 'VAT_AMOUNT_MISMATCH' not in result['rule_results'][0]['reason_codes']
    assert not result['rule_results'][0]['complete']


@pytest.mark.parametrize('taxes', [
    [],
    [{'label': 'IVA', 'rate_percent': '21', 'amount': '10'}, {'label': 'IVA', 'rate_percent': '21', 'amount': '11'}],
    [{'label': 'IVA', 'rate_percent': '21', 'amount': '10.5'}, {'label': 'IVA', 'rate_percent': '10', 'amount': '5'}],
    [{'label': 'IRPF', 'rate_percent': '15', 'amount': '15'}],
    [{'label': 'unknown tax', 'rate_percent': '21', 'amount': '21'}],
])
def test_vat_does_not_invent_tax_base_allocations(taxes):
    invoice = vat_invoice()
    invoice['taxes'] = taxes
    _, result = run([vat_rule()], invoice=invoice)
    assert result['rule_results'][0]['status'] == 'BLOCKED'
    assert 'VAT_BASE_ALLOCATION_UNAVAILABLE' in result['rule_results'][0]['reason_codes']


@pytest.mark.parametrize('rate', ['NaN', 'Infinity', '1e1'])
def test_nonfinite_or_noncanonical_rate_fails_input_contract(rate):
    with pytest.raises(dc.ContextError):
        run([vat_rule()], invoice=vat_invoice(rate=rate))


def test_forged_rate_fact_fails_reconstruction():
    bundle, _ = run([vat_rule()])
    forged = copy.deepcopy(bundle)
    forged.context['fields']['invoice.taxes.0.rate_percent']['value'] = '10'
    forged.context['context_id'] = 'dc_' + digest(canonical_bytes({key: value for key, value in forged.context.items() if key != 'context_id'}))
    with pytest.raises(dc.ContextError, match='reconstruction'):
        validate_execution_context(forged)


def test_rate_field_is_numeric_for_structured_conditions():
    check = structured([{'field': 'invoice.taxes.0.rate_percent', 'op': '>',
                         'value': {'literal': {'type': 'decimal', 'value': '10'}}}])
    check['condition']['else'] = 'FAIL'
    _, result = run([check], invoice=vat_invoice(rate='2'))
    assert result['rule_results'][0]['status'] == 'VIOLATED'


def balanced_rules():
    from rules_ingestion.loader import LoadResult
    from rules_ingestion.merge import build_merged
    from rules_ingestion.store import build_ruleset

    profile = yaml.safe_load((Path(__file__).resolve().parents[1] / 'rules_ingestion' / 'profiles' / 'balanced.yaml').read_bytes())
    return build_merged(build_ruleset(LoadResult(), [], profile, generated_at=fixtures.CAPTURED), None)['rules']


def test_real_balanced_profile_completes_with_authoritative_inputs():
    invoice, snapshots = identity()
    _, result = run(balanced_rules(), invoice=invoice, snapshots=snapshots)
    assert result['preliminary_decision'] == 'PAGAR'
    assert result['completeness']['evaluation_complete']
    assert result['completeness']['approval_eligible']
    assert all(output['status'] == 'PASS' for output in result['rule_results'])


@pytest.mark.parametrize('missing', ['active', 'order_currency', 'history'])
def test_balanced_profile_still_requires_authoritative_data(missing):
    invoice, snapshots = identity()
    if missing == 'active':
        snapshots['suppliers'].payload['records'][0].pop('active')
    elif missing == 'order_currency':
        snapshots['orders'].payload['records'][0].pop('currency')
    else:
        snapshots['processed'] = history(rows=[], availability='partial', complete=False)
    _, result = run(balanced_rules(), invoice=invoice, snapshots=snapshots)
    assert result['preliminary_decision'] == 'ESCALAR'
    assert not result['completeness']['evaluation_complete']
    assert all(output['status'] != 'UNSUPPORTED' for output in result['rule_results'])

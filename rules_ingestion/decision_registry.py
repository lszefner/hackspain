from __future__ import annotations

REGISTRY_VERSION = "decision-fields/1"
ADAPTER_VERSION = "decision-adapter/1"

ALIASES = {
    "invoice_number": "invoice.number",
    "vendor_id": "supplier.id",
    "nif": "invoice.supplier_tax_id",
    "iban": "invoice.iban",
    "pedido": "invoice.order_reference",
    "date": "invoice.issue_date",
    "fecha": "invoice.issue_date",
    "currency": "invoice.currency",
    "base": "invoice.taxable_base",
    "total": "invoice.total",
    "amount": "invoice.total",
    "importe": "invoice.total",
    "iva": "invoice.vat_amount",
    "line_items": "invoice.line_amounts",
    "master.nif": "supplier.tax_id",
    "master.iban": "supplier.iban",
    "ciudad": "supplier.city",
}

FIELD_TYPES = {
    "invoice.document_type": "document_type",
    "invoice.number": "string",
    "invoice.supplier_tax_id": "string",
    "invoice.iban": "string",
    "invoice.order_reference": "string",
    "invoice.issue_date": "date",
    "invoice.currency": "currency",
    "invoice.taxable_base": "decimal",
    "invoice.total": "decimal",
    "invoice.vat_amount": "decimal",
    "invoice.line_amounts": "decimal_list",
    "supplier.id": "string",
    "supplier.tax_id": "string",
    "supplier.iban": "string",
    "supplier.city": "string",
    "supplier.active": "boolean",
    "supplier.payment_terms_days": "nonnegative_integer",
    "order.id": "string",
    "order.supplier_id": "string",
    "order.total": "decimal",
    "order.currency": "currency",
    "erp.order_payment_status": "order_payment_status",
    "history.processed_matches": "source_ref_list",
    "history.approved_matches": "source_ref_list",
    "history.paid_matches": "source_ref_list",
}

SOURCE_AUTHORITY = {
    "invoice": (),
    "reading": (),
    "evidence": (),
    "extraction_checks": (),
    "extraction_auxiliary": (),
    "master_workbook": (),
    "source_mapping": (),
    "supplier_master": ("supplier.identity", "supplier.bank_details", "supplier.payment_terms", "supplier.active"),
    "order_master": ("order.identity", "order.amount", "order.currency"),
    "erp": ("order.payment_status",),
    "history": ("history.processed", "history.approved", "history.paid"),
    "ruleset": ("business_rules",),
}

FIELD_AUTHORITY = {
    "supplier.id": "supplier.identity",
    "supplier.tax_id": "supplier.identity",
    "supplier.iban": "supplier.bank_details",
    "supplier.city": "supplier.identity",
    "supplier.active": "supplier.active",
    "supplier.payment_terms_days": "supplier.payment_terms",
    "order.id": "order.identity",
    "order.supplier_id": "order.identity",
    "order.total": "order.amount",
    "order.currency": "order.currency",
    "erp.order_payment_status": "order.payment_status",
    "history.processed_matches": "history.processed",
    "history.approved_matches": "history.approved",
    "history.paid_matches": "history.paid",
}

CHECK_FUNCTIONS = {
    "VENDOR": "check_vendor",
    "DUPLICATES": "check_duplicates",
    "AMOUNT": "check_amount",
    "AUTHORIZATION": "check_authorization",
    "DATES": "check_dates",
    "MISSING": "check_missing",
}

BASE_DEPENDENCIES = {
    "VENDOR": ("supplier.id", "invoice.supplier_tax_id", "supplier.tax_id"),
    "DUPLICATES": ("invoice.number", "supplier.id", "invoice.total", "invoice.issue_date", "invoice.currency", "history.processed_matches"),
    "AMOUNT": ("invoice.currency",),
    "AUTHORIZATION": ("invoice.total", "invoice.currency"),
    "DATES": ("invoice.issue_date",),
    "MISSING": (),
}

FLAG_DEPENDENCIES = {
    "VENDOR": {
        "require_iban_match": (True, ("invoice.iban", "supplier.iban")),
        "require_nif_in_master": (True, ("invoice.supplier_tax_id", "supplier.tax_id")),
        "require_active": (False, ("supplier.active",)),
        "check_nif_control_digit": (False, ("invoice.supplier_tax_id",)),
    },
    "DUPLICATES": {
        "require_erp_pending": (True, ("invoice.order_reference", "order.id", "order.supplier_id", "erp.order_payment_status")),
    },
    "AMOUNT": {
        "check_line_items_sum": (True, ("invoice.line_amounts", "invoice.taxable_base")),
        "check_total_is_base_plus_iva": (True, ("invoice.taxable_base", "invoice.vat_amount", "invoice.total")),
        "check_matches_pedido": (True, ("invoice.total", "invoice.order_reference", "supplier.id", "order.id", "order.supplier_id", "order.total", "order.currency")),
        "check_iva": (False, ("invoice.vat_amount",)),
    },
    "AUTHORIZATION": {},
    "DATES": {
        "allow_future": (False, ()),
        "enforce_payment_terms": (True, ("supplier.id", "supplier.payment_terms_days")),
    },
    "MISSING": {},
}

UNIMPLEMENTED_FLAGS = {
    "VENDOR": ("require_active", "check_nif_control_digit"),
    "AMOUNT": ("check_iva",),
}

OTHER_PARAMETERS = {
    "VENDOR": (),
    "DUPLICATES": ("hard_key", "soft_key", "soft_duplicate_verdict"),
    "AMOUNT": ("tolerance_eur", "allowed_currencies", "order_currency_policy"),
    "AUTHORIZATION": ("escalate_above_eur",),
    "DATES": (),
    "MISSING": ("required_fields",),
}

DEFAULT_REQUIRED_FIELDS = ("nif", "iban", "pedido", "importe", "iva", "fecha")
SUPPORTED_PRECEDENCE = ("NO_PAGAR", "ESCALAR", "PAGAR")
VAT_LABEL_PATTERN = r"^(?:IVA|VAT)(?:\s+\d+(?:[.,]\d+)?\s*%)?$"
WITHHOLDING_LABEL_PATTERN = r"^(?:IRPF|RETENCION|RETENCIÓN|WITHHOLDING)(?:\s+\d+(?:[.,]\d+)?\s*%)?$"

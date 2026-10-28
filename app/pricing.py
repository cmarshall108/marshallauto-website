"""Payment and out-the-door estimates.

These figures are shopping aids, not credit offers, approvals, or quotes.
North Carolina highway-use tax is 3% of the total value on the bill of sale.
County and municipal property tax,
tag, title, and notary amounts vary and are shown as configurable estimates.
"""
from decimal import Decimal, ROUND_HALF_UP

from flask import current_app

MONEY = Decimal('0.01')
NC_HIGHWAY_USE_TAX_RATE = Decimal('0.03')


def money(value):
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _config_money(key, default):
    return money(current_app.config.get(key, default))


def highway_use_tax(price):
    """NC 3% highway use tax on the total bill-of-sale value."""
    return (money(price) * NC_HIGHWAY_USE_TAX_RATE).quantize(MONEY)


def out_the_door_estimate(price):
    """Estimated cash due before any trade, down payment, or lender fees."""
    selling_price = money(price)
    tax = highway_use_tax(selling_price)
    title = _config_money('ESTIMATE_TITLE_FEE', '66.75')
    tag = _config_money('ESTIMATE_LICENSE_PLATE_FEE', '46.25')
    notary = _config_money('ESTIMATE_NOTARY_FEE', '15.00')
    doc = _config_money('ESTIMATE_DOC_FEE', '0')
    property_tax = _config_money('ESTIMATE_PROPERTY_TAX', '0')
    fees = title + tag + notary + doc
    return {
        'selling_price': selling_price,
        'highway_use_tax': tax,
        'title_fee': title,
        'license_plate_fee': tag,
        'notary_fee': notary,
        'doc_fee': doc,
        'estimated_property_tax': property_tax,
        'fees_subtotal': fees,
        'estimated_total': selling_price + tax + fees + property_tax,
        'tax_rate_label': '3% North Carolina highway use tax on the bill-of-sale value',
    }


def monthly_payment(principal, annual_rate, months):
    """Standard amortizing payment. Returns None for an invalid term."""
    principal = money(principal)
    months = int(months)
    if principal <= 0 or months <= 0:
        return None
    rate = Decimal(str(annual_rate or 0)) / Decimal('100') / Decimal('12')
    if rate == 0:
        return (principal / Decimal(months)).quantize(MONEY, rounding=ROUND_HALF_UP)
    factor = (1 + rate) ** months
    payment = principal * rate * factor / (factor - 1)
    return payment.quantize(MONEY, rounding=ROUND_HALF_UP)


def payment_examples(price):
    """Illustrative examples only. They are not offers from any lender."""
    amount = money(price)
    examples = []
    for months, rate in ((36, '9.99'), (48, '9.99'), (60, '11.99'), (72, '12.99')):
        payment = monthly_payment(amount, rate, months)
        if payment is None:
            continue
        examples.append({
            'months': months,
            'rate': Decimal(rate),
            'payment': payment,
            'amount_financed': amount,
        })
    return examples

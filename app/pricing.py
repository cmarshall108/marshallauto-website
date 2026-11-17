"""Payment illustrations and the bill-of-sale split.

These figures are shopping aids, not credit offers, approvals, or quotes.
The public advertised price is the out-the-door price. Marshall Auto does not
add dealer or other extra fees to it. Title, plate, and highway-use tax are
backed out of that price for the bill of sale; they are not added on top.
"""
from decimal import Decimal, ROUND_HALF_UP

from flask import current_app

MONEY = Decimal('0.01')
NC_HIGHWAY_USE_TAX_RATE = Decimal('0.03')
# Defaults: $66.75 title + $46.25 plate. The handwritten out-the-door formula
# treats that $113 as fees that are not part of the bill-of-sale price.
DEFAULT_TITLE_FEE = Decimal('66.75')
DEFAULT_LICENSE_PLATE_FEE = Decimal('46.25')


def money(value):
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _config_money(key, default):
    return money(current_app.config.get(key, default))


def highway_use_tax(price):
    """NC 3% highway use tax on the bill-of-sale price, not the out-the-door price."""
    return (money(price) * NC_HIGHWAY_USE_TAX_RATE).quantize(MONEY)


def government_fees():
    """Title and plate fees included in the out-the-door price.

    These stay off the bill of sale. Defaults add to $113.00.
    """
    title = _config_money('ESTIMATE_TITLE_FEE', DEFAULT_TITLE_FEE)
    plate = _config_money('ESTIMATE_LICENSE_PLATE_FEE', DEFAULT_LICENSE_PLATE_FEE)
    return {
        'title_fee': title,
        'license_plate_fee': plate,
        'fees': (title + plate).quantize(MONEY),
    }


def split_otd_sale_price(otd_price):
    """Split an out-the-door price into sale price, tax, and government fees.

    Out-the-door = 1.03 * bill-of-sale price + title and plate fees.
    The bill-of-sale price is (out-the-door - fees) / 1.03. Highway-use tax is
    3% of that sale price. Fees are not part of the sale price. The three parts
    add back to the out-the-door price.

    If the out-the-door price does not cover the fees, the full amount is the
    sale price and no fees or tax are invented.
    """
    total = money(otd_price)
    fees = government_fees()
    flat_fees = fees['fees']
    if total < flat_fees:
        return {
            'otd_price': total,
            'bill_of_sale_price': total,
            'agreed_vehicle_price': total,
            'sales_tax': money(0),
            'title_fee': money(0),
            'license_plate_fee': money(0),
            'fees': money(0),
        }
    taxable = total - flat_fees
    rate = Decimal('1') + NC_HIGHWAY_USE_TAX_RATE
    sale_price = (taxable / rate).quantize(MONEY, rounding=ROUND_HALF_UP)
    tax = (taxable - sale_price).quantize(MONEY)
    return {
        'otd_price': total,
        'bill_of_sale_price': sale_price,
        'agreed_vehicle_price': sale_price,
        'sales_tax': tax,
        'title_fee': fees['title_fee'],
        'license_plate_fee': fees['license_plate_fee'],
        'fees': flat_fees,
    }


def out_the_door_estimate(price):
    """Legacy government-charge breakdown. Not shown as the customer price.

    The advertised price is the out-the-door price. Do not add this total on
    public vehicle pages.
    """
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

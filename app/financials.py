"""Administrator financial charts built from sales and rebuild-cost records.

Closed-deal profit is recognized in the Eastern Time month a vehicle sold.
The advertised and recorded sale amount is out-the-door, so kept vehicle
revenue is that amount minus the 3% highway-use tax. A saved receipt supplies
the split; otherwise the same split used by sale receipts is estimated.

Prep costs reduce profit in the sale month even when the receipt is older.
Cash-expense charts use each cost item's purchase date and include cars that
have not sold. Acquisition cash is estimated from the date the vehicle was
added, because a separate purchase date is not stored.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import noload, selectinload

from app.models import RebuildCostEntry, Vehicle, VehicleSaleReceipt
from app.pricing import split_otd_sale_price
from app.utils import DEALERSHIP_TIMEZONE, dealership_today

DEALER_TZ = DEALERSHIP_TIMEZONE
ZERO = Decimal('0.00')
CENT = Decimal('0.01')
POINT = Decimal('0.1')

RANGE_CHOICES = (
    ('month', 'Per month'),
    ('6m', 'Last 6 months'),
    ('12m', 'Last 12 months'),
    ('24m', 'Last 24 months'),
    ('ytd', 'Year to date'),
    ('all', 'All recorded months'),
)
_RANGE_MONTHS = {'6m': 6, '12m': 12, '24m': 24}
_MAX_ALL_MONTHS = 48
_MONTH_CHOICE_SPAN = 36

CATEGORY_LABELS = {
    'parts': 'Parts & Materials',
    'labor': 'Labor',
    'service': 'Service / Repair',
    'supplies': 'Shop Supplies',
    'transport': 'Transport',
    'fees': 'Fees & Taxes',
    'other': 'Other',
}
CATEGORY_COLORS = {
    'parts': '#0d6efd',
    'labor': '#6f42c1',
    'service': '#fd7e14',
    'supplies': '#20c997',
    'transport': '#0dcaf0',
    'fees': '#ffc107',
    'other': '#6c757d',
}
PAYMENT_LABELS = {
    'cash': 'Cash',
    'cashiers_check': "Cashier's Check",
    'certified_check': 'Certified Check',
    'personal_check': 'Personal Check',
    'financing': 'Financing',
    'wire': 'Wire / Bank Transfer',
    'other': 'Other / Mixed Payment',
    'not_recorded': 'Not recorded',
}


def money(value):
    if value is None or value == '':
        return ZERO
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _json_money(value):
    return float(money(value))


def _pct(part, whole):
    if not whole:
        return None
    return (part / whole * Decimal('100')).quantize(CENT, rounding=ROUND_HALF_UP)


def _points(current, prior):
    if current is None or prior is None:
        return None
    return (current - prior).quantize(POINT, rounding=ROUND_HALF_UP)


def _delta(current, prior):
    if current is None or prior is None or prior == 0:
        return None
    return ((current - prior) / abs(prior) * Decimal('100')).quantize(
        POINT, rounding=ROUND_HALF_UP,
    )


def _local_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(DEALER_TZ).date()
    return value


def _month_start(value):
    local = _local_date(value)
    if local is None:
        return None
    return date(local.year, local.month, 1)


def _add_months(month_start, delta):
    index = month_start.year * 12 + (month_start.month - 1) + delta
    year, month_index = divmod(index, 12)
    return date(year, month_index + 1, 1)


def _iter_months(start, end):
    cursor = start
    while cursor <= end:
        yield cursor
        cursor = _add_months(cursor, 1)


def _month_label(month_start):
    return month_start.strftime('%b %Y')


def _period_label(start, end):
    if start == end:
        return _month_label(start)
    return f'{_month_label(start)} – {_month_label(end)}'


def _line_total(entry):
    return money(entry.line_total)


def _category_label(key):
    return CATEGORY_LABELS.get(key, (key or 'other').replace('_', ' ').title())


def _category_color(key, index):
    return CATEGORY_COLORS.get(key, CATEGORY_COLORS['other'] if index == 0 else '#495057')


def _payment_label(key):
    return PAYMENT_LABELS.get(key or 'not_recorded', (key or 'Not recorded').replace('_', ' ').title())


def _sale_instant(vehicle):
    return vehicle.sold_at or vehicle.updated_at or vehicle.created_at


def _load_vehicles():
    return (
        Vehicle.query.options(
            noload(Vehicle.images),
            noload(Vehicle.service_records),
            noload(Vehicle.carfax_reports),
            selectinload(Vehicle.rebuild_cost_entries).noload(RebuildCostEntry.receipts),
            selectinload(Vehicle.sale_receipt).selectinload(VehicleSaleReceipt.charges),
        )
        .all()
    )


def _receipt_amounts(receipt):
    if receipt is None:
        return None
    other = sum((money(charge.amount) for charge in receipt.charges), ZERO)
    return {
        'vehicle_price': money(receipt.vehicle_price),
        'sales_tax': money(receipt.sales_tax),
        'highway_use_tax': money(receipt.highway_use_tax),
        'title_fee': money(receipt.title_registration_fee),
        'dealer_fees': money(receipt.document_fee) + money(receipt.warranty_fee) + money(receipt.tags_fee),
        'other_charges': other,
        'trade_allow': money(receipt.trade_allow),
        'down_payment': money(receipt.down_payment),
        'credits': money(receipt.credits),
        'amount_received': money(receipt.amount_received),
        'document_fee': money(receipt.document_fee),
        'warranty_fee': money(receipt.warranty_fee),
        'tags_fee': money(receipt.tags_fee),
    }


def _kept_revenue(vehicle):
    """Return OTD, remitted tax, kept vehicle revenue, and whether tax was estimated."""
    receipt = _receipt_amounts(vehicle.sale_receipt)
    if receipt is not None and (receipt['vehicle_price'] or receipt['sales_tax']):
        tax = receipt['sales_tax']
        if receipt['sales_tax'] == 0 and receipt['highway_use_tax'] > 0:
            tax = receipt['highway_use_tax']
        return {
            'otd': receipt['vehicle_price'] + receipt['sales_tax'],
            'tax': tax,
            'revenue': receipt['vehicle_price'],
            'estimated': False,
            'receipt': receipt,
        }
    if vehicle.sold_price is None:
        return {'otd': None, 'tax': ZERO, 'revenue': None, 'estimated': False, 'receipt': receipt}
    split = split_otd_sale_price(vehicle.sold_price)
    return {
        'otd': split['otd_price'],
        'tax': split['sales_tax'],
        'revenue': split['agreed_vehicle_price'],
        'estimated': True,
        'receipt': receipt,
    }


def _blank_month(month):
    return {
        'month': month,
        'label': _month_label(month),
        'units': 0,
        'priced_units': 0,
        'otd': ZERO,
        'tax': ZERO,
        'revenue': ZERO,
        'acquisition': ZERO,
        'prep': ZERO,
        'gross_profit': ZERO,
        'profit_before_tax': ZERO,
        'dealer_fees': ZERO,
        'contribution': ZERO,
        'expense_spend': ZERO,
        'acquisition_added': ZERO,
        'vehicles_added': 0,
        'days': [],
        'expense_by_category': defaultdict(lambda: ZERO),
    }


def _finalize_month(row):
    row['cogs'] = row['acquisition'] + row['prep']
    row['margin'] = _pct(row['gross_profit'], row['revenue'])
    row['avg_profit'] = (
        (row['gross_profit'] / row['priced_units']).quantize(CENT, rounding=ROUND_HALF_UP)
        if row['priced_units'] else None
    )
    row['avg_sale'] = (
        (row['revenue'] / row['priced_units']).quantize(CENT, rounding=ROUND_HALF_UP)
        if row['priced_units'] else None
    )
    row['avg_days'] = (
        round(sum(row['days']) / len(row['days'])) if row['days'] else None
    )
    row.pop('days', None)
    row.pop('expense_by_category', None)
    return row


def _sum_months(rows):
    total = _blank_month(date(2000, 1, 1))
    for row in rows:
        for key in (
            'units', 'priced_units', 'vehicles_added',
        ):
            total[key] += row[key]
        for key in (
            'otd', 'tax', 'revenue', 'acquisition', 'prep', 'gross_profit',
            'profit_before_tax', 'dealer_fees', 'contribution', 'expense_spend',
            'acquisition_added',
        ):
            total[key] += row[key]
    return _finalize_month(total)


def _group_rows(items, label_key, value_key='gross_profit', limit=8):
    rows = sorted(items, key=lambda item: item[value_key], reverse=True)
    if len(rows) <= limit:
        return rows
    kept = rows[:limit - 1]
    rest = rows[limit - 1:]
    other = {
        label_key: 'Other',
        'units': sum(item['units'] for item in rest),
        'revenue': sum((item['revenue'] for item in rest), ZERO),
        'gross_profit': sum((item['gross_profit'] for item in rest), ZERO),
    }
    other['margin'] = _pct(other['gross_profit'], other['revenue'])
    return kept + [other]


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return round((ordered[mid - 1] + ordered[mid]) / 2)


def _parse_month(value, fallback):
    """Parse YYYY-MM or YYYY-MM-DD into the first day of that month."""
    if value is None:
        return fallback
    text = str(value).strip()
    if len(text) < 7 or text[4] != '-':
        return fallback
    try:
        year = int(text[0:4])
        month = int(text[5:7])
        if year < 2000 or year > 2100 or month < 1 or month > 12:
            return fallback
        return date(year, month, 1)
    except ValueError:
        return fallback


def _month_choices(today, earliest=None, selected=None):
    """Newest-first month picker options covering recent activity."""
    current = date(today.year, today.month, 1)
    start = _add_months(current, -(_MONTH_CHOICE_SPAN - 1))
    if earliest is not None and earliest < start:
        start = max(earliest, _add_months(current, -(_MAX_ALL_MONTHS - 1)))
    if selected is not None and selected < start:
        start = selected
    choices = [
        (month.strftime('%Y-%m'), _month_label(month))
        for month in _iter_months(start, current)
    ]
    choices.reverse()
    return choices


def _resolve_window(range_key, today, earliest, selected_month=None):
    current = date(today.year, today.month, 1)
    truncated = False
    if range_key == 'month':
        target = selected_month or current
        if target > current:
            target = current
        return 'month', target, target, False
    if range_key == 'ytd':
        start = date(today.year, 1, 1)
        key = 'ytd'
        end = current
    elif range_key == 'all':
        start = earliest or _add_months(current, -5)
        if start > current:
            start = current
        if len(list(_iter_months(start, current))) < 6:
            start = _add_months(current, -5)
        if len(list(_iter_months(start, current))) > _MAX_ALL_MONTHS:
            start = _add_months(current, -(_MAX_ALL_MONTHS - 1))
            truncated = True
        key = 'all'
        end = current
    else:
        key = range_key if range_key in _RANGE_MONTHS else '12m'
        start = _add_months(current, -(_RANGE_MONTHS[key] - 1))
        end = current
    return key, start, end, truncated


def build_financial_dashboard(args=None, today=None):
    """Aggregate monthly profit, costs, and cash expenses for the admin report."""
    args = args or {}
    today = today or dealership_today()
    vehicles = _load_vehicles()

    deals = []
    expenses = []
    undated_expenses = []
    quality = []
    earliest_dates = []
    inventory = {
        'units': 0,
        'available': 0,
        'pending': 0,
        'coming_soon': 0,
        'with_cost_basis': 0,
        'missing_acquisition': 0,
        'acquisition': ZERO,
        'prep': ZERO,
        'asking_otd': ZERO,
        'estimated_revenue': ZERO,
        'estimated_tax': ZERO,
        'estimated_profit': ZERO,
        'estimated_profit_count': 0,
    }
    priced_sales = 0

    for vehicle in vehicles:
        prep_by_category = defaultdict(lambda: ZERO)
        prep_total = ZERO
        for entry in vehicle.rebuild_cost_entries:
            amount = _line_total(entry)
            category = entry.category or 'other'
            prep_total += amount
            prep_by_category[category] += amount
            item = {
                'id': entry.id,
                'vehicle_id': vehicle.id,
                'vehicle': vehicle.title,
                'category': category,
                'label': _category_label(category),
                'description': entry.description,
                'vendor': (entry.vendor or '').strip() or 'Not recorded',
                'amount': amount,
                'month': _month_start(entry.purchase_date),
                'purchased_on': entry.purchase_date,
            }
            if item['month'] is None:
                undated_expenses.append(item)
            else:
                expenses.append(item)
                earliest_dates.append(item['month'])

        acquisition = vehicle.acquisition_cost
        acquisition_missing = acquisition is None
        acquisition_amount = money(acquisition)
        if vehicle.created_at:
            earliest_dates.append(_month_start(vehicle.created_at))

        if vehicle.status != 'sold':
            inventory['units'] += 1
            if vehicle.status == 'pending':
                inventory['pending'] += 1
            elif vehicle.is_coming_soon:
                inventory['coming_soon'] += 1
            else:
                inventory['available'] += 1
            if acquisition_missing:
                inventory['missing_acquisition'] += 1
            inventory['acquisition'] += acquisition_amount
            inventory['prep'] += prep_total
            has_basis = (not acquisition_missing) or prep_total > 0
            if has_basis:
                inventory['with_cost_basis'] += 1
            if vehicle.display_price is not None and has_basis:
                split = split_otd_sale_price(vehicle.display_price)
                inventory['asking_otd'] += split['otd_price']
                inventory['estimated_tax'] += split['sales_tax']
                inventory['estimated_revenue'] += split['agreed_vehicle_price']
                inventory['estimated_profit'] += split['agreed_vehicle_price'] - acquisition_amount - prep_total
                inventory['estimated_profit_count'] += 1
            continue

        kept = _kept_revenue(vehicle)
        sale_at = _sale_instant(vehicle)
        sale_month = _month_start(sale_at)
        if sale_month:
            earliest_dates.append(sale_month)
        receipt = kept['receipt'] or {}
        dealer_fees = receipt.get('dealer_fees', ZERO) if receipt else ZERO
        revenue = kept['revenue']
        otd = kept['otd']
        profit = None
        before_tax = None
        if revenue is not None:
            profit = (revenue - acquisition_amount - prep_total).quantize(CENT, rounding=ROUND_HALF_UP)
            before_tax = ((otd or ZERO) - acquisition_amount - prep_total).quantize(
                CENT, rounding=ROUND_HALF_UP,
            )
            priced_sales += 1
        days = None
        if vehicle.created_at and sale_at:
            days = max((sale_at - vehicle.created_at).days, 0)
        salesperson = (vehicle.salesperson or (vehicle.sale_receipt.salesman if vehicle.sale_receipt else '') or '').strip() or 'Unassigned'
        payment_key = vehicle.payment_method or (vehicle.sale_receipt.payment_method if vehicle.sale_receipt else None) or 'not_recorded'
        deal = {
            'id': vehicle.id,
            'title': vehicle.title,
            'stock_number': vehicle.stock_number,
            'sold_on': _local_date(sale_at),
            'month': sale_month,
            'otd': otd,
            'tax': kept['tax'],
            'revenue': revenue,
            'acquisition': acquisition_amount,
            'prep': prep_total,
            'prep_by_category': dict(prep_by_category),
            'gross_profit': profit,
            'profit_before_tax': before_tax,
            'margin': _pct(profit, revenue) if profit is not None else None,
            'dealer_fees': dealer_fees,
            'contribution': (profit + dealer_fees) if profit is not None else None,
            'salesperson': salesperson,
            'payment_key': payment_key,
            'payment_method': _payment_label(payment_key),
            'make': vehicle.make or 'Unknown',
            'days': days,
            'tax_estimated': kept['estimated'],
            'acquisition_missing': acquisition_missing,
            'sold_at_missing': vehicle.sold_at is None,
            'receipt': receipt,
        }
        deals.append(deal)
        if revenue is None:
            quality.append({
                'kind': 'missing_price',
                'vehicle_id': vehicle.id,
                'title': vehicle.title,
                'detail': 'Sold without an amount received or a sale receipt.',
            })
        if acquisition_missing:
            quality.append({
                'kind': 'missing_acquisition',
                'vehicle_id': vehicle.id,
                'title': vehicle.title,
                'detail': 'Sold without an acquisition cost. Profit treats that cost as $0.00.',
            })
        if vehicle.sold_at is None:
            quality.append({
                'kind': 'missing_sold_at',
                'vehicle_id': vehicle.id,
                'title': vehicle.title,
                'detail': 'No sale date was stored. The report uses the last update time.',
            })
        if (
            vehicle.sold_price is not None
            and receipt
            and abs(money(vehicle.sold_price) - (receipt['vehicle_price'] + receipt['sales_tax'])) >= Decimal('1.00')
        ):
            quality.append({
                'kind': 'price_mismatch',
                'vehicle_id': vehicle.id,
                'title': vehicle.title,
                'detail': 'Amount received does not match the receipt out-the-door total. Profit uses the receipt.',
            })
        if receipt and receipt.get('highway_use_tax', ZERO) > 0 and receipt.get('sales_tax', ZERO) == 0:
            quality.append({
                'kind': 'legacy_tax',
                'vehicle_id': vehicle.id,
                'title': vehicle.title,
                'detail': 'Receipt has highway-use tax but no sales-tax split. Kept revenue is the receipt vehicle price.',
            })

    undated_total = sum((item['amount'] for item in undated_expenses), ZERO)
    for item in undated_expenses[:8]:
        quality.append({
            'kind': 'undated_expense',
            'vehicle_id': item['vehicle_id'],
            'title': item['vehicle'],
            'detail': (
                f"{item['description']} (${item['amount']:,.2f}) has no purchase date, "
                'so it is omitted from monthly expense bars. It still reduces profit when that car sells.'
            ),
        })

    earliest = min(earliest_dates) if earliest_dates else None
    current_month = date(today.year, today.month, 1)
    selected_month = _parse_month(args.get('month'), current_month)
    range_key, start, end, truncated = _resolve_window(
        args.get('range'), today, earliest, selected_month=selected_month,
    )
    if range_key == 'month':
        selected_month = start
    months = [_blank_month(month) for month in _iter_months(start, end)]
    by_month = {row['month']: row for row in months}
    span = len(months)
    prior_start = _add_months(start, -span)
    prior_end = _add_months(start, -1)
    prior_months = [_blank_month(month) for month in _iter_months(prior_start, prior_end)]
    prior_by_month = {row['month']: row for row in prior_months}
    month_choices = _month_choices(today, earliest=earliest, selected=selected_month)
    is_single_month = range_key == 'month'

    def _bucket(month):
        if month in by_month:
            return by_month[month]
        if month in prior_by_month:
            return prior_by_month[month]
        return None

    window_deals = []
    for deal in deals:
        bucket = _bucket(deal['month'])
        if bucket is None or deal['revenue'] is None:
            if bucket is not None and deal['revenue'] is None:
                bucket['units'] += 1
            continue
        if deal['month'] in by_month:
            window_deals.append(deal)
        bucket['units'] += 1
        bucket['priced_units'] += 1
        bucket['otd'] += deal['otd'] or ZERO
        bucket['tax'] += deal['tax']
        bucket['revenue'] += deal['revenue']
        bucket['acquisition'] += deal['acquisition']
        bucket['prep'] += deal['prep']
        bucket['gross_profit'] += deal['gross_profit']
        bucket['profit_before_tax'] += deal['profit_before_tax']
        bucket['dealer_fees'] += deal['dealer_fees']
        bucket['contribution'] += deal['contribution']
        if deal['days'] is not None:
            bucket['days'].append(deal['days'])

    for item in expenses:
        bucket = _bucket(item['month'])
        if bucket is None:
            continue
        bucket['expense_spend'] += item['amount']
        bucket['expense_by_category'][item['category']] += item['amount']

    for vehicle in vehicles:
        created = _month_start(vehicle.created_at)
        bucket = _bucket(created)
        if bucket is None:
            continue
        bucket['vehicles_added'] += 1
        if vehicle.acquisition_cost is not None:
            bucket['acquisition_added'] += money(vehicle.acquisition_cost)

    category_keys = []
    seen_categories = set()
    for key in list(CATEGORY_LABELS) + sorted({
        key
        for row in months
        for key in row['expense_by_category']
    }):
        if key not in seen_categories:
            seen_categories.add(key)
            if any(row['expense_by_category'].get(key, ZERO) for row in months):
                category_keys.append(key)

    category_series = {
        key: [row['expense_by_category'].get(key, ZERO) for row in months]
        for key in category_keys
    }
    finalized = [_finalize_month(row) for row in months]
    prior_final = [_finalize_month(row) for row in prior_months]
    totals = _sum_months(finalized)
    prior = _sum_months(prior_final)
    totals['margin'] = _pct(totals['gross_profit'], totals['revenue'])
    prior['margin'] = _pct(prior['gross_profit'], prior['revenue'])

    window_days = [deal['days'] for deal in window_deals if deal['days'] is not None]
    below_cost = sum(1 for deal in window_deals if deal['gross_profit'] is not None and deal['gross_profit'] < 0)
    estimated_tax_count = sum(1 for deal in window_deals if deal['tax_estimated'])
    receipt_count = sum(1 for deal in window_deals if not deal['tax_estimated'])

    cash_categories = []
    for index, key in enumerate(category_keys):
        series = category_series[key]
        amount = sum(series, ZERO)
        cash_categories.append({
            'key': key,
            'label': _category_label(key),
            'color': _category_color(key, index),
            'amount': amount,
            'share': _pct(amount, totals['expense_spend']),
            'data': [_json_money(amount_in_month) for amount_in_month in series],
        })

    sold_mix_amounts = defaultdict(lambda: ZERO)
    sold_mix_amounts['acquisition'] = totals['acquisition']
    for deal in window_deals:
        for key, amount in deal['prep_by_category'].items():
            sold_mix_amounts[key] += amount
    sold_cost_mix = []
    mix_order = ['acquisition'] + [key for key in CATEGORY_LABELS if key in sold_mix_amounts]
    mix_order += [key for key in sold_mix_amounts if key not in mix_order]
    mix_total = sum(sold_mix_amounts.values(), ZERO)
    for index, key in enumerate(mix_order):
        amount = sold_mix_amounts[key]
        if amount <= 0:
            continue
        sold_cost_mix.append({
            'key': key,
            'label': 'Acquisition' if key == 'acquisition' else _category_label(key),
            'color': '#fd7e14' if key == 'acquisition' else _category_color(key, index),
            'amount': amount,
            'share': _pct(amount, mix_total),
        })

    def _collect(key_name):
        grouped = {}
        for deal in window_deals:
            label = deal[key_name]
            row = grouped.setdefault(label, {
                'label': label,
                'units': 0,
                'revenue': ZERO,
                'gross_profit': ZERO,
                'acquisition': ZERO,
                'prep': ZERO,
            })
            row['units'] += 1
            row['revenue'] += deal['revenue'] or ZERO
            row['gross_profit'] += deal['gross_profit'] or ZERO
            row['acquisition'] += deal['acquisition']
            row['prep'] += deal['prep']
        rows = []
        for row in grouped.values():
            row['margin'] = _pct(row['gross_profit'], row['revenue'])
            row['avg_profit'] = (
                (row['gross_profit'] / row['units']).quantize(CENT, rounding=ROUND_HALF_UP)
                if row['units'] else None
            )
            rows.append(row)
        rows.sort(key=lambda item: item['gross_profit'], reverse=True)
        return rows

    salespeople = _collect('salesperson')
    makes = _collect('make')
    payments_grouped = {}
    for deal in window_deals:
        row = payments_grouped.setdefault(deal['payment_key'], {
            'key': deal['payment_key'],
            'label': deal['payment_method'],
            'units': 0,
            'otd': ZERO,
            'revenue': ZERO,
        })
        row['units'] += 1
        row['otd'] += deal['otd'] or ZERO
        row['revenue'] += deal['revenue'] or ZERO
    payments = sorted(payments_grouped.values(), key=lambda item: item['revenue'], reverse=True)

    vendors = {}
    recent_pool = []
    for item in expenses:
        if not (start <= item['month'] <= end):
            continue
        recent_pool.append(item)
        row = vendors.setdefault(item['vendor'], {'label': item['vendor'], 'amount': ZERO, 'count': 0})
        row['amount'] += item['amount']
        row['count'] += 1
    vendor_rows = sorted(vendors.values(), key=lambda item: item['amount'], reverse=True)
    vendor_total = sum((row['amount'] for row in vendor_rows), ZERO)
    for row in vendor_rows:
        row['share'] = _pct(row['amount'], vendor_total)
    recent_expenses = sorted(
        recent_pool,
        key=lambda item: (item['purchased_on'] or date.min, item['id']),
        reverse=True,
    )[:12]

    receipt_summary = {
        'dealer_fees': totals['dealer_fees'],
        'document_fee': ZERO,
        'warranty_fee': ZERO,
        'tags_fee': ZERO,
        'other_charges': ZERO,
        'title_fee': ZERO,
        'highway_use_tax': ZERO,
        'trade_allow': ZERO,
        'down_payment': ZERO,
        'credits': ZERO,
        'amount_received': ZERO,
    }
    for deal in window_deals:
        receipt = deal.get('receipt') or {}
        if not receipt:
            continue
        for key in (
            'document_fee', 'warranty_fee', 'tags_fee', 'other_charges', 'title_fee',
            'highway_use_tax', 'trade_allow', 'down_payment', 'credits', 'amount_received',
        ):
            receipt_summary[key] += receipt.get(key, ZERO)

    deals_sorted = sorted(
        window_deals,
        key=lambda deal: (deal['sold_on'] or date.min, deal['id']),
        reverse=True,
    )
    best_deals = sorted(
        [deal for deal in window_deals if deal['gross_profit'] is not None],
        key=lambda deal: deal['gross_profit'],
        reverse=True,
    )[:5]
    worst_deals = sorted(
        [deal for deal in window_deals if deal['gross_profit'] is not None],
        key=lambda deal: deal['gross_profit'],
    )[:5]

    cumulative = []
    running = ZERO
    for row in finalized:
        running += row['gross_profit']
        cumulative.append(_json_money(running))
    salesperson_chart = _group_rows(salespeople, 'label')

    charts = {
        'labels': [row['label'] for row in finalized],
        'revenue': [_json_money(row['revenue']) for row in finalized],
        'otd': [_json_money(row['otd']) for row in finalized],
        'tax': [_json_money(row['tax']) for row in finalized],
        'acquisition': [_json_money(row['acquisition']) for row in finalized],
        'prep': [_json_money(row['prep']) for row in finalized],
        'cogs': [_json_money(row['cogs']) for row in finalized],
        'gross_profit': [_json_money(row['gross_profit']) for row in finalized],
        'profit_before_tax': [_json_money(row['profit_before_tax']) for row in finalized],
        'contribution': [_json_money(row['contribution']) for row in finalized],
        'dealer_fees': [_json_money(row['dealer_fees']) for row in finalized],
        'expense_spend': [_json_money(row['expense_spend']) for row in finalized],
        'acquisition_added': [_json_money(row['acquisition_added']) for row in finalized],
        'units': [row['units'] for row in finalized],
        'margin': [float(row['margin']) if row['margin'] is not None else None for row in finalized],
        'cumulative_profit': cumulative,
        'expense_categories': [
            {
                'key': item['key'],
                'label': item['label'],
                'color': item['color'],
                'data': item['data'],
            }
            for item in cash_categories
        ],
        'sold_cost_labels': [item['label'] for item in sold_cost_mix],
        'sold_cost_amounts': [_json_money(item['amount']) for item in sold_cost_mix],
        'sold_cost_colors': [item['color'] for item in sold_cost_mix],
        'salesperson_labels': [row['label'] for row in salesperson_chart],
        'salesperson_profit': [_json_money(row['gross_profit']) for row in salesperson_chart],
        'payment_labels': [row['label'] for row in payments],
        'payment_revenue': [_json_money(row['revenue']) for row in payments],
    }

    inventory['capital'] = inventory['acquisition'] + inventory['prep']
    if not inventory['estimated_profit_count']:
        inventory['estimated_profit'] = None

    kpis = {
        'revenue': totals['revenue'],
        'otd': totals['otd'],
        'tax': totals['tax'],
        'acquisition': totals['acquisition'],
        'prep': totals['prep'],
        'cogs': totals['cogs'],
        'gross_profit': totals['gross_profit'],
        'profit_before_tax': totals['profit_before_tax'],
        'margin': totals['margin'],
        'contribution': totals['contribution'],
        'dealer_fees': totals['dealer_fees'],
        'units': totals['units'],
        'priced_units': totals['priced_units'],
        'avg_profit': totals['avg_profit'],
        'avg_sale': totals['avg_sale'],
        'below_cost': below_cost,
        'expense_spend': totals['expense_spend'],
        'undated_expenses': undated_total,
        'acquisition_added': totals['acquisition_added'],
        'vehicles_added': totals['vehicles_added'],
        'avg_days': round(sum(window_days) / len(window_days)) if window_days else None,
        'median_days': _median(window_days),
        'profit_delta': _delta(totals['gross_profit'], prior['gross_profit']),
        'revenue_delta': _delta(totals['revenue'], prior['revenue']),
        'expense_delta': _delta(totals['expense_spend'], prior['expense_spend']),
        'units_delta': _delta(Decimal(totals['units']), Decimal(prior['units'])),
        'margin_delta': _points(totals['margin'], prior['margin']),
        'estimated_tax_count': estimated_tax_count,
        'receipt_count': receipt_count,
    }
    has_data = bool(deals or expenses or undated_expenses or any(
        vehicle.acquisition_cost is not None for vehicle in vehicles
    ) or priced_sales)
    return {
        'range_key': range_key,
        'range_choices': RANGE_CHOICES,
        'month_key': selected_month.strftime('%Y-%m'),
        'month_choices': month_choices,
        'is_single_month': is_single_month,
        'period_label': _period_label(start, end),
        'prior_label': _period_label(prior_start, prior_end),
        'truncated': truncated,
        'has_data': has_data,
        'kpis': kpis,
        'months': finalized,
        'charts': charts,
        'deals': deals_sorted[:40],
        'deals_truncated': max(len(deals_sorted) - 40, 0),
        'deal_count': len(deals_sorted),
        'best_deals': best_deals,
        'worst_deals': worst_deals,
        'salespeople': salespeople,
        'makes': makes[:12],
        'payments': payments,
        'vendors': vendor_rows[:8],
        'recent_expenses': recent_expenses,
        'cash_categories': cash_categories,
        'sold_cost_mix': sold_cost_mix,
        'data_quality': quality[:25],
        'data_quality_extra': max(len(quality) - 25, 0),
        'inventory': inventory,
        'receipts': receipt_summary,
        'undated_expenses': undated_expenses,
    }


def build_financial_summary():
    """Trailing-12-month snapshot for the admin dashboard chart."""
    report = build_financial_dashboard({'range': '12m'})
    current = report['months'][-1] if report['months'] else None
    return {
        'kpis': report['kpis'],
        'current': current,
        'period_label': report['period_label'],
        'has_data': report['has_data'],
        'charts': {
            'labels': report['charts']['labels'],
            'revenue': report['charts']['revenue'],
            'cogs': report['charts']['cogs'],
            'profit': report['charts']['gross_profit'],
            'expense_spend': report['charts']['expense_spend'],
        },
    }

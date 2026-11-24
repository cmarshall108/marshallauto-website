from decimal import Decimal


STANDARD_VEHICLE_FEES = (
    ('Detail fee', Decimal('100.00')),
    ('Lot fee', Decimal('100.00')),
)
STANDARD_VEHICLE_FEES_TOTAL = sum(
    (amount for _, amount in STANDARD_VEHICLE_FEES),
    Decimal('0.00'),
)

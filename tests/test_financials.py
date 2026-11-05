import time
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from app import create_app, db, login_manager
from app.financials import build_financial_dashboard
from app.models import RebuildCostEntry, User, Vehicle, VehicleSaleReceipt
from app.pricing import split_otd_sale_price
from app.utils import format_currency
from config import TestingConfig


def _utc(year, month, day, hour=16):
    return datetime(year, month, day, hour, tzinfo=timezone.utc).replace(tzinfo=None)


class FinancialReportTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.today = date(2026, 11, 15)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def _vehicle(self, **kwargs):
        values = {
            'year': 2018,
            'make': 'Ford',
            'model': 'Focus',
            'price': Decimal('12000.00'),
            'mileage': 80000,
            'status': 'available',
            'slug': kwargs.get('slug', 'focus'),
        }
        values.update(kwargs)
        vehicle = Vehicle(**values)
        db.session.add(vehicle)
        db.session.commit()
        return vehicle

    def _cost(self, vehicle, amount, category='parts', purchased_on=None, description='Parts'):
        entry = RebuildCostEntry(
            vehicle_id=vehicle.id,
            category=category,
            description=description,
            vendor='Parts Store',
            purchase_date=purchased_on,
            quantity=1,
            unit_cost=amount,
        )
        db.session.add(entry)
        db.session.commit()
        return entry

    def report(self, range_key='12m'):
        return build_financial_dashboard({'range': range_key}, today=self.today)

    def test_sale_month_keeps_profit_and_receipt_month_keeps_expenses(self):
        sold = self._vehicle(
            slug='sold-focus',
            status='sold',
            sold_price=Decimal('10000.00'),
            sold_at=_utc(2026, 10, 20),
            created_at=_utc(2026, 8, 2),
            acquisition_cost=Decimal('6000.00'),
            salesperson='Ada',
            payment_method='cash',
        )
        self._cost(sold, Decimal('200.00'), purchased_on=date(2026, 9, 3), description='September parts')
        self._cost(sold, Decimal('50.00'), purchased_on=date(2026, 10, 4), description='October labor', category='labor')
        self._cost(sold, Decimal('25.00'), purchased_on=None, description='Undated shop supplies', category='supplies')

        report = self.report()
        october = next(row for row in report['months'] if row['label'] == 'Oct 2026')
        september = next(row for row in report['months'] if row['label'] == 'Sep 2026')
        august = next(row for row in report['months'] if row['label'] == 'Aug 2026')
        split = split_otd_sale_price(Decimal('10000.00'))
        expected_profit = split['agreed_vehicle_price'] - Decimal('6275.00')

        self.assertEqual(october['revenue'], split['agreed_vehicle_price'])
        self.assertEqual(october['tax'], split['sales_tax'])
        self.assertEqual(october['acquisition'], Decimal('6000.00'))
        self.assertEqual(october['prep'], Decimal('275.00'))
        self.assertEqual(october['gross_profit'], expected_profit)
        self.assertEqual(october['profit_before_tax'] - october['tax'], october['gross_profit'])
        self.assertEqual(september['gross_profit'], Decimal('0.00'))
        self.assertEqual(september['expense_spend'], Decimal('200.00'))
        self.assertEqual(october['expense_spend'], Decimal('50.00'))
        self.assertEqual(august['acquisition_added'], Decimal('6000.00'))
        self.assertEqual(august['vehicles_added'], 1)
        self.assertEqual(report['kpis']['undated_expenses'], Decimal('25.00'))
        self.assertEqual(report['kpis']['gross_profit'], expected_profit)
        self.assertTrue(any(item['kind'] == 'undated_expense' for item in report['data_quality']))
        self.assertEqual(report['deals'][0]['salesperson'], 'Ada')
        self.assertEqual(report['deals'][0]['tax_estimated'], True)

    def test_eastern_time_month_boundary_and_range_filters(self):
        # 04:00 UTC on Feb 1 is still Jan 31 in Sanford, NC.
        self._vehicle(
            slug='boundary',
            status='sold',
            sold_price=Decimal('5000.00'),
            sold_at=datetime(2026, 2, 1, 4, tzinfo=timezone.utc).replace(tzinfo=None),
            created_at=_utc(2026, 1, 2),
            acquisition_cost=Decimal('1000.00'),
        )
        self._vehicle(
            slug='old-sale',
            make='Toyota',
            status='sold',
            sold_price=Decimal('8000.00'),
            sold_at=_utc(2025, 10, 10),
            created_at=_utc(2025, 9, 1),
            acquisition_cost=Decimal('3000.00'),
        )
        self._vehicle(
            slug='january-sale',
            make='Honda',
            status='sold',
            sold_price=Decimal('7000.00'),
            sold_at=_utc(2026, 1, 18),
            created_at=_utc(2026, 1, 2),
            acquisition_cost=Decimal('2000.00'),
        )

        twelve = self.report('12m')
        labels = [row['label'] for row in twelve['months']]
        self.assertEqual(labels[0], 'Dec 2025')
        self.assertEqual(labels[-1], 'Nov 2026')
        self.assertEqual(next(row for row in twelve['months'] if row['label'] == 'Jan 2026')['units'], 2)
        self.assertEqual(next(row for row in twelve['months'] if row['label'] == 'Feb 2026')['units'], 0)
        self.assertEqual(twelve['kpis']['units'], 2)
        self.assertNotIn('Oct 2025', labels)

        twenty_four = self.report('24m')
        self.assertEqual(twenty_four['kpis']['units'], 3)

        ytd = self.report('ytd')
        self.assertEqual(ytd['period_label'], 'Jan 2026 – Nov 2026')
        self.assertEqual(ytd['kpis']['units'], 2)

    def test_receipt_fees_inventory_and_missing_cost_do_not_distort_profit(self):
        sold = self._vehicle(
            slug='receipt-sale',
            status='sold',
            sold_price=Decimal('11000.00'),
            sold_at=_utc(2026, 11, 2),
            created_at=_utc(2026, 11, 1),
            acquisition_cost=None,
            salesperson='',
        )
        self._cost(sold, Decimal('100.00'), purchased_on=date(2026, 11, 2))
        db.session.add(VehicleSaleReceipt(
            vehicle_id=sold.id,
            vehicle_price=Decimal('9700.00'),
            sales_tax=Decimal('300.00'),
            document_fee=Decimal('25.00'),
            warranty_fee=Decimal('0.00'),
            tags_fee=Decimal('0.00'),
            highway_use_tax=Decimal('0.00'),
            title_registration_fee=Decimal('0.00'),
            credits=Decimal('0.00'),
            down_payment=Decimal('500.00'),
            trade_allow=Decimal('0.00'),
            amount_received=Decimal('9525.00'),
            salesman='Blake',
        ))
        unsold = self._vehicle(
            slug='unsold',
            status='available',
            price=Decimal('9000.00'),
            created_at=_utc(2026, 11, 3),
            acquisition_cost=Decimal('4000.00'),
        )
        self._cost(unsold, Decimal('150.00'), purchased_on=date(2026, 11, 4), description='Unsold prep')
        db.session.commit()

        report = self.report('6m')
        november = next(row for row in report['months'] if row['label'] == 'Nov 2026')
        self.assertEqual(november['revenue'], Decimal('9700.00'))
        self.assertEqual(november['tax'], Decimal('300.00'))
        self.assertEqual(november['gross_profit'], Decimal('9600.00'))
        self.assertEqual(november['dealer_fees'], Decimal('25.00'))
        self.assertEqual(november['contribution'], Decimal('9625.00'))
        self.assertEqual(november['expense_spend'], Decimal('250.00'))
        self.assertEqual(report['kpis']['units'], 1)
        self.assertEqual(report['deals'][0]['salesperson'], 'Blake')
        self.assertFalse(report['deals'][0]['tax_estimated'])
        self.assertTrue(any(item['kind'] == 'missing_acquisition' for item in report['data_quality']))
        self.assertTrue(any(item['kind'] == 'price_mismatch' for item in report['data_quality']))
        self.assertEqual(report['inventory']['units'], 1)
        self.assertEqual(report['inventory']['capital'], Decimal('4150.00'))
        self.assertEqual(report['inventory']['estimated_profit'], split_otd_sale_price(Decimal('9000.00'))['agreed_vehicle_price'] - Decimal('4150.00'))
        self.assertEqual(report['receipts']['down_payment'], Decimal('500.00'))
        self.assertEqual(report['cash_categories'][0]['label'], 'Parts & Materials')

    def test_empty_report_still_has_a_monthly_axis(self):
        report = self.report('6m')
        self.assertFalse(report['has_data'])
        self.assertEqual(len(report['months']), 6)
        self.assertEqual(report['months'][-1]['label'], 'Nov 2026')
        self.assertEqual(report['kpis']['gross_profit'], Decimal('0.00'))
        self.assertIsNone(report['kpis']['margin'])


class FinancialPageTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config['LOGIN_DISABLED'] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self._previous_session_protection = login_manager.session_protection
        login_manager.session_protection = None
        self.admin = User(username='finance-admin', role=User.ROLE_ADMIN, profile_complete=True)
        self.admin.set_password('finance-test-password')
        self.salesman = User(
            username='finance-sales',
            role=User.ROLE_SALESMAN,
            profile_complete=True,
            totp_enabled=True,
        )
        self.salesman.set_password('sales-test-password')
        db.session.add_all([self.admin, self.salesman])
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        login_manager.session_protection = self._previous_session_protection
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def _login_admin(self):
        with patch('app.admin.rate_limit_exceeded', return_value=False):
            response = self.client.post('/admin/login', data={
                'username': self.admin.username,
                'password': 'finance-test-password',
            })
        self.assertEqual(response.status_code, 302)

    def test_financials_page_renders_chart_data_for_admin_only(self):
        sold_at = datetime.now(timezone.utc).replace(tzinfo=None)
        vehicle = Vehicle(
            year=2019, make='Nissan', model='Sentra', price=Decimal('10000.00'),
            mileage=50000, status='sold', slug='finance-sentra',
            sold_price=Decimal('10000.00'), sold_at=sold_at,
            acquisition_cost=Decimal('6000.00'), created_at=sold_at,
        )
        db.session.add(vehicle)
        db.session.commit()
        db.session.add(RebuildCostEntry(
            vehicle_id=vehicle.id, category='service', description='Inspection',
            purchase_date=sold_at.date(), quantity=1, unit_cost=Decimal('100.00'),
        ))
        db.session.commit()
        expected = split_otd_sale_price(Decimal('10000.00'))['agreed_vehicle_price'] - Decimal('6100.00')

        anonymous = self.client.get('/admin/financials')
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn('/admin/login', anonymous.headers['Location'])

        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.salesman.id)
            sess['_fresh'] = True
            sess['admin_2fa_version'] = self.salesman.auth_version
            sess['admin_2fa_at'] = time.time()
        blocked = self.client.get('/admin/financials')
        self.assertEqual(blocked.status_code, 302)
        self.assertIn('/admin/dashboard', blocked.headers['Location'])
        salesman_dashboard = self.client.get('/admin/dashboard')
        self.assertEqual(salesman_dashboard.status_code, 200)
        self.assertNotIn('chartDashboardProfit', salesman_dashboard.get_data(as_text=True))
        self.client.get('/admin/logout')

        self._login_admin()
        page = self.client.get('/admin/financials')
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn('Monthly profit', body)
        self.assertIn('id="chartProfit"', body)
        self.assertIn('id="chartExpenses"', body)
        self.assertIn(format_currency(expected), body)
        self.assertIn('Service / Repair', body)
        dashboard = self.client.get('/admin/dashboard')
        self.assertIn('chartDashboardProfit', dashboard.get_data(as_text=True))
        self.assertIn('Full financials', dashboard.get_data(as_text=True))

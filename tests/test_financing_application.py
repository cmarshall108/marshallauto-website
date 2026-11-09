import os
import re
import stat
import tempfile
import unittest
from io import BytesIO
from unittest.mock import patch

from cryptography.fernet import Fernet
from pypdf import PdfReader
from sqlalchemy.exc import IntegrityError

from app import create_app, db
from app.financing import _send_textbelt_message, _textbelt_phone, is_valid_ssn
from app.models import ChatPresence, FinancingApplication, User, Vehicle, utcnow
from app.platform import _redact_financing_request
from app.two_factor import requires_two_factor
from config import TestingConfig


class FinancingApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.encryption_key = Fernet.generate_key().decode('ascii')
        self.app = create_app(TestingConfig)
        self.app.config.update(
            FINANCING_ENCRYPTION_KEY=self.encryption_key,
            PRIVATE_UPLOAD_FOLDER=self.temp_dir.name,
            FINANCING_RATE_LIMIT=100,
        )
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add_all([
            User(
                username='finance-admin', role=User.ROLE_ADMIN,
                password_hash='unused',
            ),
            Vehicle(
                slug='finance-camry', year=2018, make='Toyota', model='Camry',
                trim='SE', vin='4T1BF1FK5JU000001', stock_number='F100',
                body_style='Sedan', title_status='clean', price=12900,
                mileage=82000, status='available',
            ),
            Vehicle(
                slug='finance-sold', year=2016, make='Honda', model='Accord',
                vin='1HGCR2F30GA000001', stock_number='F101',
                body_style='Sedan', title_status='clean', price=9000,
                mileage=100000, status='sold',
            ),
        ])
        admin = User.query.filter_by(username='finance-admin').one()
        admin.set_password('finance-test-password')
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.temp_dir.cleanup()

    def application_form(self, **overrides):
        data = {
            'vehicle_id': '1',
            'requested_amount': '10000.00',
            'applicant_name': 'Riley Example',
            'applicant_ssn': '234-12-1234',
            'applicant_dob': '1990-01-02',
            'applicant_residence_street': '1 Main St',
            'applicant_residence_city': 'Sanford',
            'applicant_residence_state': 'NC',
            'applicant_residence_zip': '27332',
            'applicant_residence_years': '4',
            'applicant_residence_months': '0',
            'applicant_previous_residence_years': '',
            'applicant_previous_residence_months': '',
            'applicant_home_phone': '9195550101',
            'applicant_cell_phone': '',
            'applicant_dependents': '0',
            'applicant_email': 'riley@example.com',
            'applicant_housing_status': 'rent',
            'applicant_landlord': 'Example Owner',
            'applicant_housing_payment': '1200',
            'applicant_employment_status': 'employed',
            'applicant_employer_name_address': 'Example Auto, 2 Market St',
            'applicant_occupation': 'Technician',
            'applicant_employment_date': '2018-01-02',
            'applicant_work_phone': '9195550102',
            'applicant_work_extension': '',
            'applicant_employment_gross': '5000',
            'applicant_employment_net': '4000',
            'applicant_previous_employer': '',
            'applicant_previous_employment_date': '',
            'applicant_other_income_gross': '',
            'applicant_other_income_source': '',
            'applicant_total_gross_income': '5000',
            'applicant_total_net_income': '4000',
            'applicant_alimony_obligation': 'no',
            'applicant_auto_1_year_model': '2014 Honda Civic',
            'applicant_auto_1_financed_by': 'Example Bank',
            'applicant_auto_1_responsibility': 'applicant',
            'applicant_auto_2_year_model': '',
            'applicant_auto_2_financed_by': '',
            'applicant_auto_2_responsibility': '',
            'applicant_license_number': 'N1234567',
            'applicant_license_state': 'NC',
            'applicant_other_qualifying_id': '',
            'applicant_signature': 'Riley Example',
            'certify_accuracy': 'yes',
            'authorize_credit_report': 'yes',
            'electronic_signature_consent': 'yes',
        }
        data.update(overrides)
        return data

    def submit(self, data=None, **overrides):
        form_data = self.application_form(**overrides) if data is None else data
        return self.client.post(
            '/financing/apply',
            data=form_data,
            base_url='https://localhost',
        )

    def test_application_page_uses_only_first_party_scripts_and_never_caches(self):
        response = self.client.get('/financing', base_url='https://localhost')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Social Security number', response.data)
        self.assertIn(b'City', response.data)
        self.assertIn(b'ZIP code', response.data)
        self.assertIn(b'data-address-zip', response.data)
        self.assertIn(b'Your information is protected', response.data)
        self.assertNotIn(b'googletagmanager.com', response.data)
        self.assertNotIn(b'connect.facebook.net', response.data)
        self.assertNotIn(b'id="chat-widget"', response.data)
        self.assertNotIn(b'id="chat-widget-panel"', response.data)
        self.assertNotIn(b'id="chat-widget-form"', response.data)
        self.assertNotIn(b'id="chat-widget-toggle"', response.data)
        self.assertNotIn(b'chat-widget.js', response.data)
        self.assertIn(b'financing.js', response.data)
        self.assertIn(b'82,000 miles', response.data)
        self.assertIn(b'name="website"', response.data)
        self.assertNotIn(b'novalidate', response.data)
        for field in (
            'certify_accuracy',
            'authorize_credit_report',
            'electronic_signature_consent',
        ):
            self.assertRegex(
                response.data,
                rf'name="{field}"[^>]*required'.encode(),
            )
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store, max-age=0')
        self.assertEqual(response.headers['Referrer-Policy'], 'same-origin')
        self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])

    def test_https_application_submission_passes_csrf_with_same_origin_referer(self):
        self.app.config['WTF_CSRF_ENABLED'] = True
        page = self.client.get('/financing', base_url='https://localhost')
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data)

        self.assertIsNotNone(token)
        self.assertEqual(page.headers['Referrer-Policy'], 'same-origin')

        response = self.client.post(
            '/financing/apply',
            data={'csrf_token': token.group(1).decode(), 'vehicle_id': '999999'},
            base_url='https://localhost',
            headers={'Referer': 'https://localhost/financing'},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Choose a vehicle that is currently available.', response.data)
        self.assertNotIn(b'Your session expired or the security token was missing.', response.data)

    def test_application_page_stays_disabled_without_encryption_key(self):
        self.app.config['FINANCING_ENCRYPTION_KEY'] = None

        response = self.client.get('/financing', base_url='https://localhost')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'temporarily unavailable', response.data)
        self.assertNotIn(b'id="financing-application"', response.data)
        self.assertNotIn(b'name="applicant_ssn"', response.data)
        self.assertNotIn(b'financing.js', response.data)

    def test_valid_application_is_saved_only_as_encrypted_pdf_and_admin_can_download(self):
        response = self.submit()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/financing/received')
        self.assertEqual(FinancingApplication.query.count(), 1)
        application = FinancingApplication.query.one()
        directory = os.path.join(self.temp_dir.name, 'financing_applications')
        path = os.path.join(directory, application.encrypted_pdf_filename)
        with open(path, 'rb') as stored_file:
            encrypted = stored_file.read()
        self.assertNotIn(b'234-12-1234', encrypted)
        self.assertNotIn(b'Riley Example', encrypted)
        self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

        pdf_bytes = Fernet(self.encryption_key.encode('ascii')).decrypt(encrypted)
        reader = PdfReader(BytesIO(pdf_bytes))
        page_text = reader.pages[0].extract_text()
        self.assertIn('Riley Example', page_text)
        self.assertIn('234-12-1234', page_text)
        self.assertIn('$10,000.00', page_text)
        self.assertIn('N1234567', page_text)
        self.assertEqual(len(reader.pages), 2)
        supplemental = reader.pages[1].extract_text()
        self.assertIn('Toyota Camry SE', supplemental)
        self.assertIn('4T1BF1FK5JU000001', supplemental)
        self.assertIn('Current mileage: 82,000 miles', supplemental)
        self.assertIn('Monthly income summary (gross / net)', supplemental)
        self.assertIn('$5,000.00 / $4,000.00', supplemental)

        positions = {}
        def capture_text(text, cm, tm, font_dict, font_size):
            stripped = text.strip()
            if stripped in ('Vehicle purchase', '$10,000.00', 'Marshall Auto website'):
                positions[stripped] = (tm[5], font_size)
        reader.pages[0].extract_text(visitor_text=capture_text)
        self.assertEqual(len(positions), 3)
        for baseline, font_size in positions.values():
            # The scanned form's top-field rules are at approximately 210 px.
            self.assertGreater(baseline - font_size * 0.22, 792 - 210 * 0.6)

        with patch('app.admin.rate_limit_exceeded', return_value=False):
            login = self.client.post('/admin/login', data={
                'username': 'finance-admin',
                'password': 'finance-test-password',
            }, base_url='https://localhost')
        self.assertEqual(login.status_code, 302)

        listing = self.client.get('/admin/financing-applications', base_url='https://localhost')
        self.assertEqual(listing.status_code, 200)
        self.assertIn(b'Download PDF', listing.data)
        self.assertNotIn(b'Riley Example', listing.data)
        notifications = self.client.get(
            '/admin/api/financing-applications/notifications?after=0',
            base_url='https://localhost',
        )
        self.assertEqual(notifications.status_code, 200)
        self.assertEqual(notifications.json['applications'][0]['vehicle'], '2018 Toyota Camry SE')
        self.assertNotIn('Riley Example', str(notifications.json))
        invalid_cursor = self.client.get(
            '/admin/api/financing-applications/notifications?after=9223372036854775808',
            base_url='https://localhost',
        )
        self.assertEqual(invalid_cursor.status_code, 400)
        download = self.client.get(
            f'/admin/financing-applications/{application.id}/download',
            base_url='https://localhost',
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.mimetype, 'application/pdf')
        self.assertEqual(download.data, pdf_bytes)
        self.assertIn('no-store', download.headers['Cache-Control'])

        deleted = self.client.post(
            f'/admin/financing-applications/{application.id}/delete',
            base_url='https://localhost',
        )
        self.assertEqual(deleted.status_code, 302)
        self.assertEqual(FinancingApplication.query.count(), 0)
        self.assertFalse(os.path.exists(path))

    def test_application_requires_all_three_authorizations(self):
        authorization_errors = {
            'certify_accuracy': b'Confirm that the information is complete and accurate.',
            'authorize_credit_report': b'Authorize the lender and seller to verify',
            'electronic_signature_consent': b'Agree to use your typed name as an electronic signature',
        }

        for field, expected_error in authorization_errors.items():
            with self.subTest(field=field):
                response = self.submit(**{field: ''})

                self.assertEqual(response.status_code, 400)
                self.assertIn(expected_error, response.data)
                self.assertEqual(FinancingApplication.query.count(), 0)

    def test_supplement_uses_current_inventory_mileage_not_submitted_value(self):
        self.client.get('/financing', base_url='https://localhost')
        vehicle = Vehicle.query.filter_by(slug='finance-camry').one()
        vehicle.mileage = 123456
        db.session.commit()
        response = self.submit(mileage='1')
        self.assertEqual(response.status_code, 302)
        application = FinancingApplication.query.one()
        path = os.path.join(self.temp_dir.name, 'financing_applications', application.encrypted_pdf_filename)
        with open(path, 'rb') as stored_file:
            pdf_bytes = Fernet(self.encryption_key.encode('ascii')).decrypt(stored_file.read())
        text = PdfReader(BytesIO(pdf_bytes)).pages[1].extract_text()
        self.assertIn('Current mileage: 123,456 miles', text)

    def test_supplement_shows_zero_mileage_without_stock_number(self):
        vehicle = Vehicle.query.filter_by(slug='finance-camry').one()
        vehicle.mileage = 0
        vehicle.stock_number = None
        db.session.commit()
        self.assertEqual(self.submit().status_code, 302)
        application = FinancingApplication.query.one()
        path = os.path.join(self.temp_dir.name, 'financing_applications', application.encrypted_pdf_filename)
        with open(path, 'rb') as stored_file:
            pdf_bytes = Fernet(self.encryption_key.encode('ascii')).decrypt(stored_file.read())
        text = PdfReader(BytesIO(pdf_bytes)).pages[1].extract_text()
        self.assertIn('Current mileage: 0 miles', text)
        self.assertNotIn('Stock #:', text)

    def test_joint_application_adds_coapplicant_and_residence_history_page(self):
        data = self.application_form(
            joint_credit='yes',
            coapplicant_name='Jordan Example',
            coapplicant_ssn='345-67-8901',
            coapplicant_dob='1991-04-05',
            coapplicant_residence_street='3 Oak St',
            coapplicant_residence_city='Sanford',
            coapplicant_residence_state='NC',
            coapplicant_residence_zip='27332',
            coapplicant_residence_years='1',
            coapplicant_residence_months='0',
            coapplicant_previous_street='8 Pine St',
            coapplicant_previous_city='Sanford',
            coapplicant_previous_state='NC',
            coapplicant_previous_zip='27332',
            coapplicant_previous_residence_years='2',
            coapplicant_previous_residence_months='0',
            coapplicant_home_phone='9195550103',
            coapplicant_dependents='0',
            coapplicant_email='jordan@example.com',
            coapplicant_housing_status='own',
            coapplicant_employment_status='retired',
            coapplicant_total_gross_income='2500',
            coapplicant_total_net_income='2200',
            coapplicant_alimony_obligation='no',
            coapplicant_signature='Jordan Example',
        )

        response = self.submit(data)

        self.assertEqual(response.status_code, 302)
        application = FinancingApplication.query.one()
        stored_path = os.path.join(
            self.temp_dir.name, 'financing_applications',
            application.encrypted_pdf_filename,
        )
        with open(stored_path, 'rb') as stored_file:
            pdf_bytes = Fernet(self.encryption_key.encode('ascii')).decrypt(
                stored_file.read(),
            )
        reader = PdfReader(BytesIO(pdf_bytes))
        self.assertEqual(len(reader.pages), 2)
        self.assertIn('Jordan Example', reader.pages[0].extract_text())
        supplement = reader.pages[1].extract_text()
        self.assertIn('Toyota Camry SE', supplement)
        self.assertIn('Current mileage: 82,000 miles', supplement)
        self.assertIn('Previous residence: 8 Pine St', supplement)

    def test_only_one_application_can_be_submitted_per_vehicle(self):
        with patch('app.routes.notify_salespeople_by_textbelt'):
            first = self.submit()
            duplicate = self.submit()

        self.assertEqual(first.status_code, 302)
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn(b'already been submitted for this vehicle', duplicate.data)
        self.assertEqual(FinancingApplication.query.count(), 1)

    def test_database_rejects_duplicate_vehicle_applications(self):
        with patch('app.routes.notify_salespeople_by_textbelt'):
            self.assertEqual(self.submit().status_code, 302)

        db.session.add(FinancingApplication(
            vehicle_id=1,
            encrypted_pdf_filename='a' * 32 + '.enc',
            joint_credit=False,
            submitted_at=utcnow(),
        ))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        self.assertEqual(FinancingApplication.query.count(), 1)

    def test_a_separate_vehicle_can_receive_its_own_application(self):
        second_vehicle = Vehicle(
            slug='finance-accord-available', year=2019, make='Honda',
            model='Accord', vin='1HGCR2F30KA000002', stock_number='F102',
            body_style='Sedan', title_status='clean', price=14900,
            mileage=70000, status='available',
        )
        db.session.add(second_vehicle)
        db.session.commit()

        with patch('app.routes.notify_salespeople_by_textbelt'):
            first = self.submit()
            second = self.submit(vehicle_id=str(second_vehicle.id))

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(FinancingApplication.query.count(), 2)

    def test_online_admin_receives_in_panel_alert_instead_of_salesperson_text(self):
        admin = User.query.filter_by(username='finance-admin').one()
        db.session.add(ChatPresence(
            id='12345678-1234-4234-8234-123456789abc',
            user_id=admin.id,
            auth_version=admin.auth_version,
            last_seen_at=utcnow(),
        ))
        db.session.commit()

        with patch('app.routes.notify_salespeople_by_textbelt') as send_text:
            response = self.submit()

        self.assertEqual(response.status_code, 302)
        send_text.assert_not_called()

    def test_offline_admin_alert_texts_all_salespeople_without_applicant_data(self):
        db.session.add_all([
            User(
                username='finance-sales-1', role=User.ROLE_SALESMAN,
                password_hash='unused', phone='(919) 555-0104',
            ),
            User(
                username='finance-sales-2', role=User.ROLE_SALESMAN,
                password_hash='unused', phone='+1 919-555-0105',
            ),
        ])
        db.session.commit()
        self.app.config['TEXTBELT_API_KEY'] = 'test-key'

        with patch('app.financing._send_textbelt_message', return_value=True) as send_text:
            response = self.submit()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(send_text.call_count, 2)
        messages = [call.args[1] for call in send_text.call_args_list]
        self.assertTrue(all('Toyota Camry SE' in message for message in messages))
        self.assertTrue(all('Riley Example' not in message for message in messages))
        self.assertTrue(all('234-12-1234' not in message for message in messages))
        self.assertEqual(
            {call.args[0] for call in send_text.call_args_list},
            {'+19195550104', '+19195550105'},
        )

    def test_textbelt_phone_normalization(self):
        self.assertEqual(_textbelt_phone('(919) 555-0104'), '+19195550104')
        self.assertEqual(_textbelt_phone('+44 20 7946 0958'), '+442079460958')
        self.assertIsNone(_textbelt_phone('not a phone number'))

    def test_textbelt_request_uses_encoded_post_and_checks_provider_response(self):
        response = BytesIO(b'{"success": true}')
        with patch(
            'app.financing.urllib.request.urlopen',
            return_value=response,
        ) as open_url:
            self.assertTrue(_send_textbelt_message(
                '+19195550104', 'New application for a vehicle.', 'test-key',
            ))

        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, 'https://textbelt.com/text')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(
            request.get_header('Content-type'),
            'application/x-www-form-urlencoded',
        )
        self.assertIn(b'key=test-key', request.data)

    def test_invalid_ssn_and_incomplete_two_year_history_are_rejected(self):
        invalid_ssn = self.submit(applicant_ssn='000-12-1234')
        self.assertEqual(invalid_ssn.status_code, 400)
        self.assertNotIn(b'000-12-1234', invalid_ssn.data)
        self.assertEqual(FinancingApplication.query.count(), 0)

        invalid_history = self.submit(
            applicant_residence_years='1',
            applicant_residence_months='0',
            applicant_previous_street='',
            applicant_previous_city='',
            applicant_previous_state='',
            applicant_previous_zip='',
            applicant_previous_residence_years='',
            applicant_previous_residence_months='',
        )
        self.assertEqual(invalid_history.status_code, 400)
        body = invalid_history.data.lower()
        self.assertTrue(
            b'previous' in body and (b'street' in body or b'address' in body or b'city' in body),
            body[:500],
        )
        self.assertEqual(FinancingApplication.query.count(), 0)

    def test_residence_requires_city_state_and_zip(self):
        response = self.submit(
            applicant_residence_street='360 Wilson Rd',
            applicant_residence_city='',
            applicant_residence_state='',
            applicant_residence_zip='',
        )
        self.assertEqual(response.status_code, 400)
        body = response.data.lower()
        self.assertIn(b'city', body)
        self.assertIn(b'zip', body)
        self.assertEqual(FinancingApplication.query.count(), 0)

        ok = self.submit(
            applicant_residence_street='360 Wilson Rd',
            applicant_residence_city='Sanford',
            applicant_residence_state='nc',
            applicant_residence_zip='27332',
        )
        self.assertIn(ok.status_code, (200, 302))
        self.assertEqual(FinancingApplication.query.count(), 1)
        # Combined line used for the lender PDF includes city and ZIP.
        from app.financing import validate_application
        vehicle = type('V', (), {'id': 1, 'title': 'Test', 'display_price': 10000, 'year': 2018, 'make': 'Honda', 'model': 'Civic', 'stock_number': '1', 'vin': None})()
        # Ensure compose helper formats correctly via a direct unit check.
        from app.financing import _compose_us_address
        self.assertEqual(
            _compose_us_address('360 Wilson Rd', 'Sanford', 'NC', '27332'),
            '360 Wilson Rd, Sanford, NC 27332',
        )

    def test_honeypot_submission_is_rejected_without_saving_application(self):
        response = self.submit(website='https://spam.example')

        self.assertEqual(response.status_code, 400)
        self.assertIn(b'could not verify this submission', response.data)
        self.assertEqual(FinancingApplication.query.count(), 0)

    def test_local_spam_score_rejects_disposable_email_and_invalid_phone(self):
        response = self.submit(
            applicant_email='riley@mailinator.com',
            applicant_cell_phone='555-555-5555',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b'could not verify this submission', response.data)
        self.assertNotIn(b'mailinator.com', response.data)
        self.assertNotIn(b'555-555-5555', response.data)
        self.assertEqual(FinancingApplication.query.count(), 0)

    def test_ssn_validation_rejects_reserved_area_group_and_serial_numbers(self):
        for value in (
            '000-12-1234', '666-12-1234', '900-12-1234',
            '234-00-1234', '234-12-0000', '234--12-1234',
        ):
            with self.subTest(value=value):
                self.assertFalse(is_valid_ssn(value))
        self.assertTrue(is_valid_ssn('234-12-1234'))
        self.assertTrue(is_valid_ssn('234121234'))

    def test_error_tracking_redacts_financing_request_data(self):
        event = {
            'request': {
                'url': 'https://example.test/financing/apply',
                'data': {'applicant_ssn': '234-12-1234'},
                'cookies': {'session': 'sensitive'},
                'headers': {'Cookie': 'sensitive'},
            },
            'user': {'email': 'applicant@example.test'},
        }

        result = _redact_financing_request(event, None)

        self.assertNotIn('data', result['request'])
        self.assertNotIn('cookies', result['request'])
        self.assertNotIn('headers', result['request'])
        self.assertNotIn('user', result)

    def test_salesman_can_view_and_download_but_not_delete_applications(self):
        self.submit()
        application = FinancingApplication.query.one()
        salesman = User(
            username='finance-sales', role=User.ROLE_SALESMAN,
            profile_complete=True,
        )
        salesman.set_password('sales-test-password')
        db.session.add(salesman)
        db.session.commit()

        with patch('app.admin.rate_limit_exceeded', return_value=False), patch(
            'app.admin.requires_two_factor', return_value=False,
        ):
            login = self.client.post('/admin/login', data={
                'username': 'finance-sales',
                'password': 'sales-test-password',
            }, base_url='https://localhost')
        self.assertEqual(login.status_code, 302)

        listing = self.client.get('/admin/financing-applications', base_url='https://localhost')
        self.assertEqual(listing.status_code, 200)
        self.assertIn(b'Download PDF', listing.data)
        self.assertNotIn(b'>Delete<', listing.data)
        self.assertIn(b'href="/admin/financing-applications"', listing.data)
        notifications = self.client.get(
            '/admin/api/financing-applications/notifications?after=0',
            base_url='https://localhost',
        )
        self.assertEqual(notifications.status_code, 200)
        self.assertEqual(notifications.json['applications'][0]['id'], application.id)
        download = self.client.get(
            f'/admin/financing-applications/{application.id}/download',
            base_url='https://localhost',
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.mimetype, 'application/pdf')
        deleted = self.client.post(
            f'/admin/financing-applications/{application.id}/delete',
            base_url='https://localhost',
        )
        self.assertEqual(deleted.status_code, 302)
        self.assertIn('dashboard', deleted.headers['Location'])
        self.assertEqual(FinancingApplication.query.count(), 1)

    def test_admin_financing_access_requires_two_factor_policy(self):
        admin = User.query.filter_by(username='finance-admin').one()
        self.app.config['FINANCING_ADMIN_TWO_FACTOR_REQUIRED'] = True

        self.assertTrue(requires_two_factor(admin))


    def test_zip_lookup_endpoint_returns_city_and_state(self):
        from unittest.mock import patch
        fake = {
            'post code': '27332',
            'country': 'United States',
            'country abbreviation': 'US',
            'state': 'North Carolina',
            'state abbreviation': 'NC',
            'places': [{'place name': 'Sanford', 'longitude': '-79', 'latitude': '35', 'state': 'North Carolina', 'state abbreviation': 'NC'}],
        }
        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return __import__('json').dumps(fake).encode()
        with patch('app.financing.urllib.request.urlopen', return_value=Resp()):
            response = self.client.get('/api/zip/27332')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['city'], 'Sanford')
        self.assertEqual(payload['state'], 'NC')
        self.assertIn('Sanford', payload['cities'])

    def test_zip_lookup_unknown_code_is_404(self):
        from unittest.mock import patch
        import urllib.error
        def boom(*args, **kwargs):
            raise urllib.error.HTTPError('https://api.zippopotam.us/us/00000', 404, 'Not Found', hdrs=None, fp=None)
        with patch('app.financing.urllib.request.urlopen', side_effect=boom):
            response = self.client.get('/api/zip/00000')
        self.assertEqual(response.status_code, 404)

if __name__ == '__main__':
    unittest.main()

import json
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from app import create_app, db
from app.models import (
    PhotoHighlightJob, Vehicle, VehicleImage, VehicleImageHighlight, utcnow,
)
from app.vin_decode import grok_photo_annotation_signature
from config import TestingConfig


class VINBackfillTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.vehicle = Vehicle(
            year=2018,
            make='Honda',
            model='Accord',
            price=15000,
            mileage=65000,
            status='available',
            vin='1HGCM82633A004352',
            slug='backfill-accord',
            features='Leather Seats',
            exterior_features='Factory paint',
            additional_specifications='Staff verified: Keep this',
        )
        self.no_vin = Vehicle(
            year=2010,
            make='Honda',
            model='Civic',
            price=5000,
            mileage=150000,
            status='sold',
            vin=None,
            slug='no-vin-civic',
        )
        db.session.add_all([self.vehicle, self.no_vin])
        db.session.commit()
        self.runner = self.app.test_cli_runner()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def decoded_result(self, vin):
        return {
            'ok': True,
            'vin': vin,
            'vehicle': {
                'vin_decode_data': {
                    'NHTSA': {'VIN': vin, 'Make': 'HONDA'},
                    'EPA': {'city08': '25'},
                },
                'year': 2021,
                'make': 'Honda',
                'model': 'Accord',
                'trim': 'EX',
                'mpg_city': 25,
                'doors': 4,
                'exterior_features': '17-inch wheels\nFactory paint',
                'safety_features': 'Backup Camera',
                'additional_specifications': 'Horsepower: 200 hp\nStaff verified: Keep this',
            },
            'features': ['Backup Camera', 'Blind Spot Monitor'],
        }

    def test_backfill_updates_missing_values_and_preserves_existing_content(self):
        with (
            patch('app.vin_backfill.is_vin_grok_enrichment_enabled', return_value=False),
            patch(
                'app.vin_backfill.decode_vin',
                side_effect=lambda vin, use_cache: self.decoded_result(vin),
            ) as decoder,
        ):
            result = self.runner.invoke(args=['backfill-vin-data', '--delay', '0'])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('1 updated, 0 failed', result.output)
        decoder.assert_called_once_with(self.vehicle.vin, use_cache=False)

        db.session.expire_all()
        updated = db.session.get(Vehicle, self.vehicle.id)
        self.assertEqual(updated.year, 2018)
        self.assertEqual(updated.make, 'Honda')
        self.assertEqual(updated.trim, 'EX')
        self.assertEqual(updated.mpg_city, 25)
        self.assertEqual(updated.doors, 4)
        self.assertEqual(
            updated.features,
            'Leather Seats, Backup Camera, Blind Spot Monitor',
        )
        self.assertEqual(
            updated.exterior_features,
            'Factory paint\n17-inch wheels',
        )
        self.assertEqual(
            updated.additional_specifications,
            'Staff verified: Keep this\nHorsepower: 200 hp',
        )
        self.assertEqual(
            json.loads(updated.vin_decode_data)['NHTSA']['VIN'],
            self.vehicle.vin,
        )
        self.assertIsNone(db.session.get(Vehicle, self.no_vin.id).vin_decode_data)

    def test_default_skips_decoded_records_and_force_refreshes_them(self):
        self.vehicle.vin_decode_data = json.dumps({
            'NHTSA': {'VIN': self.vehicle.vin},
            'Grok': {
                'estimates': {},
                'photo_annotations': {
                    'version': 1,
                    'exterior': [],
                    'interior': [],
                    'entertainment': [],
                    'image_signature': grok_photo_annotation_signature([]),
                },
            },
        })
        db.session.commit()

        with (
            patch('app.vin_backfill.is_vin_grok_enrichment_enabled', return_value=False),
            patch(
                'app.vin_backfill.decode_vin',
                side_effect=lambda vin, use_cache: self.decoded_result(vin),
            ) as decoder,
        ):
            skipped = self.runner.invoke(args=['backfill-vin-data', '--delay', '0'])
            decoder.assert_not_called()
            refreshed = self.runner.invoke(
                args=['backfill-vin-data', '--force', '--delay', '0'],
            )

        self.assertEqual(skipped.exit_code, 0, skipped.output)
        self.assertIn('0 vehicle(s) queued', skipped.output)
        self.assertEqual(refreshed.exit_code, 0, refreshed.output)
        decoder.assert_called_once_with(self.vehicle.vin, use_cache=False)

    def test_transient_decoder_failure_is_retried_and_vehicle_updated(self):
        failure = {'ok': False, 'error': 'NHTSA temporarily unavailable'}
        with patch(
            'app.vin_backfill.decode_vin',
            side_effect=[failure, self.decoded_result(self.vehicle.vin)],
        ) as decoder:
            result = self.runner.invoke(args=[
                'backfill-vin-data',
                '--delay',
                '0',
                '--retries',
                '2',
            ])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('retrying', result.output)
        self.assertIn('1 updated, 0 failed', result.output)
        self.assertEqual(decoder.call_count, 2)
        db.session.expire_all()
        self.assertTrue(db.session.get(Vehicle, self.vehicle.id).vin_decode_data)

    def test_backfill_adds_grok_data_to_existing_nhtsa_payload_without_redecoding(self):
        self.vehicle.vin_decode_data = json.dumps({
            'NHTSA': {
                'VIN': self.vehicle.vin,
                'ModelYear': '2018',
                'Make': 'Honda',
                'Model': 'Accord',
                'EngineHP': '192',
            },
            'EPA': {'city08': '30'},
        })
        db.session.commit()

        with (
            patch('app.vin_backfill.is_vin_grok_enrichment_enabled', return_value=True),
            patch(
                'app.vin_backfill.enrich_vin_decode_data_with_grok',
                return_value={'estimates': {'engine_torque_lb_ft': '192 lb-ft'}},
            ) as enrich,
            patch('app.vin_backfill.decode_vin') as decoder,
        ):
            result = self.runner.invoke(args=['backfill-vin-data', '--delay', '0'])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('1 Grok estimate(s)', result.output)
        enrich.assert_called_once()
        decoder.assert_not_called()
        db.session.expire_all()
        saved = json.loads(db.session.get(Vehicle, self.vehicle.id).vin_decode_data)
        self.assertEqual(saved['NHTSA']['EngineHP'], '192')
        self.assertEqual(saved['EPA']['city08'], '30')
        self.assertEqual(
            saved['Grok']['estimates']['engine_torque_lb_ft'],
            '192 lb-ft',
        )

    def test_backfill_reuses_completed_grok_photo_annotations_without_model_calls(self):
        self.vehicle.exterior_features = (
            'Staff checked exterior\nPhoto-observed (verify): Outdated Wheels'
        )
        self.vehicle.vin_decode_data = json.dumps({
            'NHTSA': {'VIN': self.vehicle.vin, 'ModelYear': '2018'},
            'Grok': {'estimates': {}},
        })
        exterior = VehicleImage(
            vehicle_id=self.vehicle.id,
            filename='exterior.jpg',
            highlight_status='ready',
            highlight_scene='exterior_side',
        )
        interior = VehicleImage(
            vehicle_id=self.vehicle.id,
            filename='interior.jpg',
            highlight_status='ready',
            highlight_scene='interior_dash',
        )
        cabin = VehicleImage(
            vehicle_id=self.vehicle.id,
            filename='cabin.jpg',
            highlight_status='ready',
            highlight_scene='interior_cabin',
        )
        opencv = VehicleImage(
            vehicle_id=self.vehicle.id,
            filename='opencv.jpg',
            highlight_status='ready',
            highlight_scene='interior_cabin',
        )
        db.session.add_all([exterior, interior, cabin, opencv])
        db.session.flush()
        for image, engine, label, confidence in (
            (exterior, 'grok', 'Alloy Wheels', 0.92),
            (interior, 'grok', 'Apple CarPlay', 0.9),
            (cabin, 'grok', 'Seat Surface', 0.9),
            (opencv, 'opencv', 'Leather Seats', 0.99),
        ):
            category = 'detail' if label == 'Seat Surface' else 'feature'
            db.session.add(PhotoHighlightJob(
                vehicle_image_id=image.id,
                vehicle_id=self.vehicle.id,
                status='completed',
                finished_at=utcnow(),
                result_summary=f'1 highlights; scene={image.highlight_scene}; engine={engine}',
            ))
            db.session.add(VehicleImageHighlight(
                vehicle_image_id=image.id,
                label=label,
                category=category,
                confidence=confidence,
                source='auto',
                is_visible=True,
            ))
        db.session.commit()

        with (
            patch('app.vin_backfill.is_vin_grok_enrichment_enabled', return_value=False),
            patch('app.vin_backfill.enrich_vin_decode_data_with_grok') as enrich,
            patch('app.vin_backfill.decode_vin') as decoder,
        ):
            result = self.runner.invoke(args=['backfill-vin-data', '--delay', '0'])

        self.assertEqual(result.exit_code, 0, result.output)
        enrich.assert_not_called()
        decoder.assert_not_called()
        db.session.expire_all()
        updated = db.session.get(Vehicle, self.vehicle.id)
        saved = json.loads(updated.vin_decode_data)
        self.assertEqual(
            [item['label'] for item in saved['Grok']['photo_annotations']['exterior']],
            ['Alloy Wheels'],
        )
        self.assertEqual(
            [item['label'] for item in saved['Grok']['photo_annotations']['entertainment']],
            ['Apple CarPlay'],
        )
        self.assertEqual(
            [item['label'] for item in saved['Grok']['photo_annotations']['interior']],
            ['Seat Surface'],
        )
        self.assertIn('Photo-observed (verify): Alloy Wheels', updated.exterior_features)
        self.assertIn('Staff checked exterior', updated.exterior_features)
        self.assertNotIn('Outdated Wheels', updated.exterior_features)
        self.assertIn(
            'Photo-observed (verify): Apple CarPlay',
            updated.entertainment_features,
        )
        self.assertIn(
            'Photo-observed (verify): Seat Surface',
            updated.interior_features,
        )
        self.assertNotIn('Leather Seats', updated.interior_features or '')

    def test_failed_decode_is_reported_and_exits_unsuccessfully(self):
        with patch(
            'app.vin_backfill.decode_vin',
            return_value={'ok': False, 'error': 'NHTSA unavailable'},
        ):
            result = self.runner.invoke(args=['backfill-vin-data', '--delay', '0'])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('NHTSA unavailable', result.output)
        self.assertIn('1 vehicle(s) could not be updated', result.output)
        db.session.expire_all()
        self.assertIsNone(db.session.get(Vehicle, self.vehicle.id).vin_decode_data)


if __name__ == '__main__':
    unittest.main()

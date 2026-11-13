import json
import unittest
from unittest.mock import patch

from app import create_app, db
from app.models import Vehicle
from app.vin_decode import (
    apply_nhtsa_safety_ratings_to_vehicle, group_vin_decode_data,
    lookup_nhtsa_safety_ratings, parse_nhtsa_star_rating,
    present_nhtsa_safety_ratings, safety_ratings_from_vehicle,
)
from config import TestingConfig


SAMPLE_VARIANTS = {
    'Count': 2,
    'Results': [
        {'VehicleDescription': '2021 Toyota CAMRY 4 DR AWD', 'VehicleId': 15846},
        {'VehicleDescription': '2021 Toyota CAMRY 4 DR FWD', 'VehicleId': 15820},
    ],
}

SAMPLE_DETAIL = {
    'Count': 1,
    'Results': [{
        'OverallRating': '5',
        'OverallFrontCrashRating': '5',
        'OverallSideCrashRating': '5',
        'RolloverRating': '4',
        'FrontCrashDriversideRating': '5',
        'FrontCrashPassengersideRating': '5',
        'SideCrashDriversideRating': '5',
        'SideCrashPassengersideRating': '5',
        'SidePoleCrashRating': '5',
        'RolloverPossibility': 0.1,
        'dynamicTipResult': 'No Tip',
        'NHTSAElectronicStabilityControl': 'Standard',
        'NHTSAForwardCollisionWarning': 'Standard',
        'NHTSALaneDepartureWarning': 'Standard',
        'ComplaintsCount': 10,
        'RecallsCount': 1,
        'InvestigationCount': 0,
        'ModelYear': 2021,
        'Make': 'TOYOTA',
        'Model': 'CAMRY',
        'VehicleDescription': '2021 Toyota CAMRY 4 DR FWD',
        'VehicleId': 15820,
        'FrontCrashPicture': 'https://static.nhtsa.gov/crashTest/images/2021/front.jpg',
        'SideCrashPicture': 'https://static.nhtsa.gov/crashTest/images/2021/side.jpg',
        'SidePolePicture': 'https://static.nhtsa.gov/crashTest/images/2021/pole.jpg',
        'FrontCrashVideo': 'https://static.nhtsa.gov/crashTest/videos/2021/front.wmv',
    }],
}


class NhtsaSafetyHelperTests(unittest.TestCase):
    def test_parse_star_rating(self):
        self.assertEqual(parse_nhtsa_star_rating('5'), 5)
        self.assertEqual(parse_nhtsa_star_rating('4.0'), 4)
        self.assertIsNone(parse_nhtsa_star_rating('Not Rated'))
        self.assertIsNone(parse_nhtsa_star_rating(''))

    def test_lookup_prefers_matching_drivetrain_variant(self):
        calls = []

        def fake_json(url, timeout=8.0):
            calls.append(url)
            if '/VehicleId/' in url:
                return SAMPLE_DETAIL
            if url.endswith('/model/Camry') or url.endswith('/model/CAMRY'):
                return SAMPLE_VARIANTS
            raise AssertionError(url)

        with (
            patch.dict('app.vin_decode._SAFETY_CACHE', {}, clear=True),
            patch('app.vin_decode._safety_json', side_effect=fake_json),
        ):
            result = lookup_nhtsa_safety_ratings(
                year=2021,
                make='Toyota',
                model='Camry',
                drivetrain='FWD',
                body_style='Sedan',
            )

        self.assertIsNotNone(result)
        self.assertEqual(result['VehicleId'], 15820)
        self.assertEqual(result['OverallStars'], 5)
        self.assertEqual(result['RolloverStars'], 4)
        self.assertEqual(
            result['FrontCrashPicture'],
            'https://static.nhtsa.gov/crashTest/images/2021/front.jpg',
        )
        presented = present_nhtsa_safety_ratings(result)
        self.assertIn('frontal crash 5/5', presented['Summary'])
        self.assertEqual(presented['RolloverPercent'], 10.0)
        self.assertTrue(any(test['key'] == 'frontal' and test['picture'] for test in presented['CrashTests']))
        self.assertTrue(any('/VehicleId/15820' in url for url in calls))

    def test_group_vin_decode_includes_safety_ratings(self):
        groups = group_vin_decode_data({
            'NHTSA': {'VIN': '1HGBH41JXMN109186', 'Make': 'HONDA'},
            'SafetyRatings': {
                'OverallRating': '5',
                'OverallFrontCrashRating': '5',
                'VehicleDescription': '2021 Honda Civic',
            },
        })
        labels = [group['label'] for group in groups]
        self.assertIn('NHTSA 5-Star Safety Ratings', labels)
        safety_group = next(
            group for group in groups
            if group['label'] == 'NHTSA 5-Star Safety Ratings'
        )
        self.assertTrue(any(field['label'] == 'Overall Rating' for field in safety_group['fields']))


class NhtsaSafetyVehicleTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_apply_and_display_on_vehicle_page(self):
        vehicle = Vehicle(
            slug='safety-camry',
            year=2021,
            make='Toyota',
            model='Camry',
            price=18000,
            mileage=30000,
            status='available',
            drivetrain='FWD',
            body_style='Sedan',
        )
        db.session.add(vehicle)
        db.session.commit()

        ratings = {
            'VehicleId': 15820,
            'ModelYear': 2021,
            'Make': 'TOYOTA',
            'Model': 'CAMRY',
            'OverallRating': '5',
            'OverallFrontCrashRating': '5',
            'OverallSideCrashRating': '5',
            'RolloverRating': '4',
            'FrontCrashDriversideRating': '5',
            'FrontCrashPassengersideRating': '4',
            'VehicleDescription': '2021 Toyota CAMRY 4 DR FWD',
            'OverallStars': 5,
            'FrontStars': 5,
            'SideStars': 5,
            'RolloverStars': 4,
            'RolloverPossibility': 0.099,
            'dynamicTipResult': 'No Tip',
            'FrontCrashPicture': 'https://static.nhtsa.gov/crashTest/images/2021/front.jpg',
            'NHTSAElectronicStabilityControl': 'Standard',
            'NHTSAForwardCollisionWarning': 'Standard',
            'NHTSALaneDepartureWarning': 'Standard',
            'ComplaintsCount': 12,
            'RecallsCount': 1,
            'InvestigationCount': 0,
            'source': 'nhtsa-safety-ratings',
        }
        apply_nhtsa_safety_ratings_to_vehicle(vehicle, ratings)
        vehicle.vin_decode_data = json.dumps({'SafetyRatings': ratings})
        db.session.commit()

        self.assertEqual(vehicle.nhtsa_overall_stars, 5)
        self.assertEqual(safety_ratings_from_vehicle(vehicle)['OverallStars'], 5)

        response = self.client.get('/inventory/safety-camry')
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn('NHTSA 5-Star Safety Ratings', text)
        self.assertIn('Overall crash rating', text)
        self.assertIn('Frontal crash', text)
        self.assertIn('static.nhtsa.gov/crashTest/images/2021/front.jpg', text)
        self.assertIn('Estimated rollover risk', text)
        self.assertIn('Crash-avoidance equipment', text)
        self.assertIn('NHTSA overall safety rating 5/5', text)
        self.assertIn('additionalProperty', response.get_data(as_text=True) or '')

    def test_inventory_shows_safety_badge(self):
        vehicle = Vehicle(
            slug='safety-badge-civic',
            year=2020,
            make='Honda',
            model='Civic',
            price=16000,
            mileage=40000,
            status='available',
            nhtsa_overall_rating='5',
        )
        db.session.add(vehicle)
        db.session.commit()
        response = self.client.get('/inventory')
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn('NHTSA 5/5', text)
        self.assertIn('5/5 safety', text)


if __name__ == '__main__':
    unittest.main()

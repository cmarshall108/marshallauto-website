import io
import json
import unittest
from unittest.mock import patch

from flask import Flask
from werkzeug.datastructures import MultiDict

from app.forms import VehicleForm
from app.vin_decode import (
    classify_grok_photo_annotations, decode_vin, derive_vehicle_details,
    enrich_vin_decode_data_with_grok, group_vin_decode_data, map_vehicle_fields,
)


REFERENCE_VIN = 'KM8K2CAAXMU609019'


class VinDecodeDetailTests(unittest.TestCase):
    def reference_decode(self):
        return {
            'VIN': REFERENCE_VIN,
            'VehicleDescriptor': 'KM8K2CAA*MU',
            'ModelYear': '2021',
            'Make': 'HYUNDAI',
            'MakeID': '498',
            'Model': 'Kona',
            'ModelID': '22249',
            'Trim': 'SEL',
            'BodyClass': 'Sport Utility Vehicle [SUV]/Multipurpose Vehicle [MPV]',
            'Manufacturer': 'HYUNDAI MOTOR CO',
            'ManufacturerId': '15984',
            'PlantCity': 'ULSAN',
            'PlantCountry': 'SOUTH KOREA',
            'NCSABodyType': 'Compact Utility',
            'DriveType': '4WD/4-Wheel Drive/4x4',
            'FuelTypePrimary': 'Gasoline',
            'DisplacementL': '2.0',
            'EngineCylinders': '4',
            'EngineHP': '147',
            'EngineKW': '',
            'EngineModel': 'MPI Nu',
            'EngineManufacturer': 'HMC',
            'ValveTrainDesign': 'Dual Overhead Cam (DOHC)',
            'FuelInjectionType': 'Multipoint Fuel Injection (MPFI)',
            'TransmissionSpeeds': '6',
            'TransmissionStyle': 'Automatic',
            'Doors': '4',
            'Seats': '5',
            'SeatRows': '2',
            'GVWR': 'Class 1C: 4,001 - 5,000 lb (1,814 - 2,268 kg)',
            'WheelBaseShort': '102.40',
            'WheelSizeFront': '17',
            'WheelSizeRear': '17',
            'EntertainmentSystem': '',
            'LowerBeamHeadlampLightSource': 'LED',
            'DaytimeRunningLight': 'Standard',
            'ABS': 'Standard',
            'ESC': 'Standard',
            'TractionControl': 'Standard',
            'BlindSpotMon': 'Standard',
            'ForwardCollisionWarning': 'Standard',
            'LaneDepartureWarning': 'Standard',
            'LaneKeepSystem': 'Standard',
            'RearCrossTrafficAlert': 'Standard',
            'RearVisibilitySystem': 'Standard',
            'TPMS': 'Direct',
            'AirBagLocCurtain': '1st and 2nd Rows',
            'AirBagLocFront': '1st Row (Driver and Passenger)',
            'AirBagLocSide': '1st Row (Driver and Passenger)',
            'AirBagLocKnee': '',
            'AirBagLocSeatCushion': '',
            'ErrorCode': '0',
            'ErrorText': '0 - VIN decoded clean.',
        }

    def test_reference_vin_populates_verified_vehicle_details(self):
        nhtsa = self.reference_decode()
        epa = {
            'mpg_city': 25,
            'mpg_highway': 30,
            'mpg_combined': 27,
            'epa_model': 'Kona AWD',
            'epa_option': '2.0L, 6-speed automatic',
            'epa_details': {
                'id': '12345',
                'year': '2021',
                'make': 'Hyundai',
                'model': 'Kona AWD',
                'city08': '25',
                'highway08': '30',
                'comb08': '27',
                'trany': 'Automatic',
                'drive': 'All Wheel Drive',
                'displ': '2.0',
                'cylinders': '4',
                'fuelType1': 'Regular Gasoline',
                'atvType': '',
            },
        }
        with (
            patch('app.vin_decode.fetch_nhtsa_decode', return_value=nhtsa),
            patch('app.vin_decode.lookup_epa_mpg', return_value=epa),
            patch('app.vin_decode._xai_api_key', return_value=''),
        ):
            result = decode_vin(REFERENCE_VIN, use_cache=False)

        self.assertTrue(result['ok'])
        vehicle = result['vehicle']
        self.assertEqual(vehicle['year'], 2021)
        self.assertEqual(vehicle['trim'], 'SEL')
        self.assertEqual(vehicle['mpg_combined'], 27)
        self.assertEqual(
            vehicle['exterior_features'],
            '17-inch front wheels\n17-inch rear wheels\nLED headlights\nDaytime running lights',
        )
        self.assertEqual(vehicle['interior_features'], '5-passenger seating\n2 seating rows')
        self.assertEqual(vehicle['entertainment_features'], '')
        self.assertIn('4WD/4-Wheel Drive/4x4', vehicle['mechanical_features'])
        self.assertIn('6-speed automatic transmission', vehicle['mechanical_features'])
        self.assertIn('2.0L 4-cylinder engine', vehicle['mechanical_features'])
        self.assertIn('Blind Spot Monitor', vehicle['safety_features'])
        self.assertIn('Side Curtain Airbags', vehicle['safety_features'])
        self.assertIn('Tire Pressure Monitoring System', vehicle['safety_features'])
        self.assertIn('Engine output: 147 hp', vehicle['additional_specifications'])
        self.assertIn('Wheelbase: 102.40 in', vehicle['additional_specifications'])
        self.assertIn('Backup Camera', result['features'])
        self.assertIn('Blind Spot Monitor', result['features'])
        self.assertIn('17-Inch Wheels', result['features'])
        self.assertNotIn('Alloy Wheels', result['features'])
        self.assertIn('Daytime Running Lights', result['features'])
        self.assertEqual(vehicle['doors'], '4')
        self.assertEqual(vehicle['passenger_capacity'], '5')
        self.assertEqual(
            vehicle['vin_decode_data']['NHTSA']['VehicleDescriptor'],
            'KM8K2CAA*MU',
        )
        self.assertEqual(
            vehicle['vin_decode_data']['NHTSA']['Manufacturer'],
            'HYUNDAI MOTOR CO',
        )
        self.assertEqual(vehicle['vin_decode_data']['EPA']['id'], '12345')
        self.assertEqual(
            vehicle['vin_decode_data']['EPA']['Matched configuration'],
            '2.0L, 6-speed automatic',
        )

    def test_missing_or_absent_equipment_is_not_invented(self):
        details = derive_vehicle_details({
            'WheelSizeFront': '',
            'WheelSizeRear': 'Not Applicable',
            'EntertainmentSystem': '',
            'ABS': 'Not Available',
            'BlindSpotMon': 'No',
            'TPMS': '',
            'AirBagLocFront': '',
            'EngineHP': '',
        })
        for value in details.values():
            self.assertEqual(value, '')

    def test_unavailable_values_do_not_prefill_vehicle_fields(self):
        vehicle = map_vehicle_fields({
            'Doors': 'Not Applicable',
            'Seats': 'Not Applicable',
            'GVWR': 'Not Applicable',
        })

        self.assertIsNone(vehicle['doors'])
        self.assertIsNone(vehicle['passenger_capacity'])
        self.assertIsNone(vehicle['gvwr'])

    def test_all_useful_decoder_fields_are_grouped_and_unknown_values_are_omitted(self):
        groups = group_vin_decode_data({
            'VIN': REFERENCE_VIN,
            'EngineManufacturer': 'HMC',
            'RearCrossTrafficAlert': 'Standard',
            'AirBagLocCurtain': '1st and 2nd Rows',
            'PlantCountry': 'SOUTH KOREA',
            'WheelSizeFront': '17',
            'TrailerType': 'Not Applicable',
            'EntertainmentSystem': '',
            'ErrorCode': '0',
            'ErrorText': '0 - VIN decoded clean.',
            'SomeNegativeEquipmentField': 'No',
            'SomeZeroValuedField': '0',
            'EPA': {
                'year': '2021',
                'make': 'Hyundai',
                'model': 'Kona AWD',
                'city08': '25',
                'trany': 'Automatic',
            },
        })
        by_label = {group['label']: group['fields'] for group in groups}
        displayed = {
            item['label']: item['value']
            for fields in by_label.values()
            for item in fields
        }

        self.assertEqual(displayed['Engine Manufacturer'], 'HMC')
        self.assertEqual(displayed['Rear Cross Traffic Alert'], 'Standard')
        self.assertEqual(displayed['Curtain Airbag Location'], '1st and 2nd Rows')
        self.assertEqual(displayed['Plant Country'], 'SOUTH KOREA')
        self.assertEqual(displayed['Wheel Size Front'], '17')
        self.assertEqual(displayed['Some Negative Equipment Field'], 'No')
        self.assertEqual(displayed['Some Zero Valued Field'], '0')
        self.assertEqual(by_label['EPA Fuel Economy'][0]['label'], 'EPA Year')
        self.assertEqual(by_label['EPA Fuel Economy'][0]['value'], '2021')
        self.assertNotIn('Trailer Type', displayed)
        self.assertNotIn('Error Code', displayed)
        self.assertNotIn('Error Text', displayed)

    def test_grok_estimates_are_separate_and_never_replace_nhtsa_or_epa(self):
        raw = {
            'NHTSA': self.reference_decode(),
            'EPA': {'city08': '25', 'make': 'Hyundai'},
            'Grok': {
                'estimates': {
                    'engine_torque_lb_ft': '132 lb-ft',
                    'engine_horsepower_hp': '999 hp',
                    'safety_features': 'Automatic Emergency Braking',
                },
            },
        }

        groups = group_vin_decode_data(raw)
        by_label = {group['label']: group['fields'] for group in groups}
        displayed = {
            item['label']: item['value']
            for fields in by_label.values()
            for item in fields
        }

        self.assertEqual(displayed['Engine Horsepower'], '147')
        self.assertEqual(displayed['EPA City MPG'], '25')
        self.assertEqual(
            by_label['Grok Estimates (not manufacturer-verified)'],
            [{'label': 'Engine torque', 'value': '132 lb-ft'}],
        )
        self.assertEqual(
            groups[-1]['label'],
            'Grok Estimates (not manufacturer-verified)',
        )

    def test_grok_photo_annotations_fill_only_matching_high_confidence_categories(self):
        annotations = classify_grok_photo_annotations([
            {
                'scene': 'exterior_side',
                'highlights': [
                    {
                        'label': 'Alloy Wheels',
                        'category': 'feature',
                        'confidence': 0.91,
                    },
                    {
                        'label': 'New Tires',
                        'category': 'feature',
                        'confidence': 0.72,
                    },
                    {
                        'label': 'Possible Scratch',
                        'category': 'imperfection',
                        'confidence': 0.99,
                    },
                ],
            },
            {
                'scene': 'interior_dash',
                'highlights': [
                    {
                        'label': 'Apple CarPlay',
                        'category': 'feature',
                        'confidence': 0.9,
                    },
                    {
                        'label': 'Heated Seats',
                        'category': 'feature',
                        'confidence': 0.98,
                    },
                ],
            },
            {
                'scene': 'interior_cabin',
                'highlights': [{
                    'label': 'Leather Seats',
                    'category': 'feature',
                    'confidence': 0.88,
                }, {
                    'label': 'Seat Surface',
                    'category': 'detail',
                    'confidence': 0.9,
                }, {
                    'label': 'Infotainment Screen',
                    'category': 'detail',
                    'confidence': 0.89,
                }],
            },
        ])

        self.assertEqual(
            [item['label'] for item in annotations['exterior']],
            ['Alloy Wheels'],
        )
        self.assertEqual(
            [item['label'] for item in annotations['interior']],
            ['Leather Seats', 'Seat Surface'],
        )
        self.assertEqual(
            [item['label'] for item in annotations['entertainment']],
            ['Apple CarPlay', 'Infotainment Screen'],
        )

    def test_grok_photo_observations_render_after_authoritative_nhtsa_fields(self):
        raw = {
            'NHTSA': {'VIN': REFERENCE_VIN, 'EntertainmentSystem': 'Not Applicable'},
            'Grok': {
                'estimates': {'city_mpg': '26 MPG'},
                'photo_annotations': {
                    'version': 1,
                    'exterior': [{
                        'label': 'Alloy Wheels',
                        'scene': 'exterior_side',
                        'confidence': 0.91,
                    }],
                    'interior': [],
                    'entertainment': [{
                        'label': 'Apple CarPlay',
                        'scene': 'interior_dash',
                        'confidence': 0.9,
                    }],
                },
            },
        }

        groups = group_vin_decode_data(raw)

        self.assertEqual(groups[0]['label'], 'Vehicle Identity')
        labels = [group['label'] for group in groups]
        self.assertLess(labels.index('Vehicle Identity'), labels.index(
            'Grok Estimates (not manufacturer-verified)',
        ))
        self.assertLess(labels.index('Vehicle Identity'), labels.index(
            'Grok Visual Details (verify)',
        ))
        self.assertIn('Grok Visual Details', labels[-1])
        photo_values = groups[-1]['fields']
        self.assertEqual(len(photo_values), 2)

    def test_grok_request_is_small_and_only_validates_missing_allowlisted_specs(self):
        nhtsa = self.reference_decode()
        original = json.loads(json.dumps(nhtsa))
        response_content = {
            'engine_horsepower_hp': 999,
            'engine_torque_lb_ft': 132,
            'city_mpg': 99,
            'curb_weight_lb': 'not sure',
            'safety_features': 'Airbags',
            'drivetrain': 'sedan',
        }

        def mock_urlopen(request, timeout):
            request_body = json.loads(request.data)
            self.assertEqual(request_body['model'], 'grok-4.20-0309-non-reasoning')
            self.assertEqual(request_body['max_tokens'], 128)
            self.assertEqual(request_body['temperature'], 0)
            self.assertNotIn(REFERENCE_VIN, request_body['messages'][1]['content'])
            prompt = request_body['messages'][1]['content']
            self.assertNotIn('"city_mpg"', prompt)
            self.assertNotIn('"engine_horsepower_hp"', prompt)
            return io.BytesIO(json.dumps({
                'choices': [{
                    'message': {'content': json.dumps(response_content)},
                }],
            }).encode())

        with (
            patch('app.vin_decode._xai_api_key', return_value='test-key'),
            patch('app.vin_decode.urllib.request.urlopen', side_effect=mock_urlopen),
        ):
            result = enrich_vin_decode_data_with_grok({
                'NHTSA': nhtsa,
                'EPA': {'city08': '25'},
            })

        self.assertEqual(result, {
            'estimates': {
                'engine_torque_lb_ft': '132 lb-ft',
            },
        })
        self.assertEqual(nhtsa, original)

    def test_decode_keeps_grok_failures_nonfatal_and_preserves_nhtsa_payload(self):
        nhtsa = self.reference_decode()
        with (
            patch('app.vin_decode.fetch_nhtsa_decode', return_value=nhtsa),
            patch('app.vin_decode.lookup_epa_mpg', return_value=None),
            patch('app.vin_decode._xai_api_key', return_value='test-key'),
            patch(
                'app.vin_decode.enrich_vin_decode_data_with_grok',
                side_effect=RuntimeError('provider unavailable'),
            ),
        ):
            result = decode_vin(REFERENCE_VIN, use_cache=False)

        self.assertTrue(result['ok'])
        self.assertIn('NHTSA', result['vehicle']['vin_decode_data'])
        self.assertEqual(
            result['vehicle']['vin_decode_data']['Grok']['estimates'],
            {},
        )
        self.assertTrue(any(
            'Grok estimates are temporarily unavailable' in warning
            for warning in result['warnings']
        ))


class VehicleVINDataFormTests(unittest.TestCase):
    def build_form(self, vin, decoded_vin):
        return VehicleForm(
            formdata=MultiDict({
                'year': '2021',
                'make': 'Hyundai',
                'model': 'Kona',
                'vin': vin,
                'vin_decode_data': json.dumps({'NHTSA': {'VIN': decoded_vin}}),
                'price': '12000',
                'mileage': '50000',
                'condition': 'used',
                'title_status': 'clean',
                'status': 'available',
                'drivetrain': '',
            }),
            meta={'csrf': False},
        )

    def test_decoder_data_is_only_accepted_for_its_vehicle_vin(self):
        app = Flask(__name__)
        app.config.update(SECRET_KEY='test', WTF_CSRF_ENABLED=False)
        with app.app_context():
            matching = self.build_form(REFERENCE_VIN, REFERENCE_VIN)
            self.assertTrue(matching.validate(), matching.errors)

            mismatched = self.build_form(REFERENCE_VIN, '1HGCM82633A004352')
            self.assertFalse(mismatched.validate())
            self.assertIn('vin_decode_data', mismatched.errors)


if __name__ == '__main__':
    unittest.main()

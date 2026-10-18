import json
import unittest
from unittest.mock import patch

from flask import Flask
from werkzeug.datastructures import MultiDict

from app.forms import VehicleForm
from app.vin_decode import (
    decode_vin, derive_vehicle_details, group_vin_decode_data, map_vehicle_fields,
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

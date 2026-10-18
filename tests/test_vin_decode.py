import unittest
from unittest.mock import patch

from app.vin_decode import decode_vin, derive_vehicle_details, map_vehicle_fields


REFERENCE_VIN = 'KM8K2CAAXMU609019'


class VinDecodeDetailTests(unittest.TestCase):
    def reference_decode(self):
        return {
            'VIN': REFERENCE_VIN,
            'ModelYear': '2021',
            'Make': 'HYUNDAI',
            'Model': 'Kona',
            'Trim': 'SEL',
            'BodyClass': 'Sport Utility Vehicle [SUV]/Multipurpose Vehicle [MPV]',
            'DriveType': '4WD/4-Wheel Drive/4x4',
            'FuelTypePrimary': 'Gasoline',
            'DisplacementL': '2.0',
            'EngineCylinders': '4',
            'EngineHP': '147',
            'EngineKW': '',
            'EngineModel': 'MPI Nu',
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


if __name__ == '__main__':
    unittest.main()

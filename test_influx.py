import unittest
import datetime

import influx

influx.dbname = 'test'
influx.measurement = 'hv'


class TestInflux(unittest.TestCase):

    def setUp(self) -> None:
        """test table recreated for each test"""
        influx.connect_db('localhost', 8086, reset=True)

    def tearDown(self) -> None:
        influx.client.close()

    def test_no_server(self):
        """test wait for server logic"""
        with self.assertRaises(ValueError) as err:
            influx.wait_for_server('not_a_server', 8086,
                                    nretries=0)

    def test_get_entries(self):
        """test that the empty table has no points"""
        self.assertEqual(influx.get_entries(), [])

    def test_fill(self):
        """test that one can write a point"""
        to_write = [
            {
                'measurement': influx.measurement,
                'time': datetime.datetime.now(),
                'tags': {'channel': 1},
                'fields': {
                    'voltage': 100.,
                    'leakage': 0.1
                }
            }
        ]
        influx.client.write_points(to_write)
        self.assertEqual(len(influx.get_entries()), 1)


if __name__ == "__main__":
    unittest.main()
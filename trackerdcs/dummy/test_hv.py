import unittest
from .hv import DummyHV


class TestDummyHV(unittest.TestCase):

    def setUp(self) -> None:
        self.hv = DummyHV('hv')

    def test_1(self):
        nchans = 4
        hv = DummyHV('hv', nchans)
        self.assertTrue(len(hv.channels), nchans)
        self.assertListEqual([chan.number for chan in hv.channels],
                             list(range(nchans)))
        self.assertEqual(sum(chan.vreq for chan in hv.channels), 0)

    def test_command(self):
        with self.assertRaises(ValueError) as err:
            self.hv.command('/hv2/cmd/switch/0', 'on')
        with self.assertRaises(ValueError) as err:
            self.hv.command('/hv/cmd/getlost/0', 'on')
        with self.assertRaises(IndexError) as err:
            self.hv.command('/hv/cmd/switch/1', 'on')
        self.hv.command('/hv/cmd/switch/0', 'on')
        self.assertTrue(self.hv.channels[0].on)
        self.hv.command('/hv/cmd/switch/0', 'off')
        self.assertFalse(self.hv.channels[0].on)
        voltage = 100
        self.hv.command('/hv/cmd/setv/0', voltage)
        self.assertEqual(self.hv.channels[0].vreq, voltage)
        with self.assertRaises(ValueError) as err:
            self.hv.command('/hv/cmd/setv/0', 'blah')


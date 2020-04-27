import unittest
from .hv import DummyHV


class TestDummyHV(unittest.TestCase):

    def setUp(self) -> None:
        self.hv = DummyHV()

    def test_1(self):
        nchans = 4
        hv = DummyHV(nchans)
        self.assertTrue(len(hv.channels), nchans)
        self.assertListEqual([chan.number for chan in hv.channels],
                             list(range(nchans)))
        self.assertEqual(sum(chan.vreq for chan in hv.channels), 0)

    def test_command(self):
        with self.assertRaises(ValueError) as err:
            self.hv.command('/hv2/0/switch', 'on')
        with self.assertRaises(ValueError) as err:
            self.hv.command('/hv/0/getlost', 'on')
        with self.assertRaises(IndexError) as err:
            self.hv.command('/hv/1/switch', 'on')
        self.assertFalse(self.hv.command('/hv/0/status', 'on'))
        self.hv.command('/hv/0/switch', 'on')
        self.assertTrue(self.hv.channels[0].on)
        self.assertTrue(self.hv.command('/hv/0/status', 'on'))
        self.hv.command('/hv/0/switch', 'off')
        self.assertFalse(self.hv.channels[0].on)
        voltage = 100
        self.hv.command('/hv/0/setv', voltage)
        self.assertEqual(self.hv.channels[0].vreq, voltage)
        self.assertEqual(self.hv.command('/hv/0/status', 'vreq'), voltage)
        with self.assertRaises(ValueError) as err:
            self.hv.command('/hv/0/setv', 'blah')


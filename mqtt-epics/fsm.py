#!/usr/bin/env python3

# from transitions import Machine
from transitions.extensions import LockedMachine as Machine
import enum
import paho.mqtt.client as mqtt
import json
import epics
import threading
import time
import logging

log = logging.getLogger("epics")
logging.basicConfig(format='%(asctime)s %(message)s')
log.setLevel(logging.DEBUG)

def retry(maxTries):
    def dec_fn(fn):
        def do_fn(*args, **kwargs):
            counter = 0
            ret = None
            while ret is None and counter < maxTries:
                time.sleep(.1)
                ret = fn(*args, **kwargs)
                counter += 1
            return ret
        return do_fn
    return dec_fn

class DeadbandPV(epics.PV):
    def __init__(self, *args, **kwargs):
        self.deadBand = kwargs.pop("dead_band")
        self.oldValue = 0.
        self.oldCallback = kwargs.pop("callback")
        kwargs["callback"] = self.dbCallback
        super(DeadbandPV, self).__init__(*args, **kwargs)

    def dbCallback(self, pvname, value, **kwargs):
        log.debug(f"In callback - {pvname} = {value}, old value = {self.oldValue}")
        if abs(value - self.oldValue) > self.deadBand:
            self.oldValue = value
            self.oldCallback(pvname, value, **kwargs)


class EPICSChannel(object):
    def __init__(self, board, chan, connection_callback, update_callback, verbose=False):
        self.board = board
        self.chan = chan
        self.prefix = f"cleanroom:{self.board:02}:{self.chan:03}:"
        self.PVs = {}
        for var in ["Pw", "Status"]:
            self.PVs[var] = epics.PV(self.prefix + var, auto_monitor=True, verbose=verbose, callback=update_callback, connection_callback=connection_callback)
            time.sleep(0.1)
        for var in ["V0Set", "I0Set", "VMon", "IMon"]:
            self.PVs[var] = DeadbandPV(self.prefix + var, dead_band=0.01, auto_monitor=True, verbose=verbose, callback=update_callback, connection_callback=connection_callback)
            time.sleep(0.1)

    def reconnect(self):
        for pv in self.PVs.values():
            pv.reconnect()

    def switch_on(self):
        self.PVs["Pw"].put("On")
    def switch_off(self):
        self.PVs["Pw"].put("Off")
    def is_on(self):
        # PV.get() uses cached value because we set auto_monitor to True
        # @retry(5)
        # def get():
        #     return self.PVs["Pw"].get(timeout=1, use_monitor=False)
        # ret = get()
        ret = self.PVs["Pw"].get()
        return ret == 1
    def is_off(self):
        return not self.is_on()

    @property
    def setV(self):
        return self.PVs["V0Set"].get()
    @setV.setter
    def setV(self, value):
        self.PVs["V0Set"].put(value)

    @property
    def setI(self):
        return self.PVs["I0Set"].get()
    @setI.setter
    def setI(self, value):
        self.PVs["I0Set"].put(value)

    @property
    def status(self):
        return self.PVs["Status"].get()
    @property
    def vMon(self):
        return self.PVs["VMon"].get()
    @property
    def iMon(self):
        return self.PVs["IMon"].get()

class EPICSLVChannel(EPICSChannel):
    def __init__(self, board, chan, connection_callback, update_callback, verbose=False):
        super(EPICSLVChannel, self).__init__(board, chan, connection_callback, update_callback, verbose)
    
        for var in ["UNVThr", "OVVThr"]:
            self.PVs[var] = epics.PV(self.prefix + var, auto_monitor=True, verbose=verbose, callback=update_callback, connection_callback=connection_callback)
            time.sleep(0.1)
        for var in ["Temp"]:
            self.PVs[var] = DeadbandPV(self.prefix + var, dead_band=0.01, auto_monitor=True, verbose=verbose, callback=update_callback, connection_callback=connection_callback)
            time.sleep(0.1)

    @property
    def unVThr(self):
        return self.PVs["UNVThr"].get()
    @unVThr.setter
    def unVThr(self, value):
        self.PVs["UNVThr"].put(value)

    @property
    def ovVThr(self):
        return self.PVs["OVVThr"].get()
    @ovVThr.setter
    def ovVThr(self, value):
        self.PVs["OVVThr"].put(value)

    @property
    def temp(self):
        return self.PVs["Temp"].get()


class EPICSHVChannel(EPICSChannel):
    def __init__(self, board, chan, connection_callback, update_callback, verbose=False):
        super(EPICSHVChannel, self).__init__(board, chan, connection_callback, update_callback, verbose)

        # adjust dead bands from EPICSChannel values
        # here currents are in uA
        for var in ["V0Set", "VMon"]:
            self.PVs[var].deadBand = 1.
        for var in ["I0Set", "IMon"]:
            self.PVs[var].deadBand = 0.01 # 0.01=10nA in high-power mode; 0.001=1nA in high-res mode

        for var in ["RUp", "RDWn", "ImRange"]:
            self.PVs[var] = epics.PV(self.prefix + var, auto_monitor=True, verbose=verbose, callback=update_callback, connection_callback=connection_callback)
            time.sleep(0.1)

    # ramp speed in V/s
    @property
    def rampUpSpeed(self):
        return self.PVs["RUp"].get()
    @rampUpSpeed.setter
    def rampUpSpeed(self, value):
        self.PVs["RUp"].put(value)

    # ramp speed in V/s
    @property
    def rampDownSpeed(self):
        return self.PVs["RDWn"].get()
    @rampDownSpeed.setter
    def rampDownSpeed(self, value):
        self.PVs["RDWn"].put(value)
    
    # "high" -> high-power, 3.5mA max
    # "low" -> high-resolution, 350uA max
    @property
    def range(self):
        return self.PVs["ImRange"].get()
    @range.setter
    def range(self, value):
        assert(value in ["Low", "High"])
        self.PVs["ImRange"].put(value)


class PSStates(enum.Enum):
    INIT = -1
    DISCONNECTED = 0
    CONNECTED = 1
    LV_OFF = 2
    LV_ON = 3
    HV_RAMP = 4
    HV_ON = 5
    ERROR = 6

class TrackerChannel(object):

    def __init__(self, name, lv, hv):
        log.debug("\n\n\n == Creating tracker channel == \n\n\n")

        transitions = [
            { 'trigger': 'fsm_connect_epics', 'source': PSStates.INIT, 'dest': PSStates.CONNECTED, 'before': '_connect_epics' },
            { 'trigger': 'fsm_reconnect_epics', 'source': PSStates.DISCONNECTED, 'dest': PSStates.CONNECTED, 'before': '_reconnect_epics' },
            { 'trigger': 'fsm_disconnect_epics', 'source': '*', 'dest': PSStates.DISCONNECTED },
        ]

        self.machine = Machine(model=self, states=PSStates, transitions=transitions, initial=PSStates.INIT)

        self.machine.on_enter_LV_ON('print_fsm')
        self.machine.on_enter_LV_OFF('print_fsm')
        self.machine.on_enter_HV_ON('print_fsm')
        self.machine.on_enter_HV_RAMP('print_fsm')
        self.machine.on_enter_CONNECTED('print_fsm')
        self.machine.on_enter_DISCONNECTED('print_fsm')
        self.machine.on_enter_INIT('print_fsm')
        self.machine.on_enter_ERROR('print_fsm')
        # update our state depending on the actual CAEN state as soon as we connect
        self.machine.on_enter_CONNECTED('epics_update_status')

        self.name = name
        self.lv_board, self.lv_chan = lv
        self.hv_board, self.hv_chan = hv

        self._lock = threading.Lock()
        self._changed = False

    def print_fsm(self):
        log.debug(f"FSM state: {self.state}")

    def _connect_epics(self):
        self.epics_LV = EPICSLVChannel(self.lv_board, self.lv_chan, self.epics_connection_callback, self.epics_update_callback)
        self.epics_HV = EPICSHVChannel(self.hv_board, self.hv_chan, self.epics_connection_callback, self.epics_update_callback)

        self.machine.add_transition('cmd_lv_on', PSStates.LV_OFF, None, before=self.epics_LV.switch_on)
        self.machine.add_transition('cmd_lv_off', PSStates.LV_ON, None, before=self.epics_LV.switch_off)
        self.machine.add_transition('cmd_hv_on', PSStates.LV_ON, None, before=self.epics_HV.switch_on)
        self.machine.add_transition('cmd_hv_off', PSStates.HV_ON, None, before=self.epics_HV.switch_off)

        self.PV_clear_alarm = epics.PV("cleanroom:ClearAlarm", verbose=True)
        self.machine.add_transition('cmd_clear_alarms', PSStates.ERROR, None, before=lambda: self.PV_clear_alarm.put("Yes"))

    def _reconnect_epics(self):
        self.epics_LV.reconnect()
        self.epics_HV.reconnect()

    def epics_update_status(self):
        """Force FSM states depending on what EPICS tells us on the CAEN state"""
        # the first callbacks could be called before we have initialized the EPICSChannel objects
        if self.state not in [PSStates.DISCONNECTED, PSStates.INIT]:
            if self.epics_LV.status > 1 or self.epics_HV.status > 5:
                self.to_ERROR()
                return
            if self.epics_LV.status == 1:
                if self.epics_HV.status in [3, 5]:
                    self.to_HV_RAMP()
                elif self.epics_HV.status == 1:
                    self.to_HV_ON()
                else:
                    self.to_LV_ON()
            else:
                self.to_LV_OFF()

    def epics_update_callback(self, pvname, value, **kwargs):
        log.debug(f"In update callback: got {pvname}, {value}")
        if pvname.endswith("Pw") or pvname.endswith("Status"):
            self.epics_update_status()
        with self._lock:
            self._changed = True

    def epics_connection_callback(self, pvname, conn, **kwargs):
        log.debug(f"In connection callback: got {pvname}, {conn}")
        if conn:
            self.fsm_reconnect_epics()
        else:
            self.fsm_disconnect_epics()

    def publish(self):
        if hasattr(self, "client"):
            with self._lock:
                if self._changed:
                    msg = json.dumps(self.status())
                    log.debug(f"Sending: {msg}")
                    self.client.publish('/{}/status'.format(self.name), msg)
                    self._changed = False

    def status(self):
        return {
            'lv_board': self.lv_board,
            'lv_channel': self.lv_chan,
            'hv_board': self.hv_board,
            'hv_channel': self.hv_chan,
            'lv_status': self.epics_LV.status,
            'hv_status': self.epics_HV.status,
            'lv_setV': self.epics_LV.setV,
            'hv_setV': self.epics_HV.setV,
            'lv_vMon': self.epics_LV.vMon,
            'hv_vMon': self.epics_HV.vMon,
            'lv_iMon': self.epics_LV.iMon,
            'hv_iMon': self.epics_HV.iMon,
            'lv_temp': self.epics_LV.temp,
        }

    def command(self, topic, message):
        parts = topic.split('/')[1:]
        if len(parts) == 3:
            device, cmd, command = parts
        elif len(parts) == 4:
            device, cmd, command, lvhv = parts
            assert(lvhv in ["lv", "hv"])
        if cmd != 'cmd':
            raise ValueError('command messages should be of the form /device/cmd/#')
        message = message.decode() # message arrives as bytes array
        commands = ['switch', 'setv', 'clear', 'reconnect']
        assert(device == self.name)
        assert(command in commands)
        if command == 'switch':
            assert(message in ["on", "off"])
            fn = getattr(self, f"cmd_{lvhv}_{message}")
            fn()
        elif command == 'setv':
            log.debug(f'Setting V0 to {message}')
            if lvhv == "lv":
                self.epics_LV.setV = float(message)
            elif lvhv == "hv":
                self.epics_HV.setV = float(message)
        elif command == 'clear':
            log.debug("Clearing alarms!")
            self.cmd_clear_alarms()
        elif command == 'reconnect':
            log.debug("Reconnecting!")
            self.fsm_reconnect_epics()

def on_connect(client, userdata, flags, rc):
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe('/{}/cmd/#'.format(client.device.name))
    # make sure the initial values are published at restart
    client.device.publish()

def on_message(client, userdata, msg):
    log.debug(f"Received {msg.topic}, {msg.payload}")
    # MQTT catches all exceptions in the callbacks, so they'll go unnoticed
    try:
        client.device.command(msg.topic, msg.payload)
    except Exception as e:
        log.error(f"Issue processing command: {e}")


def run(device, mqtt_host):
    device.fsm_connect_epics()

    client = mqtt.Client()
    client.device = device
    device.client = client
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(mqtt_host, 1883, 60)
    # client.loop_forever()
    client.loop_start()
    while 1:
        epics.ca.poll()
        device.publish()
        time.sleep(1)
    time.sleep(1)
    client.disconnect()
    client.loop_stop()

if __name__ == '__main__':
    import sys
    device_name, mqtt_host = sys.argv[1:]
    device = TrackerChannel(device_name, (0, 0), (12, 0))
    run(device, mqtt_host)

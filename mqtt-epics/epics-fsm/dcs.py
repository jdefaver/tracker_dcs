#!/usr/bin/env python3

from transitions.extensions import LockedMachine as Machine
from transitions.core import MachineError
import enum
import paho.mqtt.client as mqtt
import json
import threading
import time
import logging
import epics

from channel import TrackerChannel, PSStates

log = logging.getLogger("DCS")
logging.basicConfig(format="== %(asctime)s - %(name)s - %(levelname)s - %(message)s")
log.setLevel(logging.INFO)

class DCSStates(enum.Enum):
    INIT = -1
    DISCONNECTED = 0
    CONNECTED = 1
    LV_OFF = 2
    LV_MIX = 3
    LV_ON = 4
    HV_RAMP = 5
    HV_MIX = 6
    HV_ON = 7
    ERROR = 8

class TrackerDCS(object):

    def __init__(self, name, verbose=False):
        log.info(f"Initializing DCS: {name}")
        self.name = name
        self.verbose = verbose
        self.channels = []

        transitions = [
            { "trigger": "fsm_connect_epics", "source": DCSStates.INIT, "dest": DCSStates.CONNECTED, "before": "_connect_epics" },
            { "trigger": "fsm_reconnect_epics", "source": DCSStates.DISCONNECTED, "dest": DCSStates.CONNECTED, "before": "_reconnect_epics" },
            { "trigger": "fsm_disconnect_epics", "source": "*", "dest": DCSStates.DISCONNECTED },
        ]

        self._lock = threading.Lock()
        self._changed = False

        self.machine = Machine(model=self, states=DCSStates, transitions=transitions, initial=DCSStates.INIT)

        for s in DCSStates:
            getattr(self.machine, "on_enter_" + str(s).split(".")[1])("print_fsm")

        log.info(f"Done - state is {self.state}")

    def print_fsm(self):
        with self._lock:
            if self._changed:
                log.info(f"FSM state: {self.state}")

    def add_channel(self, lv, hv):
        assert(self.state is DCSStates.INIT)
        assert(len(lv) == 2 and 0 <= lv[0] <= 4 and 0 <= lv[1] <= 7)
        assert(len(hv) == 2 and 12 <= hv[0] <= 15 and 0 <= hv[1] <= 11)
        self.channels.append(TrackerChannel(f"module_{len(self.channels)}", lv, hv, verbose=self.verbose))

    def _connect_epics(self):
        for chan in self.channels:
            chan.fsm_connect_epics()
        
        self.machine.add_transition("cmd_lv_on", [DCSStates.LV_OFF, DCSStates.LV_MIX], None, before=self.switch_lv_on)
        self.machine.add_transition("cmd_lv_off", [DCSStates.LV_ON, DCSStates.LV_MIX], None, before=self.switch_lv_off)
        self.machine.add_transition("cmd_hv_on", [DCSStates.LV_ON, DCSStates.HV_MIX, DCSStates.HV_RAMP], None, before=self.switch_hv_on)
        self.machine.add_transition("cmd_hv_off", [DCSStates.HV_ON, DCSStates.HV_MIX, DCSStates.HV_RAMP], None, before=self.switch_hv_off)

        self.PV_clear_alarm = epics.PV("cleanroom:ClearAlarm", verbose=self.verbose)
        self.machine.add_transition("cmd_clear_alarms", DCSStates.ERROR, None, before=lambda: self.PV_clear_alarm.put("Yes"))

    def _reconnect_epics(self):
        for chan in self.channels:
            chan.fsm_reconnect_epics()

    def switch_lv_on(self):
        for chan in self.channels:
            try:
                chan.cmd_lv_on()
            except MachineError as e:
                log.error(e)

    def switch_lv_off(self):
        for chan in self.channels:
            try:
                chan.cmd_lv_off()
            except MachineError as e:
                log.error(e)

    def switch_hv_on(self):
        for chan in self.channels:
            try:
                chan.cmd_hv_on()
            except MachineError as e:
                log.error(e)

    def switch_hv_off(self):
        for chan in self.channels:
            try:
                chan.cmd_hv_off()
            except MachineError as e:
                log.error(e)

    def update_status(self):
        old_state = self.state
        if self.state is DCSStates.INIT:
            pass
        elif any(c.state is PSStates.ERROR for c in self.channels):
            self.to_ERROR()
        elif any(c.state is PSStates.DISCONNECTED for c in self.channels):
            self.to_DISCONNECTED()
        elif all(c.state is PSStates.CONNECTED for c in self.channels):
            self.to_CONNECTED()
        elif all(c.state is PSStates.LV_OFF for c in self.channels):
            self.to_LV_OFF()
        elif any(c.state is PSStates.LV_OFF for c in self.channels) and any(c.state is PSStates.LV_ON for c in self.channels):
            self.to_LV_MIX()
        elif all(c.state is PSStates.LV_ON for c in self.channels):
            self.to_LV_ON()
        elif all(c.state is PSStates.HV_ON for c in self.channels):
            self.to_HV_ON()
        elif any(c.state is PSStates.HV_RAMP for c in self.channels):
            self.to_HV_RAMP()
        elif any(c.state is PSStates.LV_ON for c in self.channels) and any(c.state is PSStates.HV_ON for c in self.channels):
            self.to_HV_MIX()
        else:
            log.fatal(f"Should not happen! States are {', '.join(str(c.state) for c in self.channels)}")
        if self.state != old_state:
            with self._lock:
                self._changed = True

    def command(self, topic, message):
        commands = ["switch", "setv", "clear", "refresh", "reconnect"]
        parts = topic.split("/")[1:]
        device, cmd, command = parts[:3]
        assert(device == self.name)
        assert(cmd == "cmd")
        assert(command in commands)
        if len(parts) >= 4:
            lvhv = parts[3]
            assert(lvhv in ["lv", "hv"])
        if len(parts) >= 5:
            chanID = int(parts[4].split("_")[1])
            assert(0 <= chanID < len(self.channels))
            channel = self.channels[chanID]

        message = message.decode() # message arrives as bytes array
        if command == "switch":
            assert(message in ["on", "off"])
            log.debug(f"Calling cmd: {lvhv}_{message}")
            fn = f"cmd_{lvhv}_{message}"
            getattr(self, fn)()
        elif command == "setv":
            log.debug(f"Setting {lvhv} of channel {channel.name} V0 to {message}")
            if lvhv == "lv":
                channel.epics_LV.setV = float(message)
            elif lvhv == "hv":
                channel.epics_HV.setV = float(message)
        elif command == "clear":
            log.debug("Clearing alarms!")
            self.cmd_clear_alarms()
        elif command == "refresh":
            self.publish(force=True)
        elif command == "reconnect":
            log.debug("Reconnecting!")
            self.fsm_reconnect_epics()

    def publish(self, force=False):
        for chan in self.channels:
            chan.publish(force)
        if hasattr(self, "client"):
            with self._lock:
                if self._changed or force:
                    msg = json.dumps(self.status())
                    log.debug(f"Sending: {msg}")
                    self.client.publish("/{}/status".format(self.name), msg)
                    self._changed = False

    def status(self):
        return {
            "fsm_state": str(self.state).split(".")[1],
        }

    def launch_mqtt(self, host):
        def on_connect(client, userdata, flags, rc):
            # Subscribing in on_connect() means that if we lose the connection and
            # reconnect then subscriptions will be renewed.
            client.subscribe(f"/{self.name}/cmd/#")
            # make sure the initial values are published at restart
            self.publish(force=True)

        def on_message(client, userdata, msg):
            log.debug(f"Received {msg.topic}, {msg.payload}")
            # MQTT catches all exceptions in the callbacks, so they"ll go unnoticed
            try:
                self.command(msg.topic, msg.payload)
            except Exception as e:
                log.error(f"Issue processing command: {e}")

        client = mqtt.Client()
        self.client = client
        for chan in self.channels:
            chan.client = client

        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(mqtt_host, 1883, 60)
        client.loop_start()
        while 1:
            epics.ca.poll()
            device.update_status()
            device.publish()
            time.sleep(1)
        client.disconnect()
        client.loop_stop()

if __name__ == "__main__":
    import sys
    # FIXME use argparse
    device_name, mqtt_host = sys.argv[1:3]
    verbose = False
    if len(sys.argv) > 3:
        verbose = sys.argv[3]
        if verbose == "-v":
            log.setLevel(logging.DEBUG)
            logging.getLogger("epics").setLevel(logging.DEBUG)
            verbose = True

    device = TrackerDCS(device_name, verbose=verbose)
    device.add_channel((0, 0), (12, 0))
    device.add_channel((0, 1), (12, 1))
    device.fsm_connect_epics()
    device.launch_mqtt(mqtt_host)

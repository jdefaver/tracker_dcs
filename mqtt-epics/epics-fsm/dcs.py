#!/usr/bin/env python3

import enum
import json
import threading
import time
import logging
import yaml
import argparse

from transitions.extensions import LockedMachine as Machine
from transitions.core import MachineError

import paho.mqtt.client as mqtt
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

    def __init__(self, configPath, verbose=False):
        log.info(f"Initializing DCS")
        self.verbose = verbose

        transitions = [
            { "trigger": "fsm_connect_epics", "source": DCSStates.INIT, "dest": DCSStates.CONNECTED, "before": "_connect_epics" },
            { "trigger": "fsm_reconnect_epics", "source": DCSStates.DISCONNECTED, "dest": DCSStates.CONNECTED, "before": "_reconnect_epics" },
            { "trigger": "fsm_disconnect_epics", "source": "*", "dest": DCSStates.DISCONNECTED },
        ]

        self._lock = threading.Lock()
        self._changed = True

        self.machine = Machine(model=self, states=DCSStates, transitions=transitions, initial=DCSStates.INIT)

        for s in DCSStates:
            getattr(self.machine, "on_enter_" + str(s).split(".")[1])("print_fsm")
        
        self.all_channels = {}
        self.active_channels = {}
        with open(configPath) as f:
            self.config = yaml.safe_load(f)
        self.name = self.config.get("name", "dcs")
        for chan_id,chan_cfg in self.config["channels"].items():
            self.add_channel(chan_id, chan_cfg)

        log.info(f"Done - state is {self.state}")

    def print_fsm(self):
        with self._lock:
            if self._changed:
                log.info(f"FSM state: {self.state}")

    def add_channel(self, chan_id, config):
        assert(self.state is DCSStates.INIT)
        lv = (config["lv"].pop("board"), config["lv"].pop("chan"))
        hv = (config["hv"].pop("board"), config["hv"].pop("chan"))
        if "module" in config and config["module"] == "":
            raise RuntimeError("Cannot use empty module names")
        module = config.get("module", None)
        log.debug(f"Adding channel number {chan_id} with lv={lv}, hv={hv}, module={module}")
        chan = TrackerChannel(chan_id, lv, hv, module, verbose=self.verbose)
        self.all_channels[chan_id] = chan
        if chan.active:
            self.active_channels[chan_id] = chan

    def _connect_epics(self):
        for chan in self.all_channels.values():
            chan.fsm_connect_epics()
        
        # now set all the values
        global_config = self.config.get("global", {})
        for chan_id,chan in self.all_channels.items():
            config = self.config["channels"][chan_id]
            for v_c,epics_c in [("lv", chan.epics_LV), ("hv", chan.epics_HV)]:
                values = dict(global_config.get(v_c, {}))
                values.update(config.get(v_c, {}))
                for vNm,vV in values.items():
                    if not hasattr(epics_c, vNm) or not isinstance(getattr(epics_c.__class__, vNm), property):
                        logger.error(f"{v_c} EPICS interface has no support for {vNm}")
                    else:
                        if type(vV) == str and "0b" in vV:
                            vV = int(vV, base=2)
                        log.debug(f"Channel {chan_id}: setting {v_c}.{vNm} to {vV} with type {type(vV)}")
                        setattr(epics_c, vNm, vV)

        self.machine.add_transition("cmd_lv_on", [DCSStates.LV_OFF, DCSStates.LV_MIX], None, before=self.switch_lv_on)
        self.machine.add_transition("cmd_lv_off", [DCSStates.LV_ON, DCSStates.LV_MIX], None, before=self.switch_lv_off)
        self.machine.add_transition("cmd_hv_on", [DCSStates.LV_ON, DCSStates.HV_MIX, DCSStates.HV_RAMP], None, before=self.switch_hv_on)
        self.machine.add_transition("cmd_hv_off", [DCSStates.HV_ON, DCSStates.HV_MIX, DCSStates.HV_RAMP], None, before=self.switch_hv_off)

        self.PV_clear_alarm = epics.PV("cleanroom:ClearAlarm", verbose=self.verbose)
        self.machine.add_transition("cmd_clear_alarms", DCSStates.ERROR, None, before=lambda: self.PV_clear_alarm.put("Yes"))

    def _reconnect_epics(self):
        for chan in self.all_channels.values():
            chan.fsm_reconnect_epics()

    def switch_lv_on(self):
        for chan in self.active_channels.values():
            try:
                chan.cmd_lv_on()
            except MachineError as e:
                log.error(e)

    def switch_lv_off(self):
        for chan in self.active_channels.values():
            try:
                chan.cmd_lv_off()
            except MachineError as e:
                log.error(e)

    def switch_hv_on(self):
        for chan in self.active_channels.values():
            try:
                chan.cmd_hv_on()
            except MachineError as e:
                log.error(e)

    def switch_hv_off(self):
        for chan in self.active_channels.values():
            try:
                chan.cmd_hv_off()
            except MachineError as e:
                log.error(e)

    def update_status(self):
        old_state = self.state
        if self.state is DCSStates.INIT:
            pass
        # only use ACTIVE channels to update state
        elif any(c.state is PSStates.ERROR for c in self.active_channels.values()):
            self.to_ERROR()
        elif any(c.state is PSStates.DISCONNECTED for c in self.active_channels.values()):
            self.to_DISCONNECTED()
        elif all(c.state is PSStates.CONNECTED for c in self.active_channels.values()):
            self.to_CONNECTED()
        elif all(c.state is PSStates.LV_OFF for c in self.active_channels.values()):
            self.to_LV_OFF()
        elif any(c.state is PSStates.LV_OFF for c in self.active_channels.values()) and any(c.state is PSStates.HV_ON for c in self.active_channels.values()):
            self.to_ERROR()
        elif any(c.state is PSStates.LV_OFF for c in self.active_channels.values()) and any(c.state is PSStates.LV_ON for c in self.active_channels.values()):
            self.to_LV_MIX()
        elif all(c.state is PSStates.LV_ON for c in self.active_channels.values()):
            self.to_LV_ON()
        elif all(c.state is PSStates.HV_ON for c in self.active_channels.values()):
            self.to_HV_ON()
        elif any(c.state is PSStates.HV_RAMP for c in self.active_channels.values()):
            self.to_HV_RAMP()
        elif any(c.state is PSStates.LV_ON for c in self.active_channels.values()) and any(c.state is PSStates.HV_ON for c in self.active_channels.values()):
            self.to_HV_MIX()
        else:
            log.fatal(f"Should not happen! States are {', '.join(str(c.state) for c in self.active_channels.values())}")
        if self.state != old_state:
            with self._lock:
                self._changed = True

    def command(self, topic, message):
        commands = ["switch", "setv", "clear", "refresh", "reconnect"]
        parts = topic.split("/")
        device, cmd, command = parts[:3]
        assert(device == self.name)
        assert(cmd == "cmd")
        assert(command in commands)
        if len(parts) >= 4:
            lvhv = parts[3]
            assert(lvhv in ["lv", "hv"])
        if len(parts) >= 5:
            chan_id = parts[4]
            # only propagate commands to ACTIVE channels
            if chan_id not in self.active_channels.keys():
                log.error("Channel {chan_id} is not an active channel")
                return
            channel = self.active_channels[chan_id]

        message = message.decode() # message arrives as bytes array
        if command == "switch":
            assert(message in ["on", "off"])
            log.debug(f"Calling cmd: {lvhv}_{message}")
            fn = f"cmd_{lvhv}_{message}"
            getattr(self, fn)()
        elif command == "setv":
            log.debug(f"Setting {lvhv} of channel {channel.chan_id} V0 to {message}")
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
        # publish status of ALL channels
        for chan in self.all_channels.values():
            chan.publish(force)
        if hasattr(self, "client"):
            with self._lock:
                if self._changed or force:
                    msg = json.dumps(self.status())
                    log.debug(f"Sending: {msg}")
                    self.client.publish("{}/status".format(self.name), msg)
                    self._changed = False

    def status(self):
        return {
            "fsm_state": str(self.state).split(".")[1],
        }

    def launch_mqtt(self, mqtt_host):
        def on_connect(client, userdata, flags, rc):
            # Subscribing in on_connect() means that if we lose the connection and
            # reconnect then subscriptions will be renewed.
            client.subscribe(f"{self.name}/cmd/#")
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
        for chan in self.all_channels.values():
            chan.client = client

        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(mqtt_host, 1883, 60)
        client.loop_start()
        while 1:
            epics.ca.poll()
            self.update_status()
            self.publish()
            time.sleep(1)
        client.disconnect()
        client.loop_stop()

if __name__ == "__main__":
    parser = argparse.ArgumentParser("Entry point for CAEN PS control and monitoring backend")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--mqtt-host", required=True, help="URL of MQTT broker")
    parser.add_argument("config", help="YAML configuration file listing channels")
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)
        logging.getLogger("epics").setLevel(logging.DEBUG)

    device = TrackerDCS(args.config, verbose=args.verbose)
    device.fsm_connect_epics()
    device.launch_mqtt(args.mqtt_host)

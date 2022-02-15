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

    def __init__(self, config_path, verbose=False):
        log.info(f"Initializing DCS")
        self.config_path = config_path
        self.verbose = verbose

        transitions = [
            # initial transition, does essentially the same as "fsm_reload_config", but
            # puts us into the CONNECTED state (if config loading was successful)
            { "trigger": "fsm_load_config", "source": DCSStates.INIT, "dest": DCSStates.DISCONNECTED, "before": "_load_config" },
            { "trigger": "fsm_reset", "source": [DCSStates.CONNECTED, DCSStates.LV_OFF], "dest": DCSStates.INIT, "before": "_reset" },
            { "trigger": "fsm_reconnect_epics", "source": DCSStates.DISCONNECTED, "dest": DCSStates.CONNECTED, "before": "_reconnect_epics" },
            { "trigger": "cmd_lv_on", "source": [DCSStates.LV_OFF, DCSStates.LV_MIX], "dest": None, "before": "switch_lv_on" },
            { "trigger": "cmd_lv_off", "source": [DCSStates.LV_ON, DCSStates.LV_MIX], "dest": None, "before": "switch_lv_off" },
            { "trigger": "cmd_hv_on", "source": [DCSStates.LV_ON, DCSStates.HV_MIX, DCSStates.HV_RAMP], "dest": None, "before": "switch_hv_on" },
            { "trigger": "cmd_hv_off", "source": [DCSStates.HV_ON, DCSStates.HV_MIX, DCSStates.HV_RAMP], "dest": None, "before": "switch_hv_off" },
        ]

        self._lock = threading.Lock()
        self._changed = True

        self.machine = Machine(model=self, states=DCSStates, transitions=transitions, initial=DCSStates.INIT)

        # will print a message every time we enter a state
        for s in DCSStates:
            getattr(self.machine, "on_enter_" + str(s).split(".")[1])("print_fsm")

        self.PV_clear_alarm = epics.PV("cleanroom:ClearAlarm", verbose=self.verbose)
        self.machine.add_transition("cmd_clear_alarms", DCSStates.ERROR, None, before=lambda: self.PV_clear_alarm.put("Yes"))
        
        # will hold TrackerChannel instances, as in the config
        self.all_channels = {}
        self.active_channels = {}

        log.info(f"Done - state is {self.state}")

    def print_fsm(self):
        with self._lock:
            if self._changed:
                log.info(f"FSM state: {self.state}")

    def _reset(self):
        with self._lock:
            self._changed = True  # just to make sure print_fsm() logs the change
        self.all_channels = {}
        self.active_channels = {}

    def _load_config(self):
        with open(self.config_path) as f:
            config = yaml.safe_load(f)
        self.name = config.get("name", "dcs")  # name is used to match MQTT commands

        # construct TrackerChannel objects and initialize their epics variables
        for chan_id,chan_cfg in config["channels"].items():
            self.add_channel(chan_id, chan_cfg)

        # now set all the values
        global_config = config.get("global", {})
        for chan_id,chan in self.all_channels.items():
            chan_cfg = config["channels"][chan_id]
            for v_c,epics_c in [("lv", chan.epics_LV), ("hv", chan.epics_HV)]:
                values = dict(global_config.get(v_c, {}))
                values.update(chan_cfg.get(v_c, {}))
                for vNm,vV in values.items():
                    if not hasattr(epics_c, vNm) or not isinstance(getattr(epics_c.__class__, vNm), property):
                        logger.error(f"{v_c} EPICS interface has no support for {vNm}")
                    else:
                        if type(vV) == str:
                            if vV.startswith("0b"):
                                vV = int(vV, base=2)
                            elif vV.startswith("0x"):
                                vV = int(vV, base=16)
                        log.debug(f"Channel {chan_id}: setting {v_c}.{vNm} to {vV} with type {type(vV)}")
                        setattr(epics_c, vNm, vV)

    def add_channel(self, chan_id, config):
        # we pop the board and channel: they are only used here,
        # while all the other options are forwarded to the channel constructors
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
        chan.fsm_init_epics()

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
        # cannot do anything until we have initialized channels
        if self.state == DCSStates.INIT:
            return

        commands = ["switch", "setv", "clear", "refresh", "reload", "reconnect"]
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
        elif command == "reload":
            log.info("Destroying current configuration; reloading config file and re-initializing monitoring for new list of channels!")
            self.fsm_reset()
            self.fsm_load_config()
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
    device.fsm_load_config()
    device.launch_mqtt(args.mqtt_host)

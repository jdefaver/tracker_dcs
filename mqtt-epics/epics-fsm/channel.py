#!/usr/bin/env python3

from transitions.extensions import LockedMachine as Machine
import enum
import json
import epics
import threading
import logging

from caen_epics import EPICSHVChannel, EPICSLVChannel

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
        self.log = logging.getLogger(name)
        self.log.setLevel(logging.DEBUG)
        self.log.debug(f"Creating tracker channel {name}")

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
        self.log.debug(f"FSM state: {self.state}")

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
            # if self.epics_HV.status is None or self.epics_LV.status is None:
            #     self.to_DISCONNECTED()
            if self.epics_HV.status is not None and self.epics_HV.status > 5:
                self.to_ERROR()
                return
            if self.epics_LV.status is not None and self.epics_LV.status > 1:
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
        # self.log.debug(f"In update callback: got {pvname}, {value}")
        if pvname.endswith("Pw") or pvname.endswith("Status"):
            self.epics_update_status()
        with self._lock:
            self._changed = True

    def epics_connection_callback(self, pvname, conn, **kwargs):
        # self.log.debug(f"In connection callback: got {pvname}, {conn}")
        # if conn and self.state is PSStates.DISCONNECTED:
        #     # an EPICS PV was reconnected -> we can try forcing the reconnection of all the others, and transitioning to CONNECTED
        #     self.fsm_reconnect_epics()
        # else:
        if not conn:
            self.fsm_disconnect_epics()

    def publish(self, force=False):
        if hasattr(self, "client"):
            with self._lock:
                if self._changed or force:
                    msg = json.dumps(self.status())
                    topic = f"/channels/status/{self.name}"
                    self.log.debug(f"Sending: {msg} to {topic}")
                    self.client.publish(topic, msg)
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
            'fsm_state': str(self.state)
        }

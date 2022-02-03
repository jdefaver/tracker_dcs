import enum
import json
import threading
import logging

from transitions.extensions import LockedMachine as Machine

import epics

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

    def __init__(self, chan_id, lv, hv, module=None, verbose=False):
        assert(len(lv) == 2 and 0 <= lv[0] <= 4 and 0 <= lv[1] <= 7)
        assert(len(hv) == 2 and 12 <= hv[0] <= 15 and 0 <= hv[1] <= 11)

        self.log = logging.getLogger(f"channel {chan_id}")
        self.verbose = verbose
        self.log.setLevel(logging.INFO)
        if self.verbose:
            self.log.setLevel(logging.DEBUG)
        self.log.info(f"Creating tracker channel {chan_id} for module {module}")

        self.chan_id = chan_id
        self.module = module
        self.active = (module is not None)
        self.lv_board, self.lv_chan = lv
        self.hv_board, self.hv_chan = hv

        transitions = [
            { "trigger": "fsm_connect_epics", "source": PSStates.INIT, "dest": PSStates.CONNECTED, "before": "_connect_epics" },
            { "trigger": "fsm_reconnect_epics", "source": PSStates.DISCONNECTED, "dest": PSStates.CONNECTED, "before": "_reconnect_epics" },
            { "trigger": "fsm_disconnect_epics", "source": "*", "dest": PSStates.DISCONNECTED },
        ]

        self.machine = Machine(model=self, states=PSStates, transitions=transitions, initial=PSStates.INIT)

        # debug state transitions
        for s in PSStates:
            getattr(self.machine, "on_enter_" + str(s).split(".")[1])("print_fsm")

        # update our state depending on the actual CAEN state as soon as we connect
        self.machine.on_enter_CONNECTED("epics_update_status")

        self._lock = threading.Lock()
        self._changed = False

    def print_fsm(self):
        with self._lock:
            if self._changed:
                self.log.info(f"FSM state: {self.state}")

    def _connect_epics(self):
        self.epics_LV = EPICSLVChannel(self.lv_board, self.lv_chan, self.epics_connection_callback, self.epics_update_callback)
        self.epics_HV = EPICSHVChannel(self.hv_board, self.hv_chan, self.epics_connection_callback, self.epics_update_callback)

        self.machine.add_transition("cmd_lv_on", PSStates.LV_OFF, None, before=self.epics_LV.switch_on)
        self.machine.add_transition("cmd_lv_off", PSStates.LV_ON, None, before=self.epics_LV.switch_off)
        self.machine.add_transition("cmd_hv_on", PSStates.LV_ON, None, before=self.epics_HV.switch_on)
        self.machine.add_transition("cmd_hv_off", [PSStates.HV_ON, PSStates.HV_RAMP], None, before=self.epics_HV.switch_off)

    def _reconnect_epics(self):
        self.epics_LV.reconnect()
        self.epics_HV.reconnect()

    def epics_update_status(self):
        """Force FSM states depending on what EPICS tells us on the CAEN state"""
        if self.state not in [PSStates.DISCONNECTED, PSStates.INIT]:
            if self.epics_HV.status is not None and self.epics_HV.status > 5:
                self.to_ERROR()
            elif self.epics_LV.status is not None and self.epics_LV.status > 1:
                self.to_ERROR()
            elif self.epics_LV.status == 1:
                if self.epics_HV.status in [3, 5]:
                    self.to_HV_RAMP()
                elif self.epics_HV.status == 1:
                    self.to_HV_ON()
                else:
                    self.to_LV_ON()
            else:
                self.to_LV_OFF()

    def epics_update_callback(self, pvname, value, **kwargs):
        self.log.debug(f"In update callback: got {pvname}, {value}")
        with self._lock:
            self._changed = True
        if pvname.endswith("Status"):
            self.epics_update_status()

    def epics_connection_callback(self, pvname, conn, **kwargs):
        self.log.debug(f"In connection callback: got {pvname}, {conn}")
        if not conn:
            self.fsm_disconnect_epics()

    def publish(self, force=False):
        if hasattr(self, "client") and self.state not in [PSStates.DISCONNECTED, PSStates.INIT]:
            with self._lock:
                if self._changed or force:
                    msg = json.dumps(self.status())
                    topic = f"dcs/channels"
                    self.log.debug(f"Sending: {msg} to {topic}")
                    self.client.publish(topic, msg)
                    self._changed = False

    def status(self):
        return {
            "id": self.chan_id,
            "module": self.module,

            "lv_board": self.lv_board,
            "lv_channel": self.lv_chan,
            "lv_status": self.epics_LV.status,
            "lv_setV": self.epics_LV.setV,
            "lv_vMon": self.epics_LV.vMon,
            "lv_iMon": self.epics_LV.iMon,
            "lv_maxI": self.epics_LV.maxI,
            "lv_tripTime": self.epics_LV.tripTime,
            "lv_tripInt": self.epics_LV.tripInt,
            "lv_tripExt": self.epics_LV.tripExt,
            "lv_unVthr": self.epics_LV.unVThr,
            "lv_ovVthr": self.epics_LV.ovVThr,
            "lv_rampUpTime": self.epics_LV.rampUpTime,
            "lv_rampDwnTime": self.epics_LV.rampDwnTime,
            "lv_temp": self.epics_LV.temp,

            "hv_board": self.hv_board,
            "hv_channel": self.hv_chan,
            "hv_status": self.epics_HV.status,
            "hv_setV": self.epics_HV.setV,
            "hv_vMon": self.epics_HV.vMon,
            "hv_iMon": self.epics_HV.iMon,
            "hv_maxI": self.epics_HV.maxI,
            "hv_tripTime": self.epics_HV.tripTime,
            "hv_tripInt": self.epics_HV.tripInt,
            "hv_tripExt": self.epics_HV.tripExt,
            "hv_rampUpSpeed": self.epics_HV.rampUpSpeed,
            "hv_rampDwnSpeed": self.epics_HV.rampDwnSpeed,
            "hv_imRange": self.epics_HV.imRange,
            "hv_tripMode": self.epics_HV.tripMode,
            
            "fsm_state": str(self.state).split(".")[1]
        }

import time
import epics
import logging

log = logging.getLogger("epics")
log.setLevel(logging.INFO)

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
        self._deadBand = kwargs.pop("dead_band")
        self._oldValue = 0.
        self._oldCallback = kwargs.pop("callback")
        kwargs["callback"] = self.dbCallback
        super().__init__(*args, **kwargs)

    def dbCallback(self, pvname, value, **kwargs):
        log.debug(f"In deadband-callback - {pvname} = {value}, old value = {self._oldValue}")
        if abs(value - self._oldValue) > self._deadBand:
            self._oldValue = value
            self._oldCallback(pvname, value, **kwargs)


class EPICSChannel(object):
    def __init__(self, board, chan, connection_callback, update_callback, verbose=False, sleep=0.1):
        self.board = board
        self.chan = chan
        self.prefix = f"cleanroom:{self.board:02}:{self.chan:03}:"
        self._PVs = {}
        for var in ["Pw", "Status", "Trip", "TripInt", "TripExt"]:
            self._PVs[var] = epics.PV(self.prefix + var, auto_monitor=True, verbose=verbose, callback=update_callback, connection_callback=connection_callback)
            time.sleep(sleep)
        for var in ["V0Set", "I0Set", "VMon", "IMon"]:
            self._PVs[var] = DeadbandPV(self.prefix + var, dead_band=0.01, auto_monitor=True, verbose=verbose, callback=update_callback, connection_callback=connection_callback)
            time.sleep(sleep)

    def reconnect(self):
        for pv in self._PVs.values():
            pv.reconnect()

    def switch_on(self):
        self._PVs["Pw"].put("On")
    def switch_off(self):
        self._PVs["Pw"].put("Off")
    def is_on(self):
        # PV.get() uses cached value because we set auto_monitor to True
        # @retry(5)
        # def get():
        #     return self.PVs["Pw"].get(timeout=1, use_monitor=False)
        # ret = get()
        ret = self._PVs["Pw"].get()
        return ret == 1
    def is_off(self):
        return not self.is_on()

    @property
    def status(self):
        return self._PVs["Status"].get()
    @property
    def vMon(self):
        return self._PVs["VMon"].get()
    @property
    def iMon(self):
        return self._PVs["IMon"].get()

    @property
    def setV(self):
        return self._PVs["V0Set"].get()
    @setV.setter
    def setV(self, value):
        self._PVs["V0Set"].put(value)

    @property
    def maxI(self):
        return self._PVs["I0Set"].get()
    @maxI.setter
    def maxI(self, value):
        self._PVs["I0Set"].put(value)

    @property
    def tripTime(self):
        return self._PVs["Trip"].get()
    @tripTime.setter
    def tripTime(self, value):
        self._PVs["Trip"].put(value)

    @property
    def tripInt(self):
        return self._PVs["TripInt"].get()
    @tripInt.setter
    def tripInt(self, value):
        self._PVs["TripInt"].put(value)

    @property
    def tripExt(self):
        return self._PVs["TripExt"].get()
    @tripExt.setter
    def tripExt(self, value):
        self._PVs["TripExt"].put(value)

class EPICSLVChannel(EPICSChannel):
    def __init__(self, board, chan, connection_callback, update_callback, verbose=False, sleep=0.1):
        super().__init__(board, chan, connection_callback, update_callback, verbose, sleep)
    
        for var in ["UNVThr", "OVVThr", "RUpTime", "RDwTime"]:
            self._PVs[var] = epics.PV(self.prefix + var, auto_monitor=True, verbose=verbose, callback=update_callback, connection_callback=connection_callback)
            time.sleep(sleep)
        for var in ["Temp"]:
            self._PVs[var] = DeadbandPV(self.prefix + var, dead_band=2, auto_monitor=True, verbose=verbose, callback=update_callback, connection_callback=connection_callback)
            time.sleep(sleep)

    @property
    def temp(self):
        return self._PVs["Temp"].get()

    @property
    def unVThr(self):
        return self._PVs["UNVThr"].get()
    @unVThr.setter
    def unVThr(self, value):
        self._PVs["UNVThr"].put(value)

    @property
    def ovVThr(self):
        return self._PVs["OVVThr"].get()
    @ovVThr.setter
    def ovVThr(self, value):
        self._PVs["OVVThr"].put(value)

    @property
    def rampUpTime(self):
        return self._PVs["RUpTime"].get()
    @rampUpTime.setter
    def rampUpTime(self, value):
        self._PVs["RUpTime"].put(value)

    @property
    def rampDwnTime(self):
        return self._PVs["RDwTime"].get()
    @rampDwnTime.setter
    def rampDwnTime(self, value):
        self._PVs["RDwTime"].put(value)


class EPICSHVChannel(EPICSChannel):
    def __init__(self, board, chan, connection_callback, update_callback, verbose=False, sleep=0.1):
        super().__init__(board, chan, connection_callback, update_callback, verbose, sleep)

        # adjust dead bands from EPICSChannel values
        # here currents are in uA
        for var in ["V0Set", "VMon"]:
            self._PVs[var].deadBand = 0.01
        for var in ["I0Set", "IMon"]:
            self._PVs[var].deadBand = 0.01 # 0.01=10nA in high-power mode; 0.001=1nA in high-res mode

        for var in ["RUp", "RDWn", "ImRange", "PDwn"]:
            self._PVs[var] = epics.PV(self.prefix + var, auto_monitor=True, verbose=verbose, callback=update_callback, connection_callback=connection_callback)
            time.sleep(sleep)

    # ramp speed in V/s
    @property
    def rampUpSpeed(self):
        return self._PVs["RUp"].get()
    @rampUpSpeed.setter
    def rampUpSpeed(self, value):
        self._PVs["RUp"].put(value)

    # ramp speed in V/s
    @property
    def rampDwnSpeed(self):
        return self._PVs["RDWn"].get()
    @rampDwnSpeed.setter
    def rampDwnSpeed(self, value):
        self._PVs["RDWn"].put(value)

    @property
    def tripMode(self):
        return self._PVs["PDwn"].get()
    @tripMode.setter
    def tripMode(self, value):
        assert(value in ["Kill", "Ramp"])
        self._PVs["PDwn"].put(value)

    # "high" -> high-power, 3.5mA max
    # "low" -> high-resolution, 350uA max
    @property
    def imRange(self):
        return self._PVs["ImRange"].get()
    @imRange.setter
    def imRange(self, value):
        assert(value in ["Low", "High"])
        if value == "Low":
            for var in ["I0Set", "IMon"]:
                self._PVs[var].deadBand = 0.001
        if value == "High":
            for var in ["I0Set", "IMon"]:
                self._PVs[var].deadBand = 0.01
        self._PVs["ImRange"].put(value)

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
        self.deadBand = kwargs.pop("dead_band")
        self.oldValue = 0.
        self.oldCallback = kwargs.pop("callback")
        kwargs["callback"] = self.dbCallback
        super(DeadbandPV, self).__init__(*args, **kwargs)

    def dbCallback(self, pvname, value, **kwargs):
        log.debug(f"In deadband-callback - {pvname} = {value}, old value = {self.oldValue}")
        if abs(value - self.oldValue) > self.deadBand:
            self.oldValue = value
            self.oldCallback(pvname, value, **kwargs)


class EPICSChannel(object):
    def __init__(self, board, chan, connection_callback, update_callback, verbose=False, sleep=0.1):
        self.board = board
        self.chan = chan
        self.prefix = f"cleanroom:{self.board:02}:{self.chan:03}:"
        self.PVs = {}
        for var in ["Pw", "Status"]:
            self.PVs[var] = epics.PV(self.prefix + var, auto_monitor=True, verbose=verbose, callback=update_callback, connection_callback=connection_callback)
            time.sleep(sleep)
        for var in ["V0Set", "I0Set", "VMon", "IMon"]:
            self.PVs[var] = DeadbandPV(self.prefix + var, dead_band=0.01, auto_monitor=True, verbose=verbose, callback=update_callback, connection_callback=connection_callback)
            time.sleep(sleep)

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
    def __init__(self, board, chan, connection_callback, update_callback, verbose=False, sleep=0.1):
        super(EPICSLVChannel, self).__init__(board, chan, connection_callback, update_callback, verbose, sleep)
    
        for var in ["UNVThr", "OVVThr"]:
            self.PVs[var] = epics.PV(self.prefix + var, auto_monitor=True, verbose=verbose, callback=update_callback, connection_callback=connection_callback)
            time.sleep(sleep)
        for var in ["Temp"]:
            self.PVs[var] = DeadbandPV(self.prefix + var, dead_band=0.01, auto_monitor=True, verbose=verbose, callback=update_callback, connection_callback=connection_callback)
            time.sleep(sleep)

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
    def __init__(self, board, chan, connection_callback, update_callback, verbose=False, sleep=0.1):
        super(EPICSHVChannel, self).__init__(board, chan, connection_callback, update_callback, verbose, sleep)

        # adjust dead bands from EPICSChannel values
        # here currents are in uA
        for var in ["V0Set", "VMon"]:
            self.PVs[var].deadBand = 0.01
        for var in ["I0Set", "IMon"]:
            self.PVs[var].deadBand = 0.01 # 0.01=10nA in high-power mode; 0.001=1nA in high-res mode

        for var in ["RUp", "RDWn", "ImRange"]:
            self.PVs[var] = epics.PV(self.prefix + var, auto_monitor=True, verbose=verbose, callback=update_callback, connection_callback=connection_callback)
            time.sleep(sleep)

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
        if value == "Low":
            for var in ["I0Set", "IMon"]:
                self.PVs[var].deadBand = 0.001
        if value == "High":
            for var in ["I0Set", "IMon"]:
                self.PVs[var].deadBand = 0.01
        self.PVs["ImRange"].put(value)


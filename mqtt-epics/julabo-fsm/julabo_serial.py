#!/usr/bin/env python3

from transitions.extensions import LockedMachine as Machine
from transitions.core import MachineError
import serial
import argparse
import time
import enum
import json
import threading
import logging

log = logging.getLogger("Julabo")
logging.basicConfig(format="== %(asctime)s - %(name)s - %(levelname)s - %(message)s")
log.setLevel(logging.INFO)

class JulaboSerial(object):
    def __init__(self, remote=False):
        if remote:
            self.ser = serial.serial_for_url('socket://130.104.48.63:8000', timeout=1)
        else:
            self.ser = serial.Serial('/dev/ttyUSB0', baudrate=4800, parity=serial.PARITY_EVEN, bytesize=serial.SEVENBITS, stopbits=serial.STOPBITS_ONE, rtscts=True, timeout=1)

        time.sleep(0.1)
        self.ser.flushOutput()
        self.ser.flushInput()

        log.debug("Status: {}".format(self.status()))
        log.debug("Version: {}".format(self._ask("VERSION")))

    def _write(self, s):
        cmd = bytes(s + '\r', 'ascii')
        time.sleep(.25)
        self.ser.write(cmd)

    def _read(self):
        time.sleep(.1)
        ret = self.ser.readline()
        return ret.decode('ascii').strip("\n").strip("\r")

    def _ask(self, msg):
        self._write(msg)
        return self._read()

    def status(self):
        status = self._ask('STATUS')
        number = int(status.split(" ")[0])
        message = " ".join(status.split(" ")[1:])
        return number, message

    def readActualInt(self):
        return float(self._ask("IN_PV_00"))

    def readActualExtPt100(self):
        return float(self._ask("IN_PV_02"))

    def readPower(self):
        return float(self._ask("IN_PV_01"))

    def readSetPoint(self, sp):
        return float(self._ask("IN_SP_0{}".format(sp-1)))

    def getUsedSetPoint(self):
        return int(self._ask("IN_MODE_01")) + 1

    def setWorkingTemp(self, sp, temp):
        self._write("OUT_SP_0{} {}".format(sp-1, temp))

    def useSetPoint(self, sp):
        self._write("OUT_MODE_01 {}".format(sp-1))

    def useInternal(self):
        self._write("OUT_MODE_04 0")

    def useExternalPt100(self):
        self._write("OUT_MODE_04 1")

    def externalIsUsed(self):
        return self._ask("IN_MODE_04") == "1"

    def setPressureStage(self, p):
        self._write("OUT_SP_07 {}".format(p))

    def start(self):
        self._write("OUT_MODE_05 1")

    def stop(self):
        self._write("OUT_MODE_05 0")


class JulaboStates(enum.Enum):
    DISCONNECTED = 0
    CONNECTED = 1
    OFF = 2
    ON = 3
    ERROR = 4


class JulaboFSM(object):
    def __init__(self, remote=False):
        self.remote = remote

        transitions = [
            { "trigger": "fsm_connect", "source": JulaboStates.DISCONNECTED, "dest": JulaboStates.CONNECTED, "before": "_connect_serial" }
        ]

        self.machine = Machine(model=self, states=JulaboStates, transitions=transitions, initial=JulaboStates.DISCONNECTED)

        for s in JulaboStates:
            getattr(self.machine, "on_enter_" + str(s).split(".")[1])("print_fsm")

        log.info(f"Done - state is {self.state}")

    def print_fsm(self):
        """Log FSM state every time a new state is entered - CAUTION: do not change the state while owning the lock!"""
        log.info(f"FSM state: {self.state}")

    def _connect_serial(self):
        self.julaboSerial = JulaboSerial(self.remote)

        self.machine.add_transition("cmd_on", JulaboStates.OFF, None, before=self.julaboSerial.start)
        self.machine.add_transition("cmd_off", [JulaboStates.ON, JulaboStates.ERROR], None, before=self.julaboSerial.stop)

    def update_status(self):
        if self.state is JulaboStates.DISCONNECTED:
            return None
        else:
            status = self.julaboSerial.status()
            log.debug(f"Chiller status: {status}")
            if status[0] < 0:
                self.to_ERROR()
            elif status[0] < 2:
                # manual control
                self.to_ERROR()
            elif status[0] == 3:
                self.to_ON()
            elif status[0] == 2:
                self.to_OFF()
            else:
                log.fatal(f"Could not interpret status {status}")
            return status[0]

    def command(self, topic, message):
        commands = ["start", "stop", "refresh", "reconnect", "setWT", "useSP", "useExt", "useInt", "setPress"]
        device, cmd, command = topic.split("/")
        assert(device == "julabo")
        assert(cmd == "cmd")
        assert(command in commands)

        if command == "start":
            self.cmd_on()
        elif command == "stop":
            self.cmd_off()
        elif command == "refresh":
            self.publish()
        elif command == "reconnect":
            self.fsm_connect()
        elif command == "useExt":
            self.julaboSerial.useExternalPt100()
        elif command == "useInt":
            self.julaboSerial.useInternal()
        else:
            message = json.loads(message)
            if command in ["setWT", "useSP"]:
                sp = message["setpoint"]
                assert(1 <= sp <= 3)
                if command == "setWT":
                    assert(-50 <= message["temp"] < 50)
                    self.julaboSerial.setWorkingTemp(sp, message["temp"])
                elif command == "useSP":
                    self.julaboSerial.useSetPoint(sp)
            elif command == "setPress":
                press = message["press"]
                self.julaboSerial.setPressureStage(press)

    def publish(self):
        if hasattr(self, "client"):
            msg = json.dumps(self.status())
            log.debug(f"Sending: {msg}")
            self.client.publish("julabo/status", msg)

    def status(self):
        status = {}
        try:
            status_code = self.update_status()
            status.update({
                "status_code": status_code,
                "internal_temp": self.julaboSerial.readActualInt(),
                "setpoint_1": self.julaboSerial.readSetPoint(1),
                "setpoint_2": self.julaboSerial.readSetPoint(2),
                "setpoint_3": self.julaboSerial.readSetPoint(3),
                "used_setpoint": self.julaboSerial.getUsedSetPoint(),
                "power": self.julaboSerial.readPower(),
                "ext_is_used": self.julaboSerial.externalIsUsed(),
            })
            try:
                status["external_temp"] = self.julaboSerial.readActualExtPt100()
            except ValueError:
                status["external_temp"]  = 0
        except serial.serialutil.SerialException as e:
            log.error(f"Serial error while trying to get the chiller status: {e}")
            self.to_DISCONNECTED()
        except Exception as e:
            log.error(f"Error while reading all the values for publication: {e}")
            self.to_ERROR()
        status["fsm_state"] = str(self.state).split(".")[1]
        return status

    def launch_mqtt(self, mqtt_host):
        import paho.mqtt.client as mqtt

        def on_connect(client, userdata, flags, rc):
            client.subscribe(f"julabo/cmd/#")
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

        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(mqtt_host, 1883, 60)
        client.loop_start()
        while 1:
            self.publish()
            time.sleep(5)
        client.disconnect()
        client.loop_stop()

if __name__ == '__main__':

    parser = argparse.ArgumentParser(description="Debugging tool for julabo chiller")

    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-r", "--remote", action="store_true", help="Use remote interface instead of /dev/ttyUSB0")

    parser.add_argument("--start-mqtt", action="store_true", help="Start MQTT loop and disregard any other commands")
    parser.add_argument("--mqtt-host", help="MQTT broker host")

    parser.add_argument("--status", action="store_true", help="Read status")
    parser.add_argument("--read-int", action="store_true", help="Read actual internal (bath) temperature")
    parser.add_argument("--read-sp", action="store_true", help="Read which setpoint is used")
    parser.add_argument("--read-pow", action="store_true", help="Read power level (negative=cooling power)")
    parser.add_argument("--sp", type=int, default=0, help="Specify setpoint (1-3)")
    parser.add_argument("--read-wt", action="store_true", help="Read working temperature for specified setpoint")
    parser.add_argument("--set-wt", type=float, default=float("-inf"), help="Set working temperature for specified setpoint")
    parser.add_argument("--use-sp", action="store_true", help="Use specified setpoint")
    parser.add_argument("--start", action="store_true", help="Start circulator")
    parser.add_argument("--stop", action="store_true", help="Stop circulator")
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    serialChiller = JulaboFSM(remote=args.remote)
    serialChiller.fsm_connect()

    if args.start_mqtt:
        serialChiller.launch_mqtt(args.mqtt_host)
    else:
        if args.status:
            print("Status: {}".format(serialChiller.status()))
        if args.read_int:
            print("Actual internal bath temperature: {:.2f}C".format(serialChiller.julaboSerial.readActualInt()))
        if args.read_sp:
            print("Setpoint used: {}".format(serialChiller.julaboSerial.getUsedSetPoint()))
        if args.read_pow:
            print("Power level: {}".format(serialChiller.julaboSerial.readPower()))

        if args.read_wt or args.use_sp or args.set_wt != float("-inf"):
            assert(1 <= args.sp <= 3)

        if args.read_wt:
            print("Working temperature for setpoint {}: {:.2f}C".format(args.sp, serialChiller.julaboSerial.readSetPoint(args.sp)))
        if args.set_wt != float("-inf"):
            assert(-50 <= args.set_wt <= 50)
            print("Setting working temperature for setpoint {} to {}C".format(args.sp, args.set_wt))
            serialChiller.julaboSerial.setWorkingTemp(args.sp, args.set_wt)
        if args.use_sp:
            print("Will use setpoint {}".format(args.sp))
            serialChiller.julaboSerial.useSetPoint(args.sp)

        if args.start:
            print("Starting chiller!")
            serialChiller.cmd_on()
        if args.stop:
            print("Stopping chiller!")
            serialChiller.cmd_off()

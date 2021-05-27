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

from pymodbus.client.sync import ModbusTcpClient
from pymodbus.payload import BinaryPayloadBuilder
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder

log = logging.getLogger("MARTAClient")
logging.basicConfig(format="== %(asctime)s - %(name)s - %(levelname)s - %(message)s")
log.setLevel(logging.INFO)

def getChunks(addressSet, maxLength=None):
    """Iterate over continuous chunks from a list"""
    chunk = []
    for addr in sorted(addressSet):
        if (not chunk) or (chunk[-1] == addr - 1 and (maxLength is None or len(chunk) < maxLength)):
            chunk.append(addr)
        else:
            yield (chunk[0], len(chunk))
            chunk = [addr]
    yield (chunk[0], len(chunk))

class ModbusMetric:
    width = 1
    def __init__(self, name, address, manager):
        self.name = name
        self.address = address
        self.manager = manager
        self.manager.addMetric(self)
    def read(self):
        return self.manager.get(self.address)[0]

class ModbusSetParam(ModbusMetric):
    def write(self, value):
        self.manager.write(self.address, value)

class ModbusBool(ModbusMetric):
    def __init__(self, name, address, bit, manager):
        super().__init__(name, address, manager)
        self.bit = bit
    def read(self):
        reg = super().read()
        return (reg >> self.bit) & 0b1

class ModbusSetBool(ModbusBool, ModbusSetParam):
    def write(self, value):
        value = bool(value)
        curr_values = super().read()
        print(f"Current values: {bin(curr_values)}")
        new_values = (curr_values & ~(0b1 << self.bit)) | (value << self.bit)
        print(f"New values: {bin(new_values)}")
        super().write(new_values)

class ModbusInt(ModbusMetric):
    pass

class ModbusFloat32(ModbusMetric):
    width = 2
    def read(self):
        regs = self.manager.get(self.address, width=self.width)
        decoder = BinaryPayloadDecoder.fromRegisters(regs, byteorder=Endian.Big, wordorder=Endian.Little)
        return decoder.decode_32bit_float()

class ModbusSetFloat32(ModbusFloat32, ModbusSetParam):
    def write(self, value):
        value = float(value)
        buf = BinaryPayloadBuilder(byteorder=Endian.Big, wordorder=Endian.Little)
        buf.add_32bit_float(value)
        regs = buf.to_registers()
        super().write(regs)

class ModbusRegisterManager:
    def __init__(self, client, unit=1):
        self.client = client
        self.unit = unit
        self.registers = dict()
        self.input_registers = []
        self.chunks = []

    def addMetric(self, metric):
        for addr in range(metric.address, metric.address + metric.width):
            self.registers[addr] = 0
            if isinstance(metric, ModbusSetParam):
                self.input_registers.append(addr)
        self.chunks = list(getChunks(self.registers.keys()))

    def update(self):
        for start,length in self.chunks:
            rr = self.client.read_holding_registers(start, length, unit=self.unit)
            # print(f"Reading {length} registers starting from address {start}")
            assert(not rr.isError())
            for i,addr in enumerate(range(start, start+length)):
                # print(f" Result: {addr} = {rr.registers[i]}")
                self.registers[addr] = rr.registers[i]

    def get(self, baseAddr, width=1):
        return [ self.registers[addr] for addr in range(baseAddr, baseAddr + width) ]

    def write(self, baseAddr, values):
        if isinstance(values, int):
            values = [ values ]
        assert(all(addr in self.input_registers for addr in range(baseAddr, baseAddr + len(values))))
        rr = client.write_registers(baseAddr, values, unit=self.unit)
        assert(not rr.isError())
        for i,addr in enumerate(range(baseAddr, baseAddr + len(values))):
            self.registers[addr] = values[i]

    def makeProxy(self, name, address, type="int", bit=None, input=False, **kwargs):
        if type == "int":
            proxy = ModbusInt(name, address, manager=self, **kwargs)
        elif type == "bool":
            if input:
                proxy = ModbusSetBool(name, address, manager=self, bit=bit, **kwargs)
            else:
                proxy = ModbusBool(name, address, manager=self, bit=bit, **kwargs)
        elif type == "float32":
            if input:
                proxy = ModbusSetFloat32(name, address, manager=self, **kwargs)
            else:
                proxy = ModbusFloat32(name, address, manager=self, **kwargs)
        else:
            raise ValueError(f"Unrecognized type for register {name}: {type}")
        return proxy

class MARTAStates(enum.Enum):
    INIT = -1
    DISCONNECTED = 0
    CONNECTED = 1
    CHILLER_RUNNING = 3
    CO2_RUNNING = 4
    ALARM = 5

class MARTAClient(object):

    def __init__(self, ipAddr, slaveId, configPath, verbose=False):
        log.info(f"Initializing MARTA client")
        self.verbose = verbose

        transitions = [
            { "trigger": "fsm_connect_modbus", "source": MARTAStates.INIT, "dest": MARTAStates.CONNECTED, "before": "_connect_modbus" },
            { "trigger": "fsm_reconnect_modbus", "source": MARTAStates.DISCONNECTED, "dest": MARTAStates.CONNECTED, "before": "_reconnect_modbus" },
            { "trigger": "fsm_disconnect_modbus", "source": "*", "dest": MARTAStates.DISCONNECTED },
        ]

        self._lock = threading.Lock()
        self._changed = True

        self.machine = Machine(model=self, states=MARTAStates, transitions=transitions, initial=MARTAStates.INIT)

        for s in MARTAStates:
            getattr(self.machine, "on_enter_" + str(s).split(".")[1])("print_fsm")

        self.modbus_client = ModbusTcpClient(ipAddr)

        with open(configPath) as _f:
            self.config = yaml.load(_f, Loader=yaml.loader.SafeLoader)

        log.info(f"Done - state is {self.state}")

    def print_fsm(self):
        with self._lock:
            if self._changed:
                log.info(f"FSM state: {self.state}")

    def _connect_modbus(self):
        assert(self.modbus_client.connect())

        # create register manager and read all register values
        self.modbus_manager = ModbusRegisterManager(self.modbus_client, unit=slaveId)
        for name,cfg in self.config.items():
            self.register_map[name] = self.manager.makeProxy(name, **cfg)
        self.manager.update()

        self.machine.add_transition("cmd_start_chiller", MARTAStates.CONNECTED, None, before=self.start_chiller)
        self.machine.add_transition("cmd_start_co2", MARTAStates.CHILLER_RUNNING, None, before=self.start_co2)
        self.machine.add_transition("cmd_stop_co2", MARTAStates.CO2_RUNNING, None, before=self.stop_co2)
        self.machine.add_transition("cmd_stop_chiller", MARTAStates.CHILLER_RUNNING, None, before=self.stop_chiller)
        self.machine.add_transition("cmd_clear_alarms", MARTAStates.ERROR, None, before=self.clear_alarms))

    def _reconnect_modbus(self):
        assert(self.modbus_client.connect())

    def start_chiller(self):
        try:
            self.register_map["set_start_chiller"].write(1)
        except ConnectionException as e:
            log.error("Problem writing modbus register: {e}")
            self.to_DISCONNECTED()
    def start_co2(self):
        try:
            self.register_map["set_start_co2"].write(1)
        except ConnectionException as e:
            log.error("Problem writing modbus register: {e}")
            self.to_DISCONNECTED()
    def stop_co2(self):
        try:
            self.register_map["set_start_co2"].write(0)
        except ConnectionException as e:
            log.error("Problem writing modbus register: {e}")
            self.to_DISCONNECTED()
    def stop_chiller(self):
        try:
            self.register_map["set_start_chiller"].write(0)
        except ConnectionException as e:
            log.error("Problem writing modbus register: {e}")
            self.to_DISCONNECTED()

    def update_status(self):
        if self.state is MARTAStates.INIT:
            return

        try:
            self.manager.update()
        except ConnectionException as e:
            log.error("Problem reading the modbus registers: {e}")
            self.to_DISCONNECTED()
            return
pass
        status = self.manager["status"].read()
        set_start_chiller = self.manager["set_start_chiller"].read()
        set_start_co2 = self.manager["set_start_co2"].read()

        if status == 2 and not set_start_co2:
            log.fatal("MARTA is status 2 but CO2 set parameter is off: Should not happen!")
            return
        if status == 1 and set_start_co2:
            log.fatal("MARTA is status 1 but CO2 set parameter is on: Should not happen!")
            return

        if status == 1 and not set_start_chiller:
            self.to_CONNECTED()
        elif status == 1 and set_start_chiller:
            self.to_CHILLER_RUNNING()
        elif status == 2:
            self.to_CO2_RUNNING()
        elif status == 3:
            self.to_ERROR()

    def command(self, topic, message):
        commands = ["start_chiller", "start_co2", "stop_co2", "stop_chiller", "set_flow_active", "set_temperature_setpoint", "set_speed_setpoint", "set_flow_setpoint", "clear_alarms", "reconnect", "refresh"]
        parts = topic.split("/")
        assert(len(parts) == 3)
        device, cmd, command = parts[:3]
        assert(device == "MARTA")
        assert(cmd == "cmd")
        assert(command in commands)

        message = message.decode() # message arrives as bytes array
        if command == "start_chiller":
            log.debug("Starting chiller")
            self.cmd_start_chiller()
        elif command == "start_co2":
            log.debug("Starting CO2")
            self.cmd_start_co2()
        elif command == "stop_co2":
            log.debug("Stopping CO2")
            self.cmd_stop_co2()
        elif command == "stop_chiller":
            log.debug("Stopping chiller")
            self.cmd_stop_chiller()
        elif command == "set_flow_active":
            assert(int(message) in [0, 1])
            self.register_map["set_flow_active"].write(int(message))
        elif command == "set_temperature_setpoint":
            assert(-35. <= float(message) <= 25.)
            self.register_map["set_temperature_setpoint"].write(float(message))
        elif command == "set_speed_setpoint":
            assert(0. <= float(message) <= 6000.)
            self.register_map["set_speed_setpoint"].write(float(message))
        elif command == "set_flow_setpoint":
            assert(0. <= float(message) <= 5.)
            self.register_map["set_flow_setpoint"].write(float(message))
        elif command == "clear_alarms":
            log.debug("Clearing alarms!")
            self.cmd_clear_alarms()
        elif command == "refresh":
            self.publish(force=True)
        elif command == "reconnect":
            log.debug("Reconnecting!")
            self.fsm_reconnect_modbus()

    def publish(self, force=False):
        if hasattr(self, "client"):
            msg = json.dumps(self.status())
            log.debug(f"Sending: {msg}")
            self.client.publish("MARTA/status", msg)

    def status(self):
        return {
            "fsm_state": str(self.state).split(".")[1],
            **{ name: reg.read() for name,reg in self.register_map.items() }
        }

    def launch_mqtt(self, mqtt_host):
        def on_connect(client, userdata, flags, rc):
            # Subscribing in on_connect() means that if we lose the connection and
            # reconnect then subscriptions will be renewed.
            client.subscribe(f"MARTA/cmd/#")
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

        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(mqtt_host, 1883, 60)
        client.loop_start()
        while 1:
            self.update_status()
            self.publish()
            time.sleep(1)
        client.disconnect()
        client.loop_stop()

if __name__ == "__main__":
    parser = argparse.ArgumentParser("Entry point for CAEN PS control and monitoring backend")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--mqtt-host", required=True, help="URL of MQTT broker")
    parser.add_argument("--marta-ip", required=True, help="IP address of MARTA")
    parser.add_argument("--slave-id", type=int, default=1, help="Mobdbus ID of MARTA")
    parser.add_argument("config", help="YAML configuration file listing channels")
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    device = MARTAClient(args.marta_ip, args.slave_id, args.config, verbose=args.verbose)
    device.fsm_connect_modbus()
    device.launch_mqtt(args.mqtt_host)

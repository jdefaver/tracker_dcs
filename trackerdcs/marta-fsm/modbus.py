from pymodbus.payload import BinaryPayloadBuilder
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder
from pymodbus.pdu import ModbusExceptions
from pymodbus.exceptions import ModbusException

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
            if rr.isError():
                code = ModbusExceptions.decode(rr.exception_code)
                raise ModbusException(f"Failure to read {length} registers starting from address {start}. Error code: {code}")
            for i,addr in enumerate(range(start, start+length)):
                self.registers[addr] = rr.registers[i]

    def get(self, baseAddr, width=1):
        return [ self.registers[addr] for addr in range(baseAddr, baseAddr + width) ]

    def write(self, baseAddr, values):
        if isinstance(values, int):
            values = [ values ]
        assert(all(addr in self.input_registers for addr in range(baseAddr, baseAddr + len(values))))
        rr = client.write_registers(baseAddr, values, unit=self.unit)
        if rr.isError():
            code = ModbusExceptions.decode(rr.exception_code)
            raise ModbusException(f"Failure to write {len(values)} registers starting from address {baseAddr}. Error code: {code}")
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


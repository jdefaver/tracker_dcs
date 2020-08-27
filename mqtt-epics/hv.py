import paho.mqtt.client as mqtt
from dataclasses import dataclass
import time
import json
import epics

@dataclass
class Channel(object):
    number: int
    on: bool = False
    V0Set: float = 0.


# class caenHV(epics.Device):
#     attrs = ("V0Set")

#     def __init__(self, name="cleanroom", nGrp, nChan):
#         self.attrs = ( f"0{g}:00{c}:{a}" for g in range(nGrp) for c in range(nChan) for a in self.attrs )
#         epics.Device.__init__(self, prefix=name, delim=':', attrs=self.attrs)

class DummyHV(object):

    def __init__(self, name, nchans=2):
        self.channels = [Channel(i) for i in range(nchans)]
        self.name = name

        self.PVs = { f"cleanroom:00:00{c}:V0Set": epics.PV(f"cleanroom:00:00{c}:V0Set", auto_monitor=True, verbose=True, callback=self.update) for c in range(nchans) }
        # make sure we are initialized with the correct value!
        for name,pv in self.PVs.items():
            self.update(value=pv.value, pvname=name)

    def update(self, pvname, value, **kwargs):
        print(f"In callback: got {pvname}, {value}, {kwargs}")
        chan = int(pvname.split(":")[2])
        setattr(self.channels[chan], pvname.split(":")[-1], value)
        self.publish()

    def publish(self):
        if hasattr(self, "client"):
            self.client.publish(
                '/{}/status'.format(self.name),
                json.dumps(self.status())
            )

    def command(self, topic, message):
        device, cmd, command, channel = topic.split('/')[1:]
        if cmd != 'cmd':
            raise ValueError('command messages should be of the form /device/cmd/#')
        commands = ['switch', 'setv']
        if device != self.name:
            raise ValueError('wrong hv! ', device, self.name)
        channel = int(channel)
        if command == 'switch':
            if message == 'on':
                self.channels[channel].on = True
            elif message == 'off':
                self.channels[channel].on = False
            else:
                raise ValueError('can only switch on or off')
        elif command == 'setv':
            print('setting vreq', channel, message)
            self.channels[channel].V0Set = float(message)
            print(f"Setting PV to {float(message)}")
            self.PVs[f"cleanroom:00:00{channel}:V0Set"].value = self.channels[channel].V0Set
        else:
            raise ValueError('only possible commands are', commands)

    def status(self):
        """TODO: Write unittest"""
        status_channels = []
        for channel in self.channels:
            status_channels.append({
                'number': channel.number,
                'on': int(channel.on), # issue with bools in telegraf/influxdb
                'V0Set': channel.V0Set,
            })
        return status_channels


def on_connect(client, userdata, flags, rc):
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe('/{}/cmd/#'.format(client.device.name))
    # make sure the initial values are published at restart
    client.device.publish()

def on_message(client, userdata, msg):
    print('recv', msg.topic, msg.payload)
    client.device.command(msg.topic, msg.payload)


def run(device, mqtt_host):
    client = mqtt.Client()
    client.device = device
    device.client = client
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(mqtt_host, 1883, 60)
    client.loop_forever()
    # client.loop_start()
    # while 1:
    #     client.publish(
    #         '/{}/status'.format(device.name),
    #         json.dumps(device.status())
    #     )
    #     time.sleep(1)
    # time.sleep(1)
    # client.disconnect()
    # client.loop_stop()


if __name__ == '__main__':
    import sys
    device_name, mqtt_host = sys.argv[1:]
    device = DummyHV(device_name)
    run(device, mqtt_host)

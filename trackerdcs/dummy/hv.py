import paho.mqtt.client as mqtt
from dataclasses import dataclass
import time

@dataclass
class Channel(object):
    number: int
    on: bool = False
    vreq: float = 0.


class DummyHV(object):

    def __init__(self, name, nchans=1):
        self.channels = [Channel(i) for i in range(nchans)]
        self.name = name

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
            self.channels[channel].vreq = float(message)
        else:
            raise ValueError('only possible commands are', commands)


def on_connect(client, userdata, flags, rc):
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe('/{}/cmd/#'.format(client.device.name))


def on_message(client, userdata, msg):
    print('recv', msg.topic, msg.payload)
    client.device.command(msg.topic, msg.payload)


def run(device):
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.device = device
    client.connect("localhost", 1883, 60)
    client.loop_start()
    while 1:
        client.publish('/{}/status/0'.format(device.name),
                       device.channels[0].vreq)
        time.sleep(0.5)
    time.sleep(1)
    client.disconnect()
    client.loop_stop()


if __name__ == '__main__':
    import sys
    device_name = sys.argv[1]
    device = DummyHV(device_name)
    run(device)

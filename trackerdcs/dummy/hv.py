import paho.mqtt.client as mqtt
from dataclasses import dataclass


@dataclass
class Channel(object):
    number: int
    on: bool = False
    vreq: float = 0.


class DummyHV(object):

    def __init__(self, nchans=1, name='hv'):
        self.channels = [Channel(i) for i in range(nchans)]
        self.name = name

    def command(self, topic, message):
        commands = ['switch', 'setv', 'status']
        device, channel, command = topic.split('/')[1:]
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
            self.channels[channel].vreq = float(message)
        elif command == 'status':
            return getattr(self.channels[channel], message)
        else:
            raise ValueError('only possible commands are', commands)


def on_connect(client, userdata, flags, rc):
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe('/hv/#')


def on_message(client, userdata, msg):
    print(msg.topic, msg.payload)


def connect(hv):
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect("localhost", 1883, 60)
    client.loop_forever()


if __name__ == '__main__':
    run()

#!/usr/bin/env python3

# from transitions import Machine
from transitions.extensions import LockedMachine as Machine
import enum
import paho.mqtt.client as mqtt
import json
import threading
import time
import logging

from channel import TrackerChannel

log = logging.getLogger("dcs")
logging.basicConfig(format='%(asctime)s %(message)s')
log.setLevel(logging.DEBUG)

class DCSStates(enum.Enum):
    INIT = -1
    DISCONNECTED = 0
    CONNECTED = 1
    LV_OFF = 2
    LV_MIX = 3
    LV_ON = 4
    HV_RAMP = 5
    HV_MIX = 6
    HV_ON = 7
    ERROR = 8

def TrackerDCS(object):
    def __init__(self):
        self.channels = []
        
        self.transitions = {}

        self.machine = Machine(model=self, states=DCSStates, transitions=transitions, initial=DCSStates.INIT)

    def add_channel(lv, hv):
        assert(len(lv) == 2 and 0 <= lv[0] <= 4 and 0 <= lv[1] <= 7)
        assert(len(hv) == 2 and 12 <= hv[0] <= 15 and 0 <= lv[1] <= 11)
        self.channels.append(TrackerChannel(f"module{len(self.channels)}", lv, hv))



def on_connect(client, userdata, flags, rc):
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe('/{}/cmd/#'.format(client.device.name))
    # make sure the initial values are published at restart
    client.device.publish()

def on_message(client, userdata, msg):
    log.debug(f"Received {msg.topic}, {msg.payload}")
    # MQTT catches all exceptions in the callbacks, so they'll go unnoticed
    try:
        client.device.command(msg.topic, msg.payload)
    except Exception as e:
        log.error(f"Issue processing command: {e}")


def run(device, mqtt_host):
    device.fsm_connect_epics()

    client = mqtt.Client()
    client.device = device
    device.client = client
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(mqtt_host, 1883, 60)
    # client.loop_forever()
    client.loop_start()
    while 1:
        epics.ca.poll()
        device.publish()
        time.sleep(1)
    time.sleep(1)
    client.disconnect()
    client.loop_stop()

if __name__ == '__main__':
    import sys
    device_name, mqtt_host = sys.argv[1:]
    device = TrackerChannel(device_name, (0, 0), (12, 0))
    run(device, mqtt_host)

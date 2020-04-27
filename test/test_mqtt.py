import unittest
import paho.mqtt.client as mqtt


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("/test/#")

# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    print(msg.topic+" "+str(msg.payload))


class TestMQTTBroker(unittest.TestCase):

    def test_connect(self):
        '''Test connection to mqtt broker inside the stack'''
        client = mqtt.Client()
        client.on_connect = on_connect
        client.on_message = on_message
        ret = client.connect("mosquitto", 1883, 60)
        self.assertEqual(ret, 0)


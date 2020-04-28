import unittest
import time
import paho.mqtt.client as mqtt

class TestMQTTBroker(unittest.TestCase):

    def test_connect(self):
        """Test connection to mqtt broker inside the stack"""

        def on_connect(client, userdata, flags, rc):
            # Subscribing in on_connect() means that if we lose the connection and
            # reconnect then subscriptions will be renewed.
            client.subscribe("/test/#")

        def on_message(client, userdata, msg):
            print(msg.topic + " " + str(msg.payload))

        client = mqtt.Client()
        client.on_connect = on_connect
        client.on_message = on_message
        ret = client.connect("localhost", 1883, 60)
        self.assertEqual(ret, 0)

    def test_wait_connect(self):

        def on_connect(client, userdata, flags, rc):
            # Subscribing in on_connect() means that if we lose the connection and
            # reconnect then subscriptions will be renewed.
            client.subscribe("/test/#")
            client.connected = True

        client = mqtt.Client()
        client.on_connect = on_connect
        client.connected = False
        ret = client.connect("localhost", 1883, 60)
        client.loop_start()
        while not client.connected:
            time.sleep(0.001)
        self.assertTrue(True)
        client.disconnect()
        client.loop_stop()

    def test_async_pub_sub(self):

        values = []

        def on_connect(client, userdata, flags, rc):
            # Subscribing in on_connect() means that if we lose the connection and
            # reconnect then subscriptions will be renewed.
            client.subscribe("/test/#")
            client.connected = True

        def on_message(client, userdata, message):
            global messages
            values.append(int(message.payload))

        client = mqtt.Client()
        client.on_connect = on_connect
        client.on_message = on_message
        client.connected = False
        ret = client.connect("localhost", 1883, 60)
        client.loop_start()
        while not client.connected:
            time.sleep(0.01)
        topic = '/test'
        client.subscribe(topic)
        n_messages = 10
        for i in range(n_messages):
            client.publish(topic, i)
            time.sleep(0.01)
        # leave enough time for all messages to arrive
        time.sleep(1)
        client.disconnect()
        client.loop_stop()
        self.assertListEqual(values, list(range(n_messages)))

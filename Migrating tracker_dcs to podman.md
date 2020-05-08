Migrating tracker_dcs to podman
===

First, we create a pod:

> podman pod create --name tracker_dcs -p 8086 -p 3000 -p 1880 -p 1883

Ports are shared for influxdb (8086), grafana (3000), nodered (1880), and mosquito (1883). The last one might be dropped, but for now it will allow to monitor the MQTT traffic.

Then, create the containers:

> podman run --pod tracker_dcs -d -v influxdb:/var/lib/influxdb influxdb
> podman run --pod tracker_dcs -d -v ./telegraf.conf:/etc/telegraf/telegraf.conf:ro telegraf
> podman run --pod tracker_dcs -d -v nodered_tk:/data localhost/node-red
> podman run --pod tracker_dcs -d -v mosquitto_data:/mosquitto/data -v mosquitto_log:/mosquitto/log -v ./mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf eclipse-mosquitto
> podman run --pod tracker_dcs -d -v grafana_tk:/var/lib/grafana grafana/grafana

 
For nodered, we build it to have plugins preinstalled. Could also be evolved to contain the default dashboard, etc.
> podman build -t localhost/node-red . 

Note also that for it to work with SELinux on Fedora, we have to do
> chcon -t svirt_sandbox_file_t telegraf.conf
> chcon -t svirt_sandbox_file_t mosquitto/mosquitto.conf 

Finally, we create the yaml file:
>podman generate kube tracker_dcs > tracker_dcs.yaml

The unit test works.
Then, I want to run the demo. For that I first build the trackerdcs image:
>podman build -t localhost/trackerdcs .

Then I run them in the pod.

>podman run --pod tracker_dcs -d localhost/trackerdcs python dummy/hv.py hv localhost
>podman run --pod tracker_dcs -d localhost/trackerdcs python dummy/hv.py lv localhost
>podman run --pod tracker_dcs -d localhost/trackerdcs python dummy/sensor.py sensor_1 localhost

Note that compared with the docker-compose approach we connect to localhost since we run in the same pod.

Same for the telegraf config: put localhost everywhere.


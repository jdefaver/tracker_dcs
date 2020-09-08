Migrating tracker_dcs to podman
===

First, we create a pod:

```
podman pod create --name tracker_dcs -p 8086 -p 3000 -p 1880 -p 1883
```

Ports are shared for influxdb (8086), grafana (3000), nodered (1880), and mosquito (1883). The last one might be dropped, but for now it will allow to monitor the MQTT traffic.

First build the required image (later could put them on a registry):

```
podman build -t localhost/node-red ./node-red
podman build -t localhost/pyepics ./mqtt-epics
```

Creat the directories which will be bind-mounted:
```
mkdir influxdb grafana
```

Then run the DB:
```
podman run --pod tracker_dcs -d --init --userns=keep-id --name tdcs_influx -v ./influxdb:/var/lib/influxdb influxdb
```
If it's the first time, might need to create a test DB:
```
$ podman exec -it tdcs_influx influx
> create database testdb
```

Then, run the containers:

```
podman run --pod tracker_dcs -d --init --name telegraf -v ./telegraf.conf:/etc/telegraf/telegraf.conf:ro telegraf
podman run --pod tracker_dcs -d --init --userns=keep-id --name tdcs_mosquitto -v mosquitto_data:/mosquitto/data -v mosquitto_log:/mosquitto/log -v ./mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf eclipse-mosquitto
podman run --pod tracker_dcs -d --init --userns=keep-id -u $(id -u) --name tdcs_grafana -v ./grafana:/var/lib/grafana grafana/grafana
podman run --pod tracker_dcs -d --init --userns=keep-id --name tdcs_node-red -v ./node-red/data:/data localhost/node-red
```

We use `--userns=keep-id` and (`-u $(id -u)` for grafana because by default it runs with a different user) to be able to write to the bind volumes.

CC7 note: it seems the containers should run as root with `-u 0:0` and no `--userns`; also `--init` is not supported there.

We can then run the HV:
```
podman run --pod tracker_dcs -d --init -e EPICS_CA_NAME_SERVERS=130.104.48.188 -e EPICS_CA_AUTO_ADDR_LIST=NO -v ./mqtt-epics/hv.py:/usr/src/app/hv.py localhost/pyepics python -u hv.py hv localhost
```

When running in the UCL network EPICS can also work with `-e EPICS_CA_AUTO_ADDR_LIST=130.104.48.188` instead of the above.

To connect from outside the UCL network (with the pod running on the DAQ PC), run in three separate terminals:
- `sshuttle -r server02.fynu.ucl.ac.be 130.104.48.0/24`
- `ssh -L 1880:localhost:1880 130.104.48.63`
- `ssh -L 3000:localhost:3000 130.104.48.63`
You can then point your browser to `localhost:1880` or `localhost:3000`.


## From Christophe

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


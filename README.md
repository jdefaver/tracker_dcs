# Tracker DCS Lyon

## Overall Architecture

The system follows a microservice architecture. Each module in this architecture is deployed as a docker container. 

The whole architecture is described and managed as a docker-compose stack. 

Users interact with the architecture through a gateway with two modules: 

* a grafana web server: monitoring dashboards
* a node-red web server: labview equivalent for the slow control and logic

The connection to these modules is secured with TLS. 

In the stack, modules mostly communicate with the MQTT protocol.  

![](doc/architecture.png)


## Installation 

The architecture is based on Docker, and we use docker images built for X86_64 systems. Therefore, the tracker DCS stack can run on any computer with this architecture. 

The software stack is described and managed by docker-compose. 

First, clone this repository to your machine, and go inside: 

```
git clone https://github.com/cbernet/tracker_dcs.git
cd tracker_dcs
```

Then, install the docker engine and docker-compose for your machine as instructed below. Both tools are available in Docker Desktop.

### Mac OS

[Install Docker Desktop on a mac](https://docs.docker.com/docker-for-mac/install/)

### Linux

[Install the docker engine](https://docs.docker.com/engine/install/) for your platform. 

Then, I suggest to [install docker-compose with pip](https://docs.docker.com/compose/install/#install-using-pip), the python package manager. Make sure you use python3, and that pip is connected to your version of python3. 

### Windows

Please note the system requirements before attempting the install, docker desktop cannot be installed on all versions of Windows!

[Install Docker Desktop on Windows](https://docs.docker.com/docker-for-windows/install/)


## Running

```
docker-compose up -d 
```

## Accessing the services from the host machine

### Grafana and Node-red web GUIs

* grafana: [http://localhost:3000](http://localhost:3000)
* nodered: [http://localhost:1880](http://localhost:1880)

Passwords : ask Colin


## TODO

* think about user interface and access
* think about global architecture: inputs, outputs, role of mqtt and db, ... 
* data generator in nodered to mqtt
* influxDB loader (mqtt listener)
  * python script? **Telegraf?**
  * think about topic naming for the loader
* grafana dashboard to look at the data from influxdb
  * how to initialize pre-built dashboard?    
* install nodered modules npm 
  * ask pavel about the utility of each package
* test mqtt broker from outside, from inside 
* set up a mockup test suite? 
* security: 
  * grafana: just change password
  * nodered: how to handle credentials
  * influxdb: keep it confined - expose? 
  * mosquitto: keep it confined - securing mosquitto is too painful. 
* backups: set up a backup procedure for all named volumes in the stack 



# Tracker DCS Lyon

## TODO

* data generator in nodered to mqtt
* influxDB loader (mqtt listener)
  * python script?  
  * think about topic naming for the loader
* grafana dashboard to look at the data from influxdb
* install nodered modules npm 
* test mqtt broker from outside, from inside 
* set up a mockup test suite? 


## Installation 

* install docker-compose POINT TO INSTRUCTIONS FOR THE MAC
* pip instructions for other machines 


## Running

```
docker-compose up -d 
```

## Accessing the services from the host machine

### Grafana and Node-red web GUIs

* grafana: [http://localhost:3000](http://localhost:3000)
* nodered: [http://localhost:1880](http://localhost:1880)

### InfluxDB 

Explain: 

* command line access
* from python




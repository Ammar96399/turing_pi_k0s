from fastapi import FastAPI
import time
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
import uvicorn

app = FastAPI()

INFLUX_URL = "http://localhost:8086"
TOKEN = "gE-akWY-Ws4RlOlOqV1Njk0S6Vmw-s7LDf3ULC3wP5Sr7MYBobcd2wDnvtqkeHKr9Stya0KlIbpaI4Qo0j9Auw=="
ORG = "IRISA"
BUCKET = "test"
SIM_RATIO = 6.0 

class SimClock:
    def __init__(self):
        self.real_start_time = time.time()
        self.sim_start_timestamp = 1704067200

clock = SimClock()
client = InfluxDBClient(url=INFLUX_URL, token=TOKEN, org=ORG)
write_api = client.write_api(write_options=SYNCHRONOUS)

@app.post("/reset")
def reset_clock(start_timestamp: int = 1704067200):
    """Resets the simulation clock to now."""
    clock.real_start_time = time.time()
    clock.sim_start_timestamp = start_timestamp
    return {"status": "Simulation Reset", "sim_start": start_timestamp}

@app.post("/data/{measurement}")
def post_data(measurement: str, value: float, device: str = "unknown"):
    # 1. Calculate Virtual Time
    elapsed_real = time.time() - clock.real_start_time
    # print(f"elapsed_real = time.time() - clock.real_start_time")
    # print(f"{elapsed_real} = {time.time()} - {clock.real_start_time}")
    sim_now = clock.sim_start_timestamp + (elapsed_real * SIM_RATIO)
    # print(f"sim_now = clock.sim_start_timestamp + (elapsed_real * SIM_RATIO)")
    # print(f"{sim_now} = {clock.sim_start_timestamp} + ({elapsed_real} * {SIM_RATIO})")
    
    # 2. Create Influx Point
    point = Point(measurement) \
        .tag("device", device) \
        .field("value", value) \
        .time(int(sim_now * 1e9), WritePrecision.NS)
    
    # 3. Write to Influx
    write_api.write(bucket=BUCKET, org=ORG, record=point)
    
    return {"sim_time": sim_now, "sent_value": value}

if __name__ == "__main__":
    # Listen on all interfaces so outside devices can reach it
    uvicorn.run(app, host="0.0.0.0", port=5000)
import argparse
import datetime as dt
import os
import time
import json
import urllib.parse
import urllib.request
import urllib.error
from influxdb_client import InfluxDBClient

# --- CONSTANTS FOR SIMULATION (set via CLI args) ---
STC_IRRADIANCE: float | None = None  # W/m2 (Standard Test Condition)
SYSTEM_LOSSES: float | None = None   # fraction (cabling, inverter, dirt)

BATTERY_CAPACITY_WH: float | None = None
INITIAL_SOC_WH: float | None = None
MAX_CHARGE_W: float | None = None

CHARGE_EFFICIENCY: float | None = None
DISCHARGE_EFFICIENCY: float | None = None
LOCAL_TZ: dt.tzinfo | None = None

def parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()

def fetch_csv(lat, lon, start, end):
    params = {
        "lat": f"{lat}", "lon": f"{lon}",
        "startyear": str(start.year), "endyear": str(end.year),
        "outputformat": "csv", "timeinterval": "1"
    }
    url = "https://re.jrc.ec.europa.eu/api/seriescalc?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url) as r:
        return r.read().decode("utf-8")

def parse_pvgis_csv(csv_text: str):
    rows, header, in_data = [], None, False
    for line in csv_text.splitlines():
        if line.startswith("time,"):
            header = line.split(","); in_data = True; continue
        if in_data:
            if line.startswith("#"): break
            rows.append(line.split(","))
    return header, rows

def fetch_sim_time(time_proxy_url: str) -> float:
    url = f"{time_proxy_url.rstrip('/')}/time"
    with urllib.request.urlopen(url) as r:
        payload = json.loads(r.read().decode("utf-8"))
    return float(payload["time"])

def send_to_time_proxy(time_proxy_url: str, measurement: str, value: float, device: str) -> bool:
    params = urllib.parse.urlencode({
        "value": float(value),
        "device": device,
    })
    url = f"{time_proxy_url.rstrip('/')}/data/{measurement}?{params}"
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"time_proxy write failed: HTTP {e.code} {body}")
        return False
    except urllib.error.URLError as e:
        print(f"time_proxy write failed: {e}")
        return False

def build_hourly_generation_map(header, rows, start_date, end_date, wp):
    time_idx = header.index("time")
    ghi_idx = header.index("G(i)")
    hourly_gen_wh = {}
    daily_gen_wh = {}
    for r in rows:
        if len(r) != 6:
            continue
        t = dt.datetime.strptime(r[time_idx], "%Y%m%d:%H%M").replace(tzinfo=LOCAL_TZ)
        t_hour = t.replace(minute=0, second=0, microsecond=0)
        if not (start_date <= t.date() <= end_date):
            continue
        irradiance_wm2 = float(r[ghi_idx])
        p_gen = (irradiance_wm2 / STC_IRRADIANCE) * wp * (1 - SYSTEM_LOSSES)
        gen_wh = max(0.0, p_gen) * 1.0
        hourly_gen_wh[t_hour] = hourly_gen_wh.get(t_hour, 0.0) + gen_wh
        daily_gen_wh[t_hour.date()] = daily_gen_wh.get(t_hour.date(), 0.0) + gen_wh
    return hourly_gen_wh, daily_gen_wh

def query_consumed_wh(query_api, bucket, start_dt, end_dt, wattmeter_device):
    start_iso = start_dt.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    end_iso = end_dt.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: {start_iso}, stop: {end_iso})
  |> filter(fn: (r) => r._measurement == "power_W" and r._field == "value" and r.device == "{wattmeter_device}")
  |> integral(unit: 1h)
'''
    tables = query_api.query(flux)
    total = 0.0
    for table in tables:
        for record in table.records:
            total += float(record.get_value())
    return total

def update_battery_soc(soc_wh, gen_wh, consumed_wh):
    if gen_wh >= consumed_wh:
        surplus = gen_wh - consumed_wh
        charge_cap_wh = MAX_CHARGE_W * 1.0
        charge_wh = min(surplus * CHARGE_EFFICIENCY, charge_cap_wh)
        return min(BATTERY_CAPACITY_WH, soc_wh + charge_wh)
    deficit = consumed_wh - gen_wh
    draw_needed = deficit / DISCHARGE_EFFICIENCY
    draw = min(draw_needed, soc_wh)
    return soc_wh - draw

def run_simulation(header, rows, start_date, end_date, wp, hourly_load_w):
    time_idx = header.index("time")
    ghi_idx = header.index("G(i)") 
    
    soc = INITIAL_SOC_WH
    results = []
    
    # Header reflects Watt units now
    new_header = header + ["P_gen_w", "Load_w", "Battery_SoC_wh", "Grid_Impact_w"]

    for r in rows:
        if len(r) != 6: 
            continue
        t = dt.datetime.strptime(r[time_idx], "%Y%m%d:%H%M")
        if not (start_date <= t.date() <= end_date): continue

        # 1. Calculate Generation in Watts
        irradiance_wm2 = float(r[ghi_idx])
        # P (W) = (Actual Sun / 1000) * Rated Wp * (1 - Losses)
        p_gen = (irradiance_wm2 / STC_IRRADIANCE) * wp * (1 - SYSTEM_LOSSES)
        
        # 2. Net Power (Generation - Consumption)
        net_p = p_gen - hourly_load_w
        grid_impact = 0

        if net_p > 0: # Surplus: Charge battery
            # charge_amount is in Wh for this hour
            charge_power = min(net_p * CHARGE_EFFICIENCY, MAX_CHARGE_W)
            new_soc = min(BATTERY_CAPACITY_WH, soc + charge_power)
            
            # Grid impact is the remaining surplus that didn't go to battery
            grid_impact = net_p - (new_soc - soc) / CHARGE_EFFICIENCY
            soc = new_soc
        else: # Deficit: Draw from battery
            needed = abs(net_p) / DISCHARGE_EFFICIENCY
            draw = min(needed, soc)
            soc -= draw
            # Remaining deficit comes from grid
            grid_impact = (needed - draw) * -1 

        results.append(r + [f"{p_gen:.2f}", f"{hourly_load_w:.2f}", f"{soc:.2f}", f"{grid_impact:.2f}"])
    
    return new_header, results

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--stc-irradiance", type=float, required=True)
    p.add_argument("--system-losses", type=float, required=True)
    p.add_argument("--battery-capacity-wh", type=float, required=True)
    p.add_argument("--initial-soc-wh", type=float, required=True)
    p.add_argument("--max-charge-w", type=float, required=True)
    p.add_argument("--charge-efficiency", type=float, required=True)
    p.add_argument("--discharge-efficiency", type=float, required=True)
    p.add_argument("--tz-offset-hours", type=float, required=True)
    p.add_argument("--wp", type=float, default=150.0, help="Solar Peak Power in Watts")
    p.add_argument("--load", type=float, default=5.0, help="Fallback hourly load in Watts")
    p.add_argument("--device", default="solar_model", help="Device tag to send with data")
    p.add_argument("--wattmeter-device", default="wattmeter", help="Device tag for power_W")
    p.add_argument("--time-proxy-url", default=os.getenv("TIME_PROXY_URL", "http://localhost:5000"))
    args = p.parse_args()

    global STC_IRRADIANCE
    global SYSTEM_LOSSES
    global BATTERY_CAPACITY_WH
    global INITIAL_SOC_WH
    global MAX_CHARGE_W
    global CHARGE_EFFICIENCY
    global DISCHARGE_EFFICIENCY
    global LOCAL_TZ

    STC_IRRADIANCE = args.stc_irradiance
    SYSTEM_LOSSES = args.system_losses
    BATTERY_CAPACITY_WH = args.battery_capacity_wh
    INITIAL_SOC_WH = args.initial_soc_wh
    MAX_CHARGE_W = args.max_charge_w
    CHARGE_EFFICIENCY = args.charge_efficiency
    DISCHARGE_EFFICIENCY = args.discharge_efficiency
    LOCAL_TZ = dt.timezone(dt.timedelta(hours=args.tz_offset_hours))

    start, end = parse_date(args.start), parse_date(args.end)
    
    print("Fetching data from PVGIS...")
    csv_text = fetch_csv(args.lat, args.lon, start, end)
    header, rows = parse_pvgis_csv(csv_text)
    if not header or not rows:
        raise RuntimeError("PVGIS returned no hourly data. Check date range and API parameters.")
    hourly_gen_wh, daily_gen_wh = build_hourly_generation_map(header, rows, start, end, args.wp)

    influx_url = os.getenv("INFLUX_URL")
    influx_token = os.getenv("INFLUX_TOKEN")
    influx_org = os.getenv("INFLUX_ORG")
    influx_bucket = os.getenv("INFLUX_BUCKET")
    influx_client = None
    query_api = None
    if influx_url and influx_token and influx_org and influx_bucket:
        influx_client = InfluxDBClient(url=influx_url, token=influx_token, org=influx_org)
        query_api = influx_client.query_api()
    else:
        print("InfluxDB env vars missing; will use fallback load.")

    last_day = None
    last_hour = None
    soc_wh = INITIAL_SOC_WH

    print("Starting time_proxy polling loop...")
    try:
        while True:
            sim_ts = fetch_sim_time(args.time_proxy_url)
            sim_dt = dt.datetime.fromtimestamp(sim_ts, LOCAL_TZ)

            if sim_dt.date() > end:
                print("Simulation range finished.")
                break

            if last_day != sim_dt.date():
                last_day = sim_dt.date()
                if last_day in daily_gen_wh:
                    send_to_time_proxy(args.time_proxy_url, "solar_energy_daily_wh", daily_gen_wh[last_day], args.device)

            current_hour = sim_dt.replace(minute=0, second=0, microsecond=0)
            if last_hour != current_hour:
                print(f"current_hour = {current_hour}")
                last_hour = current_hour
                gen_wh = hourly_gen_wh.get(current_hour, 0.0)
                print(f"gen_wh={gen_wh}")
                send_to_time_proxy(args.time_proxy_url, "solar_energy_hourly_wh", gen_wh, args.device)

                consumed_wh = None
                if query_api and influx_bucket:
                    start_dt = current_hour - dt.timedelta(hours=1)
                    end_dt = current_hour
                    consumed_wh = query_consumed_wh(query_api, influx_bucket, start_dt, end_dt, args.wattmeter_device)
                if consumed_wh is None:
                    consumed_wh = args.load * 1.0

                soc_wh = update_battery_soc(soc_wh, gen_wh, consumed_wh)
                send_to_time_proxy(args.time_proxy_url, "battery_soc_wh", soc_wh, args.device)

            time.sleep(1)
    finally:
        if influx_client:
            influx_client.close()

if __name__ == "__main__":
    main()
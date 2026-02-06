import argparse
import csv
import datetime as dt
import urllib.parse
import urllib.request

# --- CONSTANTS FOR SIMULATION ---
STC_IRRADIANCE = 1000.0  # W/m2 (Standard Test Condition)
SYSTEM_LOSSES = 0.14     # 14% (cabling, inverter, dirt)

# Converted to Wh (10kWh -> 10000Wh)
BATTERY_CAPACITY_WH = 100.0
INITIAL_SOC_WH = 100.0   
MAX_CHARGE_W = 50.0      # Max power battery can take in Watts

CHARGE_EFFICIENCY = 0.95 
DISCHARGE_EFFICIENCY = 0.94

def parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()

def fetch_csv(lat, lon, start, end):
    params = {
        "lat": f"{lat}", "lon": f"{lon}",
        "startyear": str(start.year), "endyear": str(end.year),
        "outputformat": "csv", "hourly": "1"
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
    p.add_argument("--wp", type=float, default=100.0, help="Solar Peak Power in Watts")
    p.add_argument("--load", type=float, default=5.0, help="Constant hourly load in Watts")
    p.add_argument("--out", default="solar_sim_results.csv")
    args = p.parse_args()

    start, end = parse_date(args.start), parse_date(args.end)
    
    print("Fetching data from PVGIS...")
    csv_text = fetch_csv(args.lat, args.lon, start, end)
    header, rows = parse_pvgis_csv(csv_text)
    
    print(f"Running simulation for {args.wp}Wp system...")
    new_header, final_rows = run_simulation(header, rows, start, end, args.wp, args.load)

    with open(args.out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(new_header)
        writer.writerows(final_rows)

    print(f"Simulation complete. {len(final_rows)} hours processed.")

if __name__ == "__main__":
    main()
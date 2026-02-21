import argparse
import datetime as dt
import os
import time
import json
import logging
import urllib.parse
import urllib.request
import urllib.error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# --- CONSTANTS FOR SIMULATION (set via CLI args) ---
STC_IRRADIANCE: float  # W/m2 (Standard Test Condition)
SYSTEM_LOSSES: float  # fraction (cabling, inverter, dirt)

BATTERY_CAPACITY_WH: float
INITIAL_SOC_WH: float
MAX_CHARGE_W: float

CHARGE_EFFICIENCY: float
DISCHARGE_EFFICIENCY: float
LOCAL_TZ: dt.tzinfo


def first_day_of_each_month(year: int):
    return [dt.date(year, month, 1) for month in range(1, 13)]


def fetch_csv(lat, lon, start, end):
    params = {
        "lat": f"{lat}",
        "lon": f"{lon}",
        "startyear": str(start.year),
        "endyear": str(end.year),
        "outputformat": "csv",
        "timeinterval": "1",
    }
    url = "https://re.jrc.ec.europa.eu/api/seriescalc?" + urllib.parse.urlencode(params)
    logger.info("Fetching PVGIS CSV from %s", url)
    with urllib.request.urlopen(url) as r:
        return r.read().decode("utf-8")


def parse_pvgis_csv(csv_text: str):
    rows, header, in_data = [], None, False
    for line in csv_text.splitlines():
        if line.startswith("time,"):
            header = line.split(",")
            in_data = True
            continue
        if in_data:
            if line.startswith("#"):
                break
            rows.append(line.split(","))
    return header, rows


def fetch_sim_time(time_proxy_url: str) -> float:
    url = f"{time_proxy_url.rstrip('/')}/time"
    with urllib.request.urlopen(url) as r:
        payload = json.loads(r.read().decode("utf-8"))
    return float(payload["time"])


def send_to_time_proxy(
    time_proxy_url: str, measurement: str, value: float, device: str
) -> bool:
    params = urllib.parse.urlencode(
        {
            "value": float(value),
            "device": device,
        }
    )
    url = f"{time_proxy_url.rstrip('/')}/data/{measurement}?{params}"
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            ok = 200 <= resp.status < 300
            if ok:
                logger.debug(
                    "time_proxy write ok: measurement=%s value=%.4f device=%s",
                    measurement,
                    value,
                    device,
                )
            return ok
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error("time_proxy write failed: HTTP %s %s", e.code, body)
        return False
    except urllib.error.URLError as e:
        logger.error("time_proxy write failed: %s", e)
        return False


def fetch_from_time_proxy(
    time_proxy_url: str,
    measurement: str,
    device: str,
    experiment_id: str,
    window_seconds: int,
):
    params = urllib.parse.urlencode(
        {
            "device": device,
            "experiment_id": experiment_id,
            "window_seconds": window_seconds,
        }
    )
    url = f"{time_proxy_url.rstrip('/')}/data/{measurement}?{params}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return payload
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error("time_proxy read failed: HTTP %s %s", e.code, body)
        return None
    except urllib.error.URLError as e:
        logger.error("time_proxy read failed: %s", e)
        return None


def build_hourly_generation_map(header, rows, selected_dates, wp):
    time_idx = header.index("time")
    ghi_idx = header.index("G(i)")
    hourly_gen_wh = {}
    daily_gen_wh = {}
    for r in rows:
        if len(r) != 6:
            continue
        t = dt.datetime.strptime(r[time_idx], "%Y%m%d:%H%M").replace(tzinfo=LOCAL_TZ)
        t_hour = t.replace(minute=0, second=0, microsecond=0)
        if t.date() not in selected_dates:
            continue
        irradiance_wm2 = float(r[ghi_idx])
        p_gen = (irradiance_wm2 / STC_IRRADIANCE) * wp * (1 - SYSTEM_LOSSES)
        gen_wh = max(0.0, p_gen) * 1.0
        hourly_gen_wh[t_hour] = hourly_gen_wh.get(t_hour, 0.0) + gen_wh
        daily_gen_wh[t_hour.date()] = daily_gen_wh.get(t_hour.date(), 0.0) + gen_wh
    return hourly_gen_wh, daily_gen_wh


def query_consumed_wh(
    time_proxy_url: str,
    wattmeter_device: str,
    experiment_id: str,
    window_seconds: int,
):
    payload = fetch_from_time_proxy(
        time_proxy_url,
        "power_W",
        wattmeter_device,
        experiment_id,
        window_seconds,
    )
    if not payload:
        return 0.0
    value = payload.get("value")
    return float(value) if value is not None else 0.0


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


def calculate_daily_energy_breakdown(
    daily_gen_wh: float, soc_wh: float, static_overhead_wh: float = 131.0
) -> dict:
    """
    Calculate daily energy breakdown based on battery state.

    Returns a dictionary with:
    - total_wh: gross daily generation
    - static_wh: static overhead
    - net_wh: generation after static overhead
    - apps_wh: energy allocated to apps
    - battery_wh: energy allocated to battery recharge
    """
    total_wh = daily_gen_wh
    static_wh = static_overhead_wh
    net_daily_gen = total_wh - static_wh

    if net_daily_gen <= 0:
        return {
            "total_wh": total_wh,
            "static_wh": static_wh,
            "net_wh": 0.0,
            "apps_wh": 0.0,
            "battery_wh": 0.0,
        }

    soc_percentage = soc_wh / BATTERY_CAPACITY_WH

    if soc_percentage < 0.25:
        app_allocation_pct = 0.25
    elif soc_percentage < 0.5:
        app_allocation_pct = 0.5
    elif soc_percentage < 0.75:
        app_allocation_pct = 0.75
    else:
        app_allocation_pct = 1.0

    apps_wh = net_daily_gen * app_allocation_pct
    battery_wh = net_daily_gen - apps_wh

    logger.debug(
        "Energy allocation: SOC=%.1f%% -> %.0f%% to apps, %.0f%% to recharge; "
        "net_gen=%.2f Wh, app_share=%.2f Wh",
        soc_percentage * 100,
        app_allocation_pct * 100,
        (1 - app_allocation_pct) * 100,
        net_daily_gen,
        apps_wh,
    )

    return {
        "total_wh": total_wh,
        "static_wh": static_wh,
        "net_wh": net_daily_gen,
        "apps_wh": apps_wh,
        "battery_wh": battery_wh,
    }


def calculate_available_energy_for_apps(
    daily_gen_wh: float, soc_wh: float, static_overhead_wh: float = 131.0
) -> float:
    """Backwards-compatible wrapper to get app allocation only."""
    return calculate_daily_energy_breakdown(daily_gen_wh, soc_wh, static_overhead_wh)[
        "apps_wh"
    ]


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
        if not (start_date <= t.date() <= end_date):
            continue

        # 1. Calculate Generation in Watts
        irradiance_wm2 = float(r[ghi_idx])
        # P (W) = (Actual Sun / 1000) * Rated Wp * (1 - Losses)
        p_gen = (irradiance_wm2 / STC_IRRADIANCE) * wp * (1 - SYSTEM_LOSSES)

        # 2. Net Power (Generation - Consumption)
        net_p = p_gen - hourly_load_w
        grid_impact = 0

        if net_p > 0:  # Surplus: Charge battery
            # charge_amount is in Wh for this hour
            charge_power = min(net_p * CHARGE_EFFICIENCY, MAX_CHARGE_W)
            new_soc = min(BATTERY_CAPACITY_WH, soc + charge_power)

            # Grid impact is the remaining surplus that didn't go to battery
            grid_impact = net_p - (new_soc - soc) / CHARGE_EFFICIENCY
            soc = new_soc
        else:  # Deficit: Draw from battery
            needed = abs(net_p) / DISCHARGE_EFFICIENCY
            draw = min(needed, soc)
            soc -= draw
            # Remaining deficit comes from grid
            grid_impact = (needed - draw) * -1

        results.append(
            r
            + [
                f"{p_gen:.2f}",
                f"{hourly_load_w:.2f}",
                f"{soc:.2f}",
                f"{grid_impact:.2f}",
            ]
        )

    return new_header, results


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--stc-irradiance", type=float, required=True)
    p.add_argument("--system-losses", type=float, required=True)
    p.add_argument("--battery-capacity-wh", type=float, required=True)
    p.add_argument("--initial-soc-wh", type=float, required=True)
    p.add_argument("--max-charge-w", type=float, required=True)
    p.add_argument("--charge-efficiency", type=float, required=True)
    p.add_argument("--discharge-efficiency", type=float, required=True)
    p.add_argument("--tz-offset-hours", type=float, required=True)
    p.add_argument("--wp", type=float, default=100.0, help="Solar Peak Power in Watts")
    p.add_argument(
        "--device", default="solar_model", help="Device tag to send with data"
    )
    p.add_argument(
        "--wattmeter-device", default="wattmeter", help="Device tag for power_W"
    )
    p.add_argument(
        "--time-proxy-url", default=os.getenv("TIME_PROXY_URL", "http://localhost:5000")
    )
    p.add_argument("--experiment-id", default=os.getenv("EXPERIMENT_ID", ""))
    p.add_argument(
        "--consumption-window-seconds",
        type=int,
        default=int(os.getenv("CONSUMPTION_WINDOW_SECONDS", "3600")),
    )
    args = p.parse_args()
    logger.info("Starting solar model with args: %s", args)

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

    selected_dates = first_day_of_each_month(args.year)
    selected_dates_set = set(selected_dates)
    selected_dates_by_day_index = {
        day_index: selected_dates[day_index] for day_index in range(12)
    }
    fetch_start = dt.date(args.year, 1, 1)
    fetch_end = dt.date(args.year, 12, 31)

    logger.info("Fetching data from PVGIS...")
    csv_text = fetch_csv(args.lat, args.lon, fetch_start, fetch_end)
    header, rows = parse_pvgis_csv(csv_text)
    if not header or not rows:
        raise RuntimeError(
            "PVGIS returned no hourly data. Check date range and API parameters."
        )
    logger.info("PVGIS data loaded: %d rows", len(rows))
    hourly_gen_wh, daily_gen_wh = build_hourly_generation_map(
        header, rows, selected_dates_set, args.wp
    )
    logger.info(
        "Generated hourly and daily maps for first day of each month: hourly=%d daily=%d",
        len(hourly_gen_wh),
        len(daily_gen_wh),
    )

    simulation_start_date = None
    last_day = None
    last_hour = None
    soc_wh = INITIAL_SOC_WH

    logger.info("Starting time_proxy polling loop...")
    try:
        while True:
            sim_ts = fetch_sim_time(args.time_proxy_url)
            sim_dt = dt.datetime.fromtimestamp(sim_ts, LOCAL_TZ)
            logger.debug("Sim time: %s", sim_dt.isoformat())

            if simulation_start_date is None:
                simulation_start_date = sim_dt.date()

            day_index = (sim_dt.date() - simulation_start_date).days
            if day_index < 0:
                time.sleep(1)
                continue

            if day_index >= 12:
                logger.info("Simulation range finished after 12 representative days.")
                break

            scenario_date = selected_dates_by_day_index[day_index]

            if last_day != sim_dt.date():
                last_day = sim_dt.date()

                if scenario_date in daily_gen_wh:
                    breakdown = calculate_daily_energy_breakdown(
                        daily_gen_wh[scenario_date], soc_wh
                    )
                    daily_availble_energy = breakdown["apps_wh"]
                    logger.info(
                        "Sending daily energy for simulated day %s using scenario %s",
                        last_day.isoformat(),
                        scenario_date.isoformat(),
                    )
                    send_to_time_proxy(
                        args.time_proxy_url,
                        "solar_energy_daily_wh",
                        daily_availble_energy,
                        args.device,
                    )
                    send_to_time_proxy(
                        args.time_proxy_url,
                        "solar_energy_daily_total_wh",
                        breakdown["total_wh"],
                        args.device,
                    )
                    send_to_time_proxy(
                        args.time_proxy_url,
                        "solar_energy_daily_static_wh",
                        breakdown["static_wh"],
                        args.device,
                    )
                    send_to_time_proxy(
                        args.time_proxy_url,
                        "solar_energy_daily_battery_wh",
                        breakdown["battery_wh"],
                        args.device,
                    )

            current_hour = sim_dt.replace(minute=0, second=0, microsecond=0)
            if last_hour != current_hour:
                logger.info("New hour: %s", current_hour.isoformat())
                scenario_hour = dt.datetime.combine(
                    scenario_date,
                    dt.time(hour=current_hour.hour),
                    tzinfo=LOCAL_TZ,
                )
                gen_wh = hourly_gen_wh.get(scenario_hour, 0.0)
                logger.info("Hourly generation: %.2f Wh", gen_wh)
                send_to_time_proxy(
                    args.time_proxy_url, "solar_energy_hourly_wh", gen_wh, args.device
                )
                # This if is to not start consuming energy before the experiment starts
                if last_hour != None:
                    consumed_wh = query_consumed_wh(
                        args.time_proxy_url,
                        args.wattmeter_device,
                        args.experiment_id,
                        args.consumption_window_seconds,
                    )
                    logger.info("Consumed energy: %.2f Wh", consumed_wh)
                else: 
                    consumed_wh = 0
                    logger.info("Consumed energy: %.2f Wh", consumed_wh)
                soc_wh = update_battery_soc(soc_wh, gen_wh, consumed_wh)
                logger.info("Battery SoC: %.2f Wh", soc_wh)
                send_to_time_proxy(
                    args.time_proxy_url, "battery_soc_wh", soc_wh, args.device
                )
                last_hour = current_hour
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Solar model stopped by user.")
    finally:
        logger.info("Running battery update block for one more hour before exit.")
        extension_end_time = time.time() + 3600
        extension_last_hour = last_hour

        while time.time() < extension_end_time:
            sim_ts = fetch_sim_time(args.time_proxy_url)
            sim_dt = dt.datetime.fromtimestamp(sim_ts, LOCAL_TZ)

            current_hour = sim_dt.replace(minute=0, second=0, microsecond=0)
            if extension_last_hour != current_hour:
                extension_last_hour = current_hour

                gen_wh = 0.0

                consumed_wh = query_consumed_wh(
                    args.time_proxy_url,
                    args.wattmeter_device,
                    args.experiment_id,
                    args.consumption_window_seconds,
                )
                logger.info("Consumed energy: %.2f Wh", consumed_wh)

                soc_wh = update_battery_soc(soc_wh, gen_wh, consumed_wh)
                logger.info("Battery SoC: %.2f Wh", soc_wh)
                send_to_time_proxy(
                    args.time_proxy_url, "battery_soc_wh", soc_wh, args.device
                )

            time.sleep(1)


if __name__ == "__main__":
    main()

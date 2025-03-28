import requests
import csv
import sys
import time
from datetime import datetime, timedelta

PROMETHEUS_URL = "http://131.254.100.29:30090"

QUERY_WINDOW = timedelta(minutes=5)

def query_prometheus(query, start_time, end_time, step="5s"):
    params = {
        "query": query,
        "start": start_time,
        "end": end_time,
        "step": step
    }
    try:
        response = requests.get(f"{PROMETHEUS_URL}/api/v1/query_range", params=params)
        response.raise_for_status()
        return response.json()["data"]["result"]
    except requests.RequestException as e:
        print(f"Error querying Prometheus: {e}")
        return []

def append_to_csv(cpu_usage_data, temp_data, filename, seen_timestamps):
    cpu_usage_dict = {}
    temp_dict = {}

    for result in cpu_usage_data:
        node = result["metric"].get("instance", "unknown")
        for timestamp, value in result["values"]:
            cpu_usage_dict[(int(float(timestamp)), node)] = float(value)

    for result in temp_data:
        node = result["metric"].get("instance", "unknown")
        for timestamp, value in result["values"]:
            temp_dict[(int(float(timestamp)), node)] = float(value)

    file_exists = os.path.isfile(filename)
    with open(filename, mode="a", newline="") as csvfile:
        fieldnames = ["unix_timestamp", "node", "cpu_usage_percent", "cpu_temp_celsius"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

        if not file_exists:
            writer.writeheader()

        all_timestamps = set(cpu_usage_dict.keys()).union(temp_dict.keys())
        for timestamp, node in sorted(all_timestamps):
            key = (timestamp, node)
            if key not in seen_timestamps:
                writer.writerow({
                    "unix_timestamp": timestamp,
                    "node": node,
                    "cpu_usage_percent": cpu_usage_dict.get(key, ""),
                    "cpu_temp_celsius": temp_dict.get(key, "")
                })
                seen_timestamps.add(key)

    return seen_timestamps

def main():
    if len(sys.argv) != 3:
        print("Usage: python prometheus_to_csv.py <output_csv_file> <one_minute>")
        sys.exit(1)

    output_file = sys.argv[1]
    one_minute = int(sys.argv[2])
    seen_timestamps = set()

    cpu_usage_query = 'rate(node_cpu_seconds_total{mode="user"}[5m]) * 100'
    temp_query = 'node_hwmon_temp_celsius'

    print(f"Starting infinite loop to append data to {output_file}")
    while True:
        end_time = datetime.now()
        start_time = end_time - QUERY_WINDOW

        print(f"[{end_time}] Querying CPU usage: {cpu_usage_query}")
        cpu_usage_data = query_prometheus(
            query=cpu_usage_query,
            start_time=start_time.timestamp(),
            end_time=end_time.timestamp(),
            step="5s"
        )

        print(f"[{end_time}] Querying CPU temperature: {temp_query}")
        temp_data = query_prometheus(
            query=temp_query,
            start_time=start_time.timestamp(),
            end_time=end_time.timestamp(),
            step="5s"
        )

        if cpu_usage_data or temp_data:
            print(f"[{end_time}] Appending data to {output_file}")
            seen_timestamps = append_to_csv(cpu_usage_data, temp_data, output_file, seen_timestamps)
        else:
            print(f"[{end_time}] No new data returned from Prometheus.")

        time.sleep(one_minute)

if __name__ == "__main__":
    import os
    main()

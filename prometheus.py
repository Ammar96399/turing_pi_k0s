import requests
import csv
import json
from datetime import datetime

PROMETHEUS_URL = "http://131.254.100.29:30090/api/v1/query"

QUERY = 'min_over_time((1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[15m]))) * 100 [15m:10s]) < 30'

OUTPUT_CSV = "cpu_usage_per_node.csv"

def fetch_prometheus_data(url, query):
    """
    Fetch CPU usage data per node from Prometheus API.
    """
    params = {"query": query}
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from Prometheus: {e}")
        return None

def save_to_csv(data, filename):
    """
    Save CPU usage data per node to a CSV file.
    """
    if not data or data.get("status") != "success":
        print("No valid data to save.")
        return

    with open(filename, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["timestamp", "node", "cpu_usage_percent"])

        for result in data["data"]["result"]:
            node = result["metric"].get("instance", "unknown")
            node_name = node.split(":")[0]
            value = float(result["value"][1])
            timestamp = datetime.fromtimestamp(float(result["value"][0])).isoformat()

            writer.writerow([timestamp, node_name, f"{value:.2f}"])

    print(f"CPU usage data per node saved to {filename}")

def main():
    data = fetch_prometheus_data(PROMETHEUS_URL,  QUERY)
    if data:
        print("Raw Prometheus CPU usage data per node:")
        print(json.dumps(data, indent=2))

        save_to_csv(data, OUTPUT_CSV)

if __name__ == "__main__":
    main()
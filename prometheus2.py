from asyncio import sleep
import requests
import json
from datetime import datetime

PROMETHEUS_URL = "http://131.254.100.29:30090/api/v1/query"

# New query to get the last 10 CPU usage values
QUERY = '((1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[75s]))) * 100)[75s:7s500ms]'

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

def print_last_10_values(data):
    """
    Print the last 10 CPU usage values per node.
    """
    if not data or data.get("status") != "success":
        print("No valid data to display.")
        return

    print("\nLast 10 CPU Usage Values per Node:")
    for result in data["data"]["result"]:
        node = result["metric"].get("instance", "unknown")
        node_name = node.split(":")[0]
        values = result["values"]  # Range vector returns a list of [timestamp, value] pairs
        print(f"\nNode: {node_name}")
        for timestamp, value in values:
            dt = datetime.fromtimestamp(float(timestamp)).isoformat()
            print(f"  {dt}: {float(value):.2f}%")
        
        # Verify the count
        print(f"  Total values: {len(values)}")

def main():
    while True:
        data = fetch_prometheus_data(PROMETHEUS_URL, QUERY)
        if data:
            print("Raw Prometheus CPU usage data (last 10 values per node):")
            print(json.dumps(data, indent=2))

            print_last_10_values(data)
        sleep(1)

if __name__ == "__main__":
    main()
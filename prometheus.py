from asyncio import sleep
import requests
import json
from datetime import datetime
from enum import Enum
import subprocess

PROMETHEUS_URL = "http://131.254.100.29:30090/api/v1/query"

QUERY = '((1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[75s]))) * 100)[75s:7s500ms]'

class NodeType(Enum):
    RK1 = "rk1"
    CM4 = "cm4"

class NodeRole(Enum):
    WORKER = "worker"
    MASTER = "master"

class NodeStatus(Enum):
    ON = "on"
    OFF = "off"

class Node:
    def __init__(self, ip, id, type, role, status):
        self.ip = ip
        self.id = id
        self.type = NodeType(type)
        self.role = NodeRole(role)
        self.status = NodeStatus(status)

    def __repr__(self):
        return (f"Node(ip={self.ip}, id={self.id}, "
                f"type={self.type.value}, role={self.role.value}, "
                f"status={self.status.value})")
    
def get_node_by_ip(ip):
    for node in STATE:
        if node.ip == ip:
            return node
    return None

STATE = [
    Node(
        ip="131.254.100.18",
        id=1,
        type=NodeType.RK1,
        role=NodeRole.WORKER,
        status=NodeStatus.ON
    ),
    Node(
        ip="131.254.100.24",
        id=2,
        type=NodeType.RK1,
        role=NodeRole.WORKER,
        status=NodeStatus.ON
    ),
    Node(
        ip="131.254.100.29",
        id=3,
        type=NodeType.CM4,
        role=NodeRole.MASTER,
        status=NodeStatus.ON
    )
]

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

def determine_action(values, ip):
    """
    Determine the action based on the last 10 CPU usage values.
    - All < 30%: turn_off
    - All > 80%: turn_on
    - Otherwise: nothing
    """
    if not values or len(values) < 10:
        return "nothing"

    cpu_values = [float(value[1]) for value in values]
    node = get_node_by_ip(ip)
    
    if all(v < 30 for v in cpu_values):
        turn_off_node(node)
    elif all(v > 80 for v in cpu_values):
        turn_on_node()
    
def turn_off_node(node):
    """
    Turn off the specified node.
    """
    try:
        if node.role != NodeRole.MASTER:
            node_id = node.id
            command = ["ssh", "root@131.254.100.14", "tpi", "power", "--node", str(node_id), "off"]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            node.status = NodeStatus.OFF
            print(f"Node {node_id} turned off successfully. Output: {result.stdout}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to turn off node {node_id}. Error: {e.stderr}")

def turn_on_node():
    """
    Turn on the specified node.
    """
    try:
        for node in STATE:
            if node.status == NodeStatus.OFF:
                node_id = node.id
                command = ["ssh", "root@131.254.100.14", "tpi", "power", "--node", str(node_id), "on"]
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                node.status = NodeStatus.ON
                print(f"Node {node_id} turned on successfully. Output: {result.stdout}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to turn on node {node_id}. Error: {e.stderr}")

def process_cpu_usage(data):
    """
    Process the data and return node IP and action.
    """
    if not data or data.get("status") != "success":
        print("No valid data to process.")
        return []

    actions = []
    for result in data["data"]["result"]:
        node = result["metric"].get("instance", "unknown")
        ip = node.split(":")[0] 
        values = result["values"] 
        
        determine_action(values, ip)

def sync_state():
    """
    Synchronize the state of the nodes with the actual state.
    """
    command = ["ssh", "root@131.254.100.14", "tpi", "power", "status"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        output = result.stdout.split("\n")
        for node in STATE:
            if f"node{node.id}: On" in output:
                node.status = NodeStatus.ON
            else:
                node.status = NodeStatus.OFF
    except subprocess.CalledProcessError as e:
        print(f"Failed to synchronize the state of the nodes. Error: {e.stderr}")

def main():
    sync_state()
    print("Initial state of the nodes:")
    for node in STATE:
        print(node)
    while True: 
        data = fetch_prometheus_data(PROMETHEUS_URL, QUERY)
        if data:
            # print("Raw Prometheus CPU usage data (last 10 values per node):")
            # print(json.dumps(data, indent=2))

            process_cpu_usage(data)
        sleep(1)

if __name__ == "__main__":
    main()
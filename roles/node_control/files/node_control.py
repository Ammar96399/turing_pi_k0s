import logging
from collections import deque
import sys
from time import sleep
import requests
from enum import Enum
import subprocess

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("node_control_log.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

PROMETHEUS_URL = "http://131.254.100.18:30090/api/v1/query"

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
    def __init__(self, ip, id, type, role, status, kube_name):
        self.ip = ip
        self.id = id
        self.type = NodeType(type)
        self.role = NodeRole(role)
        self.status = NodeStatus(status)
        self.kube_name = kube_name

    def __repr__(self):
        return (f"Node(ip={self.ip}, id={self.id}, "
                f"type={self.type.value}, role={self.role.value}, "
                f"status={self.status.value}, kube_name={self.kube_name})")
    
STATE = [
    Node(
        ip="131.254.100.18",
        id=1,
        type=NodeType.RK1,
        role=NodeRole.MASTER,
        status=NodeStatus.ON,
        kube_name="rk1-0",
    ),
    Node(
        ip="131.254.100.24",
        id=2,
        type=NodeType.RK1,
        role=NodeRole.WORKER,
        status=NodeStatus.ON,
        kube_name="rk1-1",
    ),
    Node(
        ip="131.254.100.29",
        id=3,
        type=NodeType.CM4,
        role=NodeRole.WORKER,
        status=NodeStatus.ON,
        kube_name="rpi2",
    )
]

def get_node_by_ip(ip):
    for node in STATE:
        if node.ip == ip:
            return node
    return None

def number_of_nodes_on():
    count = 0
    for node in STATE:
        if node.status == NodeStatus.ON:
            count += 1
    return count

def query_prometheus(node: Node, range_str):
    cpu_query = f'100 * (1 - avg(rate(node_cpu_seconds_total{{mode="idle", instance="{node.ip}:9100"}}[{range_str}s])))'
    cpu_url = f"{PROMETHEUS_URL}"
    
    cpu_usage = 0.0

    if node.status == NodeStatus.ON:
        try:
            response = requests.get(cpu_url, params={"query": cpu_query}, timeout=10)
            response.raise_for_status()
            data = response.json()
            logger.info(f"Querying CPU usage for node {node.ip}")
            
            if data['status'] == 'success' and 'data' in data and 'result' in data['data']:
                if data['data']['result']:
                    value = data['data']['result'][0]['value'][1]
                    cpu_usage = float(value) if value != "NaN" else 0.0
                    logger.info(f"CPU usage for node {node.ip}: {cpu_usage:.2f}%")

        except requests.exceptions.RequestException as e:
            logger.error(f"Error querying CPU usage for instance {node}: {e}")
    
    return cpu_usage

def determine_action(history, window_size, min_threshold, max_threshold):
    action_taken = False

    newest_values_sum = sum(values[-1] for values in history.values() if values)
    logger.info(f"Newest values sum: {newest_values_sum}")

    for ip, values in history.items():
        node = get_node_by_ip(ip)
        if node.status == NodeStatus.OFF:
            logger.info(f"Node {ip} is off. Skipping action.")
            continue

        if action_taken:
            logger.info(f"Action already taken for node {ip}. Skipping.")
            continue

        if len(values) < window_size:
            continue

        if all(v < min_threshold for v in values):
            if newest_values_sum * 1.1 < (number_of_nodes_on() * 100): 
                action_taken = turn_off_node(ip)
                if action_taken:
                    clear_history(history)
        elif all(v > max_threshold for v in values):
            action_taken = turn_on_node()
            if action_taken:
                clear_history(history)
        else:   
            logger.info(f"Node {ip} is in a normal state. No action required.")

def turn_off_node(ip) -> bool:
    node = get_node_by_ip(ip)
    try:
        if node.role != NodeRole.MASTER:
            evict_node(ip)
            # command = ["sudo", "ssh", "ubuntu@", ip, "sudo", "shutdown", "now"]
            # process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            # stdout, stderr = process.communicate()
            # if process.returncode == 0:
            #     logger.info(f"Node {node_id} turned off successfully. Output: {stdout}")
            # else:
            #     logger.error(f"Failed to turn off node {node_id}. Error: {stderr}")
            node_id = node.id
            command = ["ssh", "root@131.254.100.14", "tpi", "power", "--node", str(node_id), "off"]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = process.communicate()
            if process.returncode == 0:
                node.status = NodeStatus.OFF
                logger.info(f"Node {node_id} turned off successfully. Output: {stdout}")
            else:
                logger.error(f"Failed to turn off node {node_id}. Error: {stderr}")
            return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to turn off node {node_id}. Error: {e.stderr}")

    return False

def turn_on_node() -> bool:
    try:
        for node in STATE:
            if node.status == NodeStatus.OFF:
                node_id = node.id
                command = ["ssh", "root@131.254.100.14", "tpi", "power", "--node", str(node_id), "on"]
                result = subprocess.call(command, text=True)
                # Wait for the node to join the k0s cluster
                master_node = get_master_node()
                if master_node:
                    kube_name = node.kube_name
                    join_command = ["sudo", "k0s", "kubectl", "get", "nodes", kube_name, "-o", "jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}'"]
                    for _ in range(60):
                        try:
                            result = subprocess.run(join_command, text=True, capture_output=True)
                            if result.stdout.strip() == "'True'":
                                logger.info(f"Node {node_id} successfully joined the k0s cluster.")
                                break
                        except subprocess.CalledProcessError as e:
                            logger.error(f"Error checking node {node_id} join status: {e.stderr}")
                        sleep(1)
                    else:
                        logger.warning(f"Node {node_id} did not join the k0s cluster within the timeout period.")
                uncordon_command = ["sudo", "k0s", "kubectl", "uncordon", kube_name]
                subprocess.run(uncordon_command, text=True)
                subprocess.run(["sudo", "k0s", "kubectl", "create", "job", "--from=cronjob/descheduler", "descheduler-node-control", "-n", "kube-system"], text=True)
                node.status = NodeStatus.ON
                logger.info(f"Node {node_id} turned on successfully. Output: {result}")
                return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to turn on node {node_id}. Error: {e.stderr}")
    return False

def sync_state():
    command = ["ssh", "root@131.254.100.14", "tpi", "power", "status"]
    try:
        result = subprocess.run(command, text=True, capture_output=True)
        output = result.stdout.split("\n")
        for node in STATE:
            if f"node{node.id}: On" in output:
                node.status = NodeStatus.ON
            else:
                node.status = NodeStatus.OFF
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to synchronize the state of the nodes. Error: {e.stderr}")

def clear_history(history):
    for ip in history:
        history[ip].clear()
        logger.info(f"Cleared history for node {ip}.")
    logger.info("History cleared.")

def get_master_node() -> Node:
    for node in STATE:
        if node.role == NodeRole.MASTER:
            return node
    return None

def evict_node(ip) -> bool: 
    node = get_node_by_ip(ip)
    
    if node:
        command = ["sudo", "k0s", "kubectl", "drain", 
                   node.kube_name,
                   "--ignore-daemonsets", 
                   "--force", 
                   "--delete-emptydir-data"]
        
        logger.info(f"Evicting node {node.ip} with ID {node.id}, with command: {command}")

        try:
            result = subprocess.run(command, text=True, capture_output=True)
            if result.returncode == 0:
                logger.info(f"Command executed successfully with result code: {result.returncode}")
                return True
            else:
                logger.warning(f"Command executed with result code: {result.returncode}")
                logger.warning(f"stdout: {result.stdout}")
                logger.warning(f"stderr: {result.stderr}")
                return False
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to evict node {node.ip}. Error: {e.stderr}")
    return False

def main():
    if len(sys.argv) != 5:
        logger.error("Usage: python script.py <range> <window_size> <min_threshold> <max_threshold>")
        logger.error("Example: python script.py 20 10 30 80")
        sys.exit(1)
    
    range = int(sys.argv[1])
    window_size = int(sys.argv[2])
    min_threshold = int(sys.argv[3])
    max_threshold = int(sys.argv[4])
    
    logger.info(f"Starting node controller with range: {range}.")

    sync_state()
    logger.info(f"Initial state of the nodes: {STATE}")

    usage_history = {node.ip: deque(maxlen=window_size) for node in STATE}

    while True: 
        for node in STATE:
            cpu_usage = query_prometheus(node, range)
            if cpu_usage:
                usage_history[node.ip].append(cpu_usage)
                logger. info(f"Last {window_size} values: {list(usage_history[node.ip])}")
                
        determine_action(usage_history, window_size, min_threshold, max_threshold)

        sleep(range)

if __name__ == "__main__":
    main()

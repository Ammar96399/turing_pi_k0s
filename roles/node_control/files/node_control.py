import csv
import logging
from collections import deque
import os
import sys
from time import sleep
import time
import requests
from enum import Enum
import subprocess

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

class HistoryState(Enum):
    NORMAL = "normal"
    UNBALANCED = "unbalanced"
    OVERLOADED = "overloaded"
    UNDERLOADED = "underloaded"

class Node:
    def __init__(self, ip, id, type, role, status, kube_name, window_size):
        self.ip = ip
        self.id = id
        self.type = NodeType(type)
        self.role = NodeRole(role)
        self.status = NodeStatus(status)
        self.kube_name = kube_name
        self.history = deque(maxlen=window_size) 
        self.history_state = HistoryState.NORMAL

    def __repr__(self):
        return (f"Node(ip={self.ip}, id={self.id}, "
                f"type={self.type.value}, role={self.role.value}, "
                f"status={self.status.value}, kube_name={self.kube_name})")
    
    def add_value_to_history(self, value):
        self.history.append(value)
        logger.info(f"Added value {value} to history of node {self.id}. Current history: {list(self.history)}")

    def update_history_state(self, min_threshold, max_threshold):
        if self.status == NodeStatus.OFF:
            self.history_state = HistoryState.NORMAL
            return
        
        if self.history.maxlen is not None and len(self.history) < self.history.maxlen:
            self.history_state = HistoryState.NORMAL
            return
        
        if all(v < min_threshold for v in self.history):
            self.history_state = HistoryState.UNDERLOADED
        elif all(v > max_threshold for v in self.history):
            self.history_state = HistoryState.OVERLOADED
        else:
            self.history_state = HistoryState.NORMAL
    
    def turn_on(self):
        command = ["ssh", "root@131.254.100.14", "tpi", "power", "--node", str(self.id), "on"]
        result = subprocess.call(command, text=True)
        if result == 0:
            self.status = NodeStatus.ON
            logger.info(f"Node {self.id} turned on successfully. Current status: {self.status}")
        else:
            logger.error(f"Failed to turn on node {self.id}. Error code: {result}")
            return False
    
    def turn_off(self):
        command = ["ssh", "root@131.254.100.14", "tpi", "power", "--node", str(self.id), "off"]
        result = subprocess.call(command, text=True)
        if result == 0:
            self.status = NodeStatus.OFF
            logger.info(f"Node {self.id} turned off successfully. Current status: {self.status}")
        else:
            logger.error(f"Failed to turn off node {self.id}. Error code: {result}")

    def evict(self):
        command = ["sudo", "k0s", "kubectl", "drain", 
                   self.kube_name,
                   "--ignore-daemonsets", 
                   "--force", 
                   "--delete-emptydir-data"]
        
        logger.info(f"Evicting node {self.id}, with command: {command}")

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
            logger.error(f"Failed to evict node {self.id}. Error: {e.stderr}")

    def uncordon_node(self):
        uncordon_command = ["sudo", "k0s", "kubectl", "uncordon", self.kube_name]
        subprocess.run(uncordon_command, text=True)

    def clear_history(self):
        self.history.clear()
        logger.info(f"Cleared history of node {self.id}. Current history: {list(self.history)}")
        self.history_state = HistoryState.NORMAL
    
    def wait_for_join(self):
        for _ in range(60):
            try:
                join_command = ["sudo", "k0s", "kubectl", "get", "nodes", self.kube_name, "-o", "jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}'"]

                result = subprocess.run(join_command, text=True, capture_output=True)
                if result.stdout.strip() == "'True'":
                    logger.info(f"Node {self.id} successfully joined the k0s cluster.")
                    break
            except subprocess.CalledProcessError as e:
                logger.error(f"Error checking node {self.id} join status: {e.stderr}")
                sleep(1)
        else:
            logger.warning(f"Node {self.id} did not join the k0s cluster within the timeout period.")

        
class Cluster:  
    def __init__(self, window_size=10):
        self.nodes = [
            Node(
                ip="131.254.100.18",
                id=1,
                type=NodeType.RK1,
                role=NodeRole.MASTER,
                status=NodeStatus.ON,
                kube_name="rk1-0",
                window_size=window_size
            ),
            Node(
                ip="131.254.100.24",
                id=2,
                type=NodeType.RK1,
                role=NodeRole.WORKER,
                status=NodeStatus.ON,
                kube_name="rk1-1",
                window_size=window_size
            ),Node(
                ip="131.254.100.29",
                id=3,
                type=NodeType.CM4,
                role=NodeRole.WORKER,
                status=NodeStatus.ON,
                kube_name="rpi2",
                window_size=window_size
            ),
        ]
        self.history_state = HistoryState.NORMAL

    def get_node_by_ip(self, ip):
        for node in self.nodes:
            if node.ip == ip:
                return node
        raise ValueError(f"No node found with IP: {ip}")
    
    def get_worker_node_by_type(self, type):
        for node in self.nodes:
            if node.type == type and node.role == NodeRole.WORKER:
                return node
        raise ValueError(f"No worker node found with type: {type}")
    
    def get_number_of_nodes_on(self):
        count = 0
        for node in self.nodes:
            if node.status == NodeStatus.ON:
                count += 1
        return count
    
    def get_master_node(self):
        for node in self.nodes:
            if node.role == NodeRole.MASTER:
                return node
        raise ValueError("No master node found in the cluster.")
    
    def update_history_state(self):
        node_history_states = [node.history_state for node in self.nodes]
        if all(state == HistoryState.NORMAL for state in node_history_states):
            self.history_state = HistoryState.NORMAL
        elif any(state == HistoryState.UNDERLOADED for state in node_history_states) and not any(state == HistoryState.OVERLOADED for state in node_history_states):
            self.history_state = HistoryState.UNDERLOADED
        elif any(state == HistoryState.OVERLOADED for state in node_history_states) and not any(state == HistoryState.UNDERLOADED for state in node_history_states):
            self.history_state = HistoryState.OVERLOADED
        else:
            self.history_state = HistoryState.UNBALANCED

    def all_nodes_on(self):
        for node in self.nodes:
            if node.status == NodeStatus.OFF:
                return False
        return True
    
    def all_nodes_off(self):
        for node in self.nodes:
            if node.role != NodeRole.MASTER and node.status == NodeStatus.ON:
                return False
        return True
    
    def clear_history(self):
        for node in self.nodes:
            node.clear_history()
        self.history_state = HistoryState.NORMAL
        logger.info("Cleared history of all nodes.")
    
    def trigger_descheduler(self):
        subprocess.run([
            "sudo", "k0s", "kubectl", "create", "job", 
            "--from=cronjob/descheduler", 
            f"descheduler-node-control-{int(time.time())}",  # unique job name
            "-n", "kube-system"
        ], text=True)
    
    def scale_up(self):
        smallest_node =  self.get_worker_node_by_type(NodeType.CM4)
        largest_node = self.get_worker_node_by_type(NodeType.RK1)

        logger.info("Scaling up the cluster.")
        if self.all_nodes_on():
            logger.info("All nodes are already on. No scaling up required.")
            return
        
        if largest_node.status == NodeStatus.OFF:
            largest_node.turn_on()
            largest_node.wait_for_join()
            largest_node.uncordon_node()
            self.trigger_descheduler()
            self.clear_history()
            return
        
        if smallest_node.status == NodeStatus.OFF:
            smallest_node.turn_on()
            smallest_node.wait_for_join()
            smallest_node.uncordon_node()
            self.trigger_descheduler()
            self.clear_history()
            return
    
    def scale_down(self):
        smallest_node =  self.get_worker_node_by_type(NodeType.CM4)
        largest_node = self.get_worker_node_by_type(NodeType.RK1)

        logger.info("Scaling down the cluster.")
        if self.all_nodes_off():
            logger.info("All nodes are already off. No scaling down required.")
            return
        if smallest_node.status == NodeStatus.ON:
            smallest_node.evict()
            smallest_node.turn_off()
            self.clear_history()
            return
        if largest_node.status == NodeStatus.ON:
            largest_node.evict()
            largest_node.turn_off()
            self.clear_history()
            return
        
    def log_cluster_state_to_csv(self):
        csv_file = "cluster_state_log.csv"
        fieldnames = ["timestamp", "ip", "id", "type", "role", "status", "kube_name"]

        try:
            write_header = not os.path.exists(csv_file)

            with open(csv_file, mode="a", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=fieldnames)

                if write_header:
                    writer.writeheader()

                timestamp = time.time()
                for node in self.nodes:
                    writer.writerow({
                        "timestamp": timestamp,
                        "ip": node.ip,
                        "id": node.id,
                        "type": node.type.value,
                        "role": node.role.value,
                        "status": node.status.value,
                        "kube_name": node.kube_name
                    })
            logger.info("Cluster state logged to CSV file.")
        except Exception as e:
            logger.error(f"Failed to log cluster state to CSV. Error: {e}")

    def __repr__(self):
        return f"ClusterState(nodes={self.nodes})"


def query_prometheus(node: Node, range_str):
    try:
        step = int(range_str)
    except ValueError:
        logger.error(f"Invalid range_str: {range_str}")
        return 0.0

    query = f'100 * (1 - avg(rate(node_cpu_seconds_total{{mode="idle", instance="{node.ip}:9100"}}[{range_str * 2}s])))'
    params = {
        "query": query
    }
    cpu_url = PROMETHEUS_URL

    cpu_usage = 0.0

    if node.status == NodeStatus.ON:
        try:
            response = requests.get(cpu_url, params=params)
            response.raise_for_status()
            data = response.json()
            logger.info(f"Querying CPU usage for node {node.id}")

            if data['status'] == 'success' and 'data' in data and 'result' in data['data']:
                if data['data']['result']:
                    value = data['data']['result'][0]['value'][1]
                    cpu_usage = float(value) if value != "NaN" else 0.0
                    logger.info(f"CPU usage for node {node.id}: {cpu_usage:.2f}%")
        except requests.exceptions.RequestException as e:
            logger.error(f"Error querying CPU usage for instance {node}: {e}")

    return cpu_usage


def sync_state(cluster):
    command = ["ssh", "root@131.254.100.14", "tpi", "power", "status"]
    try:
        result = subprocess.run(command, text=True, capture_output=True)
        output = result.stdout.split("\n")
        for node in cluster.nodes:
            if f"node{node.id}: On" in output:
                node.status = NodeStatus.ON
            else:
                node.status = NodeStatus.OFF
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to synchronize the state of the nodes. Error: {e.stderr}")


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

    cluster = Cluster(window_size)

    sync_state(cluster)

    logger.info(f"Initial state of the nodes: {cluster.nodes}")

    while True: 

        prometheus_values = {}
        newest_sum = 0.0
        nb_nodes_on = cluster.get_number_of_nodes_on()

        for node in cluster.nodes:
            cpu_usage = query_prometheus(node, range)
            if cpu_usage:
                prometheus_values[node.id] = cpu_usage

        if len(prometheus_values) == nb_nodes_on:
            for node in cluster.nodes:
                if node.status == NodeStatus.ON:
                    node.add_value_to_history(prometheus_values[node.id])      
                    node.update_history_state(min_threshold, max_threshold)
                    newest_sum += prometheus_values[node.id]

        cluster.update_history_state()

        if cluster.history_state == HistoryState.UNBALANCED:
            logger.info("Cluster is unbalanced. Taking action.")
            cluster.trigger_descheduler()
        
        elif cluster.history_state == HistoryState.UNDERLOADED and (newest_sum * 1.1 < (nb_nodes_on - 1) * 100):
            logger.info("Cluster is underloaded. Taking action.")
            cluster.scale_down()

        elif cluster.history_state == HistoryState.OVERLOADED:
            logger.info("Cluster is overloaded. Taking action.")
            cluster.scale_up()

        elif cluster.history_state == HistoryState.NORMAL:
            logger.info("Cluster is in a normal state. No action required.")

        sleep(range)

if __name__ == "__main__":
    main()

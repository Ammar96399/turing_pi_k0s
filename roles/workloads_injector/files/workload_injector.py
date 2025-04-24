import csv
import time
import pika
import json
from pathlib import Path
import sys
import glob

# Command-line arguments
OBSERVATORY = str(sys.argv[1]) if len(sys.argv) > 1 else "event_driven_observatory"
WAIT_TIME = int(sys.argv[2]) if len(sys.argv) > 2 else 60
BROKER = "131.254.19.109"
PORT = 5672
BASE_PATH = f"/home/akazem/Documents/PhD/projects/energy_proportionality/cluster_configuration/turing_pi_emulation/roles/workloads_injector/files/{OBSERVATORY}"
EXCHANGE = "event_driven_observatory"

# RabbitMQ Connection Setup
credentials = pika.PlainCredentials("guest", "guest") 
parameters = pika.ConnectionParameters(BROKER, PORT, "/", credentials)
connection = pika.BlockingConnection(parameters)
channel = connection.channel()

# Declare the direct exchange (create if it doesn’t exist)
channel.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)

def publish_cpu_load(csv_file, folder_name, minute):
    with open(csv_file, "r") as file:
        reader = list(csv.DictReader(file))
        if minute >= len(reader):
            print(f"Error: Minute {minute} exceeds the number of rows in {csv_file}")
            return

        cm4_cpu = float(reader[minute]["cm4"]) * 100
        rk1_cpu = float(reader[minute]["rk1"]) * 100

        payload = json.dumps({"cm4": cm4_cpu, "rk1": rk1_cpu, "timestamp": time.time()}).encode()

        # Routing key is the folder name (e.g., "workload_1")
        routing_key = folder_name

        # Publish to the exchange with the routing key
        channel.basic_publish(
            exchange=EXCHANGE,
            routing_key=routing_key,
            body=payload,
            properties=pika.BasicProperties(
                delivery_mode=2 
            )
        )

        print(f"Published: {EXCHANGE}/{routing_key} -> cm4: {cm4_cpu}%, rk1: {rk1_cpu}%")

def process_workload_folders():
    for minute in range(1, 1443):  # 1440 minutes = 1 day + header row + finish message
        if minute == 1442:
            print("Sensing a finish message...")
            for i in range(1, 57):  # workload_1 to workload_56
                folder_name = f"workload_{i}"
                folder_path = Path(BASE_PATH) / folder_name / "turing_pi"
                payload = json.dumps({"cm4": -1, "rk1": -1, "timestamp": time.time()}).encode()
                channel.basic_publish(
                    exchange=EXCHANGE,
                    routing_key=folder_name,
                    body=payload,
                    properties=pika.BasicProperties(
                        delivery_mode=2  
                    )
                )
        else: 
            for i in range(1, 57):  # workload_1 to workload_56
                folder_name = f"workload_{i}"
                folder_path = Path(BASE_PATH) / folder_name / "turing_pi"

                csv_files = glob.glob(str(folder_path / "*.csv"))
                if not csv_files:
                    print(f"Warning: No CSV found in {folder_path}")
                    continue

                csv_file = csv_files[0]
                print(f"Processing {csv_file} (Folder: {folder_name})")
                publish_cpu_load(csv_file, folder_name, minute)

            time.sleep(WAIT_TIME)

    connection.close()

    print("Finished publishing all workload folders.")

def log_unix_time(file_path):
    log_file_path = Path(file_path)
    log_file_path.parent.mkdir(parents=True, exist_ok=True)  # Create parent directories if they don't exist
    with open(log_file_path, "a") as log_file:  # Open in append mode to create the file if it doesn't exist
        log_file.write(f"{time.time()}\n")

if __name__ == "__main__":
    print(f"Base path: {BASE_PATH}")
    print(f"Exchange: {EXCHANGE}")
    print(f"Waiting time between lines: {WAIT_TIME} seconds")
    try:
        process_workload_folders()
        log_unix_time("/home/akazem/Documents/PhD/projects/energy_proportionality/results/turing_pi_emulation/with_overload/event_driven/test_5/experiment_end_time.txt")
    except KeyboardInterrupt:
        print("Stopped by user.")
        connection.close()
    except ValueError:
        print("Error: Waiting time must be an integer (e.g., 60)")
        connection.close()
    except Exception as e:
        print(f"Unexpected error: {e}")
        connection.close()


        
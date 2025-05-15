import csv
import time
import pika
import json
from pathlib import Path
import sys
import glob

OBSERVATORY = str(sys.argv[1]) if len(sys.argv) > 1 else "event_driven_observatory"
WAIT_TIME = int(sys.argv[2]) if len(sys.argv) > 2 else 60
BROKER = "131.254.19.109"
PORT = 5672
BASE_PATH = f"/home/akazem/Documents/PhD/projects/energy_proportionality/cluster_configuration/turing_pi_emulation/roles/workloads_injector/files/{OBSERVATORY}"
EXCHANGE = "event_driven_observatory"

credentials = pika.PlainCredentials("guest", "guest") 
parameters = pika.ConnectionParameters(BROKER, PORT, "/", credentials)
connection = pika.BlockingConnection(parameters)
channel = connection.channel()

channel.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)

def publish_cpu_loads(results, minute):
    for i in range(1, 57): 
        folder_name = f"workload_{i}"
        payload = {"events": results[i-1][minute-1], "timestamp": time.time()}
        payload = json.dumps(payload).encode()
        routing_key = folder_name
        channel.basic_publish(
            exchange=EXCHANGE,
            routing_key=routing_key,
            body=payload,
            properties=pika.BasicProperties(
                delivery_mode=2  
            )
        )
        print(f"Published: {EXCHANGE}/{routing_key} -> events: {results[i-1][minute-1]}")


def read_events_from_csv_files(folder_path):
    result = []
    for i in range(1, 57): 
        workload_result = []
        folder_name = f"workload_{i}"
        folder_path = Path(BASE_PATH) / folder_name / "turing_pi"

        csv_files = glob.glob(str(folder_path / "*.csv"))
        if not csv_files:
            print(f"Warning: No CSV found in {folder_path}")
            continue
        
        reader = list(csv.DictReader(open(csv_files[0], "r")))
        for row in reader:
            events = round(float(row["cm4"]) * 86)
            workload_result.append(events)
        result.append(workload_result)
    return result


def process_workload_folders():
    results = read_events_from_csv_files(BASE_PATH)
    for minute in range(1, 1442):
        if minute == 1441:
            print("Sensing a finish message...")
            for i in range(1, 57):
                folder_name = f"workload_{i}"
                payload = json.dumps({"events": -1, "timestamp": time.time()}).encode()
                channel.basic_publish(
                    exchange=EXCHANGE,
                    routing_key=folder_name,
                    body=payload,
                    properties=pika.BasicProperties(
                        delivery_mode=2  
                    )
                )
        else: 
            publish_cpu_loads(results, minute)
            time.sleep(WAIT_TIME)

    connection.close()

    print("Finished publishing all workload folders.")


def check_queue_message_count():
    queue = channel.queue_declare(queue="information", passive=True)
    queue_count = queue.method.message_count

    if queue_count == 56:
        print("There are exactly 56 messages in the 'information' queue.")
        return True
    else:
        print(f"Message count mismatch: {queue_count} messages found in the 'information' queue.")
        return False


def log_unix_time(file_path):
    log_file_path = Path(file_path)
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file_path, "a") as log_file:
        log_file.write(f"{round(time.time())}\n")

if __name__ == "__main__":
    print(f"Base path: {BASE_PATH}")
    print(f"Exchange: {EXCHANGE}")
    print(f"Waiting time between lines: {WAIT_TIME} seconds")
    try:
        process_workload_folders()
        while not check_queue_message_count():
            time.sleep(1)
        log_unix_time("/home/akazem/Documents/PhD/projects/energy_proportionality/results/turing_pi_emulation/with_overload/long_term/gold/test_6/experiment_end_time.txt")
    except KeyboardInterrupt:
        print("Process interrupted by user.")
        connection.close()
    except Exception as e:
        print(f"An error occurred: {e}")
        connection.close()

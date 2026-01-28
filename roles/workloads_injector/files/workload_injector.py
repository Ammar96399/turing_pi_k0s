import csv
import time
import pika
import json
from pathlib import Path
import sys
import glob
import os

OBSERVATORY = str(sys.argv[1]) if len(sys.argv) > 1 else "event_driven_observatory"
WAIT_TIME = int(sys.argv[2]) if len(sys.argv) > 2 else 60
BROKER = "131.254.19.109"
PORT = 5672
BASE_PATH = f"/home/akazem/Documents/PhD/projects/energy_proportionality/cluster_configuration/turing_pi_emulation/roles/workloads_injector/files/{OBSERVATORY}/applications"
EXCHANGE = "event_driven_observatory"

credentials = pika.PlainCredentials("guest", "guest") 
parameters = pika.ConnectionParameters(BROKER, PORT, "/", credentials)
connection = pika.BlockingConnection(parameters)
channel = connection.channel()

channel.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)

def publish_cpu_loads(results_dict, minute):
    for folder_name, event_list in results_dict.items():
        try:
            events_for_minute = event_list[minute - 1] 
    
            payload = {
                "events": events_for_minute, 
                "timestamp": time.time()
            }
            payload = json.dumps(payload).encode()
            routing_key = folder_name
            channel.basic_publish(
                exchange=EXCHANGE,
                routing_key=routing_key,
                body=payload,
                # properties=pika.BasicProperties(
                #     delivery_mode=2  
                # )
            )
            print(f"Published: {EXCHANGE}/{routing_key} -> events: {events_for_minute}")
        except IndexError:
            print(f"Warning: No event data available for minute {minute} in folder {folder_name}. Skipping publication.")
        except Exception as e:
            print(f"Error publishing for folder {folder_name}: {e}")


def read_events_from_csv_files(base_folder_path):
    results_dict = {}
    base_path = Path(base_folder_path)
    for item in os.listdir(base_path):
        folder_path = base_path / item
        if folder_path.is_dir():
            folder_name = item
            csv_files = glob.glob(str(folder_path / "*.csv"))
            workload_result = []
            try:
                with open(csv_files[0], "r") as file:
                    reader = csv.DictReader(file)
                    for row in reader:
                        try:
                            events = round(float(row["cpu_rate"]) * 110) 
                            workload_result.append(events)
                        except (ValueError, KeyError) as e:
                            print(f"Error processing row in {csv_files[0]}: {e}. Skipping row.")
                            continue
                results_dict[folder_name] = workload_result
            except Exception as e:
                print(f"Error reading file {csv_files[0]}: {e}")
                continue 
    return results_dict


def process_workload_folders():
    results = read_events_from_csv_files(BASE_PATH)
    base_path = Path(BASE_PATH)
    for minute in range(1, 1442): # TODO: Do not forget to change this to 1
        if minute == 1441:
            print("Sensing a finish message...")
            for item in os.listdir(base_path):
                folder_path = base_path / item
                if folder_path.is_dir():
                    folder_name = item
                else: 
                    return 0
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

    print("Finished publishing all workload folders.")


def check_queue_message_count():
    queue = channel.queue_declare(queue="information", passive=True)
    queue_count = queue.method.message_count

    if queue_count >= 48:
        print("There are exactly 48 messages in the 'information' queue.")
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
        connection.close()
        log_unix_time("/home/akazem/Documents/PhD/projects/energy_proportionality/results/turing_pi_emulation/new_workload/long_term/green/test_1/experiment_end_time.txt")
    except KeyboardInterrupt:
        print("Process interrupted by user.")
        connection.close()
    # except Exception as e:
    #     print(f"An error occurred: {e}")
    #     connection.close()

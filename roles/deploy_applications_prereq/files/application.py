import os
import subprocess
import sys
import time
import pika
import json
from kubernetes import client, config

BROKER = "131.254.19.109"
PORT = 5672 
EXCHANGE = "event_driven_observatory"
QUEUE_NAME = f"workload_{sys.argv[1]}"
ONE_MINUTE = int(sys.argv[2])

def run_stress_ng(value, one_minute):
    command = f"stress-ng --cpu 1 --cpu-method union -t {one_minute}s -l {value}"
    print(f"Running command: {command}")
    process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        print(f"Error running stress-ng: {stderr.decode('utf-8')}")
    else:
        print(stdout.decode('utf-8'))
        
def get_node_model():
    config.load_incluster_config()
    v1 = client.CoreV1Api()
    node_name = os.environ.get("NODE_NAME")
    if not node_name:
        print("NODE_NAME environment variable not set")
        sys.exit(1)
    node = v1.read_node(name=node_name)
    model = node.metadata.labels.get("model")
    
    if not model:
        print(f"No 'model' label found on node {node_name}")
        sys.exit(1)
    return model

def treat_outdated_messages():
    while True:
        print("Checking for outdated messages...")
        total = 0
        messages_found = 0
        while True: 
            method, _, body = channel.basic_get(queue=QUEUE_NAME, auto_ack=False)
            if method is None:
                break
            try:
                payload = body.decode()
                data = json.loads(payload)
                total += data.get(get_node_model())
                messages_found += 1
                channel.basic_ack(delivery_tag=method.delivery_tag)
            except ValueError:
                print(f"Received invalid JSON: {payload}")

        processing_time = total / 100.0 * ONE_MINUTE
        
        if messages_found > 0:
            run_stress_ng(100, processing_time)

        else:
            print("No outdated messages found.")
            return

def treat_messages():
    def on_message(ch, method, properties, body):
        payload = body.decode()
        timestamp = int(time.time())
        try:
            data = json.loads(payload)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            run_stress_ng(int(data.get(get_node_model())), ONE_MINUTE)
        except json.JSONDecodeError:
            print(f"Received invalid JSON: {payload}")

    channel.basic_qos(prefetch_count=1) 
    channel.basic_consume(queue=QUEUE_NAME, on_message_callback=on_message)
    channel.start_consuming()

if __name__ == "__main__": 
    if len(sys.argv) != 3:
        print("Usage: python sysbench_from_csv.py <workload_number> <one_minute>")
        sys.exit(1)

    credentials = pika.PlainCredentials("guest", "guest") 
    parameters = pika.ConnectionParameters(BROKER, PORT, "/", credentials)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    channel.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)

    channel.queue_declare(queue=QUEUE_NAME, durable=True)

    channel.queue_bind(exchange=EXCHANGE, queue=QUEUE_NAME, routing_key=QUEUE_NAME)

    total = treat_outdated_messages()

    print(f"Connected to {BROKER}:{PORT}")
    print("Waiting for messages... (Press CTRL+C to exit)")
    try:
        treat_messages()
    except KeyboardInterrupt:
        print("Stopped by user.")
        channel.stop_consuming()
    finally:
        connection.close()
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

TOTAL_OUTDATED_MESSAGES = 0


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
        finish_message = False

        while True: 
            method, _, body = channel.basic_get(queue=QUEUE_NAME, auto_ack=False)
            if method is None:
                break
            try:
                payload = body.decode()
                data = json.loads(payload)
                if data.get(get_node_model()) == -1:
                    print("Finish message received in outdated messages. Ignoring.")
                    finish_message = True
                else: 
                    total += data.get(get_node_model())
                    messages_found += 1
                    log_delay(data.get("timestamp"))
                channel.basic_ack(delivery_tag=method.delivery_tag)
            except ValueError:
                print(f"Received invalid JSON: {payload}")

        processing_time = total / 100.0 * ONE_MINUTE

        if round(processing_time) == 0:
            processing_time = 1
        
        if messages_found > 0:
            print(f"Total: {total}, Processing time: {processing_time} seconds")
            run_stress_ng(100, round(processing_time))
            time.sleep(processing_time)
            return False
            
        if finish_message:
            time.sleep(processing_time)
            treat_finish_message()
            return True
    
        else:
            print("No outdated messages found.")
            return False


def log_delay(timestamp):
    channel.queue_declare(queue=f"{QUEUE_NAME}_delay", durable=True)
    channel.queue_bind(exchange=EXCHANGE, queue=f"{QUEUE_NAME}_delay", routing_key=f"{QUEUE_NAME}_delay")
    delay = time.time() - timestamp
    payload = json.dumps({"delay": delay}).encode()
    channel.basic_publish(
        exchange=EXCHANGE,
        routing_key=f"{QUEUE_NAME}_delay",
        body=payload,
        properties=pika.BasicProperties(
            delivery_mode=2  
        )
    )
    print(f"Delay logged: {delay} seconds")



def treat_messages():
    def on_message(ch, method, properties, body):
        payload = body.decode()
        try:
            data = json.loads(payload)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            if data.get(get_node_model()) == -1:
                print("Finish message received. Stopping.")
                ch.stop_consuming()
                return
            log_delay(data.get("timestamp"))
            run_stress_ng(int(data.get(get_node_model())), ONE_MINUTE)
        except json.JSONDecodeError:
            print(f"Received invalid JSON: {payload}")

    channel.basic_qos(prefetch_count=1) 
    channel.basic_consume(queue=QUEUE_NAME, on_message_callback=on_message)
    channel.start_consuming()


def treat_finish_message():
    channel.queue_declare(queue="information", durable=True)
    channel.queue_bind(exchange=EXCHANGE, queue="information", routing_key="information")
    payload = json.dumps({"app": QUEUE_NAME, "total": TOTAL_OUTDATED_MESSAGES, "timestamp": time.time()}).encode()
    channel.basic_publish(
        exchange=EXCHANGE,
        routing_key="information",
        body=payload,
        properties=pika.BasicProperties(
            delivery_mode=2  
        )
    )


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

    final_message = treat_outdated_messages()

    print(f"Connected to {BROKER}:{PORT}")
    try:
        if final_message:
            print("Finish message received. Stopping.")
            channel.stop_consuming()
        else:
            print("Waiting for messages... (Press CTRL+C to exit)")
            treat_messages()
    except KeyboardInterrupt:
        print("Stopped by user.")
        channel.stop_consuming()
    finally:
        treat_finish_message()
        connection.close()
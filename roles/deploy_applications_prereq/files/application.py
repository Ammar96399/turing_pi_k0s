import subprocess
import sys
import time
import pika
import json
from datetime import datetime

BROKER = "131.254.19.109"
PORT = 5672 
EXCHANGE = "event_driven_observatory"
QUEUE_NAME = f"workload_{sys.argv[1]}"
ONE_MINUTE = int(sys.argv[2])

TOTAL_OUTDATED_MESSAGES = 0


def run_sysbench(value):
    events = value * ONE_MINUTE
    if events == 0:
        print("No events to process. Skipping sysbench.")
        return
    command = f"stress-ng --cpu 1 --cpu-ops {events}"
    # command = f"sysbench cpu --cpu-max-prime=10000 --threads=1 --events={events} run"
    print(f"Running command: {command}")
    process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        print(f"Error running sysbench: {stderr.decode('utf-8')}")
    else:
        print(stdout.decode('utf-8'))


def treat_outdated_messages():
    while True:
        print("Checking for outdated messages...")
        total = 0
        messages_found = 0
        finish_message = False
        delay_logged = False

        while True: 
            method, _, body = channel.basic_get(queue=QUEUE_NAME, auto_ack=False)
            if method is None:
                break
            try:
                payload = body.decode()
                data = json.loads(payload)
                if data.get("events") == -1:
                    print("Finish message received in outdated messages. Ignoring.")
                    finish_message = True
                else: 
                    if not delay_logged:
                        log_delay(data.get("timestamp"))
                        delay_logged = True
                    log_delay_every_message(time.time(), data.get("timestamp"))
                    total += data.get("events")
                    messages_found += 1
                channel.basic_ack(delivery_tag=method.delivery_tag)
            except ValueError:
                print(f"Received invalid JSON: {payload}")

        if messages_found > 0:
            run_sysbench(total)
        else:
            print("No outdated messages found.")
            return False
            
        if finish_message:
            treat_finish_message()
            return True
        

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


def log_delay_every_message(timestamp, message_timestamp):
    channel.queue_declare(queue=f"{QUEUE_NAME}_delay_every_meesage", durable=True)
    channel.queue_bind(exchange=EXCHANGE, queue=f"{QUEUE_NAME}_delay_every_meesage", routing_key=f"{QUEUE_NAME}_delay_every_meesage")
    delay = timestamp - message_timestamp
    payload = json.dumps({"delay": delay}).encode()
    channel.basic_publish(
        exchange=EXCHANGE,
        routing_key=f"{QUEUE_NAME}_delay_every_meesage",
        body=payload,
        properties=pika.BasicProperties(
            delivery_mode=2  
        )
    )
    print(f"Delay logged: {delay} seconds")


def treat_messages():
    def on_message(ch, method, properties, body):
        start_time = time.time()
        start_time_str = datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S.%f')
        print(f"Starting on_message callback at {start_time_str}")
        
        payload = body.decode()
        try:
            data = json.loads(payload)
            if data.get("events") == -1:
                ch.basic_ack(delivery_tag=method.delivery_tag) 
                print("Finish message received. Stopping.")
                ch.stop_consuming()
                return
            log_delay_every_message(time.time(), data.get("timestamp"))
            run_sysbench(round(data.get("events")))
            ch.basic_ack(delivery_tag=method.delivery_tag)
        except json.JSONDecodeError:
            print(f"Received invalid JSON: {payload}")
        
        end_time = time.time()
        end_time_str = datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S.%f')
        duration = end_time - start_time
        print(f"Finished on_message callback at {end_time_str}. Duration: {duration:.4f} seconds")

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
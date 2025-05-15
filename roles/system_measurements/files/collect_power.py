import pyvisa
import time
import csv
import sys
import os

VISA_ADDRESS = "TCPIP0::10.0.0.20::inst0::INSTR"


def connect_instrument():
    """Establish connection to the instrument with retry logic."""
    rm = pyvisa.ResourceManager()
    for attempt in range(10):  # Try reconnecting up to 5 times
        try:
            dmm = rm.open_resource(VISA_ADDRESS)
            dmm.timeout = 10000  # Increase timeout to 10 seconds
            dmm.write("*CLS")  # Clear previous errors
            print("Connected to:", dmm.query("*IDN?"))
            dmm.write("CONF:CURR:DC 10")  # Set to 10A measurement
            return dmm
        except pyvisa.VisaIOError as e:
            print(f"Connection attempt {attempt + 1} failed: {e}")
            time.sleep(5) 
    print("Failed to connect to instrument after multiple attempts.")
    return None


def main():
    if len(sys.argv) != 2:
        print("Usage: python collect_power.py <output_csv_file>")
        sys.exit(1)
    
    csv_filename = sys.argv[1]
    dmm = connect_instrument()
    if dmm is None:
        return

    with open(csv_filename, mode='w', newline='') as csvfile:
        csv_writer = csv.writer(csvfile)
        csv_writer.writerow(["unixtime", "current_A"])

        print("Starting data logging in 10 A mode. Press Ctrl+C to stop.")
        try:
            next_time = time.time() + 1
            while True:
                experiment_end_file = os.path.join(
                    os.path.dirname(csv_filename), "experiment_end_time.txt"
                )
                if os.path.exists(experiment_end_file):
                    print(f"File {experiment_end_file} exists. Stopping in 5 minutes.")
                    stop_time = time.time() + 15 * 60 
                    while time.time() < stop_time:
                        timestamp = time.time()
                        try:
                            current_value = float(dmm.query("READ?").strip())
                        except ValueError as e:
                            print(f"Error converting measurement to float: {e}")
                            continue 

                        csv_writer.writerow([timestamp, current_value])
                        csvfile.flush()

                        sleep_time = max(0, next_time - time.time())
                        time.sleep(sleep_time)
                        next_time += 1
                    print("5 minutes elapsed. Stopping data logging.")

                    break
                try:
                    timestamp = time.time()
					
                    try:
                        current_value = float(dmm.query("READ?").strip())
                    except ValueError as e:
                        print(f"Error converting measurement to float: {e}")
                        continue
                    
                    csv_writer.writerow([timestamp, current_value])
                    csvfile.flush()

                    sleep_time = max(0, next_time - time.time())
                    time.sleep(sleep_time)
                    next_time += 1
                
                except pyvisa.VisaIOError as e:
                    print(f"VISA I/O Error: {e}, attempting to reconnect...")
                    dmm.close()
                    dmm = connect_instrument()
                    if dmm is None:
                        break

        except KeyboardInterrupt:
            print("Data logging stopped by user.")
        finally:
            if dmm:
                dmm.close() 
                print("Instrument connection closed.")


if __name__ == "__main__":
    main()

import pyvisa
import time
import csv
import sys
import os
import argparse

VISA_ADDRESS = "TCPIP0::10.0.0.20::inst0::INSTR"

def connect_instrument(ac_mode=False):
    """Establish connection to the instrument with retry logic."""
    rm = pyvisa.ResourceManager()
    for attempt in range(10):
        try:
            dmm = rm.open_resource(VISA_ADDRESS)
            dmm.timeout = 10000
            dmm.write("*CLS")
            print("Connected to:", dmm.query("*IDN?"))

            if ac_mode:
                dmm.write("CONF:CURR:AC 3")    # AC current mode
                # dmm.write("CURR:AC:NPLC 0.2")
            else:
                dmm.write("CONF:CURR:DC 3")    # DC current mode
                dmm.write("CURR:DC:NPLC 0.2")

            return dmm
        except pyvisa.VisaIOError as e:
            print(f"Connection attempt {attempt + 1} failed: {e}")
            time.sleep(5) 
    print("Failed to connect to instrument after multiple attempts.")
    return None

def main():
    parser = argparse.ArgumentParser(description="Collect current measurements from instrument.")
    parser.add_argument("--output", default="result.csv", help="Output CSV file")
    parser.add_argument("--minutes", type=int, default=None, help="Duration to log data (in minutes)")
    parser.add_argument("--precision", type=int, default=6, help="Number of decimals for current")
    parser.add_argument("--ac", action="store_true", help="Measure AC current instead of DC")
    args = parser.parse_args()

    csv_filename = args.output
    duration_seconds = args.minutes * 60 if args.minutes else None
    result_round = args.precision
    ac_mode = args.ac

    dmm = connect_instrument(ac_mode=ac_mode)
    if dmm is None:
        return

    with open(csv_filename, mode='w', newline='') as csvfile:
        csv_writer = csv.writer(csvfile)
        csv_writer.writerow(["unixtime", "current_A", "power_W"])

        print(f"Starting data logging in {'AC' if ac_mode else 'DC'} mode. Press Ctrl+C to stop.")
        start_time = time.time()
        try:
            next_time = start_time + 1
            while True:
                if duration_seconds and (time.time() - start_time) >= duration_seconds:
                    print(f"Specified duration ({args.minutes} minutes) elapsed. Stopping data logging.")
                    break

                timestamp = time.time()
                try:
                    current_value = float(dmm.query("READ?").strip())
                except ValueError as e:
                    print(f"Error converting measurement to float: {e}")
                    continue

                current_value = round(current_value, result_round)
                # Compute power assuming 230 V RMS
                power_value = current_value * (12)
                csv_writer.writerow([int(timestamp), current_value, power_value])
                csvfile.flush()

                sleep_time = max(0, next_time - time.time())
                time.sleep(sleep_time)
                next_time += 1

        except KeyboardInterrupt:
            print("Data logging stopped by user.")
        finally:
            if dmm:
                dmm.close() 
                print("Instrument connection closed.")

if __name__ == "__main__":
    main()

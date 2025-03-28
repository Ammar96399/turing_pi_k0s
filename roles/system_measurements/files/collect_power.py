import pyvisa
import time
import csv
import argparse

# Parse command-line arguments
parser = argparse.ArgumentParser(description='Collect current data and save to a CSV file.')
parser.add_argument('filename', type=str, help='The CSV file to save the data')
args = parser.parse_args()

rm = pyvisa.ResourceManager()
inst = rm.open_resource('TCPIP0::10.0.0.20::inst0::INSTR')
inst.timeout = 5000

#print(inst.query('*IDN?'))

inst.write('*RST')
time.sleep(1)
inst.write('CONF:CURR:DC 10')
inst.write('CURR:DC:RANG 10')
inst.write('SAMP:COUN 1')
inst.write('TRIG:SOUR IMM')

start_time = time.time()

with open(args.filename, 'w', newline='') as f:
	writer = csv.writer(f)
	writer.writerow(['unixtime', 'timestamp', 'current'])

	while True:
		for _ in range(5):
			inst.write('INIT')
			current = float(inst.query('FETC?'))

			current_time = time.time()
			timestamp = current_time - start_time

			writer.writerow([current_time, timestamp, current])
			#print(f"{timestamp:.1f}: I={current:.4f} A")

inst.close() 

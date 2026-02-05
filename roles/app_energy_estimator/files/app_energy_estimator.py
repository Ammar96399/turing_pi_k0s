import csv
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

CM4_TOTAL_CORES = 4
CM4_BOGOS_PER_SEC = 436.0

app = FastAPI(title="Application Energy Estimator")

def cm4_cores_to_ops(cores: float) -> float:
	"""Convert CM4-equivalent cores to operations per second (bogos/s).

	A full CM4 (4 cores) delivers 436 bogos/s, so per-core rate is 436 / 4.
	"""
	if cores < 0:
		raise ValueError("cores must be non-negative")
	per_core_ops = CM4_BOGOS_PER_SEC / CM4_TOTAL_CORES
	return cores * per_core_ops


def estimate_energy_24h(bogos: float) -> float:
	"""Estimate energy consumption for running at given load for 24 hours.
	
	Args:
		bogos: Processing load in bogos (0-2644 for full Turing Pi cluster)
	
	Returns:
		kwh: Total energy in kilowatt-hours
	"""
	if bogos < 0 or bogos > 2644:
		raise ValueError("bogos must be between 0 and 2644")
	
	csv_path = Path(__file__).parent / "power_usage.csv"
	
	power_data = {}
	with open(csv_path, 'r') as f:
		reader = csv.reader(f)
		next(reader)  # Skip header
		for row in reader:
			load = float(row[0])
			power = float(row[1])
			power_data[load] = power
	
	sorted_loads = sorted(power_data.keys())
	
	if bogos in power_data:
		power_watts = power_data[bogos]
	else:
		# Linear interpolation between nearest points
		for i in range(len(sorted_loads) - 1):
			if sorted_loads[i] < bogos < sorted_loads[i + 1]:
				l1, l2 = sorted_loads[i], sorted_loads[i + 1]
				p1, p2 = power_data[l1], power_data[l2]
				
				t = (bogos - l1) / (l2 - l1)
				power_watts = p1 + t * (p2 - p1)
				break
		else:
			if bogos < sorted_loads[0]:
				power_watts = power_data[sorted_loads[0]]
			else:
				power_watts = power_data[sorted_loads[-1]]
	
	watt_hours = power_watts * 24
	
	return round(watt_hours, 4)
	
class WorkloadResponse(BaseModel):
    cores: float
    energy: float

@app.get("/")
async def root():
  return {
		"service": "Application Energy Estimator",
		"endpoints": {
			"get_energy": "/energy/{cores}"
		}
	}
  
@app.get("/energy/{cores}", response_model=WorkloadResponse)
async def get_energy(cores: float):
  bogos = cm4_cores_to_ops(cores)
  energy = estimate_energy_24h(bogos)
  return WorkloadResponse(cores=cores, energy=energy)
  
@app.get("/health")
async def health():
	"""Health check endpoint"""
	try:
		cm4_cores_to_ops(0)
		return {"status": "healthy", "config_loaded": True}
	except Exception as e:
		return {"status": "unhealthy", "error": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=49094)
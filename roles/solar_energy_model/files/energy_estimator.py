import csv
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

CM4_TOTAL_CORES = 4
CM4_BOGOS_PER_SEC = 436.0

app = FastAPI(title="Application Energy Estimator")
	
class WorkloadResponse(BaseModel):
    energy: float

@app.get("/")
async def root():
  return {
		"service": "Application Energy Estimator",
		"endpoints": {
			"get_energy": "/energy/{cores}"
		}
	}
  
@app.get("/energy", response_model=WorkloadResponse)
async def get_energy():
  energy = 20
  return WorkloadResponse(energy=energy)
  
@app.get("/health")
async def health():
	"""Health check endpoint"""
	try:
		return {"status": "healthy", "config_loaded": True}
	except Exception as e:
		return {"status": "unhealthy", "error": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=49095)
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import requests
import uvicorn
from datetime import datetime

app = FastAPI(title="Solar Energy Estimator")


class PVGISInput(BaseModel):
	lat: float = Field(..., description="Latitude in decimal degrees")
	lon: float = Field(..., description="Longitude in decimal degrees")
	peakpower_kw: float = Field(..., description="Installed peak power (kWp)")
	from datetime import datetime

	from fastapi import FastAPI, HTTPException
	from pydantic import BaseModel, Field
	import requests
	import uvicorn

	app = FastAPI(title="Solar Energy Estimator")


	class PVGISInput(BaseModel):
		lat: float = Field(..., description="Latitude in decimal degrees")
		lon: float = Field(..., description="Longitude in decimal degrees")
		peakpower_kw: float = Field(..., description="Installed peak power (kWp)")
		loss: float = Field(14.0, description="System losses in percent")
		angle: float = Field(35.0, description="Tilt angle (degrees)")
		aspect: float = Field(0.0, description="Azimuth (0=south, -90=east, 90=west)")
		raddatabase: str = Field("PVGIS-SARAH2", description="Radiation database")
		date: str = Field(..., description="Date to evaluate (YYYY-MM-DD)")


	class PVGISMonthInput(BaseModel):
		lat: float = Field(..., description="Latitude in decimal degrees")
		lon: float = Field(..., description="Longitude in decimal degrees")
		month: int = Field(..., ge=1, le=12, description="Month (1-12)")
		peakpower_kw: float = Field(..., description="Installed peak power (kWp)")
		loss: float = Field(14.0, description="System losses in percent")
		angle: float = Field(35.0, description="Tilt angle (degrees)")
		aspect: float = Field(0.0, description="Azimuth (0=south, -90=east, 90=west)")
		raddatabase: str = Field("PVGIS-SARAH3", description="Radiation database")


	@app.get("/")
	async def root():
		return {
			"service": "Solar Energy Estimator",
			"endpoints": {
				"get_energy_pvgis": "/energy/pvgis",
				"get_energy_pvgis_month": "/energy/pvgis/month",
			},
		}


	@app.post("/energy/pvgis")
	async def get_energy_pvgis(payload: PVGISInput):
		if payload.peakpower_kw <= 0:
			raise HTTPException(status_code=400, detail="peakpower_kw must be > 0")

		try:
			date_obj = datetime.strptime(payload.date, "%Y-%m-%d")
		except ValueError:
			raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

		params = {
			"lat": payload.lat,
			"lon": payload.lon,
			"peakpower": payload.peakpower_kw,
			"loss": payload.loss,
			"angle": payload.angle,
			"aspect": payload.aspect,
			"raddatabase": payload.raddatabase,
			"outputformat": "json",
			"pvcalculation": 1,
			"startyear": date_obj.year,
			"endyear": date_obj.year,
		}

		try:
			resp = requests.get(
				"https://re.jrc.ec.europa.eu/api/v5_2/seriescalc",
				params=params,
				timeout=15,
			)
			resp.raise_for_status()
			data = resp.json()
			hourly = data["outputs"]["hourly"]

			energy_wh = 0.0
			for row in hourly:
				time_str = row.get("time", "")
				if not time_str.startswith(date_obj.strftime("%Y%m%d")):
					continue
				power_w = row.get("P")
				if power_w is None:
					continue
				energy_wh += float(power_w)

			return {
				"energy_wh": round(energy_wh, 2),
				"date": payload.date,
				"source": "pvgis-seriescalc",
				"params": params,
			}
		except (KeyError, TypeError) as exc:
			raise HTTPException(status_code=502, detail=f"Unexpected PVGIS response: {exc}")
		except requests.RequestException as exc:
			raise HTTPException(status_code=502, detail=f"PVGIS request failed: {exc}")


	@app.post("/energy/pvgis/month")
	async def get_energy_pvgis_month(payload: PVGISMonthInput):
		if payload.peakpower_kw <= 0:
			raise HTTPException(status_code=400, detail="peakpower_kw must be > 0")

		params = {
			"lat": payload.lat,
			"lon": payload.lon,
			"month": payload.month,
			"global": 1,
			"angle": payload.angle,
			"aspect": payload.aspect,
			"raddatabase": payload.raddatabase,
			"outputformat": "json",
		}

		try:
			resp = requests.get(
				"https://re.jrc.ec.europa.eu/api/DRcalc",
				params=params,
				timeout=15,
			)
			resp.raise_for_status()
			data = resp.json()

			daily_profile = data["outputs"]["daily_profile"]
			energy_wh = 0.0
			for row in daily_profile:
				if int(row.get("month", 0)) != payload.month:
					continue
				gi = float(row.get("G(i)", 0.0))
				energy_wh += (gi / 1000.0) * payload.peakpower_kw * (1 - payload.loss / 100.0) * 1000.0

			return {
				"energy_wh": round(energy_wh, 2),
				"month": payload.month,
				"source": "pvgis-drcalc",
				"params": params,
			}
		except (KeyError, TypeError, ValueError) as exc:
			raise HTTPException(status_code=502, detail=f"Unexpected PVGIS response: {exc}")
		except requests.RequestException as exc:
			raise HTTPException(status_code=502, detail=f"PVGIS request failed: {exc}")


	@app.get("/health")
	async def health():
		try:
			return {"status": "healthy", "config_loaded": True}
		except Exception as e:
			return {"status": "unhealthy", "error": str(e)}


	if __name__ == "__main__":
		uvicorn.run(app, host="0.0.0.0", port=49095)
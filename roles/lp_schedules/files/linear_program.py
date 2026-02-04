from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Union
from pulp import LpProblem, LpMaximize, LpVariable, LpBinary, lpSum, COIN_CMD, value, LpStatus
import uvicorn
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="K8s Graceful Degradation Solver - ARM64")

class SolveRequest(BaseModel):
    budget: float
    applications: Dict

@app.post("/solve")
async def solve_optimization(request: SolveRequest):
    logger.info("=" * 80)
    logger.info("Received optimization request")
    logger.info(f"Budget: {request.budget}")
    logger.info(f"Applications: {list(request.applications.keys())}")
    
    try:
        # 1. Setup Data
        logger.info("Starting data setup...")
        weights, energy_cost, Lmax, dependencies = {}, {}, {}, {}
        Index_to_Name = {}
        Apps = []
        
        for name, d in request.applications.items():
            logger.info(f"Processing application: {name}")
            idx = d["index"]
            Index_to_Name[idx] = name
            Apps.append(idx)
            weights[idx] = d.get("weight", 1)
            
            level_ids = [lvl["level"] for lvl in d["levels"]]
            for lvl in d["levels"]:
                energy_cost[(idx, lvl["level"])] = lvl["cost"]
                dependencies[(idx, lvl["level"])] = lvl.get("dep", [])
            Lmax[idx] = max(level_ids) if level_ids else 0
            
            logger.info(f"  Index: {idx}, Weight: {weights[idx]}, Lmax: {Lmax[idx]}")

        App_Levels = list(energy_cost.keys())
        total_w = sum(weights.values())
        
        logger.info(f"Setup complete: Apps={Apps}, total_weight={total_w}, App_Levels count={len(App_Levels)}")

        # 2. Build Model
        logger.info("Building optimization model...")
        model = LpProblem("Service_Optimization", LpMaximize)
        x = LpVariable.dicts("x", App_Levels, 0, 1, LpBinary)
        z = {i: LpVariable(f"z_{i}", 0, Lmax[i]) for i in Apps}
        
        # Objective: maximize weighted normalized levels
        model += lpSum((z[i] * (1.0/Lmax[i])) * (weights[i]/total_w) for i in Apps if Lmax[i] > 0)
        
        # Budget constraint
        model += lpSum(energy_cost[i, j] * x[i, j] for i, j in App_Levels) <= request.budget

        # Link z to active levels
        for i in Apps:
            levels = [lvl for (idx, lvl) in App_Levels if idx == i]
            for j in levels:
                model += z[i] >= j * x[(i, j)]
            for j in range(0, Lmax[i]):
                higher = [x[(i, k)] for k in levels if k > j]
                if higher:
                    model += z[i] <= j + Lmax[i] * lpSum(higher)

        # Cross-app dependency constraints
        logger.info("Adding dependency constraints...")
        for (i, j), dep_list in dependencies.items():
            for dep_dict in dep_list:
                for d_idx_str, d_lvl in dep_dict.items():
                    d_idx = int(d_idx_str)
                    if d_lvl == "*":
                        relevant_vars = [x[(d_idx, m)] for m in range(1, Lmax[d_idx] + 1) if (d_idx, m) in App_Levels]
                        if relevant_vars:
                            model += x[(i, j)] <= lpSum(relevant_vars)
                            logger.info(f"  App {i} Level {j} depends on any level of App {d_idx}")
                    else:
                        d_lvl = int(d_lvl)
                        if (d_idx, d_lvl) in App_Levels:
                            model += x[(i, j)] <= x[(d_idx, d_lvl)]
                            logger.info(f"  App {i} Level {j} depends on App {d_idx} Level {d_lvl}")

        # 3. Solve
        logger.info("Solving optimization problem...")
        # Use system CBC solver (ARM64 compatible) instead of bundled x86_64 version
        try:
            solver = COIN_CMD(path='/usr/bin/cbc', msg=False)
            status = model.solve(solver)
        except:
            logger.warning("System CBC not found, falling back to bundled solver")
            from pulp import PULP_CBC_CMD
            status = model.solve(PULP_CBC_CMD(msg=False))
        
        logger.info(f"Solver status: {LpStatus[model.status]}")
        
        if LpStatus[model.status] == 'Optimal':
            energy_used = sum(energy_cost[i, j] for i, j in App_Levels if value(x[i, j]) > 0.5)
            logger.info(f"Energy used: {energy_used}/{request.budget}")
            
            # Build result in A{i}L{j} format to match linear_program.py
            result = {f"A{i}L{j}": int(value(x[(i, j)]) > 0.5) for (i, j) in App_Levels}
            
            # Log human-readable solution
            active_levels = []
            for i in Apps:
                app_name = Index_to_Name[i]
                max_lvl = 0
                for j in range(1, Lmax[i] + 1):
                    if (i, j) in App_Levels and value(x.get((i, j), 0)) > 0.5:
                        max_lvl = max(max_lvl, j)
                        active_levels.append(f"{app_name} L{j}")
                logger.info(f"  {app_name}: Level {max_lvl}")
            
            logger.info(f"Active levels: {', '.join(active_levels)}")
            logger.info("=" * 80)
            return result
        else:
            logger.warning(f"Solver status not optimal: {LpStatus[model.status]}")
            logger.info("=" * 80)
            return {f"A{i}L{j}": 0 for (i, j) in App_Levels}

    except Exception as e:
        logger.error(f"ERROR during optimization: {type(e).__name__}: {str(e)}", exc_info=True)
        logger.info("=" * 80)
        raise HTTPException(status_code=500, detail=str(e))
    
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)

#!/usr/bin/env python3
"""Generate an LPSchedule CRD with updated costs and budget.

- Updates each level cost using get-limit -> app-energy
- Updates budget using solar_energy_model /energy
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict

import requests
import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

DEFAULT_INPUT = (
    Path(__file__).resolve().parent
    / "lpschedule_template.yml"
)
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "test.energy.yml"

DEFAULT_GET_LIMIT_URL = (
    "http://10.96.112.206:49091"
)
DEFAULT_APP_ENERGY_URL = (
    "http://10.109.123.8:49094"
)
DEFAULT_SOLAR_ENERGY_URL = (
    "http://10.109.255.196:49095"
)
DEFAULT_NAMESPACE = "lp-schedules-operator"


def normalize_url(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"http://{url}"


def parse_limit_value(limit_value: str) -> float:
    if limit_value.endswith("m"):
        return float(limit_value[:-1]) / 1000.0
    return float(limit_value)


def fetch_app_limit(get_limit_url: str, level_name: str) -> float:
    get_limit_url = normalize_url(get_limit_url)
    response = requests.get(f"{get_limit_url}/limit/{level_name}", timeout=10)
    response.raise_for_status()
    data = response.json()
    if "max_value" not in data:
        raise ValueError(f"Missing max_value in get-limit response: {data}")
    return parse_limit_value(str(data["max_value"]))


def fetch_app_energy(app_energy_url: str, cores: float) -> float:
    app_energy_url = normalize_url(app_energy_url)
    response = requests.get(f"{app_energy_url}/energy/{cores}", timeout=10)
    response.raise_for_status()
    data = response.json()
    if "energy" not in data:
        raise ValueError(f"Missing energy in app energy response: {data}")
    return float(data["energy"])


def fetch_solar_budget(solar_energy_url: str) -> float:
    solar_energy_url = normalize_url(solar_energy_url)
    response = requests.get(f"{solar_energy_url}/energy", timeout=10)
    response.raise_for_status()
    data = response.json()
    if "energy" not in data:
        raise ValueError(f"Missing energy in solar response: {data}")
    return float(data["energy"])


def update_crd(
    crd: Dict[str, Any],
    get_limit_url: str,
    app_energy_url: str,
    solar_energy_url: str,
) -> Dict[str, Any]:
    spec = crd.get("spec", {})

    # Update budget from solar energy model
    spec["budget"] = fetch_solar_budget(solar_energy_url)

    # Update level costs using get-limit -> app-energy
    apps = spec.get("applications", {})
    limit_cache: Dict[str, float] = {}
    energy_cache: Dict[str, float] = {}

    for _, app_data in apps.items():
        levels = app_data.get("levels", [])
        app_index = app_data.get("index")
        for level in levels:
            level_index = level.get("level")
            level_name = f"A{app_index}L{level_index}"

            if level_name in limit_cache:
                app_limit = limit_cache[level_name]
            else:
                app_limit = fetch_app_limit(get_limit_url, level_name)
                limit_cache[level_name] = app_limit

            if level_name in energy_cache:
                energy_cost = energy_cache[level_name]
            else:
                energy_cost = fetch_app_energy(app_energy_url, app_limit)
                energy_cache[level_name] = energy_cost

            level["cost"] = energy_cost

    crd["spec"] = spec
    return crd


def apply_crd(crd: Dict[str, Any]) -> None:
    metadata = crd.get("metadata", {})
    api_version = crd.get("apiVersion", "")
    kind = crd.get("kind", "")

    if "/" not in api_version:
        raise ValueError(f"Invalid apiVersion: {api_version}")

    group, version = api_version.split("/", 1)
    name = metadata.get("name")
    namespace = metadata.get("namespace", DEFAULT_NAMESPACE)

    if not name:
        raise ValueError("CRD metadata.name is required")

    if kind.lower() == "lpschedule":
        plural = "lpschedules"
    else:
        plural = f"{kind.lower()}s"

    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config()

    api = client.CustomObjectsApi()
    try:
        api.create_namespaced_custom_object(
            group=group,
            version=version,
            namespace=namespace,
            plural=plural,
            body=crd,
        )
        print(f"Created {kind} {name} in {namespace}")
    except ApiException as exc:
        if exc.status == 409:
            api.patch_namespaced_custom_object(
                group=group,
                version=version,
                namespace=namespace,
                plural=plural,
                name=name,
                body=crd,
            )
            print(f"Patched {kind} {name} in {namespace}")
        else:
            raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate CRD with energy-adjusted costs and budget."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="Path to input CRD YAML (default: test.yml)",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Path to output CRD YAML",
    )
    parser.add_argument(
        "--get-limit-url",
        default=DEFAULT_GET_LIMIT_URL,
        help="Base URL for get-limit service",
    )
    parser.add_argument(
        "--app-energy-url",
        default=DEFAULT_APP_ENERGY_URL,
        help="Base URL for app energy estimator",
    )
    parser.add_argument(
        "--solar-energy-url",
        default=DEFAULT_SOLAR_ENERGY_URL,
        help="Base URL for solar energy model",
    )

    args = parser.parse_args()

    with open(args.input, "r") as f:
        crd = yaml.safe_load(f)

    updated = update_crd(crd, args.get_limit_url, args.app_energy_url, args.solar_energy_url)

    with open(args.output, "w") as f:
        yaml.safe_dump(updated, f, sort_keys=False)

    print(f"Wrote updated CRD to {args.output}")

    apply_crd(updated)


if __name__ == "__main__":
    main()

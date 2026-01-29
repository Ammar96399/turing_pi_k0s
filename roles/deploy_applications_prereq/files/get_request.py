import sys
import yaml
import os

def get_value(name):
    # Get the path to the YAML configuration file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(
        script_dir, 
        "/home/akazem/Documents/PhD/projects/energy_proportionality/cluster_configuration/turing_pi_emulation/roles/workloads_injector/files/nepal_scenario/configs/workloads_configuration.yml"
    )
    
    with open(config_path, 'r') as f:
        data = yaml.safe_load(f)
    
    for workload in data['configuration']:
        if workload['name'] == name:
            # Convert CPU limit to Kubernetes format (e.g., 0.1 -> 100m)
            if isinstance(workload['max_value'], (int, float)):
                value = workload['max_value'] / 10
                if value < 1:
                    return f"{int(value * 1000)}m"
                else:
                    return f"{int(value)}"
    return None
    
if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python get_request.py <name>")
        sys.exit(1)
    
    name = sys.argv[1]
    max_value = get_value(name)
    
    if max_value is not None:
        print(max_value)
    else:
        print(f"Error: Workload '{name}' not found in configuration")
        sys.exit(1)
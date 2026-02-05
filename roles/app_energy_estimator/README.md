# App Energy Estimator

## Introduction
Because of the heterogeneous nature of the cluster on which I am conducting my experiments, it is not easy to estimate the energy consumption of a given application. This is primarly due to the fact that we cannot know on which type of hardware the app will be running. Addictionnaly, the RK1 model has a heterogeneous CPU with 4 performance core and 4 efficiency cores. So I had to come up with a simple solution for that problem.

## Proposed Solution
For the heterogeneous CPU, the solution is simple: 
- I used the following command to pin the process to core number 7 ```stress-ng --1 --cpu-ops 100000000 --timeout 10s --metrics-brief --taskset 7```. I did the same thing for all the other cores, and took the mean processing power. 

For the heterogeneous nature of the cluster, I did something similar too. I took the sum of the processing capabilities of 2 RK1 and 1 CM4. I then remove the idle power consumption, and connected the 3 together. I put the CM4 at the end because it is the one that will be powered off first. 

## Problems
Unless a workload needs more processing power that what 2 RK1 can provide, the estimation will always be equal to what an estimation on an RK1 alone is. 
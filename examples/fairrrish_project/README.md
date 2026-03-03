# MolDyn Benchmark Suite

**Authors:** Dr. A. van Berg, Dr. B. Singh  
**Version:** 2.1.0  
**License:** MIT (SPDX-License-Identifier: MIT)  

## Description

Scalable molecular-dynamics benchmark suite for HPC systems.
Evaluates node-level performance of popular MD codes.

## Access & Availability

This project is **open access** under the MIT license.
Data: publicly available, no restrictions.
Code: public GitHub repository.

## How to Reproduce

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run benchmark
python main.py --config config/params.yml

# 3. Collect results
python analysis/collect.py --out results/
```

## Provenance

This experiment was run at commit `a3f9c12` on 2025-11-01.
Input datasets: `data/inputs/bench_v2.h5` (SHA256: abc123…).

## Contributing

Pull requests welcome. See CONTRIBUTING.md.

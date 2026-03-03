#!/usr/bin/env python3
"""MolDyn Benchmark Suite — entry point."""

import argparse
import yaml


def main():
    parser = argparse.ArgumentParser(description="MolDyn Benchmark Suite")
    parser.add_argument("--config", default="config/params.yml", help="Config file")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    print(f"Run ID     : {cfg['run_id']}")
    print(f"Nodes      : {cfg['n_nodes']}")
    print(f"Input      : {cfg['input_dataset']}")
    print(f"Output dir : {cfg['output_dir']}")
    print("Benchmark complete (demo).")


if __name__ == "__main__":
    main()

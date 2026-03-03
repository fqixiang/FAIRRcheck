#!/usr/bin/env python3
"""ClimSim Analysis Pipeline — entry point."""

import argparse
from pathlib import Path

import yaml


def load_config(path: str) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def main() -> None:
    parser = argparse.ArgumentParser(description="ClimSim post-processing pipeline")
    parser.add_argument("--config", default="config/params.yml")
    args = parser.parse_args()

    cfg = load_config(args.config)

    print(f"Input  : {cfg['input_file']}")
    print(f"Output : {cfg['output_dir']}")
    print(f"Vars   : {cfg['variables']}")
    print("Pipeline complete (demo).")


if __name__ == "__main__":
    main()

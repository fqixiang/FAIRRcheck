#!/usr/bin/env python3
"""Plot results from the ClimSim analysis."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot ClimSim analysis output")
    parser.add_argument("--input", required=True, help="Input NetCDF file")
    parser.add_argument("--out",   default="results/", help="Output directory")
    args = parser.parse_args()
    print(f"Plotting {args.input} → {args.out} (demo)")


if __name__ == "__main__":
    main()

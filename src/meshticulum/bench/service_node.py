"""Explicit finite public-service entry point; no action on import or --help."""
import argparse
import json

from .node import run_public_service


def main():
    parser = argparse.ArgumentParser(description='Run one pre-admitted public MR1 transfer through the exclusive radio worker.')
    parser.add_argument('--unit', required=True, choices=('unit-a', 'unit-b', 'unit-c'))
    parser.add_argument('--grant-dir', required=True)
    args = parser.parse_args()
    result = run_public_service(args.unit, args.grant_dir)
    # Run metadata only; body/key material lives solely in the private run path.
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get('complete') is True else 1


if __name__ == '__main__':
    raise SystemExit(main())

"""Generate isolated fictional labels and self-reports; no personal data is read."""
import argparse
import json
from focuspet.learning.synthetic import write_examples

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="examples/synthetic/generated")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(write_examples(args.output, args.seed), indent=2))

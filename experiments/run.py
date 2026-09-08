import argparse
import json
from focuspet.learning.evaluation import evaluate_config

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="experiments/config.json")
    args = parser.parse_args()
    print(json.dumps(evaluate_config(args.config), indent=2))

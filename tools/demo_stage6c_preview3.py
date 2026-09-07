"""Run a short main-linked Preview3 bilateral market demonstration."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from asset_simulation.audit_stage6c_preview3 import replay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--turns", type=int, default=12)
    parser.add_argument("--output", type=Path, default=Path("stage6c-preview3-demo.json"))
    args = parser.parse_args()
    result = replay(args.seed, args.years, max_turns=args.turns, keep_turns=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "seed": result["seed"],
        "committed_turns": result["committed_turns"],
        "failure": result["failure"],
        "total_unserved_bbl": result["total_unserved_bbl"],
        "mean_abs_buyer_quantity_deviation_fraction": result["mean_abs_buyer_quantity_deviation_fraction"],
        "same_turn_reverse_switch_count": result["same_turn_reverse_switch_count"],
    }))


if __name__ == "__main__":
    main()

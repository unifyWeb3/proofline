"""Small developer-facing fixture runner and evidence inspector."""

from __future__ import annotations

import argparse
import json

from .engine import LocalEngine
from .fixtures import FIXTURE_NOW, all_fixtures, write_fixture_files


def main() -> int:
    parser = argparse.ArgumentParser(prog="proofline")
    parser.add_argument("command", choices=("fixtures", "write-evidence"))
    parser.add_argument("--root", default="fixtures/evidence")
    args = parser.parse_args()
    if args.command == "write-evidence":
        write_fixture_files(args.root)
        return 0
    engine = LocalEngine()
    output = []
    for job_id, fixture in all_fixtures().items():
        result = engine.submit(
            fixture["policy"],
            fixture["agreement"],
            fixture["envelope"],
            fixture["evidence"],
            now=FIXTURE_NOW,
        )
        output.append(
            {
                "job_id": job_id,
                "expected_verdict": fixture["expected_verdict"],
                "verdict": result.receipt.verdict,
                "reason_code": result.receipt.reason_code,
                "evaluation_invoked": result.receipt.evaluation_invoked,
                "finality_status": result.receipt.finality_status,
                "adapter_signal": result.receipt.adapter_signal,
            }
        )
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

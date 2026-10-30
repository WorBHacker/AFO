"""A hidden check of «Отчёт по продажам»: report.json holds exactly every region and its right total."""

import csv
import json
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

GIVEN = Path(__file__).resolve().parent.parent / "files"


def main() -> int:
    totals: dict[str, Decimal] = {}
    with (GIVEN / "sales.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            totals[row["region"]] = totals.get(row["region"], Decimal(0)) + Decimal(row["amount"])
    try:
        report = json.loads(Path("report.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(f"report.json is not readable JSON: {exc}")
        return 1
    if not isinstance(report, dict) or set(report) != set(totals):
        print(f"regions {sorted(report) if isinstance(report, dict) else report!r}, expected {sorted(totals)}")
        return 1
    for region, total in totals.items():
        want = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        try:
            got = Decimal(str(report[region]))
        except ArithmeticError:
            print(f"{region}: {report[region]!r} is not a number")
            return 1
        if abs(got - want) > Decimal("0.005"):
            print(f"{region}: {got}, expected {want}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

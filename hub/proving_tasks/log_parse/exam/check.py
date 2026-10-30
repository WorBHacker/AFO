"""A hidden check of «Разбор журнала»: the edge cases the docstring names, and the tests as given."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
sys.dont_write_bytecode = True
GIVEN = Path(__file__).resolve().parent.parent / "files"

CASES = [
    ([], {}),
    (["\n", "   \n", ""], {}),
    (["2026-10-01 12:00:01 ERROR E1 a\r\n", "2026-10-01 12:00:02 ERROR E1 b\r\n"], {"E1": 2}),
    (["2026-10-01   12:00:01   ERROR   E22   spaces\n"], {"E22": 1}),
    (["2026-10-01\t12:00:01\tERROR\tE3\ttabs\n"], {"E3": 1}),
    (["2026-10-01 12:00:01 error E5 lower\n", "2026-10-01 12:00:01 Error E5 mixed\n"], {}),
    (["2026-10-01 12:00:01 ERROR\n", "2026-10-01 12:00:01 ERROR X1 bad\n", "2026-10-01 12:00:01 ERROR E\n"], {}),
    (["2026-10-01 12:00:01 ERROR E12a odd\n", "2026-10-01 12:00:01 ERROR e12 lower code\n"], {}),
    (["2026-10-01 12:00:01 WARN E9 not an error\n", "2026-10-01 12:00:01 INFO E9\n"], {}),
    (["2026-10-01 12:00:01 ERROR E9\n"], {"E9": 1}),
]


def main() -> int:
    tests = Path("test_logparse.py")
    if not tests.is_file() or tests.read_bytes().replace(b"\r\n", b"\n") != (GIVEN / "test_logparse.py").read_bytes(
    ).replace(b"\r\n", b"\n"):  # fmt: skip
        print("test_logparse.py was changed or removed")
        return 1
    from logparse import count_errors

    for lines, want in CASES:
        try:
            got = count_errors(list(lines))
        except Exception as exc:
            print(f"{lines!r}: raised {exc!r}")
            return 1
        if got != want:
            print(f"{lines!r}: {got!r}, expected {want!r}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

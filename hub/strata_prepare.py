"""(45.2в) A model of Strata made ready without starting it - run by Strata's own Python, not the hub's:
    python -I -X utf8 strata_prepare.py <terminal> <strata launch arguments...>
Strata's command has no such step - `launch --no-tui --yes` starts the model, and with it the download of tens of GB
would hold the graphics card. Its own Runner.run(start=False) does the engine, model, config and tune steps and
writes the run config, nothing loaded (the stand, 45.0). Each of its steps is printed as a JSON line
{"stage", "text", "pct", "level"}; the last line is {"prepared": {"profile", "config"}}. Standard library and
Strata only: the hub's modules are not on this Python's path."""

import json
import sys


def main() -> int:
    terminal, args = sys.argv[1], sys.argv[2:]
    sys.path.insert(0, terminal)
    if sys.platform == "win32":
        sys.modules["_wmi"] = None  # as strata.py does: CPython 3.12's WMI helper can crash a worker thread

    from strata_cli.launch import cmd, runner

    def printer(quiet: bool = False):
        def emit(event) -> None:
            line = {"stage": event.stage, "text": event.text, "pct": event.pct, "level": event.level}
            print(json.dumps(line, ensure_ascii=False), flush=True)

        return emit

    prepared: dict = {}
    run = runner.Runner.run

    def prepare_only(self, *, start: bool = True):
        result = run(self, start=False)
        prepared.update(profile=self.profile, config=str(getattr(self, "config_path", "") or ""))
        return result

    cmd._printer = printer
    runner.Runner.run = prepare_only
    from strata_cli.cli import main as strata

    code = strata(["launch", *args, "--no-tui", "--yes"])
    if code == 0 and prepared.get("config"):
        print(json.dumps({"prepared": prepared}), flush=True)
    return code or (0 if prepared.get("config") else 4)


if __name__ == "__main__":
    sys.exit(main())

"""A hidden check of «Вынеси повторяющийся код»: no copy of the formula is left outside price_with_tax, the
behaviour is the original's on many inputs, the tests and the visible check are as given."""

import ast
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
sys.dont_write_bytecode = True
GIVEN = Path(__file__).resolve().parent.parent / "files"


def same(name: str) -> bool:
    mine = Path(name)
    return mine.is_file() and mine.read_bytes().replace(b"\r\n", b"\n") == (GIVEN / name).read_bytes().replace(
        b"\r\n", b"\n"
    )


def original(source: str) -> dict:
    space: dict = {}
    exec(compile(source, "original", "exec"), space)
    return space


def main() -> int:
    for name in ("test_inventory.py", "check_refactor.py"):
        if not same(name):
            print(f"{name} was changed or removed")
            return 1
    source = Path("inventory.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        inside = isinstance(node, ast.FunctionDef) and node.name == "price_with_tax"
        if isinstance(node, ast.FunctionDef) and not inside:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and sub.value == 1.2:
                    print(f"{node.name} still multiplies by 1.2 itself")
                    return 1
    import inventory

    before = original((GIVEN / "inventory.py").read_text(encoding="utf-8"))
    rng = random.Random(7)
    for _ in range(300):
        items = [{"price": round(rng.uniform(0, 500), 2), "qty": rng.randint(0, 9)} for _ in range(rng.randint(0, 6))]
        for name in ("order_total", "cheapest"):
            if getattr(inventory, name)(items) != before[name](items):
                print(f"{name} changed its answer for {items}")
                return 1
        for item in items:
            if inventory.item_price(item) != before["item_price"](item):
                print(f"item_price changed for {item}")
                return 1
            if inventory.price_with_tax(item["price"]) != before["item_price"](item):
                print(f"price_with_tax({item['price']}) is not the price with VAT")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

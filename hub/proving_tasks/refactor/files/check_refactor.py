"""Is the price with VAT computed in one place? Exit 0 - yes."""

import ast
import sys
from pathlib import Path

tree = ast.parse(Path("inventory.py").read_text(encoding="utf-8"))
functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
if "price_with_tax" not in functions:
    print("there is no function price_with_tax")
    sys.exit(1)
for name in ("item_price", "order_total", "cheapest"):
    calls = [n for n in ast.walk(functions.get(name, ast.Module(body=[], type_ignores=[])))
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "price_with_tax"]  # fmt: skip
    if not calls:
        print(f"{name} does not use price_with_tax")
        sys.exit(1)
sys.exit(0)

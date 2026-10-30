"""A hidden check of «Найди ошибку в магазине»: the discount is taken once, from the whole sum, rounded down to
whole cents, on many carts; the tests are as given."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
sys.dont_write_bytecode = True
GIVEN = Path(__file__).resolve().parent.parent / "files"


def main() -> int:
    tests = Path("test_shop.py")
    if not tests.is_file() or tests.read_bytes().replace(b"\r\n", b"\n") != (GIVEN / "test_shop.py").read_bytes(
    ).replace(b"\r\n", b"\n"):  # fmt: skip
        print("test_shop.py was changed or removed")
        return 1
    from shop import Cart

    rng = random.Random(11)
    for _ in range(300):
        cart, cents = Cart(), 0
        for n in range(rng.randint(0, 5)):
            price = rng.randint(1, 99_999)
            qty = rng.randint(1, 7)
            cart.add(f"item{n}", f"{price // 100}.{price % 100:02d}", qty)
            cents += price * qty
        percent = rng.choice([0, 1, 5, 10, 15, 33, 50, 100])
        want = cents - cents * percent // 100
        if cart.total(percent) != f"{want // 100}.{want % 100:02d}":
            print(f"{cart.lines} at {percent}%: {cart.total(percent)}, expected {want // 100}.{want % 100:02d}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

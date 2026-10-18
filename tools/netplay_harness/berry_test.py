#!/usr/bin/env python3
"""
Berry planting check: warp to the hub Farming Field, stand below an empty soil patch and press A.
The game should offer to plant a berry (the patch is an invisible berry tree object, which
v2.2.1 stopped the player from interacting with).
Needs a ROM built with ROGUE_MP_TEST. Usage: berry_test.py --core .. --rom .. --map .. --state overworld.state
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Core, Link, load_symbols  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", required=True)
    ap.add_argument("--rom", required=True)
    ap.add_argument("--map", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", default="berry_test_out")
    ap.add_argument("--berry", type=int, required=True, help="item id of ITEM_ORAN_BERRY")
    ap.add_argument("--blend", action="store_true", help="test Pokeblock blending instead of planting")
    ap.add_argument("--blend-seconds", type=int, default=200, help="how long to let the blender run")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    syms = load_symbols(args.map)
    c = Core(args.core, args.rom, 0, Link(), args.out, sym=syms)
    c.load_state(open(args.state, "rb").read())

    def step(n):
        for _ in range(n):
            c.run_frame()

    def cmd(n, *a):
        data = b"".join(int(x).to_bytes(2, "little") for x in (list(a) + [0, 0, 0, 0])[:4])
        c.poke("gRogueMPTestArg", data)
        c.poke("gRogueMPTestCommand", bytes([n]))
        step(4)

    def press(b, rel=40):
        c.pressed = {b}
        step(4)
        c.pressed = set()
        step(rel)

    step(30)
    if args.blend:
        # Pokeblock blending with the novice in the Market
        cmd(8, args.berry, 20)
        cmd(7, 2, 13, 12, 7)     # MAP_ROGUE_AREA_MARKET, below the novice blender
        step(180)
        press("UP", 20)
        k = 0
        for b in ["A", "A", "A", "A", "A", "A", "A", "A", "A"]:
            press(b, 50)
            c.screenshot(os.path.join(args.out, f"b{k:02d}.png"))
            k += 1
        for i in range(args.blend_seconds // 16):  # the blender minigame (the NPC partner drives it)
            step(1000)
            c.screenshot(os.path.join(args.out, f"b{k:02d}.png"))
            k += 1
        for i in range(16):
            press("A" if i < 10 else "B", 60)
            c.screenshot(os.path.join(args.out, f"b{k:02d}.png"))
            k += 1
        return

    cmd(8, args.berry, 5)
    cmd(7, 2, 4, 23, 5)      # MAP_ROGUE_AREA_FARMING_FIELD, below BERRY_TREE_HUB_11
    step(180)
    c.screenshot(os.path.join(args.out, "s00_arrived.png"))
    press("UP", 20)
    c.screenshot(os.path.join(args.out, "s01_facing.png"))
    press("A", 60)
    c.screenshot(os.path.join(args.out, "s02_interact.png"))
    for i in range(3, 10):
        press("A", 60)
        c.screenshot(os.path.join(args.out, f"s{i:02d}.png"))


if __name__ == "__main__":
    main()

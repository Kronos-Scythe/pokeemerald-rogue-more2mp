#!/usr/bin/env python3
"""
Pokeblock scattering during an Adventure: start an Adventure through the real Adventure Entrance
script, enter a route room, then scatter a type Pokeblock from the bag like a player would.
Expects VAR_ROGUE_ACTIVE_POKEBLOCK to be set and the Pokeblock to be used up.
Needs a ROM built with ROGUE_MP_TEST. Usage:
  pokeblock_test.py --core .. --rom .. --map .. --state overworld.state --pokeblock <item id>
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Core, Link, load_symbols  # noqa: E402

VAR_ROGUE_ACTIVE_POKEBLOCK = 0x402F
ADVPATH_ROOM_ROUTE = 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", required=True)
    ap.add_argument("--rom", required=True)
    ap.add_argument("--map", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", default="pokeblock_test_out")
    ap.add_argument("--pokeblock", type=int, required=True, help="item id of a type Pokeblock, e.g. ITEM_POKEBLOCK_FIRE")
    ap.add_argument("--species", type=int, default=25, help="party Pokemon to take on the Adventure")
    ap.add_argument("--pocket-left", type=int, default=1, help="LEFT presses from the Items pocket to Poke Blocks")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    syms = load_symbols(args.map)
    c = Core(args.core, args.rom, 0, Link(), args.out, sym=syms)
    c.load_state(open(args.state, "rb").read())
    failures = []
    k = [0]

    def check(cond, msg):
        print(("  PASS " if cond else "  FAIL ") + msg, flush=True)
        if not cond:
            failures.append(msg)

    def step(n):
        for _ in range(n):
            c.run_frame()

    def shot(label):
        c.screenshot(os.path.join(args.out, f"{k[0]:03d}_{label}.png"))
        k[0] += 1

    def cmd(n, *a):
        data = b"".join(int(x).to_bytes(2, "little") for x in (list(a) + [0, 0, 0, 0])[:4])
        c.poke("gRogueMPTestArg", data)
        c.poke("gRogueMPTestCommand", bytes([n]))
        step(4)

    def report(item=0):
        cmd(10, VAR_ROGUE_ACTIVE_POKEBLOCK, item)
        raw = c.peek("gRogueMPTestOut", 8)
        v = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, 8, 2)]
        return {"activePokeblock": v[0], "runActive": v[1], "roomType": v[2], "bagCount": v[3]}

    def press(b, rel=40):
        c.pressed = {b}
        step(4)
        c.pressed = set()
        step(rel)

    step(30)
    cmd(4, args.species)

    print("== Start an Adventure", flush=True)
    cmd(7, 2, 5, 9, 9)       # MAP_ROGUE_AREA_ADVENTURE_ENTRANCE
    step(180)
    shot("entrance")
    addr = syms["Rogue_StartAdventureInternal"]
    cmd(11, addr & 0xFFFF, addr >> 16)
    for i in range(30):
        press("A", 60)
        if i % 3 == 0:
            shot("starting")
        if report()["runActive"]:
            break
    step(600)
    shot("adventure_paths")
    st = report(args.pokeblock)
    print("   ", st, flush=True)
    check(st["runActive"] == 1, "an Adventure is active")

    print("== Enter a route", flush=True)
    cmd(9, ADVPATH_ROOM_ROUTE)
    step(600)
    for _ in range(6):
        press("B", 30)
    shot("route")
    st = report(args.pokeblock)
    print("   ", st, flush=True)
    check(st["roomType"] == ADVPATH_ROOM_ROUTE, "standing in a route room")
    check(st["activePokeblock"] == 0, "no Pokeblock active yet")
    cmd(8, args.pokeblock, 3)  # the hub bag is put away during an Adventure, so hand them out here
    before = report(args.pokeblock)["bagCount"]
    check(before == 3, "Pokeblocks are in the bag")

    print("== Scatter the Pokeblock from the bag", flush=True)
    press("START", 30)
    shot("start_menu")
    press("A", 120)          # Bag (first entry while on an Adventure)
    shot("bag")
    for i in range(args.pocket_left):
        press("LEFT", 30)
        shot(f"pocket{i}")
    press("A", 30)           # the Pokeblock
    shot("item_menu")
    press("A", 120)          # Use
    shot("used")
    for _ in range(8):
        press("A", 60)
        shot("message")
    for _ in range(3):
        press("B", 30)
    step(60)
    shot("after")
    st = report(args.pokeblock)
    print("   ", st, flush=True)
    check(st["activePokeblock"] == args.pokeblock, "the scattered Pokeblock is active on the route")
    check(st["bagCount"] == before - 1, "one Pokeblock was used up")

    print("RESULT:", "ALL PASSED" if not failures else f"{len(failures)} FAILED", flush=True)
    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    main()

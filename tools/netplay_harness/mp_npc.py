#!/usr/bin/env python3
"""
End to end check of the in-game flow: both players talk to the Multiplayer receptionist in the
PokeConnect building, one chooses Become Host and the other Join Group, exactly like players would.
Same arguments as mp_test.py (needs a ROGUE_MP_TEST build only for the warp hook).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mp_test import MPTest, CONNECTED  # noqa: E402


def warp(t, s, who, group, num, x, y):
    data = b"".join(v.to_bytes(2, "little") for v in (group, num, x, y))
    s.p[who].poke("gRogueMPTestArg", data)
    t.poke_cmd(s.p[who], 7)
    s.step(200)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", required=True)
    ap.add_argument("--rom", required=True)
    ap.add_argument("--map", required=True)
    ap.add_argument("--out", default="mp_test_out")
    ap.add_argument("--latency", type=int, default=4)
    ap.add_argument("--reboot", action="store_true")
    args = ap.parse_args()

    t = MPTest(args)
    s = t.new_session(args.latency)
    s.link.connect()
    s.step(30)

    # In front of the Multiplayer receptionist (MAP_ROGUE_INTERIOR_POKE_CONNECT, NPC at 25,2)
    warp(t, s, 0, 3, 4, 25, 3)
    warp(t, s, 1, 3, 4, 25, 3)
    k = [0, 0]

    def shot(who):
        s.p[who].screenshot(os.path.join(args.out, f"n{who}_{k[who]:02d}.png"))
        k[who] += 1

    def talk_to_receptionist(who, choose_host):
        s.press(who, "UP", 4, 20)
        for _ in range(14):                 # greeting, alpha warning, link cable explanation
            s.press(who, "A", 4, 60)
        shot(who)
        for _ in range(3):                  # save prompt: Yes, then saving
            s.press(who, "A", 4, 90)
            shot(who)
        s.step(300)
        shot(who)                           # "Please decide which of you will become the Group Host"
        s.press(who, "A", 4, 80)            # -> Join Group / Host Group / Exit
        shot(who)
        if choose_host:
            s.press(who, "DOWN", 4, 20)
        shot(who)
        s.press(who, "A", 4, 80)
        shot(who)

    talk_to_receptionist(0, True)
    s.step(200)
    shot(0)
    talk_to_receptionist(1, False)
    for _ in range(40):
        s.step(30)
        if all(t.link_state(p) == CONNECTED for p in s.p):
            break
    shot(0)
    shot(1)
    t.check(all(t.link_state(p) == CONNECTED for p in s.p), "connected through the PokeConnect receptionist")
    for who in (0, 1):
        for _ in range(3):
            s.press(who, "A", 4, 60)
            shot(who)
    s.step(300)
    t.compare_mirrors(s, "after connecting via the receptionist")
    shot(0)
    shot(1)
    print("RESULT:", "ALL PASSED" if not t.failures else f"{len(t.failures)} FAILED")
    sys.exit(0 if not t.failures else 1)


if __name__ == "__main__":
    main()

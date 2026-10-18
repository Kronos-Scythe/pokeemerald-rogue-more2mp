#!/usr/bin/env python3
"""
Player to player interaction over the link: the client walks up to the host's character and talks
to them. Both games should enter the talk script, then leave it cleanly when the trade is declined.
Same arguments as mp_test.py.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mp_test import MPTest, OFF_STATES, OFF_STATE_POS, SIZE_STATE, TEST_CMD_HOST, TEST_CMD_JOIN  # noqa: E402


def pos_of(t, viewer, pid):
    raw = t.mp_read(viewer, OFF_STATES + pid * SIZE_STATE + OFF_STATE_POS, 4)
    return int.from_bytes(raw[0:2], "little", signed=True), int.from_bytes(raw[2:4], "little", signed=True)


def walk_to(t, s, who, target):
    for _ in range(60):
        x, y = pos_of(t, s.p[who], who)
        if (x, y) == target:
            return True
        if x != target[0]:
            d = "RIGHT" if target[0] > x else "LEFT"
        else:
            d = "DOWN" if target[1] > y else "UP"
        s.hold(who, d, 16)
        s.step(4)
    return False


def in_field(t, p):
    t.poke_cmd(p, 10)  # test hook reports whether the game is in the overworld (gMain is in IWRAM)
    p.run_frame()
    return p.peek_u("gRogueMPTestOut", 2, 8) == 1


def give(t, s, who, species):
    s.p[who].poke("gRogueMPTestArg", species.to_bytes(2, "little"))
    t.poke_cmd(s.p[who], 4)
    s.step(10)


def party(t, s, who):
    t.poke_cmd(s.p[who], 5)
    s.step(4)
    raw = s.p[who].peek("gRogueMPTestOut", 12)
    return [int.from_bytes(raw[i:i + 2], "little") for i in range(0, 12, 2)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", required=True)
    ap.add_argument("--rom", required=True)
    ap.add_argument("--map", required=True)
    ap.add_argument("--out", default="mp_test_out")
    ap.add_argument("--latency", type=int, default=4)
    ap.add_argument("--reboot", action="store_true")
    ap.add_argument("--trade", action="store_true", help="give both players a Pokemon and trade them")
    ap.add_argument("--battle", action="store_true", help="host fights a wild battle while connected")
    args = ap.parse_args()

    t = MPTest(args)
    s = t.new_session(args.latency)
    s.link.connect()
    s.step(30)
    t.poke_cmd(s.p[0], TEST_CMD_HOST)
    t.poke_cmd(s.p[1], TEST_CMD_JOIN)
    t.check(t.wait_connected(s) is not None, "connected")
    s.step(120)

    if args.battle:
        give(t, s, 0, 25)
        s.p[0].poke("gRogueMPTestArg", (19).to_bytes(2, "little"))  # Rattata
        t.poke_cmd(s.p[0], 6)
        for i in range(40):
            s.press(0, "B", 4, 40)
            if i % 4 == 0:
                s.shots(f"b0_battle_{i:02d}")
        # Run: bottom right of the action menu
        for _ in range(3):
            s.press(0, "DOWN", 4, 10)
            s.press(0, "RIGHT", 4, 10)
            s.press(0, "A", 4, 60)
            for _ in range(5):
                s.press(0, "B", 4, 30)
        s.step(600)
        s.shots("b1_after_battle")
        st = [t.link_state(p) for p in s.p]
        print("    link states after battle:", st, [t.link_stats(p)["reconnects"] for p in s.p])
        t.check(t.wait_connected(s, 3600) is not None, "connected after the host's battle")
        s.step(300)
        t.compare_mirrors(s, "after battle")

    # Give the host some room, then put the client on the tile to the host's left
    s.hold(0, "RIGHT", 48)
    s.step(60)
    hx, hy = pos_of(t, s.p[0], 0)
    print(f"    host at {(hx, hy)}, client at {pos_of(t, s.p[1], 1)}")
    t.check(walk_to(t, s, 1, (hx - 1, hy)), "client walked next to the host")
    s.hold(1, "RIGHT", 4)   # face the host
    s.step(30)
    s.shots("i0_adjacent")

    if args.trade:
        give(t, s, 0, 25)    # host: Pikachu
        give(t, s, 1, 133)   # client: Eevee
        print("    parties before:", party(t, s, 0), party(t, s, 1))

    s.press(1, "A", 4, 90)  # talk
    s.step(120)
    s.shots("i1_talking")
    s.press(1, "A", 4, 60)  # client advances greeting
    s.step(60)
    s.shots("i2_trade_prompt")

    if args.trade:
        s.press(1, "A", 4, 90)   # YES, trade
        # Advance both games until the Pokemon have changed hands (that happens during the trade scene)
        for i in range(120):
            if party(t, s, 0)[0] == 133 and party(t, s, 1)[0] == 25:
                break
            s.press(i % 2, "A", 4, 40)
            if i % 10 == 0:
                s.shots(f"i2_trade_{i:03d}")
        # The trade animation is long; press A ("Take good care of ..." only takes A) for whoever is still
        # in the trade scene until both games are back in the overworld. Pressing A in the field would
        # talk to the other player again and start another trade.
        for i in range(120):
            fields = [in_field(t, p) for p in s.p]
            if all(fields):
                break
            for who in (0, 1):
                if not fields[who]:
                    s.p[who].pressed = {"A"}
            s.step(4)
            for p in s.p:
                p.pressed = set()
            s.step(40)
        t.check(all(in_field(t, p) for p in s.p), "both games returned to the overworld after the trade")
        s.step(300)
        before_after = (party(t, s, 0), party(t, s, 1))
        print("    parties after:", before_after)
        t.check(before_after[0][0] == 133 and before_after[1][0] == 25, "Pokemon were swapped by the trade")
    else:
        s.press(1, "DOWN", 4, 20)  # NO
        s.press(1, "A", 4, 60)
        for _ in range(6):
            s.press(1, "A", 4, 30)
            s.press(0, "A", 4, 30)
        s.step(120)
        for _ in range(3):
            s.press(0, "A", 4, 40)  # host closes "Bye for now!"
    s.step(200)
    s.shots("i3_done")

    # Both should be free to move again
    before = [pos_of(t, s.p[i], i) for i in (0, 1)]
    s.hold(0, "UP", 32)
    s.hold(1, "DOWN", 32)
    s.step(60)
    after = [pos_of(t, s.p[i], i) for i in (0, 1)]
    print(f"    positions before {before} after {after}")
    t.check(before[0] != after[0] and before[1] != after[1], "both players can move after the conversation")
    t.compare_mirrors(s, "after interaction")
    s.shots("i4_after")
    print("RESULT:", "ALL PASSED" if not t.failures else f"{len(t.failures)} FAILED")
    sys.exit(0 if not t.failures else 1)


if __name__ == "__main__":
    main()

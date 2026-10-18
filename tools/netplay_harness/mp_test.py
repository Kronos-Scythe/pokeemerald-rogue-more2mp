#!/usr/bin/env python3
"""
Automated 2 player link cable multiplayer test for Emerald Rogue.

Needs a ROM built with ROGUE_MP_TEST defined (include/config/mp_test.h), its .map file and a
gpSP libretro core (https://github.com/libretro/gpsp, `make platform=unix`).

  python3 mp_test.py --core gpsp_libretro.so --rom pokeemerald.gba --map pokeemerald.map --out results/

Scenarios: connect, mirror equality (desync check), movement, latency, dropped packets,
full disconnect + automatic reconnect, client game reset + rejoin.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Session, Core, Link, load_symbols  # noqa: E402

# struct RogueNetMultiplayer layout (2 players), see include/rogue.h
OFF_PROFILES = 0
OFF_STATES = 80
OFF_GAMESTATE = 520
SIZE_PROFILE = 40
SIZE_STATE = 220
SIZE_GAMESTATE = 868
OFF_STATE_POS = 192

# struct MPLinkSession: stats (8 x u16) then state
LINK_STATS = ["seqGaps", "hashMismatches", "digestMismatches", "resyncRequestsSent",
              "resyncRequestsRecv", "reconnects", "msgsRecv", "msgsSent"]
STATE_NAMES = ["OFF", "LINKING", "HELLO", "SYNCING", "CONNECTED", "RECONNECTING"]
CONNECTED = 4

TEST_CMD_HOST, TEST_CMD_JOIN, TEST_CMD_CLOSE = 1, 2, 3


def boot_to_overworld(core_path, rom, workdir, syms):
    """Plays through the new game intro once and returns a savestate standing in the hub."""
    link = Link()
    c = Core(core_path, rom, 0, link, workdir, sym=syms)

    def step(n):
        for _ in range(n):
            c.run_frame()

    def press(b, hold=4, rel=20):
        c.pressed = {b}
        step(hold)
        c.pressed = set()
        step(rel)

    step(60 * 14)
    for i in range(1, 40):
        press("A" if i % 4 else "START")
        step(40)
    for _ in range(4):
        press("DOWN")
    for _ in range(3):
        press("A")
        step(60)
    for _ in range(24):
        press("A")
        step(30)
    for _ in range(24):
        press("A")
        step(20)
        press("A")
        step(20)
    for _ in range(5):
        press("DOWN")
    press("A")
    step(30)
    for _ in range(16):
        press("A")
        step(30)
    for _ in range(18):
        press("A")
        step(40)
    for _ in range(10):
        press("B")
        step(30)
    step(120)
    c.screenshot(os.path.join(workdir, "boot_overworld.png"))
    return c.save_state()


class MPTest:
    def __init__(self, args):
        self.args = args
        self.syms = load_symbols(args.map)
        for s in ("gRogueMultiplayer", "gRogueMPLink", "gRogueMPTestCommand"):
            assert s in self.syms, f"missing symbol {s} (build with ROGUE_MP_TEST)"
        os.makedirs(args.out, exist_ok=True)
        state_path = os.path.join(args.out, "overworld.state")
        if os.path.exists(state_path) and not args.reboot:
            self.state = open(state_path, "rb").read()
        else:
            self.state = boot_to_overworld(args.core, args.rom, args.out, self.syms)
            open(state_path, "wb").write(self.state)
        self.failures = []

    # --- helpers ---------------------------------------------------------
    def new_session(self, latency):
        s = Session(self.args.core, self.args.rom, sym=None, latency=latency, workdir=self.args.out)
        for p in s.p:
            p.sym = self.syms
            p.load_state(self.state)
        return s

    def link_state(self, p):
        return p.peek_u("gRogueMPLink", 1, 16)

    def link_stats(self, p):
        raw = p.peek("gRogueMPLink", 16)
        return {k: int.from_bytes(raw[i * 2:i * 2 + 2], "little") for i, k in enumerate(LINK_STATS)}

    def mp_base(self, p):
        return p.deref("gRogueMultiplayer")

    def mp_read(self, p, off, n):
        base = self.mp_base(p)
        if base == 0:
            return None
        return p.ewram_read(base + off, n)

    def wait_connected(self, s, max_frames=2400):
        for f in range(0, max_frames, 10):
            s.step(10)
            if all(self.link_state(p) == CONNECTED for p in s.p):
                return f + 10
        return None

    def check(self, cond, msg):
        print(("  PASS " if cond else "  FAIL ") + msg)
        if not cond:
            self.failures.append(msg)

    def compare_mirrors(self, s, label):
        """Every mirrored region must be byte identical to the owner's copy."""
        host, client = s.p
        ok = True
        for owner, other, pid in ((host, client, 0), (client, host, 1)):
            for name, off, size in (("profile", OFF_PROFILES + pid * SIZE_PROFILE, SIZE_PROFILE),
                                    ("playerState", OFF_STATES + pid * SIZE_STATE, SIZE_STATE)):
                a = self.mp_read(owner, off, size)
                b = self.mp_read(other, off, size)
                same = a is not None and a == b
                if not same:
                    diff = [i for i in range(size) if a is None or b is None or a[i] != b[i]][:8]
                    print(f"    {name}[{pid}] differs at {diff}")
                ok &= same
        a = self.mp_read(host, OFF_GAMESTATE, SIZE_GAMESTATE)
        b = self.mp_read(client, OFF_GAMESTATE, SIZE_GAMESTATE)
        if a != b:
            print("    gameState differs at", [i for i in range(SIZE_GAMESTATE) if a[i] != b[i]][:8])
        ok &= (a == b)
        self.check(ok, f"{label}: mirrors identical on both games")
        return ok

    def poke_cmd(self, p, cmd):
        p.poke("gRogueMPTestCommand", bytes([cmd]))

    def report(self, s, label):
        for i, p in enumerate(s.p):
            print(f"    p{i} state={STATE_NAMES[self.link_state(p)]} stats={self.link_stats(p)}")
        s.shots(label)

    # --- scenarios -------------------------------------------------------
    def run(self):
        lat = self.args.latency
        print(f"== Connect (latency {lat} frames)")
        s = self.new_session(lat)
        s.link.connect()
        s.step(30)
        self.poke_cmd(s.p[0], TEST_CMD_HOST)
        self.poke_cmd(s.p[1], TEST_CMD_JOIN)
        frames = self.wait_connected(s)
        self.check(frames is not None, f"both games connected (after {frames} frames)")
        s.step(240)
        self.report(s, "01_connected")
        self.compare_mirrors(s, "idle")

        print("== Movement")
        s.hold(0, "RIGHT", 40)
        s.hold(0, "DOWN", 24)
        s.hold(1, "LEFT", 32)
        s.step(180)
        hp = self.mp_read(s.p[0], OFF_STATES + OFF_STATE_POS, 4)
        cp = self.mp_read(s.p[1], OFF_STATES + OFF_STATE_POS, 4)
        print(f"    host pos {hp.hex()} client's view {cp.hex()}")
        self.compare_mirrors(s, "after movement")
        self.report(s, "02_moved")

        print("== Dropped packets")
        before = self.link_stats(s.p[1])
        s.link.drop_next(0, 3)
        s.hold(0, "LEFT", 32)
        s.step(300)
        after = self.link_stats(s.p[1])
        self.check(after["seqGaps"] > before["seqGaps"], "client noticed the dropped messages")
        self.compare_mirrors(s, "after dropped packets (resynced)")

        print("== Disconnect and automatic reconnect")
        s.link.disconnect()
        s.step(600)
        self.report(s, "03_disconnected")
        self.check(all(self.link_state(p) != CONNECTED for p in s.p), "both games noticed the disconnect")
        s.link.connect()
        frames = self.wait_connected(s, 3600)
        self.check(frames is not None, f"both games reconnected on their own (after {frames} frames)")
        s.step(300)
        self.report(s, "04_reconnected")
        self.compare_mirrors(s, "after reconnect")
        self.check(all(self.link_stats(p)["reconnects"] >= 1 for p in s.p), "reconnect counted on both")

        print("== Client resets the game and joins again")
        s.p[1].load_state(self.state)  # back to before multiplayer was started
        s.step(60)
        self.poke_cmd(s.p[1], TEST_CMD_JOIN)
        frames = self.wait_connected(s, 3600)
        self.check(frames is not None, f"client rejoined after a reset (after {frames} frames)")
        s.step(300)
        self.report(s, "05_rejoined")
        self.compare_mirrors(s, "after rejoin")

        print("== Speed mismatch (host running at 2x, like fast-forward)")
        for _ in range(600):
            s.p[0].run_frame()
            s.p[0].run_frame()
            s.p[1].run_frame()
            s.link.frame += 1
        s.step(600)
        frames = self.wait_connected(s, 3600)
        self.check(frames is not None, "connected after a period of mismatched speeds")
        s.step(300)
        self.report(s, "05b_speed")
        self.compare_mirrors(s, "after speed mismatch")

        print("== Saving the game while connected")
        for who in (0, 1):
            before = self.link_stats(s.p[who])["reconnects"]
            s.press(who, "START", 4, 20)
            s.press(who, "DOWN", 4, 10)
            s.press(who, "DOWN", 4, 10)
            s.press(who, "A", 4, 40)      # Save
            s.press(who, "A", 4, 60)      # Yes
            s.press(who, "A", 4, 60)      # Overwrite (if asked)
            s.step(300)
            s.press(who, "B", 4, 30)
            s.press(who, "B", 4, 30)
            s.step(120)
            s.shots(f"05c_saved_p{who}")
            reconnects = self.link_stats(s.p[who])["reconnects"] - before
            print(f"    p{who} reconnects during save: {reconnects}")
        frames = self.wait_connected(s, 3600)
        self.check(frames is not None, "still connected after both players saved")
        s.step(300)
        self.compare_mirrors(s, "after saving")

        print("== Long soak")
        for i in range(10):
            s.hold(i % 2, ["RIGHT", "LEFT", "UP", "DOWN"][i % 4], 32)
            s.step(30)
        s.step(300)
        self.compare_mirrors(s, "after soak")
        totals = [self.link_stats(p) for p in s.p]
        self.check(all(t["hashMismatches"] == 0 for t in totals), "no hash mismatches outside the dropped packet test")
        self.report(s, "06_soak")

        print()
        print("RESULT:", "ALL PASSED" if not self.failures else f"{len(self.failures)} FAILED")
        for f in self.failures:
            print("  -", f)
        return not self.failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", required=True)
    ap.add_argument("--rom", required=True)
    ap.add_argument("--map", required=True)
    ap.add_argument("--out", default="mp_test_out")
    ap.add_argument("--latency", type=int, default=4)
    ap.add_argument("--reboot", action="store_true", help="replay the intro even if a savestate exists")
    args = ap.parse_args()
    ok = MPTest(args).run()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

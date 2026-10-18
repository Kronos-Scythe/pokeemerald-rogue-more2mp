#!/usr/bin/env python3
"""
Link cable co-op test through real RetroArch netplay (two headless RetroArch processes on localhost).

Each instance uses its own config dir with: null video/audio, network commands enabled
(READ/WRITE_CORE_MEMORY), the network remote gamepad for button presses and gpsp_serial = "mul_poke".
Savestates can't be loaded while netplay is running, so both boot from a save file standing in the hub
and press A through the title screen (Continue). Needs a ROGUE_MP_TEST ROM + .map.

  retroarch_test.py --core gpsp_libretro.so --rom rogue.gba --map rogue.map --srm rogue.srm --work dir [--soak 600]
"""
import argparse
import os
import shutil
import socket
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import load_symbols  # noqa: E402
from mp_test import (OFF_PROFILES, OFF_STATES, OFF_GAMESTATE, SIZE_PROFILE, SIZE_STATE, SIZE_GAMESTATE,  # noqa: E402
                     OFF_STATE_POS, LINK_STATS, STATE_NAMES, CONNECTED)

BTN = {"B": 0, "Y": 1, "SELECT": 2, "START": 3, "UP": 4, "DOWN": 5, "LEFT": 6, "RIGHT": 7, "A": 8}

CFG = """video_driver = "null"
audio_driver = "null"
input_driver = "null"
menu_driver = "null"
savestate_directory = "{d}/states"
savefile_directory = "{d}/saves"
system_directory = "{d}/sys"
savestate_auto_load = "false"
savestate_auto_save = "false"
sort_savestates_enable = "false"
sort_savefiles_enable = "false"
network_cmd_enable = "true"
network_cmd_port = "{cmd}"
network_remote_enable = "true"
network_remote_base_port = "{remote}"
network_remote_enable_user_p1 = "true"
netplay_nickname = "player{i}"
netplay_ip_port = "{np}"
netplay_public_announce = "false"
netplay_use_mitm_server = "false"
core_options_path = "{d}/opts.cfg"
global_core_options = "true"
config_save_on_exit = "false"
pause_nonactive = "false"
fastforward_ratio = "1.0"
"""


class RA:
    def __init__(self, i, args, syms, host):
        self.i, self.args, self.syms, self.host = i, args, syms, host
        self.dir = os.path.join(args.work, f"p{i}")
        for sub in ("states", "saves", "sys"):
            os.makedirs(os.path.join(self.dir, sub), exist_ok=True)
        self.cmd_port = 55350 + i
        self.remote_port = 55400 + i * 10
        rom_base = os.path.splitext(os.path.basename(args.rom))[0]
        # Boot from a real save file and go through the title screen ("Continue")
        shutil.copy(args.srm, os.path.join(self.dir, "saves", rom_base + ".srm"))
        with open(os.path.join(self.dir, "retroarch.cfg"), "w") as f:
            f.write(CFG.format(d=self.dir, cmd=self.cmd_port, remote=self.remote_port, i=i, np=args.port))
        with open(os.path.join(self.dir, "opts.cfg"), "w") as f:
            f.write('gpsp_serial = "mul_poke"\n')
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(2)
        self.proc = None

    def start(self):
        net = ["--host"] if self.host else ["--connect", "127.0.0.1"]
        self.log = open(os.path.join(self.dir, "retroarch.log"), "a")
        self.proc = subprocess.Popen(
            ["retroarch", "--config", os.path.join(self.dir, "retroarch.cfg"), "-L", self.args.core,
             self.args.rom, "--port", str(self.args.port), "--verbose"] + net,
            stdout=self.log, stderr=subprocess.STDOUT)
        self.started = time.time()

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None

    def command(self, text):
        self.sock.sendto(text.encode(), ("127.0.0.1", self.cmd_port))
        return self.sock.recvfrom(65536)[0].decode().strip()

    def read(self, addr, n):
        out = b""
        while n > 0:
            chunk = min(n, 256)
            reply = self.command(f"READ_CORE_MEMORY {addr:x} {chunk}").split()
            if len(reply) < 3 or reply[2] == "-1":
                raise IOError(f"read failed: {reply}")
            out += bytes(int(x, 16) for x in reply[2:])
            addr += chunk
            n -= chunk
        return out

    def write(self, addr, data):
        self.command(f"WRITE_CORE_MEMORY {addr:x} " + " ".join(f"{b:02x}" for b in data))

    def sym(self, name):
        return self.syms[name]

    def link_state(self):
        return self.read(self.sym("gRogueMPLink") + 16, 1)[0]

    def link_stats(self):
        raw = self.read(self.sym("gRogueMPLink"), 20)
        d = {k: struct.unpack_from("<H", raw, i * 2)[0] for i, k in enumerate(LINK_STATS)}
        d["lastLoss"] = raw[17]
        d["linkErrors"] = hex(raw[18])
        d["checksumErrors"] = raw[19]
        d["realigns"] = struct.unpack("<H", self.read(self.sym("gLinkSlaveRealigns"), 2))[0]
        return d

    def mp(self, off, n):
        base = struct.unpack("<I", self.read(self.sym("gRogueMultiplayer"), 4))[0]
        return None if base == 0 else self.read(base + off, n)

    def test_cmd(self, cmd, *args):
        data = b"".join(int(a).to_bytes(2, "little") for a in (list(args) + [0, 0, 0, 0])[:4])
        self.write(self.sym("gRogueMPTestArg"), data)
        self.write(self.sym("gRogueMPTestCommand"), bytes([cmd]))

    def button(self, name, down):
        # struct remote_message { int port; int device; int index; int id; uint16_t state; }
        msg = struct.pack("<iiiiHxx", 0, 1, 0, BTN[name], 1 if down else 0)  # padded to sizeof(struct remote_message)
        self.sock.sendto(msg, ("127.0.0.1", self.remote_port))

    def hold(self, name, seconds):
        self.button(name, True)
        time.sleep(seconds)
        self.button(name, False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", required=True)
    ap.add_argument("--rom", required=True)
    ap.add_argument("--map", required=True)
    ap.add_argument("--srm", required=True, help="save file standing in the hub (128K flash)")
    ap.add_argument("--work", default="retroarch_test")
    ap.add_argument("--port", type=int, default=55435)
    ap.add_argument("--soak", type=int, default=60, help="seconds of wandering around while connected")
    args = ap.parse_args()
    os.makedirs(args.work, exist_ok=True)
    syms = load_symbols(args.map)
    failures = []

    def check(cond, msg):
        print(("  PASS " if cond else "  FAIL ") + msg, flush=True)
        if not cond:
            failures.append(msg)

    host, client = RA(0, args, syms, True), RA(1, args, syms, False)

    def report():
        for p in (host, client):
            try:
                print(f"    p{p.i} state={STATE_NAMES[p.link_state()]} stats={p.link_stats()}", flush=True)
            except Exception as e:
                print(f"    p{p.i} unreadable: {e}", flush=True)

    def in_field(p):
        cb2 = struct.unpack("<I", p.read(syms["gMain"] + 4, 4))[0]
        return (cb2 & ~1) == syms["CB2_Overworld"]

    def continue_game(p):
        time.sleep(max(0, 16 - (time.time() - p.started)))
        for _ in range(12):
            if in_field(p):
                time.sleep(2)
                p.hold("B", 0.1)
                return True
            p.hold("A", 0.1)
            time.sleep(1.5)
        return in_field(p)

    def wait_connected(timeout):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if host.link_state() == CONNECTED and client.link_state() == CONNECTED:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def mirrors_equal(label):
        time.sleep(4)
        ok = True
        for owner, other, pid in ((host, client, 0), (client, host, 1)):
            for name, off, size in (("profile", OFF_PROFILES + pid * SIZE_PROFILE, SIZE_PROFILE),
                                    ("playerState", OFF_STATES + pid * SIZE_STATE, SIZE_STATE)):
                a, b = owner.mp(off, size), other.mp(off, size)
                if a != b:
                    # The owner may have moved between the two reads; retry once
                    time.sleep(2)
                    a, b = owner.mp(off, size), other.mp(off, size)
                if a != b:
                    print(f"    {name}[{pid}] differs", flush=True)
                    ok = False
        a, b = host.mp(OFF_GAMESTATE, SIZE_GAMESTATE), client.mp(OFF_GAMESTATE, SIZE_GAMESTATE)
        if a != b:
            print("    gameState differs", flush=True)
            ok = False
        check(ok, f"{label}: both games hold identical copies of each other's data")

    try:
        print("== Starting RetroArch netplay host and client", flush=True)
        host.start()
        time.sleep(4)
        client.start()
        time.sleep(8)
        print("   ", host.command("GET_STATUS"), "|", client.command("GET_STATUS"), flush=True)
        for p in (host, client):
            check(continue_game(p), f"p{p.i} loaded the save into the field (title screen -> Continue)")

        host.test_cmd(1)    # in-game: Host Group
        client.test_cmd(2)  # in-game: Join Group
        check(wait_connected(60), "connected over RetroArch netplay")
        report()
        mirrors_equal("idle")

        print("== Movement", flush=True)
        host.hold("RIGHT", 0.6)
        client.hold("LEFT", 0.5)
        host.hold("DOWN", 0.4)
        hp = host.mp(OFF_STATES + OFF_STATE_POS, 4)
        time.sleep(2)
        print(f"    host pos {hp.hex()} seen by client {client.mp(OFF_STATES + OFF_STATE_POS, 4).hex()}", flush=True)
        mirrors_equal("after movement")

        print(f"== Soak ({args.soak} seconds of wandering)", flush=True)
        dirs = ["RIGHT", "LEFT", "UP", "DOWN"]
        for k in range(args.soak // 2):
            (host if k % 2 == 0 else client).hold(dirs[k % 4], 0.5)
            time.sleep(1.5)
        report()
        mirrors_equal("after soak")
        stats = [host.link_stats(), client.link_stats()]
        check(all(s["reconnects"] == 0 for s in stats), "the connection never dropped during the soak")

        print("== Client RetroArch closes and reconnects (rejoin)", flush=True)
        client.stop()
        time.sleep(10)
        report()
        check(host.link_state() != CONNECTED, "host noticed the client left")
        client.start()
        check(continue_game(client), "restarted client loaded its save")
        client.test_cmd(2)
        check(wait_connected(90), "client rejoined after restarting RetroArch")
        report()
        mirrors_equal("after rejoin")
    finally:
        host.stop()
        client.stop()

    print("RESULT:", "ALL PASSED" if not failures else f"{len(failures)} FAILED", flush=True)
    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    main()

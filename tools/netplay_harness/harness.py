#!/usr/bin/env python3
"""
Minimal libretro frontend used to test link-cable multiplayer without RetroArch.

Loads two independent copies of a libretro core (e.g. gpSP) in one process and
wires their netpacket interfaces together, emulating what RetroArch netplay does
for cores that use RETRO_ENVIRONMENT_SET_NETPACKET_INTERFACE (gpSP "Link Cable -
Pokemon Gen3 mode"). Instance 0 is the netplay host (link master), instance 1 the
client.

Features: scripted input, configurable packet latency, link drop/restore
(to test disconnect/rejoin), RAM peeks by symbol (pokeemerald.sym), PNG screenshots.
"""
import ctypes as C
import os
import shutil
import struct
import tempfile
import zlib
from collections import deque

RETRO_ENVIRONMENT_GET_CAN_DUPE = 3
RETRO_ENVIRONMENT_SET_PERFORMANCE_LEVEL = 8
RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY = 9
RETRO_ENVIRONMENT_SET_PIXEL_FORMAT = 10
RETRO_ENVIRONMENT_SET_INPUT_DESCRIPTORS = 11
RETRO_ENVIRONMENT_GET_VARIABLE = 15
RETRO_ENVIRONMENT_SET_VARIABLES = 16
RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE = 17
RETRO_ENVIRONMENT_GET_LOG_INTERFACE = 27
RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY = 31
RETRO_ENVIRONMENT_GET_CORE_OPTIONS_VERSION = 52
RETRO_ENVIRONMENT_SET_NETPACKET_INTERFACE = 78
RETRO_ENVIRONMENT_EXPERIMENTAL = 0x10000

RETRO_MEMORY_SAVE_RAM = 0
RETRO_MEMORY_SYSTEM_RAM = 2

# RetroPad ids
BTN = {
    "B": 0, "Y": 1, "SELECT": 2, "START": 3, "UP": 4, "DOWN": 5, "LEFT": 6,
    "RIGHT": 7, "A": 8, "X": 9, "L": 10, "R": 11,
}

ENV_CB = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
VIDEO_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
AUDIO_CB = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
AUDIO_BATCH_CB = C.CFUNCTYPE(C.c_size_t, C.c_void_p, C.c_size_t)
INPUT_POLL_CB = C.CFUNCTYPE(None)
INPUT_STATE_CB = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)
LOG_CB = C.CFUNCTYPE(None, C.c_int, C.c_char_p)  # varargs ignored

NP_SEND = C.CFUNCTYPE(None, C.c_int, C.c_void_p, C.c_size_t, C.c_uint16)
NP_POLL_RECV = C.CFUNCTYPE(None)
NP_START = C.CFUNCTYPE(None, C.c_uint16, NP_SEND, NP_POLL_RECV)
NP_RECEIVE = C.CFUNCTYPE(None, C.c_void_p, C.c_size_t, C.c_uint16)
NP_STOP = C.CFUNCTYPE(None)
NP_POLL = C.CFUNCTYPE(None)
NP_CONNECTED = C.CFUNCTYPE(C.c_bool, C.c_uint16)
NP_DISCONNECTED = C.CFUNCTYPE(None, C.c_uint16)


class NetpacketCallback(C.Structure):
    _fields_ = [
        ("start", NP_START),
        ("receive", NP_RECEIVE),
        ("stop", NP_STOP),
        ("poll", NP_POLL),
        ("connected", NP_CONNECTED),
        ("disconnected", NP_DISCONNECTED),
        ("protocol_version", C.c_char_p),
    ]


class RetroVariable(C.Structure):
    _fields_ = [("key", C.c_char_p), ("value", C.c_char_p)]


class RetroGameInfo(C.Structure):
    _fields_ = [("path", C.c_char_p), ("data", C.c_void_p), ("size", C.c_size_t), ("meta", C.c_char_p)]


class LogCallback(C.Structure):
    _fields_ = [("log", C.c_void_p)]


# Variadic log callback can't be expressed in ctypes; use a tiny C shim-free trick:
# hand the core a function with the fixed (level, fmt) prefix and ignore the rest.
_log_fn = LOG_CB(lambda level, fmt: None)


def write_png(path, width, height, rgb_rows):
    raw = b"".join(b"\x00" + row for row in rgb_rows)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


class Link:
    """In-process stand-in for RetroArch's netplay packet relay."""

    def __init__(self, latency_frames=2):
        self.latency = latency_frames
        self.instances = []
        self.queues = {}  # dest id -> deque of (deliver_frame, src_id, bytes)
        self.frame = 0
        self.up = False
        self.stats = {"sent": 0, "delivered": 0, "dropped": 0}
        self.drop = {}  # src id -> number of upcoming packets to drop

    def attach(self, inst):
        self.instances.append(inst)
        self.queues[inst.client_id] = deque()

    def send(self, src_id, dest_id, data):
        if not self.up:
            return
        self.stats["sent"] += 1
        # Only drop packets that carry game data (gpSP status-only packets are 8 bytes of payload)
        if self.drop.get(src_id, 0) > 0 and len(data) == 24 and (data[4] & 0x80):
            self.drop[src_id] -= 1
            self.stats["dropped"] += 1
            return
        targets = [i.client_id for i in self.instances if i.client_id != src_id] if dest_id == 0xFFFF else [dest_id]
        for t in targets:
            if t in self.queues:
                self.queues[t].append((self.frame + self.latency, src_id, data))

    def deliver(self, inst):
        q = self.queues[inst.client_id]
        while q and q[0][0] <= self.frame:
            _, src, data = q.popleft()
            if self.up:
                inst.np_receive(src, data)
                self.stats["delivered"] += 1

    def drop_next(self, src_id, count):
        """Silently lose the next `count` data packets sent by `src_id` (simulates a bad network)."""
        self.drop[src_id] = count

    def connect(self):
        """Start a netplay session (host = client id 0)."""
        self.up = True
        for inst in self.instances:
            inst.np_start()
        host = self.instances[0]
        for inst in self.instances[1:]:
            ok = host.np_connected(inst.client_id)
            assert ok, "core refused client"

    def disconnect(self):
        """Drop the session, like a player closing RetroArch / losing connection."""
        self.up = False
        for q in self.queues.values():
            q.clear()
        host = self.instances[0]
        for inst in self.instances[1:]:
            host.np_disconnected(inst.client_id)
        for inst in self.instances:
            inst.np_stop()


class Core:
    def __init__(self, core_path, rom_path, client_id, link, workdir, options=None, sram=None, sym=None):
        self.client_id = client_id
        self.link = link
        self.workdir = workdir
        self.options = {"gpsp_serial": "mul_poke", "gpsp_drc": "enabled", "gpsp_bios": "builtin"}
        if options:
            self.options.update(options)
        self._optbufs = {}
        self.sym = sym or {}
        # Each instance needs its own copy of the shared object so globals are separate.
        so_copy = os.path.join(workdir, f"core{client_id}.so")
        shutil.copy(core_path, so_copy)
        self.lib = C.CDLL(so_copy, mode=os.RTLD_LOCAL)
        self.np = None
        self.frame_rgb565 = None
        self.fb_w = self.fb_h = self.fb_pitch = 0
        self.pressed = set()
        self.frames = 0
        self.sysdir = os.path.join(workdir, f"sys{client_id}").encode()
        os.makedirs(self.sysdir, exist_ok=True)

        self._env = ENV_CB(self._environment)
        self._video = VIDEO_CB(self._video_refresh)
        self._audio = AUDIO_CB(lambda l, r: None)
        self._audio_batch = AUDIO_BATCH_CB(lambda data, frames: frames)
        self._poll = INPUT_POLL_CB(lambda: None)
        self._state = INPUT_STATE_CB(self._input_state)
        self._send = NP_SEND(self._np_send)
        self._poll_recv = NP_POLL_RECV(self._np_poll_receive)

        L = self.lib
        L.retro_set_environment.argtypes = [ENV_CB]
        L.retro_set_video_refresh.argtypes = [VIDEO_CB]
        L.retro_set_audio_sample.argtypes = [AUDIO_CB]
        L.retro_set_audio_sample_batch.argtypes = [AUDIO_BATCH_CB]
        L.retro_set_input_poll.argtypes = [INPUT_POLL_CB]
        L.retro_set_input_state.argtypes = [INPUT_STATE_CB]
        L.retro_load_game.argtypes = [C.POINTER(RetroGameInfo)]
        L.retro_load_game.restype = C.c_bool
        L.retro_get_memory_data.argtypes = [C.c_uint]
        L.retro_get_memory_data.restype = C.c_void_p
        L.retro_get_memory_size.argtypes = [C.c_uint]
        L.retro_get_memory_size.restype = C.c_size_t
        L.retro_serialize_size.restype = C.c_size_t
        L.retro_serialize.argtypes = [C.c_void_p, C.c_size_t]
        L.retro_serialize.restype = C.c_bool
        L.retro_unserialize.argtypes = [C.c_void_p, C.c_size_t]
        L.retro_unserialize.restype = C.c_bool

        L.retro_set_environment(self._env)
        L.retro_set_video_refresh(self._video)
        L.retro_set_audio_sample(self._audio)
        L.retro_set_audio_sample_batch(self._audio_batch)
        L.retro_set_input_poll(self._poll)
        L.retro_set_input_state(self._state)
        L.retro_init()

        self._rom_path = rom_path.encode()
        with open(rom_path, "rb") as f:
            self._rom = f.read()
        self._rom_buf = C.create_string_buffer(self._rom, len(self._rom))
        info = RetroGameInfo(self._rom_path, C.cast(self._rom_buf, C.c_void_p), len(self._rom), None)
        assert L.retro_load_game(C.byref(info)), "retro_load_game failed"
        if sram is not None:
            self.load_sram(sram)
        link.attach(self)

    # --- libretro callbacks -------------------------------------------------
    def _environment(self, cmd, data):
        cmd &= ~RETRO_ENVIRONMENT_EXPERIMENTAL
        if cmd == RETRO_ENVIRONMENT_SET_PIXEL_FORMAT:
            return C.cast(data, C.POINTER(C.c_int))[0] == 2  # RGB565
        if cmd in (RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY, RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY):
            C.cast(data, C.POINTER(C.c_char_p))[0] = self.sysdir
            return True
        if cmd == RETRO_ENVIRONMENT_GET_VARIABLE:
            var = C.cast(data, C.POINTER(RetroVariable))[0]
            key = var.key.decode()
            if key in self.options:
                buf = self._optbufs.setdefault(key, C.create_string_buffer(self.options[key].encode()))
                var.value = C.cast(buf, C.c_char_p)
                C.cast(data, C.POINTER(RetroVariable))[0] = var
                return True
            return False
        if cmd == RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE:
            C.cast(data, C.POINTER(C.c_bool))[0] = False
            return True
        if cmd == RETRO_ENVIRONMENT_SET_NETPACKET_INTERFACE:
            self.np = C.cast(data, C.POINTER(NetpacketCallback))[0]
            return True
        if cmd == RETRO_ENVIRONMENT_GET_CAN_DUPE:
            C.cast(data, C.POINTER(C.c_bool))[0] = True
            return True
        if cmd == RETRO_ENVIRONMENT_GET_CORE_OPTIONS_VERSION:
            C.cast(data, C.POINTER(C.c_uint))[0] = 0
            return True
        if cmd in (RETRO_ENVIRONMENT_SET_PERFORMANCE_LEVEL, RETRO_ENVIRONMENT_SET_INPUT_DESCRIPTORS,
                   RETRO_ENVIRONMENT_SET_VARIABLES):
            return True
        return False

    def _video_refresh(self, data, w, h, pitch):
        if data:
            self.fb_w, self.fb_h, self.fb_pitch = w, h, pitch
            self.frame_rgb565 = C.string_at(data, pitch * h)

    def _input_state(self, port, device, index, id_):
        if port != 0:
            return 0
        if id_ == 256:  # RETRO_DEVICE_ID_JOYPAD_MASK
            m = 0
            for b in self.pressed:
                m |= 1 << BTN[b]
            return m
        for b in self.pressed:
            if BTN[b] == id_:
                return 1
        return 0

    # --- netpacket plumbing -------------------------------------------------
    def _np_send(self, flags, buf, length, dest):
        self.link.send(self.client_id, dest, C.string_at(buf, length))

    def _np_poll_receive(self):
        self.link.deliver(self)

    def np_start(self):
        if self.np:
            self.np.start(self.client_id, self._send, self._poll_recv)

    def np_stop(self):
        if self.np and self.np.stop:
            self.np.stop()

    def np_connected(self, cid):
        return bool(self.np.connected(cid)) if self.np and self.np.connected else True

    def np_disconnected(self, cid):
        if self.np and self.np.disconnected:
            self.np.disconnected(cid)

    def np_receive(self, src, data):
        buf = C.create_string_buffer(data, len(data))
        self.np.receive(C.cast(buf, C.c_void_p), len(data), src)

    # --- helpers ------------------------------------------------------------
    def run_frame(self):
        if self.link.up:
            self.link.deliver(self)
        self.lib.retro_run()
        self.frames += 1

    def mem(self, kind=RETRO_MEMORY_SYSTEM_RAM):
        ptr = self.lib.retro_get_memory_data(kind)
        size = self.lib.retro_get_memory_size(kind)
        return ptr, size

    def load_sram(self, data):
        ptr, size = self.mem(RETRO_MEMORY_SAVE_RAM)
        C.memmove(ptr, data, min(size, len(data)))

    def sram(self):
        ptr, size = self.mem(RETRO_MEMORY_SAVE_RAM)
        return C.string_at(ptr, size)

    def ewram_read(self, addr, n):
        """Read n bytes at a GBA EWRAM address (0x02xxxxxx)."""
        ptr, size = self.mem(RETRO_MEMORY_SYSTEM_RAM)
        off = addr - 0x02000000
        assert 0 <= off and off + n <= size, hex(addr)
        return C.string_at(ptr + off, n)

    def peek(self, name, n=4, offset=0):
        addr = self.sym[name] + offset
        return self.ewram_read(addr, n)

    def peek_u(self, name, n=4, offset=0):
        return int.from_bytes(self.peek(name, n, offset), "little")

    def poke(self, name, data, offset=0):
        ptr, size = self.mem(RETRO_MEMORY_SYSTEM_RAM)
        off = self.sym[name] + offset - 0x02000000
        C.memmove(ptr + off, data, len(data))

    def deref(self, name):
        """Value of a pointer variable."""
        return self.peek_u(name, 4)

    def screenshot(self, path):
        if not self.frame_rgb565:
            return
        rows = []
        for y in range(self.fb_h):
            line = self.frame_rgb565[y * self.fb_pitch:y * self.fb_pitch + self.fb_w * 2]
            out = bytearray()
            for (px,) in struct.iter_unpack("<H", line):
                out += bytes((((px >> 11) & 31) << 3, ((px >> 5) & 63) << 2, (px & 31) << 3))
            rows.append(bytes(out))
        write_png(path, self.fb_w, self.fb_h, rows)

    def save_state(self):
        n = self.lib.retro_serialize_size()
        buf = C.create_string_buffer(n)
        assert self.lib.retro_serialize(buf, n)
        return buf.raw

    def load_state(self, data):
        buf = C.create_string_buffer(data, len(data))
        assert self.lib.retro_unserialize(buf, len(data))


def load_symbols(path):
    """Reads symbols from a .sym file or a linker .map file."""
    syms = {}
    if not os.path.exists(path):
        return syms
    with open(path) as f:
        for line in f:
            parts = line.split()
            try:
                if path.endswith(".map"):
                    # "                0x0203a000                gSymbolName"
                    if len(parts) == 2 and parts[0].startswith("0x") and parts[1].isidentifier():
                        syms[parts[1]] = int(parts[0], 16)
                elif len(parts) >= 4:
                    syms[parts[-1]] = int(parts[0], 16)
            except ValueError:
                pass
    return syms


class Session:
    """Two linked instances advanced in lockstep frames."""

    def __init__(self, core, rom, sym=None, latency=2, srams=(None, None), workdir=None):
        self.workdir = workdir or tempfile.mkdtemp(prefix="nphar")
        self.sym = load_symbols(sym) if sym else {}
        self.link = Link(latency)
        self.p = [Core(core, rom, i, self.link, self.workdir, sram=srams[i], sym=self.sym) for i in range(2)]

    def step(self, frames=1):
        for _ in range(frames):
            for inst in self.p:
                inst.run_frame()
            self.link.frame += 1

    def press(self, who, buttons, hold=6, release=6):
        if isinstance(buttons, str):
            buttons = [buttons]
        self.p[who].pressed = set(buttons)
        self.step(hold)
        self.p[who].pressed = set()
        self.step(release)

    def hold(self, who, buttons, frames):
        if isinstance(buttons, str):
            buttons = [buttons]
        self.p[who].pressed = set(buttons)
        self.step(frames)
        self.p[who].pressed = set()

    def shots(self, name):
        for i, inst in enumerate(self.p):
            inst.screenshot(os.path.join(self.workdir, f"{name}_p{i}.png"))

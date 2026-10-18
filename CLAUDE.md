# Emerald Rogue – link cable co-op fork

Fork of [Pokabbie/pokeemerald-rogue](https://github.com/Pokabbie/pokeemerald-rogue) (v2.2.1 EX) where
multiplayer runs over the **link cable** (RetroArch netplay, like Pokémon Quetzal) instead of the
Rogue Assistant app. 2 players: a host and a partner.

- **Only work in the fork** (`Kronos-Scythe/pokeemerald-rogue-more2mp`). Never push to or open PRs against
  `Pokabbie/pokeemerald-rogue`. GitHub's "Compare & pull request" button defaults to upstream – change the
  base repository to the fork.
- The owner plays **EX**. EX and Vanilla have different save layouts and the game doesn't check which one
  wrote a save, so a Vanilla ROM corrupts EX saves.

## Branches

| Branch | Based on | What |
|---|---|---|
| `claude/emerald-rogue-player-limit-ex` | upstream `expansion` (2.2.1) | **EX, link cable co-op – use this one** |
| `claude/emerald-rogue-player-limit-pnbwv9` | upstream `vanilla` (2.2.1) | Vanilla, old 4-player Rogue Assistant experiment |

Upstream branches: `expansion` = EX, `vanilla` = Vanilla. On the EX branch the Makefile defaults to
`EXPANSION := 1`.

## Building

The owner builds on Windows in WSL (Ubuntu) at `/mnt/g/github/pokeemerald-rogue-more2mp`.
Pokabbie's INSTALL.md route is preferred: Windows 10 WSL1 section + Installation section
(WSL1, agbcc installed into the repo, devkitARM via `dkp-pacman -S gba-dev`).

```bash
sudo apt install build-essential binutils-arm-none-eabi gcc-arm-none-eabi libnewlib-arm-none-eabi git libpng-dev unzip wget
git checkout claude/emerald-rogue-player-limit-ex
sh init_deps.sh          # downloads poryscript + ups tool
make tools
make RELEASE=1 -j4       # RELEASE=1 matches the official ROM; plain `make` is a debug build
```

Output: `pokeemerald.gba` (32 MB). Verified to build with exactly these commands on Ubuntu
(arm-none-eabi-gcc 13.2, no agbcc).

Iteration tip: once a ROM exists, every `make` re-runs the QueryBaker data bake (~20 min, single
threaded). It only bakes species/encounter data, so when you haven't touched that data you can skip it
with `GITHUB_REPOSITORY_OWNER=local make RELEASE=1 -j16` (the Makefile skips local-only tools on CI).

### Problems already hit and their fixes

- **`Temporary failure resolving ...` in apt/wget**: WSL has no DNS. `wsl --shutdown` from PowerShell; if
  that doesn't help, set `nameserver 8.8.8.8` in `/etc/resolv.conf` and
  `[network] generateResolvConf = false` in `/etc/wsl.conf`. Turn off any VPN.
- **`init_deps.sh` says "Skipping (Already exists)" but tools are missing**: a failed earlier run left empty
  folders. `rm -rf tools/poryscript tools/ups` and re-run.
- **`fatal: detected dubious ownership`**: `git config --global --add safe.directory /mnt/g/github/pokeemerald-rogue-more2mp`.
- **Checkout blocked by "local changes" to `.pory`/`.vcxproj`/`.cs` files**: line-ending noise from cloning
  with Windows git. `git stash`, then checkout.
- **`generated/quest_consts.h: No such file` in `make tools`**: happens on the Vanilla branch. Re-run
  `make tools`. The EX branch builds QueryBaker during the main `make` instead.
- **`No rule to make target 'src/string.h'`**: fixed on the EX branch (`#include <string.h>`); otherwise
  install agbcc into the repo per INSTALL.md.
- **`make: command not found`**: the apt install step was skipped.
- **`pkg-config: Permission denied`** during `make tools`: harmless.
- **`claude: command not found` after installing Claude Code**:
  `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc`.
- **QueryBaker "Baking Local Data..."** can print nothing for 20+ minutes. That's normal.
- The WSL user is `root` (`getpwuid(1000) failed` on start). Builds fine as root.
- Some `.pory` files use CRLF line endings (e.g. `data/scripts/Rogue/Rogue_Multiplayer.pory`); keep them
  CRLF when editing (Python text mode silently converts them).

## Playing co-op (RetroArch)

1. Both players: RetroArch 1.17+ with the **gpSP** core (Online Updater → Core Downloader →
   "Nintendo - Game Boy Advance (gpSP)"; update it, the Pokémon link mode is recent).
2. Both load the **same** ROM build (identical patch). Different builds refuse to connect.
3. Quick Menu → Core Options → **Link Cable Connectivity: "Link Cable - Pokemon Gen3 mode"**
   ("Automatic" also picks it for large Pokémon ROM hacks).
4. One player hosts netplay (Netplay → Host), the other connects. The netplay host does not have to be the
   in-game host.
5. In game, both go to the PokeConnect building and talk to the Multiplayer receptionist: one picks
   **Host Group**, the other **Join Group**.

Errors shown by the receptionist: different game version/patch, EX vs Vanilla, revision mode mismatch,
both picked the same option, partner on a different Adventure, lost connection to host.

## Sharing the ROM

Don't distribute the `.gba`. Make a patch with RomPatcher.js (https://www.marcrobledo.com/RomPatcher.js/):
"Create patch" with a clean Emerald ROM as original and the built `pokeemerald.gba` as modified (UPS/BPS).
Friends apply it to their own clean Emerald ROM.

## How the link co-op works

`src/rogue_multiplayer_link.c` (transport) + `src/rogue_multiplayer.c` (game logic, unchanged mostly).

- Uses the vanilla Emerald cable layer (`src/link.c`) in multiplayer mode. `OpenLink()` then our own
  `gLinkCallback` (so the vanilla player exchange never runs and `gReceivedRemoteLinkPlayers` stays 0),
  link error screens suppressed. One 8-halfword command per frame per player. Message ids
  `0x3A00 | type` (unused by vanilla `LINKCMD_*`), word 1 = sequence number | arg, 12 bytes payload.
- gpSP "Pokemon Gen3 mode": the netplay host is the link master; only non-zero commands are sent,
  reliably and in order; a peer is dropped after 240 silent frames; a client can only (re)join while the
  master is in handshake, so on loss both sides close and reopen the link.
- HELLO/WELCOME handshake: protocol version, build fingerprint (struct sizes + save version + version
  string), EX flag, save version, revision mode, host/join role, per-attempt nonce.
- Replicated regions: each side's `RogueNetPlayerProfile` and `RogueNetPlayer`, plus the host's
  `RogueNetGameState` (hub map, difficulty, time, adventure seed). Changes go as 10 byte CHUNKs then a
  COMMIT with a hash; the receiver stages chunks and applies only when the hash matches. Sequence gaps,
  hash mismatches and idle DIGEST mismatches request a full resend (RESYNC). Stats live at the start of
  `gRogueMPLink` (fixed offsets, read by the test harness).
- Lost partner (4 s silence or link error) → popup, partner hidden, link reopened after 1 s; partner rejoins
  the same session automatically. A client mid-adventure may only rejoin a host on the same adventure
  (seed check). Client gives up after 2 minutes; host keeps waiting.
- Remote players: remote slot 0 uses the existing net player gfx/palette 8 and follow/ride mon. (Remote
  slots 1–2 from the old 4-player work remain in code but are unused with 2 players.)
- Talking to the other player targets them (`interactionTargetId`); trading requires both to have a
  tradeable Pokémon.

## Testing without RetroArch

`tools/netplay_harness/` is a Python libretro frontend that runs two gpSP instances in one process and
relays their netpackets (latency, packet drops, disconnects configurable).

```bash
git clone https://github.com/libretro/gpsp && make -C gpsp platform=unix   # gpsp_libretro.so
# build a test ROM: uncomment #define ROGUE_MP_TEST in include/config/mp_test.h (never commit it enabled)
python3 tools/netplay_harness/mp_test.py --core gpsp_libretro.so --rom pokeemerald.gba --map pokeemerald.map --out out --latency 4
python3 tools/netplay_harness/mp_interact.py ... --trade      # walk up to the other player and trade
python3 tools/netplay_harness/mp_interact.py ... --battle     # wild battle while connected
python3 tools/netplay_harness/mp_npc.py ...                   # real PokeConnect receptionist flow
python3 tools/netplay_harness/berry_test.py ... --state out/overworld.state --berry 531   # berry planting
```

The first run plays the new-game intro and saves `out/overworld.state` (savestates are per ROM build;
use `--reboot` after rebuilding). `mp_test.py` checks connect, byte-identical mirrors on both games,
movement, dropped packets, disconnect + auto reconnect, client reset + rejoin, 2x speed mismatch, saving
while connected. All pass at 1, 4 and 15 frames latency.

## Other fixes on the EX branch

- Berry planting: ported upstream 5ccb74cf (v2.2.1a). 2.2.1 stopped the player interacting with invisible
  object events, and empty soil is an invisible berry tree object. Verified in the harness (no prompt
  without the fix, "Want to plant a Berry?" with it).
- Pokéblock blending (Market) starts and runs; not verified to completion in the harness (the blender
  minigame needs timed presses).

# Emerald Rogue – 4-player multiplayer fork

Fork of [Pokabbie/pokeemerald-rogue](https://github.com/Pokabbie/pokeemerald-rogue) (v2.2.1) that raises
multiplayer (via Rogue Assistant) from 2 to 4 players.

- **Only work in the fork** (`Kronos-Scythe/pokeemerald-rogue-more2mp`). Never push to or open PRs against
  `Pokabbie/pokeemerald-rogue`. GitHub's "Compare & pull request" button defaults to upstream – change the
  base repository to the fork.
- The owner plays **EX**. EX and Vanilla have different save layouts and the game doesn't check which one
  wrote a save, so a Vanilla ROM corrupts EX saves.

## Branches

| Branch | Based on | Flavour |
|---|---|---|
| `claude/emerald-rogue-player-limit-ex` | upstream `expansion` (2.2.1) | **EX – use this one** |
| `claude/emerald-rogue-player-limit-pnbwv9` | upstream `vanilla` (2.2.1) | Vanilla |

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

Output: `pokeemerald.gba` (32 MB). This branch was verified to build with exactly these commands on Ubuntu
(arm-none-eabi-gcc 13.2, no agbcc).

### Problems already hit and their fixes

- **`Temporary failure resolving ...` in apt/wget**: WSL has no DNS. `wsl --shutdown` from PowerShell; if
  that doesn't help, set `nameserver 8.8.8.8` in `/etc/resolv.conf` and
  `[network] generateResolvConf = false` in `/etc/wsl.conf`. Turn off any VPN.
- **`init_deps.sh` says "Skipping (Already exists)" but tools are missing**: a failed earlier run left empty
  folders. `rm -rf tools/poryscript tools/ups` and re-run.
- **`fatal: detected dubious ownership`**: `git config --global --add safe.directory /mnt/g/github/pokeemerald-rogue-more2mp`.
- **Checkout blocked by "local changes" to `.pory`/`.vcxproj`/`.cs` files**: line-ending noise from cloning
  with Windows git. `git stash`, then checkout.
- **`generated/quest_consts.h: No such file` in `make tools`**: happens on the Vanilla branch (its
  `make tools` builds QueryBaker before generated headers exist). Re-run `make tools`. The EX branch builds
  QueryBaker during the main `make` instead.
- **`No rule to make target 'src/string.h'`**: fixed on the EX branch (`#include <string.h>`); otherwise
  install agbcc into the repo per INSTALL.md.
- **`make: command not found`**: the apt install step was skipped.
- **`pkg-config: Permission denied`** during `make tools`: harmless.
- **`claude: command not found` after installing Claude Code**:
  `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc`.
- **QueryBaker "Baking Local Data..."** can print nothing for 20+ minutes. That's normal.
- The WSL user is `root` (`getpwuid(1000) failed` on start). Builds fine as root.

## Sharing the ROM

Don't distribute the `.gba`. Make a patch with RomPatcher.js (https://www.marcrobledo.com/RomPatcher.js/):
"Create patch" with a clean Emerald ROM as original and the built `pokeemerald.gba` as modified (UPS/BPS).
Friends apply it to their own clean Emerald ROM. Everyone in a group needs the same new ROM plus Rogue
Assistant; the handshake refuses old/new and EX/Vanilla mixes.

## What the 4-player change does

Most of it is in `src/rogue_multiplayer.c`.

1. **Limit and joining**: `NET_PLAYER_CAPACITY` 2 → 4 (`include/constants/rogue.h`). Host is always slot 0;
   joiners get the first free slot (`FindFreePlayerId`). Full group → `CONN_ERR_SESSION_FULL`, with a
   message in `data/maps/Rogue_Interior_PokeConnect/scripts.pory`.
2. **Old/new ROM protection**: `RogueNetHandshake` gained `clientSupportsMultiPlayer` and
   `hostSupportsMultiPlayer` bits. Mismatches report `CONN_ERR_WRONG_SAVE_VERSION`. Save format is unchanged.
3. **Characters**: `OBJ_EVENT_ID_MULTIPLAYER` 241–244 → 241–248 (2 per player: body + follow mon). Other
   players map to "remote slots" 0–2 (`RogueMP_GetRemoteSlotForPlayer`). New gfx ids
   `OBJ_EVENT_GFX_NET_PLAYER_REMOTE_1/2_NORMAL` are appended after the follow-mon gfx, so no existing id moved.
4. **Palettes** (`sNetPlayerPaletteSlots`): remote slot 0 → pal 8 (as before), slot 1 → pal 11 (reserved,
   unused), slot 2 → pal 7 (disables wild follow-mon slot 1 while connected; spawned wild mons using it are
   removed). Maps that place FOLLOW_MON_1/2 objects (Day Care, Ride Training, Safari tutorial, Intro, final
   boss) can show colour clashes.
5. **Follow/ride mons**: only remote slot 0 shows one. The remote ride mon uses the MP follow-mon gfx slot
   (`src/rogue_ridemon.c`) instead of borrowing a wild slot.
6. **Targeted interactions**: `RogueNetPlayer.interactionTargetId`. Set from `gSpecialVar_LastTalked` when
   talking to a player object, or adopted while idle when another player targets you. Status sync and cmd
   buffers only respond to requests aimed at the local player. `RogueMP_GetRemotePlayerId()` returns the
   current target, or the first other active player. Hub name/variant always come from the host
   (`NET_PLAYER_ID_HOST`).

Not play-tested yet. Still to try: 3–4 players in hub and routes, trades between different pairs,
leave/rejoin, loading an existing EX 2.2.1 save. Not checked: how Rogue Assistant handles several joins
and marks leavers inactive.

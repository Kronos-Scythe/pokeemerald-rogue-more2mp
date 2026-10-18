#ifndef ROGUE_MULTIPLAYER_LINK_H
#define ROGUE_MULTIPLAYER_LINK_H

// Link cable transport for Rogue multiplayer.
//
// Replaces the Rogue Assistant memory syncing. Uses the vanilla Emerald serial (link cable)
// layer in multiplayer mode, which emulators can carry over the network
// (e.g. RetroArch netplay + gpSP core with "Link Cable - Pokemon Gen3 mode").

enum
{
    MPLINK_STATE_OFF,
    MPLINK_STATE_LINKING,       // waiting for the cable/netplay link to come up
    MPLINK_STATE_HELLO,         // link is up, exchanging HELLO/WELCOME
    MPLINK_STATE_SYNCING,       // accepted, waiting for the initial state from the partner
    MPLINK_STATE_CONNECTED,     // fully synced, partner visible
    MPLINK_STATE_RECONNECTING,  // partner lost, link is being reopened
};

void RogueMPLink_Start(bool8 asHost);
void RogueMPLink_Stop(void);
void RogueMPLink_Update(void);

u8 RogueMPLink_GetState(void);
bool8 RogueMPLink_IsPartnerConnected(void);
bool8 RogueMPLink_HasJoinedSession(void);
u8 RogueMPLink_GetFailReason(void);
void RogueMPLink_ClearFailReason(void);

#endif

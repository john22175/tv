# MultiHub Tizen Receiver App

This directory contains a packaged Tizen Web application scaffold for Samsung TVs.

App ID:
`MHubRcvr01.MultiHubReceiver`

Package:
`MHubRcvr01`

What it does:
- launches fullscreen on the TV
- defaults to the MultiHub desktop host at `http://10.171.64.186:65331`
- auto-binds to the correct receiver by the TV's requester IP
- falls back to a previously known alias only if needed
- polls `GET /receiver-state-current` from the desktop app
- renders image, video, audio, and fallback receiver text locally inside the TV app
- saves the desktop `Sources` library locally on the TV for offline source selection
- opens the saved-source picker with the Up arrow; use Left/Right, Enter, and Down to choose or close

## Install

1. Open Tizen Studio.
2. Import this folder as an existing Tizen Web project.
3. Create or select a Samsung TV certificate profile.
4. Build and package the app.
5. Install it onto the TV with Device Manager / `sdb`.

## Receiver Behavior

When the app opens on the TV, it is immediately ready.

MultiHub stages receiver state for each saved TV using the TV host/IP, and the TV app resolves its own receiver slot automatically when it polls the desktop app.

When the desktop app starts, already-running receiver apps receive a revisioned source manifest. Each TV stores the complete refreshed library before making it active, so an interrupted transfer leaves its previous saved library available. Open a pending receiver app to let it complete a requested sync.

## MultiHub Integration

The desktop app now looks for an installed Samsung app with app ID
`MHubRcvr01.MultiHubReceiver` or a name containing `MultiHub` + `Receiver`.

If found, MultiHub launches that app instead of Samsung Internet.

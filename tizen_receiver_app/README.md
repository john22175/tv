# MultiHub Tizen Receiver

The editable Samsung TV receiver project lives in [`app/`](app/). Its app ID is `MHubRcvr01.MultiHubReceiver` and its package ID is `MHubRcvr01`.

## Published sources

At startup, and when **Refresh Sources** is selected on the TV, the receiver reads the public `main` branch of this repository. It downloads only regular files beneath [`sources/`](../sources/), preserving source folders. Receiver code, the Vercel dashboard, hidden marker files, and development artifacts are never shown as TV sources.

Sources must remain below 95 MiB. Git LFS is not supported because the receiver downloads GitHub raw URLs directly. Use the [`frontend/`](../frontend/) dashboard to publish and remove sources; changes are available after the next TV source refresh without reinstalling this app.

## Receiver-code releases

Build output, signing data, and historical packages are intentionally local-only. To deploy receiver code to developer-mode TVs:

1. Copy `deploy.targets.example.json` to `deploy.targets.json` and enter each TV's host, SDB serial, dashboard `receiverId` (`tv-1` through `tv-6`), and Tizen certificate profile.
2. Run `./scripts/deploy-receiver.ps1` from this directory.
3. Use `-WhatIf` first to review the per-TV connect, uninstall, install, and launch commands without changing a TV.

The deployment command builds the app, creates a receiver-specific package for every configured TV, uninstalls `MHubRcvr01`, installs the signed WGT, and opens the receiver. The packaged `receiverId` lets the Vercel dashboard show the TV's green/red listener indicator and stage a source to that TV without remote pairing.

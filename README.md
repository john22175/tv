# Samsung Smart TV MultiHub

Desktop PyQt app for staging local media, arranging device markers on a hub canvas, and sending supported files to real TVs on your network.

## Features

- Upload local videos, images, PDFs, and presentation files.
- Drag source items onto the on-screen TV to preview them locally.
- Add device nodes and drag them anywhere on the hub stage.
- Discover DLNA/UPnP media-renderer TVs on the local network.
- Send image, video, and audio files to compatible TVs through AVTransport.
- Pair with Samsung TVs over the local remote WebSocket transport for key commands.
- Optionally connect a SmartThings Personal Access Token to enumerate cloud-linked Samsung devices.

## Connection model

- `DLNA / UPnP`: Used for real media playback from this app to a TV when the TV exposes an `AVTransport` media-renderer service.
- `Samsung Remote`: Used for LAN pairing and remote key presses such as `KEY_HOME`.
- `Tizen Receiver App`: Optional Samsung TV app path that launches a packaged local receiver app instead of Samsung Internet.
- `SmartThings`: Used for device inventory and cloud-side commands. It does not guarantee arbitrary local-file playback by itself.

## Run

```powershell
python -m pip install -r requirements.txt
python main.py
```

## Using the app

1. Click `Upload Source Files` and choose media from your computer.
2. Click `Discover TVs` to find local renderers.
3. Select a discovered TV in the `TV Connections` list.
4. Drag a source onto the TV surface in the stage.
5. If the selected TV supports DLNA playback for that media type, the app will host the file locally and instruct the TV to play it.

## SmartThings setup

1. Create a SmartThings Personal Access Token in the SmartThings account UI.
2. Paste the token into the app and click `Load SmartThings TVs`.
3. Cloud devices will be added to the connection list.

## Tizen receiver app

- A packaged Tizen web app scaffold is included in [tizen_receiver_app](tizen_receiver_app/README.md).
- App ID: `MHubRcvr01.MultiHubReceiver`
- If the app is installed on a Samsung TV, MultiHub will try to launch it before falling back to Samsung Internet.

## Practical limitations

- Raw `.ppt` and `.pptx` files are previewed as staged presentation assets, but most TVs will not play them directly. Export to video or PDF for reliable playback.
- PDF playback over DLNA is TV-dependent and usually unreliable; the app keeps PDF support as a local preview.
- Some Samsung TVs expose remote control but not a renderer endpoint, and some expose a renderer endpoint but reject certain codecs or container types.
- Some Samsung TVs can appear in Chromecast discovery but still refuse a generic local-media Cast session. When that happens, the app now falls back to the web receiver and stops retrying Chromecast for that TV during the current run.

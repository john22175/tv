Review the uploaded PyQt Samsung Smart TV MultiHub codebase and implement the following refactor.

Remove any html/web interface for app core.

Goal:
Convert the UI into a cleaner 2-tab layout and expand TV discovery/playback support beyond DLNA.

Requirements:

1. UI restructure
- Replace the current single sidebar/stage layout with two main tabs:
  - Tab 1: “Sources & Controls”
    - Source upload/list panel
    - TV discovery controls
    - SmartThings token input if still kept
    - Chromecast discovery button
    - Samsung LAN discovery button
    - selected endpoint details
    - remote-control buttons
    - media send/open receiver controls
  - Tab 2: “Device Stage”
    - Keep the draggable device/stage marker interface here
    - Keep the large TV preview/drop surface here
    - Preserve drag-and-drop from source list to TV surface if possible

2. Discovery refactor
- Keep existing DLNA/UPnP discovery, but rename it clearly as DLNA discovery.
- Add independent Samsung LAN API discovery:
  - Scan the local /24 subnet.
  - Test:
    - http://<ip>:8001/api/v2/
    - https://<ip>:8002/api/v2/
  - If JSON response indicates Tizen/Samsung TV, create a TVEndpoint with:
    - host
    - manufacturer="Samsung"
    - model_name from API response if available
    - samsung_remote_port=8002 preferred
    - source="Samsung LAN"
  - Do not require DLNA discovery for Samsung remote support.
- Add Chromecast discovery support:
  - Use an appropriate Python library if available, preferably pychromecast.
  - Discover Chromecast devices separately.
  - Add them as TVEndpoint/source entries with source="Chromecast".
  - Include enough metadata to cast a media URL later.

3. Endpoint capability model
- Update TVEndpoint if needed so one endpoint can represent:
  - DLNA playback capability
  - Samsung LAN remote-control capability
  - Chromecast playback capability
  - Web receiver capability
- The UI should show capabilities clearly:
  - DLNA playback
  - Samsung remote
  - Chromecast
  - Web receiver

4. Samsung LAN remote control
- Reuse the existing send_samsung_remote_key function where possible.
- Ensure Samsung LAN-discovered TVs can send KEY_HOME and other basic remote commands.
- Save/reuse Samsung remote token if the TV returns one.
- Make remote-control failure messages clear, especially when the TV requires the user to approve the connection prompt.

5. Local web receiver for media display
- Extend the existing MediaHTTPServer or add a new receiver route.
- Add a simple web receiver page, for example:
  - /receiver/<receiver_id>
- The page should display the currently selected media:
  - images in full screen
  - videos with controls/autoplay if possible
  - audio with controls
  - PDFs/documents as a fallback message unless converted
- Add an endpoint or in-memory state that lets the desktop app update which media the receiver page should display.
- Add a UI action:
  - “Open Receiver on TV”
  - For Samsung LAN TVs, attempt to open/navigate the TV browser to the receiver URL using available remote/browser mechanisms if practical.
  - If direct browser launch/navigation is not reliable, show/copy the receiver URL and provide a clear status message.

6. Media sending behavior
When a user drops or sends a source to a selected TV:
- If endpoint supports DLNA and file is castable, use existing play_to_renderer.
- Else if endpoint supports Chromecast, cast the local MediaHTTPServer URL through Chromecast.
- Else if endpoint supports web receiver, update the web receiver page to show the media.
- Else show a clear message explaining no playback route is available.

7. Keep backwards compatibility
- Existing file preview should still work locally.
- Existing DLNA play/pause functions should still work.
- Existing SmartThings code can remain, but do not make it the primary non-expiring path.
- Do not remove demo sources unless necessary.

8. Code quality
- Keep networking/discovery code out of main_window.py where possible.
- Put Samsung LAN and Chromecast discovery in connectors.py or separate connector modules.
- Keep UI event handlers small.
- Add good status messages and exception handling.
- Avoid blocking the PyQt UI thread; use the existing Worker/QThreadPool pattern.

Acceptance tests:
- Running the app should show two tabs.
- DLNA discovery button still works, even if it finds zero.
- Samsung LAN discovery should find TVs responding on ports 8001/8002, like:
  - 10.171.64.125
  - 10.171.64.174
  - 10.171.64.175
  - 10.171.64.176
  - 10.171.64.177
- Samsung LAN-discovered TVs should appear in the endpoint list with “Samsung remote” capability.
- Selecting a Samsung LAN TV should allow a test KEY_HOME command.
- Media files should still preview locally.
- If no DLNA/Chromecast playback exists, sending media should fall back to the local web receiver route.
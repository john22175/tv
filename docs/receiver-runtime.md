# Receiver runtime and reusable PiP

## Source of truth

Production media is read only from the public GitHub repository
`john22175/t-sources`, under its `sources/` directory. The local
[`sources/`](../sources/) directory in this code repository is not a
production media source and must not be used to reconcile the dashboard with a
TV.

These locations must agree:

| Consumer | Required source configuration |
| --- | --- |
| Vercel dashboard | `SOURCE_GITHUB_OWNER=john22175`, `SOURCE_GITHUB_REPOSITORY=t-sources`, `SOURCE_GITHUB_BRANCH=main` |
| Receiver package | `sourceRepository` in `tizen_receiver_app/deploy.targets.json`: `john22175/t-sources`, branch `main` |
| Receiver fallback package config | `tizen_receiver_app/app/js/source-repository.js`: `john22175/t-sources`, branch `main` |

The dashboard source library and TV **Refresh Sources** both enumerate the
same `t-sources/sources/` tree. The dashboard does this directly with its
server-side GitHub credentials. TVs request Vercel's public
`/api/receiver-library` index, which is cached for 60 seconds; Vercel uses
those credentials to read the tree and each TV still downloads media from the
public `t-sources` raw URL. This avoids GitHub's 60-per-hour shared-IP limit
for unauthenticated REST calls. If their file lists differ, first confirm the
TV has the current receiver package, wait up to one minute for the index cache,
then press **Refresh Sources** on that TV. Do not use the retired `tv/sources/`
tree as a repair source.

## Receiver source-menu controls and rollback

On a deployed receiver, press the number matching its immutable package ID to
open its local Saved Sources menu: **1** for `tv-1`, through **6** for `tv-6`.
Remote keys cannot open a menu on a different physical TV; the matching-number
rule prevents one receiver from responding to another receiver's shortcut.

The menu is a vertical file-explorer list: folders appear before files,
**Up/Down** move through the list, **Enter** opens a folder or plays a source,
and **Left** goes to the parent folder (or closes the menu at the root).
**Right** moves focus to the vertical action column on the list's right;
there **Up/Down** select **Refresh Sources** or **View Logs**, **Enter** runs
the selected action, and **Left** returns to the source list.

Before this navigation revision, **Up** opened the menu, source cards were a
left-to-right wrapping carousel, and Refresh/Logs were above the cards. To
roll back only this interaction/layout, revert the commit named **“Use
number-key file explorer source menu”**; it does not alter the source cache,
GitHub/Vercel configuration, media, or staged commands.

## Reusable picture in picture

There is exactly one managed PiP composition. It is stored in the public media
repository and is therefore reusable from the TV source menu, from the
dashboard, and on another TV:

```text
sources/Welcome/Temp/Welcome_Filled.pip.json
sources/Welcome/Temp/Welcome_Filled_overlay.png
```

- **Ctrl+V** in the dashboard PiP tab converts the clipboard image to PNG and
  replaces `Welcome_Filled_overlay.png`; it never creates timestamped root
  files.
- **Save & Stage** replaces `Welcome_Filled.pip.json` in the same location and
  stages that recipe as an ordinary source command.
- The recipe records the selected base path, overlay path, layout, and optional
  background-removal color. It remains usable until the next **Save & Stage**
  replaces it.
- Saving validates that both referenced source files are still present in
  `t-sources` before committing. This prevents a new broken recipe.

The TV lists the recipe as **Picture in Picture · Welcome_Filled** in
`Welcome/Temp`; select it or Push To it on any receiver to reuse the
composition. The companion `Welcome_Filled_overlay.png` remains a normal image
source, while the recipe is fetched directly so custom JSON caching cannot hide
it from the TV menu.

## PiP error diagnosis

`Picture in Picture Error` means the recipe could not be fetched, was invalid,
or points to a source that no longer exists. The most recent broken recipe was
diagnosed as the last case: `Welcome_Filled.pip.json` referenced a timestamped
root `Pasted_*.png` file that returned GitHub HTTP 404. The overlay bytes are
not recoverable from a recipe alone.

To replace such a legacy recipe, open the PiP tab, paste or choose an existing
overlay, then use **Save & Stage**. The dashboard stores the replacement in
`Welcome/Temp` and removes older managed `Welcome_Filled.pip.json` recipes.

## PowerPoint slide shows

The Tizen web runtime cannot play an Office file directly. When a `.ppt` or
`.pptx` is uploaded, the `t-sources` **Render PowerPoint slides** GitHub Action
uses LibreOffice to render one PNG per slide and writes a neighboring
`<presentation>.slides.json` manifest. The generated PNGs live beneath hidden
`sources/.presentations/`, so they do not fill the dashboard or TV source
menus.

The dashboard shows **Rendering slides** until the manifest appears. Then push
the original PowerPoint file; the receiver resolves its manifest, displays the
PNG slides for 10 seconds each, and repeats from slide one. This is an
event-driven GitHub Action run per presentation upload—there is no Blob,
Vercel conversion, or receiver polling added for it.

## Receiver network behavior

The receiver no longer contacts the retired local desktop endpoint at
`10.171.64.201:65331`. It has only these recurring/explicit network flows:

| Trigger | Destination | Purpose |
| --- | --- | --- |
| Startup and TV **Refresh Sources** | Vercel `/api/receiver-library`, then public `t-sources` raw media | Read the 60-second-cached source index and cache changed media. TVs do not call GitHub's REST API directly. |
| Every 60 seconds, plus startup | Vercel `/api/receiver/tv-N` | Read the current command from the cached private control manifest. |
| Dashboard action | GitHub/Vercel | Upload media or write/stage a control-manifest command. |

There is no Blob access, local-desktop polling, TV heartbeat, or background TV
write loop.

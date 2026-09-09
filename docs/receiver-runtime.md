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
same `t-sources/sources/` tree. If their file lists differ, first confirm the
TV has the current receiver package, then press **Refresh Sources** on that
TV. Do not use the retired `tv/sources/` tree as a repair source.

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

The recipe is listed as a normal source in `Welcome/Temp`; select or Push To it
on any receiver to reuse the composition.

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
| Startup and TV **Refresh Sources** | Public `t-sources` | Read and cache the source tree and changed media. |
| Every 60 seconds, plus startup | Vercel `/api/receiver/tv-N` | Read the current command from the cached private control manifest. |
| Dashboard action | GitHub/Vercel | Upload media or write/stage a control-manifest command. |

There is no Blob access, local-desktop polling, TV heartbeat, or background TV
write loop.

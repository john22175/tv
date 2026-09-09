# TV Sources Dashboard

This password-protected Next.js dashboard is hosted on Vercel, but it does not
use Vercel Blob. Media lives in a public GitHub source repository; TV commands
live in a private GitHub control repository.

## How it works

| Task | Where it happens | Result |
| --- | --- | --- |
| Add media | Authenticated browser -> GitHub Contents API | One media commit; the source is immediately published. |
| Refresh Sources on a TV | TV -> public source repository | The TV checks immediately, independently of Push To. |
| Push To | Dashboard -> private control manifest -> Vercel endpoint -> TV | One manifest commit even when several TVs are selected. |
| Picture in picture | Dashboard -> `Welcome/Temp/Welcome_Filled.pip.json` in the public source repository -> ordinary Push To | A selected source, pasted image, or generated link QR replaces the one reusable overlay, then stages it. |
| PowerPoint slideshow | Dashboard -> `t-sources` GitHub Action -> rendered PNG slides -> TV | The uploaded `.ppt` or `.pptx` becomes a looping 10-second slideshow when rendering is ready. |
| TV command check | TV -> Vercel every 60 seconds | No Blob read, status, or heartbeat write. |

The dashboard works from any internet connection. TVs do not need to share
Wi-Fi with the dashboard; each TV does need internet access and a running
receiver app.

## Prerequisites

Create these repositories before configuring Vercel:

| Repository | Visibility | Contents |
| --- | --- | --- |
| `tv` | Existing setting | This dashboard and Tizen receiver source. |
| `t-sources` | Public | `sources/` media tree only. |
| `tv-control` | Private | `receiver-manifest.json` only. |

Seed `t-sources` with a `sources/` directory (copy the current media tree).
Seed `tv-control` with a first commit and this file at its root:

```json
{
  "version": 1,
  "updatedAt": "2026-01-01T00:00:00.000Z",
  "receivers": {
    "tv-1": null,
    "tv-2": null,
    "tv-3": null,
    "tv-4": null,
    "tv-5": null,
    "tv-6": null
  }
}
```

Create two GitHub Apps:

| App | Install only on | Permission | Token use |
| --- | --- | --- | --- |
| TV Source Upload | `t-sources` | Contents: Read and write | A short-lived installation token is returned only to an authenticated dashboard browser for a direct upload. |
| TV Control | `tv-control` | Contents: Read and write | Server-side only, to read and write `receiver-manifest.json`. |

An installation token cannot be restricted to `sources/` within a repository.
That is why `t-sources` must contain media only: never install the source
upload App on the dashboard/code repository.

## Vercel environment variables

Copy [`.env.example`](.env.example) as the reference. Add every variable to
the required Vercel environments, without a `NEXT_PUBLIC_` prefix.

| Variables | Purpose |
| --- | --- |
| `SOURCE_DASHBOARD_PASSWORD`, `SESSION_SECRET` | Dashboard sign-in/session. |
| `SOURCE_GITHUB_OWNER`, `SOURCE_GITHUB_REPOSITORY`, `SOURCE_GITHUB_BRANCH` | The public source repository. |
| `GITHUB_SOURCE_MANAGER_TOKEN` | Server-only fine-grained token for source listing, folder management, moving, and deleting. Restrict it to `t-sources`, Contents read/write. |
| `GITHUB_SOURCE_UPLOAD_APP_ID`, `GITHUB_SOURCE_UPLOAD_INSTALLATION_ID`, `GITHUB_SOURCE_UPLOAD_PRIVATE_KEY` | GitHub App credentials for direct browser uploads to `t-sources`. |
| `CONTROL_GITHUB_OWNER`, `CONTROL_GITHUB_REPOSITORY`, `CONTROL_GITHUB_BRANCH` | The private control repository. |
| `GITHUB_CONTROL_APP_ID`, `GITHUB_CONTROL_INSTALLATION_ID`, `GITHUB_CONTROL_PRIVATE_KEY` | Server-only GitHub App credentials for the command manifest. |

For multiline private keys, paste the PEM as one Vercel value with literal
`\n` separators, as illustrated in `.env.example`. Do not expose an App key,
an installation token, or the manager token in browser variables, logs, or a
receiver package.

Production uses the explicit `SOURCE_GITHUB_*` values only. The dashboard will
fail clearly if they are missing rather than falling back to the retired code
repository.

## Deploy and receiver migration

1. Import the existing `tv` repository into Vercel with `frontend` as its root
   directory. Set the environment variables above and deploy to Preview first.
2. In Preview, add a small image, create a folder, move/delete a test source,
   push it to one test receiver, and confirm a single `tv-control` commit.
3. Copy `tizen_receiver_app/deploy.targets.example.json` to
   `deploy.targets.json`. Set `sourceRepository` to `john22175/t-sources`, then enter
   every TV's host, serial, receiver ID, and certificate profile.
4. Run `./scripts/deploy-receiver.ps1 -WhatIf`, then deploy one TV at a time.
   The receiver update points source refreshes at `t-sources` and changes its
   command poll interval to 60 seconds.
5. After each installation, press **Refresh Sources** on the TV and verify a
   newly uploaded source; then use the dashboard's **Push To** for that TV.

Until a receiver is redeployed, it continues to read the repository baked into
its previous package. Keep the old source library available until all TVs pass
this check.

## Operational limits

- One direct upload is handled at a time in the dashboard. The raw file limit
  is 95 MiB; browser Base64 encoding uses substantially more memory, so test
  large uploads on the actual dashboard computer before relying on them.
- Keep ordinary GitHub media repositories comfortably below 1 GB and avoid
  Git LFS: the receiver downloads regular public GitHub raw URLs.
- TV source refreshes use Vercel's public `/api/receiver-library` index, cached
  for 60 seconds, before downloading changed media from `t-sources`. This
  prevents all TVs on one network from exhausting GitHub's unauthenticated
  REST API limit.
- A Push To action produces one private-manifest commit for all selected TVs.
  Do not add a per-TV write loop or background heartbeat.
- A newly pushed command arrives on the next command check—normally within
  about one minute. A new source appears when **Refresh Sources** is pressed;
  it does not wait for that command interval.
- A PowerPoint upload starts one GitHub Actions conversion only for that file.
  The dashboard shows **Rendering slides** until its generated slideshow is
  committed; then push the original `.ppt`/`.pptx` to a TV. Slide images are
  stored under the hidden `sources/.presentations/` folder and loop every 10
  seconds. The workflow needs GitHub Actions' `contents: write` permission.
- The dashboard intentionally does not show online/offline TV dots. It can
  show the last staged command, but a website cannot infer that a TV is alive
  without reintroducing a write-heavy presence protocol.

## Local verification

```powershell
cd frontend
npm ci
npm test
npm run build
```

For local direct-upload testing, set the GitHub and session variables in a
local `.env.local` that is never committed. The browser uploads directly to
GitHub, so no Vercel Blob callback or tunnel is required.

## Rollback reference

The retired Blob design, its known quota behavior, required settings, and a
controlled rollback checklist are in
[`docs/legacy-blob-rollback.md`](../docs/legacy-blob-rollback.md). Do not
re-enable it casually: it exhausted the observed Hobby plan through normal
background polling.

The live receiver/source-repository agreement and the reusable PiP layout are
documented in [`docs/receiver-runtime.md`](../docs/receiver-runtime.md).

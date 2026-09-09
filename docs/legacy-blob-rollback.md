# Legacy Vercel Blob implementation and rollback guide

This document records the Blob-backed version that existed before the GitHub
source/control migration. It is a rollback reference, not a design to enable
alongside the current implementation.

## Why it was retired

Each receiver called the public Vercel endpoint every 30 seconds. A successful
call read a per-TV stage object, read a per-TV status object, and periodically
wrote a heartbeat. For six TVs, that was approximately 69,120 Blob simple
operations and 1,728 heartbeat writes per day before manual dashboard uploads
or pushes. That unattended load exhausted the Hobby store's operation quota.

When the quota was exhausted, Vercel receiver logs showed `GET 502` for paths
such as `/api/receiver/tv-5`; the underlying app response was `Blob read failed
(403).` This does not indicate that the TVs are on the wrong Wi-Fi or that all
receiver apps are closed.

## What the legacy version contained

| Area | Legacy behavior |
| --- | --- |
| Upload | Browser uploaded to Vercel Blob at `pending/<requestId>/<path>`. |
| Publish | `publish-source.yml` downloaded that Blob file, committed it to `sources/`, then deleted the temporary Blob. |
| Per-TV command | `receiver-stage/tv-N.json` in Blob. |
| Presence | `receiver-status/tv-N.json`, written at most once per five minutes by a receiver poll. |
| Polling | Receiver command interval was 30 seconds. |
| Dashboard chooser | Displayed inferred green/red presence dots based on the Blob heartbeat. |

The key legacy files were:

- `frontend/lib/receivers.ts` — Blob stage/status reads, writes, and heartbeat.
- `frontend/app/api/uploads/route.ts` — `@vercel/blob/client` token/callback handler.
- `frontend/components/source-dashboard.tsx` — client `@vercel/blob` upload and publish-workflow waiting UI.
- `.github/workflows/publish-source.yml` — Blob-to-GitHub publisher.
- `tizen_receiver_app/app/js/receiver-control.js` — 30-second command polling.

## Legacy configuration

The old production design required all of the following secrets/configuration:

| Location | Legacy value |
| --- | --- |
| Vercel environment | `BLOB_READ_WRITE_TOKEN` for a public Blob store. |
| Vercel environment | `GITHUB_SOURCE_MANAGER_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPOSITORY`, and `GITHUB_BRANCH`. |
| GitHub Actions secret | The same `BLOB_READ_WRITE_TOKEN`. |
| GitHub Actions setting | Workflow token permitted to write contents on the production branch. |
| Package dependency | `@vercel/blob`. |

Never recover a token from shell history, build logs, screenshots, or a prior
deployment's generated files. Obtain it from Vercel's encrypted environment
variables or issue a new token when reconnecting a store.

## Controlled rollback procedure

Only roll back if the GitHub source/control design cannot operate and restoring
the legacy behavior is preferable to waiting for the Blob quota reset. Rolling
back reintroduces the operation problem described above.

1. Preserve the current GitHub source repositories and the current dashboard
   deployment URL. Do not delete media or the control manifest.
2. Use `git log` to identify the last commit before the Blob-free migration,
   then deploy that exact known-good revision (or use `git revert` for the
   migration commit). Do not hand-copy old source snippets into the new code.
3. Restore `@vercel/blob`, the legacy upload route/component/receiver module,
   and `publish-source.yml` from that revision. Restore the receiver's
   30-second control configuration only if the matching legacy deployment is
   live.
4. Connect a public Blob store to the Vercel project and set
   `BLOB_READ_WRITE_TOKEN` in the applicable Vercel environments. Add that
   same token as the GitHub Actions secret. Enable workflow write permission.
5. Deploy to a Preview environment. Upload one small disposable image, confirm
   that the workflow commits it to `sources/`, stage it to one test TV, then
   remove the image.
6. Before promoting to Production, calculate expected operations for the
   actual number of running TVs and the expected duration. Treat a Blob quota
   error as a hard stop; do not keep retrying a failing poll.

## Safer rollback alternatives

If only direct browser upload fails, retain the Blob-free command manifest and
temporarily add media through GitHub Desktop/Git to `tv-sources/sources/`.
If a migrated TV cannot read `tv-sources`, reinstall its prior signed receiver
package while the rest of the fleet remains on the new control path. Neither
case requires restoring Blob.

## Before declaring a rollback complete

- Verify the Vercel deployment matches the intended revision.
- Verify one upload and one single-TV stage end-to-end.
- Inspect Blob operation usage after ten minutes; do not assume low storage
  size means low operation usage.
- Record the rollback date, deployed commit, active TV count, poll interval,
  and reason in the operational log.
- Create a dated plan to return to the Blob-free version; the legacy design
  has no safe unattended long-term quota budget on the observed Hobby plan.

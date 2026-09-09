# TV Dashboard: Blob-Free Transition Plan

## Implementation status (2026-09-09)

The repository implementation is complete for the Blob-free runtime path:

- The dashboard uploads directly to GitHub through a short-lived source-repository GitHub App token; no upload passes through Blob or a Vercel Function body.
- Receiver commands now use one GitHub `receiver-manifest.json` commit per Push To action, including multi-TV and picture-in-picture pushes.
- Blob code, the Blob publish workflow, and the `@vercel/blob` dependency have been removed. The legacy version is documented in [`docs/legacy-blob-rollback.md`](docs/legacy-blob-rollback.md).
- The receiver has a configurable source repository and a 60-second command interval. Its package must still be deployed to each TV after the external repositories and Vercel variables are configured.

External setup still required: create/populate `tv-sources` and `tv-control`, create/install the two GitHub Apps, add the documented Vercel variables, then deploy the dashboard and receiver packages. See [`frontend/README.md`](frontend/README.md) for the exact order.

## At a glance: what changes and how it will work

| Area | Today | Target state |
| --- | --- | --- |
| Dashboard hosting and sign-in | Vercel + dashboard password | **Unchanged.** Vercel remains the dashboard and authenticated API host. |
| Media upload path | Browser -> Vercel Blob -> GitHub Action -> `sources/` | Browser -> GitHub directly -> `tv-sources` repository. The file is already published when the commit succeeds. |
| TV source refresh button | TV reads `sources/` from the current repository | **Unchanged behavior.** It reads `sources/` from the new public source repository after the receiver migration. Pressing **Refresh Sources** checks immediately; it does not wait for command polling. |
| `Push To` command state | One Vercel Blob object and one Blob status object per TV | One command manifest in a separate GitHub control repository. A multi-TV push changes that manifest in one commit. |
| TV command delivery | Every TV polls Blob through Vercel every 30 seconds and writes a heartbeat | TVs poll the existing Vercel receiver endpoint. The endpoint serves a cached GitHub manifest; TVs never write a heartbeat. Target delivery is within the next 60 seconds after a push. |
| TV green/red availability dots | Derived from frequent Blob-backed heartbeats | Removed. The dashboard shows **last staged** and **last command revision**, not inferred live presence. |
| TV receiver installation | Existing receiver code uses the current source repository and a 30-second command interval | One planned receiver update changes its source repository and uses a 60-second command interval. It is a one-time deployment to all TVs. |
| Vercel Blob | Used for uploads, stage commands, and heartbeats | Removed from the runtime path. No recurring Blob operation can consume a monthly quota. |

## Decision and goals

The target architecture uses GitHub for the two things GitHub is good at here: publishing a small number of durable media files and storing a small control document. It does **not** use GitHub's REST API as a high-frequency TV polling service.

This design has four goals:

1. Keep the existing password-protected web dashboard as the day-to-day interface.
2. Support the existing 95 MiB per-media-file limit without Vercel Blob.
3. Keep remote source pushes working from any internet connection.
4. Remove all unattended write loops and put explicit limits around every remaining external API.

## Target architecture

```text
                         authenticated dashboard user
                                      |
                                      v
                         Vercel / Next.js dashboard
                         (this repository: tv)
                            |                 |
          short-lived, repo-only token         | server-side GitHub App token
                            |                 |
                            v                 v
                   public tv-sources repo   private tv-control repo
                     sources/<media>       receiver-manifest.json
                            |                 ^
                            |                 |
                            +-- TV Refresh ---+--- cached Vercel /api/receiver/tv-N
                                      (each TV reads only its command)
```

### Repositories

Use two new repositories in addition to this dashboard repository:

| Repository | Visibility | Contents | Reason |
| --- | --- | --- | --- |
| `tv` (existing) | Existing setting | Dashboard and receiver source code | Keeps application code separate from user-uploaded media. |
| `tv-sources` | Public | Only `sources/` media and folders | TVs can continue to download public raw GitHub URLs. A browser upload token can be restricted to this repository instead of also granting access to dashboard code. Media commits do not trigger Vercel dashboard deployments. |
| `tv-control` | Private | `receiver-manifest.json` only | Keeps command state separate from media and from Vercel deployment triggers. TVs never receive GitHub credentials; Vercel reads this file server-side. |

`tv-sources` preserves the existing `sources/` root folder so the receiver's source-library logic remains familiar.

## What works before the current Blob reset

The Blob quota lock does not prevent a Vercel deployment or GitHub commits. The transition should therefore be ordered so useful functionality returns before the Blob reset date.

| Capability | Before migration | After Phase 1 | After Phase 2 |
| --- | --- | --- | --- |
| Browse published library | Works | Works | Works |
| TV Refresh Sources | Works for already committed sources | Works | Works from `tv-sources` after receiver update |
| Dashboard Push To | Fails with Blob 403/502 | Works from the GitHub control manifest | Works |
| Dashboard file picker upload | Fails because it needs Blob | Still unavailable | Works by committing directly to `tv-sources` |
| Direct Git/GitHub Desktop source upload | Works | Works | Temporary fallback only |

Until Phase 2 is complete, new media can be added directly to `sources/` with GitHub Desktop/Git. That is a temporary operational workaround, not the final dashboard experience.

## Required setup

### 1. Create and configure the GitHub repositories

1. Create public `tv-sources` and private `tv-control` repositories.
2. Copy the current `sources/` tree into `tv-sources/sources/` and verify that every raw media URL is reachable without GitHub authentication.
3. Create `receiver-manifest.json` in `tv-control` with an empty command for each known receiver.
4. Protect `main` only if the dashboard GitHub App is explicitly allowed to write to it; otherwise the dashboard will be unable to publish media or commands.

### 2. Create GitHub Apps instead of exposing a personal access token

Use separate GitHub Apps so a temporary browser token has the smallest practical scope.

| App | Installed repositories | Permissions | Where its token is used |
| --- | --- | --- | --- |
| **TV Source Upload App** | `tv-sources` only | Contents: Read and write | The dashboard browser receives a short-lived installation token immediately before an upload. |
| **TV Control App** | `tv-control` only | Contents: Read and write | Vercel only. It reads and updates the command manifest; its token never reaches the browser or a TV. |

Store the Apps' private keys, App IDs, and installation IDs as encrypted Vercel environment variables. Do not put them in a `NEXT_PUBLIC_*` variable, source code, a TV package, or the command manifest.

> GitHub installation tokens cannot be limited to a particular directory. Separating `tv-sources` from the dashboard repository is therefore a security requirement: a browser upload token may write files in `tv-sources`, but it cannot alter dashboard code or the control manifest.

### 3. Keep the existing dashboard session model

The dashboard password/session remains the authorization boundary. A source-upload token may be issued only after all of these checks pass:

1. The user has a valid dashboard session.
2. The path is a permitted path beneath `sources/`.
3. The filename extension and raw `File.size` pass the existing validation rules.
4. No other upload is currently active for that dashboard session.

The token must be kept in JavaScript memory only, used for the one GitHub request, and discarded after success or failure. It must not be placed in local storage, a cookie accessible to JavaScript, analytics, error reporting, or logs.

## Detailed flows

### A. Upload and publish new media

1. The dashboard validates the selected local file before any network call.
2. The dashboard requests a short-lived `tv-sources` installation token from a new authenticated Vercel route.
3. The browser Base64-encodes the file and makes a direct `PUT` request to GitHub's Contents API for `tv-sources/sources/<path>`.
4. GitHub creates one commit. A successful `201`/`200` response is the publish confirmation; there is no Blob upload or GitHub Action waiting step.
5. The dashboard clears the temporary token, refreshes its source list, and reports the resulting commit revision.
6. A TV sees the new media the next time its user presses **Refresh Sources**, or during its normal startup source refresh.

The browser-to-GitHub request bypasses Vercel's request-body limit. GitHub supports CORS requests and the Contents API accepts Base64 file content. The repository's enforced 100 MiB object limit is why this project keeps its lower 95 MiB media limit.

### B. Stage a source to one or more TVs

1. The dashboard verifies that the selected source already exists in `tv-sources`.
2. One authenticated dashboard request sends the source and all selected receiver IDs to Vercel.
3. Vercel reads `tv-control/receiver-manifest.json`, changes all selected receiver entries, assigns a unique command revision to each, and writes the complete manifest back in **one commit**.
4. Vercel invalidates its cached manifest immediately after the commit succeeds.
5. Each TV calls the existing `/api/receiver/tv-N` endpoint on its next interval. The endpoint selects just that TV's command from the cached manifest.
6. The receiver compares the revision with the revision already applied. It changes playback only for a newer revision.

No TV writes a status record. A staging command is expected to arrive within 60 seconds after the manifest write; TV startup also performs an immediate command check.

### C. Source refresh on a TV

The **Refresh Sources** button remains independent from Push To:

1. The TV immediately reads the public `tv-sources` GitHub `main` branch.
2. It lists only files under `sources/` and syncs/downloads them as it does today.
3. It ignores `tv-control` entirely.

A recently staged source that is not yet present locally can still cause the receiver to refresh its source library before playback, preserving the existing fallback behavior.

## Required code changes

### Dashboard (`frontend/`)

- Replace `@vercel/blob` upload flow and Blob callback routes with direct GitHub-upload session routes.
- Add GitHub App token creation server-side; do not reuse or expose `GITHUB_SOURCE_MANAGER_TOKEN` in the browser.
- Change source-library GitHub configuration from the current monorepo to `tv-sources`.
- Replace Blob-backed `receivers.ts` reads/writes with a manifest client for `tv-control`.
- Change `POST /api/receivers` to support a batch `receiverIds` request and write one manifest commit.
- Keep `GET /api/receiver/[receiverId]` and its response shape stable so installed TVs continue to work during the control migration.
- Replace live online/offline dots with `Last staged` and command-revision display. Do not silently treat a failed status request as `Loading TVs…` forever; show a clear retryable error.
- Add a visible upload queue, one-at-a-time enforcement, retry/cancel messaging, and large-file memory warning.

### GitHub workflow

- Retire `publish-source.yml` only after direct GitHub uploads have passed the acceptance tests.
- Remove Blob cleanup and Blob environment dependencies from that workflow.
- Keep source-file validation in the dashboard before token issuance and verify GitHub's committed result after upload.

### Tizen receiver (`tizen_receiver_app/`)

- Change the source GitHub owner/repository constants to `tv-sources`.
- Change the command poll interval from 30 seconds to 60 seconds.
- Retain the existing receiver ID and `/api/receiver/tv-N` command endpoint, including revision de-duplication.
- Remove only UI language that promises a live heartbeat if it exists; the TV does not need to send a presence event.
- Rebuild and deploy this version to all six TVs during the migration window.

## Capacity budget and guardrails

The original failure occurred because an unattended per-TV loop performed Blob reads every 30 seconds and wrote a heartbeat every five minutes. The target design has no unattended external writes.

| Resource | Design budget | Guardrail |
| --- | --- | --- |
| GitHub media file size | 95 MiB raw file maximum | Warn for files larger than 50 MiB. A 95 MiB Base64 payload is about 127 MiB and can use several hundred MiB of browser memory while encoding. |
| GitHub media writes | Normally a few per day | One upload at a time; limit dashboard commits to 4/minute and 100/hour. GitHub documents secondary content-creation limits of 80/minute and 500/hour. |
| GitHub control writes | One commit per Push To action, regardless of TV count | Serialize manifest writes; reread and retry on a SHA conflict. Never write a separate manifest per TV. |
| TV command requests | 6 TVs x 1/minute = about 259,200 endpoint requests/month | Cache one manifest, not six per-TV files. A command update invalidates that cache. |
| GitHub control reads | At most one cached manifest refresh per cache interval, plus revalidation after a command change | TVs call Vercel; they do not call GitHub REST APIs with a token. |
| Vercel deployments | Source-code deploys only | `tv-sources` and `tv-control` commits must not trigger dashboard deployments. |
| Source repository size | Keep below approximately 1 GB when practical; strongly below 5 GB | Replacing a video retains older Git history. Periodically review repository size and archive/replace media deliberately. |

GitHub's documented file and repository limits, API rate limits, and CORS support should be treated as product constraints, not as targets to run at continuously:

- [GitHub repository limits](https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits)
- [GitHub REST rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
- [GitHub Contents API](https://docs.github.com/en/rest/repos/contents)
- [GitHub CORS support](https://docs.github.com/en/rest/using-the-rest-api/using-cors-and-jsonp-to-make-cross-origin-requests)

## Rollout phases

### Phase 0 — Preserve current state and prepare

- Do not delete the current Blob store, current staging files, or workflow yet.
- Create the two GitHub repositories and Apps.
- Populate `tv-sources` with a verified copy of the current source library.
- Create an empty control manifest and validate its schema with fixture data.
- Add all required Vercel environment variables in Development first, then Preview, then Production.

**Exit criteria:** No secret is browser-visible except a temporary source-repo installation token; all repository permissions are restricted as described above.

### Phase 1 — Restore dashboard Push To without Blob

- Implement the private control-manifest reader/writer and the batch stage endpoint.
- Keep `/api/receiver/tv-N` stable and serve commands from the manifest.
- Remove Blob status reads from the dashboard so the receiver chooser always displays all configured TVs.
- Show `Last staged` rather than online/offline dots.
- Deploy to Preview, then Production.

**Expected result:** Push To works before the current Blob quota resets. Existing TVs need no reinstall for this phase. The upload button may remain temporarily disabled with an honest explanation and a GitHub Desktop fallback.

### Phase 2 — Add direct GitHub media upload

- Implement one-use upload-session issuance and direct browser Contents API publishing.
- Keep uploads serialized and confirm the committed SHA before reporting success.
- Test browser memory, error handling, and retry behavior at the file sizes listed below.
- Replace the Blob upload UI only after the test matrix passes.

**Expected result:** The dashboard file picker publishes media without Blob or the publish workflow.

### Phase 3 — Migrate TV source library

- Build a receiver that points to `tv-sources` and uses the 60-second command interval.
- Deploy it one TV at a time, verifying source refresh, staged playback, and picture-in-picture after each installation.
- Keep the old source repository available until every TV has passed verification.

**Expected result:** Every TV uses the source-only public repository and the private control manifest through Vercel.

### Phase 4 — Decommission Blob safely

- Disable/remove Blob environment variables from Preview and Production after a stable observation period.
- Remove Blob SDK dependencies and the Blob publish workflow.
- Leave an architecture note in the repository explaining why periodic command heartbeats must not be reintroduced.

**Exit criteria:** Vercel Blob usage remains at zero after at least one full media upload, TV refresh, single-TV push, multi-TV push, and picture-in-picture push.

## Acceptance test matrix

| Test | Expected result |
| --- | --- |
| Upload 1 MiB image | Direct GitHub commit succeeds; dashboard lists it; a TV finds it after Refresh Sources. |
| Upload 25 MiB video | Same as above, with visible progress and no Vercel Blob request. |
| Upload 75 MiB video | Browser remains responsive enough to finish; committed GitHub object checksum/size matches local file. |
| Upload 95 MiB video | Validate on the intended dashboard computer and network before declaring this path supported. A failure must leave no partially published source. |
| Cancel/interrupted upload | Dashboard clears the temporary token and offers retry; no source record is presented as published. |
| Stage one TV | One control commit; selected TV changes only once, within 60 seconds. |
| Stage six TVs | One control commit; all six commands receive independent revisions; no six-write loop occurs. |
| Reopen dashboard | TV chooser renders all six TVs even if no receiver has recently checked in. |
| Refresh Sources on each TV | Each TV reads `tv-sources`, lists only media below `sources/`, and can play a newly uploaded file. |
| Off-site dashboard use | Upload and Push To work from a network unrelated to the TV Wi-Fi. |
| Blob usage dashboard | No new Blob Simple or Advanced Operations result from any test. |

## Rollback plan

### Before receiver migration

If Phase 1 has a problem, restore the prior Vercel deployment. Existing receiver packages continue using their current GitHub source library. Blob-backed staging remains unavailable until the Blob quota resets, but no media or receiver installation is lost.

### During receiver migration

Keep the prior signed receiver package and deployment targets. If a migrated TV cannot refresh from `tv-sources`, reinstall the prior receiver package for that TV and leave the source repository available while investigating.

### After direct-upload launch

Disable the dashboard direct-upload control if a GitHub API, memory, or token issue appears. Existing published source files remain in GitHub; no media is deleted by disabling the feature. Use GitHub Desktop as the temporary publishing fallback.

## Operational checklist

- Review GitHub App installations and permissions monthly.
- Monitor GitHub API response headers and dashboard commit-rate telemetry; alert before the UI-imposed limits are reached.
- Monitor Vercel Function invocation and data-cache usage after the first full month.
- Review `tv-sources` repository size quarterly.
- Keep media files under 95 MiB and avoid Git LFS; the receiver downloads ordinary GitHub raw URLs.
- Do not add a background TV heartbeat, per-TV status write, or sub-minute polling loop without first calculating its monthly operation budget.
- Document every new remote store's hard quota, reset window, and failure mode before putting it on an unattended timer.

## User decisions required before implementation

1. Approve the three-repository layout (`tv`, `tv-sources`, `tv-control`).
2. Approve creation of two GitHub Apps and their limited permissions.
3. Confirm that a one-time receiver redeployment to all six TVs is acceptable.
4. Confirm whether a 60-second staged-command delivery target is acceptable.
5. Confirm whether files near 95 MiB should be supported in the web uploader after the required browser/network acceptance test, or whether the web UI should cap direct uploads lower and route large files to GitHub Desktop.

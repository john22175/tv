# MultiHub TV Library

This repository contains the desktop control app, Tizen receiver, and source-management dashboard for MultiHub TVs. The production media library is kept in a separate public GitHub source repository, and TV command state is kept in a separate private GitHub control repository.

| Directory | Purpose |
| --- | --- |
| [`sources/`](sources/) | Legacy/local source tree; production receivers use the configured `tv-sources` repository after migration. |
| [`frontend/`](frontend/) | Password-protected Next.js dashboard deployed to Vercel. |
| [`tizen_receiver_app/app/`](tizen_receiver_app/app/) | Canonical editable Tizen receiver project. |
| [`multihub/`](multihub/) | Desktop PyQt controller and local media server. |

The receiver reads public GitHub source files without credentials. The dashboard protects editing with a Vercel environment password; it cannot make source media private. The Blob-free architecture and deployment setup are documented in [`frontend/README.md`](frontend/README.md); the migration plan is in [`transition_plan.md`](transition_plan.md), and the legacy rollback reference is in [`docs/legacy-blob-rollback.md`](docs/legacy-blob-rollback.md).

See [`tizen_receiver_app/README.md`](tizen_receiver_app/README.md) for receiver deployment.

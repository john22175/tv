# MultiHub TV Library

This repository contains the public source library, desktop control app, Tizen receiver, and source-management dashboard for MultiHub TVs.

| Directory | Purpose |
| --- | --- |
| [`sources/`](sources/) | The only files published to TV receiver libraries. |
| [`frontend/`](frontend/) | Password-protected Next.js dashboard deployed to Vercel. |
| [`tizen_receiver_app/app/`](tizen_receiver_app/app/) | Canonical editable Tizen receiver project. |
| [`multihub/`](multihub/) | Desktop PyQt controller and local media server. |

The receiver and dashboard use public GitHub source files. The dashboard protects write access with a Vercel environment password; it cannot make the source files private because TVs download them without GitHub credentials.

See [`frontend/README.md`](frontend/README.md) for Vercel setup and [`tizen_receiver_app/README.md`](tizen_receiver_app/README.md) for receiver deployment.

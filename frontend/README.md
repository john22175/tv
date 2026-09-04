# TV Sources Dashboard

This Next.js app manages the repository's public [`sources/`](../sources/) directory. The TV receiver reads those files directly from GitHub without credentials, so the dashboard password protects **editing access only**. It does not make source files private.

## Before you start

- Commit and push this repository, including `.github/workflows/publish-source.yml`, before importing it into Vercel.
- The dashboard's GitHub workflow always commits to `main`; keep `main` as the production branch.
- Use Node.js 20.9 or newer locally. This project was verified with Node 22.

## 1. Create the GitHub access token

1. In GitHub, open **Settings → Developer settings → Personal access tokens → Fine-grained tokens** and create a token.
2. Set its resource owner to the account that owns `john22175/tv`, then restrict repository access to **Only select repositories** → `tv`.
3. Under **Repository permissions**, set **Contents** to **Read and write** and **Actions** to **Read and write**. Leave every other permission at its minimum/default value.
4. Generate and copy the token now. It will be used only as Vercel's `GITHUB_SOURCE_MANAGER_TOKEN`; never put it in a file, browser variable, or `NEXT_PUBLIC_*` variable.

The dashboard reads/deletes source files through GitHub's Contents API and triggers the publish workflow with the Actions API.

## 2. Import the repository into Vercel

1. In Vercel, select **Add New → Project** and import the `john22175/tv` GitHub repository.
2. In **Configure Project**, set **Root Directory** to `frontend`.
3. Confirm the framework is **Next.js**. Leave the default install command and build command (`npm run build`) unchanged.
4. Set **Production Branch** to `main`.
5. Do not deploy yet; add the environment variables below first.

## 3. Create and connect Vercel Blob

1. Open the new Vercel project, then go to **Storage → Create Database → Blob**.
2. Choose **Public** access. The temporary upload must be downloadable by the GitHub Actions runner; the final source file will also be public on GitHub.
3. Create the store and connect it to **Production**, **Preview**, and **Development**.
4. Vercel adds `BLOB_READ_WRITE_TOKEN` automatically. Do not expose it to the browser.

The dashboard uses authenticated browser-to-Blob uploads so media never passes through a Vercel Function. This is required for files larger than Vercel Functions' 4.5 MB request limit. [Vercel's client-upload guide](https://vercel.com/docs/vercel-blob/client-upload) documents this flow.

## 4. Add Vercel environment variables

In **Project → Settings → Environment Variables**, add every variable below to **Production**, **Preview**, and **Development**. None may use the `NEXT_PUBLIC_` prefix.

| Variable | Value |
| --- | --- |
| `SOURCE_DASHBOARD_PASSWORD` | A long, unique password used to sign in to the dashboard. |
| `SESSION_SECRET` | A random 32-byte-or-longer secret used to sign session cookies. Generate one with `node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))"`. |
| `GITHUB_SOURCE_MANAGER_TOKEN` | The fine-grained GitHub token from step 1. |
| `GITHUB_OWNER` | `john22175` |
| `GITHUB_REPOSITORY` | `tv` |
| `GITHUB_BRANCH` | `main` |
| `BLOB_READ_WRITE_TOKEN` | Created automatically when the Blob store was connected. Verify it is present. |

Save the variables, then deploy the project. Future pushes to `main` deploy the dashboard automatically.

## 5. Enable the GitHub publish workflow

1. In the GitHub repository, open **Settings → Actions → General**.
2. Under **Workflow permissions**, select **Read and write permissions** and save. This lets `publish-source.yml` commit the validated file to `sources/`.
3. Open **Settings → Secrets and variables → Actions → New repository secret**.
4. Create a secret named `BLOB_READ_WRITE_TOKEN` and paste the exact same token stored in Vercel.

The workflow deletes the temporary Blob after it either publishes or fails. Never add the token as a repository variable or commit it to `.env.example`.

## 6. Verify the live workflow

1. Open the Vercel production URL and sign in with `SOURCE_DASHBOARD_PASSWORD`.
2. Upload a small supported file such as a PNG. The dashboard should show upload progress, then **Waiting for GitHub to publish it**.
3. In GitHub, open the **Actions** tab and confirm **Publish TV source** completes successfully.
4. Confirm the file appears in [`sources/`](../sources/) on the `main` branch and in the dashboard's Published library table.
5. On a TV, open the receiver and select **Refresh Sources**. The new file should appear without reinstalling the receiver app.
6. Delete the test source in the dashboard and confirm a deletion commit appears on `main`.

## Local development (optional)

```powershell
cd frontend
npm ci
npm run dev
```

For real uploads from a local server, install the Vercel CLI, run `vercel link`, then use `vercel env pull .env.local`. Vercel Blob's completion callback must be reachable from the internet, so use a tunnel such as ngrok for local upload testing or verify uploads in the deployed Preview/Production environment instead.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Login always fails | Verify `SOURCE_DASHBOARD_PASSWORD` is set in the deployment's environment, then redeploy. |
| Upload is rejected before starting | Check the flat filename, supported extension, unique name, and 95 MiB per-file limit. |
| Upload waits more than five minutes | Open the failed **Publish TV source** workflow run. Common causes are a missing GitHub token permission or missing GitHub `BLOB_READ_WRITE_TOKEN` secret. |
| Workflow cannot push | Confirm `main` is not blocking GitHub Actions through branch protection, or allow the `github-actions[bot]` token to push. |
| TV does not show a published source | Confirm the file is under root `sources/`, then use **Refresh Sources** or restart the receiver. |

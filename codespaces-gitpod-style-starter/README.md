# Cloud Dev Environments (Codespaces/Gitpod–style)

This is a practical starter that mirrors how **GitHub Codespaces** and **Gitpod** work:

- **Workspace as Code** using Dev Containers (`.devcontainer/devcontainer.json`)
- **Prebuilds** via GitHub Actions (build/push the devcontainer image)
- **Control plane** (FastAPI) that resolves the repo → image → launches an IDE container
- **Kubernetes** manifests for running workspaces as Pods (with Ingress)
- **Reverse proxy** ready: use Traefik/NGINX to route `https://<ws-id>.<domain>` to the workspace

> You can run locally with Docker first, then move to Kubernetes.

## Quick start (local Docker)

1. Install: Docker Desktop, Python 3.11+, `pip install -r backend/requirements.txt`
2. Run API:
   ```bash
   cd backend
   uvicorn app:app --reload --port 8000
   ```
3. Open the UI:
   - `frontend/index.html` (static page)
4. Create a workspace:
   - Paste a public Git repo URL + username → click **Launch**.
   - The control plane will:
     - try to use a **prebuilt image** (if provided),
     - else resolve **devcontainer.json** from repo,
     - else **fallback** to a base image and start `code-server`,
     - clone the repo **inside** the container,
     - return a **URL** to the web IDE.

## Prebuilds (instant starts)

- Push your repo with a `.devcontainer/devcontainer.json`.
- Enable the provided GitHub Action: `.github/workflows/devcontainer-prebuild.yml`.
- The action builds your devcontainer and pushes it to GHCR.
- Store the resulting image reference in your DB (or copy it manually) and pass to the API when launching.

## Kubernetes (prod-like)

- Use `k8s/workspace-pod.yaml` and `k8s/ingress-example.yaml`.
- One Pod per workspace, port 8080 (coder/code-server default).
- Add persistence via PVC for home, optionally snapshot to S3/GCS on stop.
- Front with Traefik/NGINX for subdomain routing.

## Security

- Add OAuth (GitHub/GitLab) to the control plane.
- Lock down egress with NetworkPolicies; consider gVisor/Kata/Firecracker if you need stronger isolation.
- Keep an idle reaper to stop workspaces after inactivity.

## Folders

- `backend/` – FastAPI control plane
- `frontend/` – barebones UI (form posts to API)
- `.github/workflows/` – prebuild pipeline
- `k8s/` – manifests to run on Kubernetes
- `templates/` – fallback devcontainer snippets


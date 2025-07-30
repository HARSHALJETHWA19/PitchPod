# backend/app.py
import os, subprocess, uuid, time, json, tempfile
from fastapi import FastAPI, Form
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

app = FastAPI()
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# Change in production (or set IDE_PASSWORD env)
PASSWORD = os.getenv("IDE_PASSWORD", "devpass123")

# -------------- utility --------------

def sh(cmd, allow_fail=False):
    """Run a shell command, return stdout. On error, raise with combined output."""
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        return out.strip()
    except subprocess.CalledProcessError as e:
        if allow_fail:
            return e.output
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{e.output}")

def resolve_devcontainer(repo_url: str) -> dict:
    """
    Read .devcontainer/devcontainer.json from the repo (shallow clone).
    If absent, fallback to simple detection (Node/Python/etc.) and return a minimal spec.
    """
    with tempfile.TemporaryDirectory() as tmp:
        sh(["git", "clone", "--depth", "1", repo_url, tmp])
        dc_path = os.path.join(tmp, ".devcontainer", "devcontainer.json")
        if os.path.exists(dc_path):
            with open(dc_path) as f:
                return json.load(f)

        files = os.listdir(tmp)
        # very light detection
        if "package.json" in files:
            return {
                "image": "mcr.microsoft.com/devcontainers/javascript-node:1-20-bullseye",
                "forwardPorts": [3000],
                "postCreateCommand": "npm ci"
            }
        if "requirements.txt" in files:
            return {
                "image": "mcr.microsoft.com/devcontainers/python:3.11",
                "postCreateCommand": "pip install -r requirements.txt || true"
            }
        if "pom.xml" in files:
            return {"image": "mcr.microsoft.com/devcontainers/java:17"}
        if "main.go" in files:
            return {"image": "mcr.microsoft.com/devcontainers/go:1.22"}
        # default minimal base; we’ll inject code-server at runtime
        return {"image": "mcr.microsoft.com/devcontainers/base:ubuntu"}

def default_port_for_image(image: str) -> str:
    """
    code-server default ports by image family:
      - coder/code-server: 8080
      - linuxserver/code-server: 8443
      - generic (when we run code-server ourselves): 8080
    """
    img = image.lower()
    if "linuxserver/code-server" in img or "lscr.io/linuxserver/code-server" in img:
        return "8443"
    return "8080"

def is_codeserver_image(image: str) -> bool:
    img = image.lower()
    return any(x in img for x in [
        "coder/code-server", "ghcr.io/coder/code-server",
        "linuxserver/code-server", "lscr.io/linuxserver/code-server"
    ])

def wait_for_port(container_id: str, container_port: str, timeout_sec: int = 30) -> str:
    """
    Poll `docker port <id> <port>` until it returns a mapping or timeout.
    Returns the host port string (e.g., "32770").
    """
    start = time.time()
    last_err = None
    while time.time() - start < timeout_sec:
        try:
            pline = sh(["docker", "port", container_id, container_port])
            # Formats like: "0.0.0.0:32770" or "0.0.0.0:32770\n"
            return pline.split(":")[-1].strip()
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for port mapping on {container_id}/{container_port}: {last_err}")

# -------------- API --------------

@app.post("/start-workspace")
def start_workspace(
    repo: str = Form(...),
    username: str = Form(...),
    prebuilt_image: str = Form(None)  # optional: pass GHCR image from your prebuild pipeline
):
    """
    Start a Codespaces/Gitpod-like workspace:
      - Resolve devcontainer.json, or fallback by detection.
      - Prefer prebuilt image if provided; else use devcontainer image.
      - If it's already a code-server image, just run it.
      - Otherwise, install code-server inside a generic image and run it.
      - Clone the repo inside the container (/workspace).
      - Return the URL to open the IDE.
    """
    try:
        ws_id = f"{username}-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}".lower()
        work_vol = f"code_{ws_id}"
        tools_vol = f"tools_{username}".lower()   # persistent toolchains (optional future use)

        devc = resolve_devcontainer(repo)
        base_image = prebuilt_image or devc.get("image") or "mcr.microsoft.com/devcontainers/base:ubuntu"
        container_port = default_port_for_image(base_image)
        codeserver_img = is_codeserver_image(base_image)

        # Build docker run command (no host path mounts → cross-platform friendly)
        run_cmd = [
            "docker", "run", "-d",
            "-p", f"0:{container_port}",
            "--name", ws_id,
            "--label", f"workspace_id={ws_id}",
            "-v", f"{work_vol}:/workspace",
            "-v", f"{tools_vol}:/opt/tools"
        ]

        if codeserver_img:
            # Known code-server image: run default entrypoint, pass PASSWORD via env
            run_cmd += ["-e", f"PASSWORD={PASSWORD}", base_image]
            container_id = sh(run_cmd)

            # First-time: clone repo inside
            sh([
                "docker","exec","-i",container_id,"bash","-lc",
                f"apt-get update || true; apt-get install -y git curl || true; "
                f"rm -rf /workspace/* 2>/dev/null || true; git clone '{repo}' /workspace || true"
            ])
        else:
            # Generic devcontainer image: install & run code-server ourselves.
            # IMPORTANT: Do NOT pass --password; export PASSWORD env instead.
            run_cmd += [
                base_image,
                "bash", "-lc",
                f"""
set -e
apt-get update && apt-get install -y curl git ca-certificates || true

# Clone repo (if volume empty)
if [ -z "$(ls -A /workspace 2>/dev/null)" ]; then
  git clone '{repo}' /workspace || true
fi

# Install code-server
curl -fsSL https://code-server.dev/install.sh | sh

# Set password via environment (newer code-server refuses --password flag)
export PASSWORD='{PASSWORD}'

# Start code-server in foreground so the container stays alive
exec code-server /workspace --auth password --bind-addr 0.0.0.0:{container_port}
"""
            ]
            container_id = sh(run_cmd)

        # Optional: run postCreateCommand from devcontainer.json
        pcc = devc.get("postCreateCommand")
        if pcc:
            sh(["docker","exec","-i",container_id,"bash","-lc", f"cd /workspace && {pcc}"], allow_fail=True)

        # Wait until Docker exposes the mapped host port, then return URL
        host_port = wait_for_port(container_id, container_port, timeout_sec=30)
        return {
            "message": "Workspace started",
            "workspace_url": f"http://localhost:{host_port}",
            "container_id": container_id,
            "image_used": base_image,
            "devcontainer_used": bool(devc)
        }

    except Exception as e:
        # Try to include recent docker logs when something goes wrong
        detail = str(e)
        try:
            # Extract a container id if present in the message (best-effort)
            # Fallback: list last container
            last = sh(["docker", "ps", "-aq"], allow_fail=True).splitlines()
            cand = last[0] if last else None
            if cand:
                logs = sh(["docker", "logs", "--tail", "100", cand], allow_fail=True)
                detail += f"\n\n--- docker logs (tail) ---\n{logs}"
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"detail": detail})

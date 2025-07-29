# import os, subprocess, uuid, shutil
# from fastapi import FastAPI, Form
# from fastapi.middleware.cors import CORSMiddleware
# from starlette.responses import JSONResponse
# import shutil
# import time

# app = FastAPI()
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
# )

# @app.post("/start-workspace")
# def start_workspace(repo: str = Form(...), username: str = Form(...)):
#     try:
#         workspace_id = f"{username}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
#         workspace_path = f"/tmp/{workspace_id}"
#         if os.path.exists(workspace_path):
#             try:
#                 shutil.rmtree(workspace_path)
#             except Exception as cleanup_error:
#                 return JSONResponse(status_code=500, content={"detail": f"Failed to delete existing workspace: {cleanup_error}"})


#         # Create .devcontainer
#         dev_path = os.path.join(workspace_path, ".devcontainer")
#         os.makedirs(dev_path, exist_ok=True)

#         with open(os.path.join(dev_path, "devcontainer.json"), "w") as f:
#             f.write(f'''{{
#   "name": "Universal DevContainer",
#   "build": {{
#     "dockerfile": "Dockerfile"
#   }},
#   "settings": {{
#     "terminal.integrated.shell.linux": "/bin/bash"
#   }},
#   "postCreateCommand": "git pull",
#   "forwardPorts": [3000, 5000, 8000, 8888],
#   "remoteUser": "vscode"
# }}''')

#         with open(os.path.join(dev_path, "Dockerfile"), "w") as f:
#             f.write('''FROM mcr.microsoft.com/devcontainers/universal:2
# RUN apt-get update && apt-get install -y git curl vim''')

#         # Launch IDE container
#         cmd = [
#             "docker", "run", "-d",
#             "-v", f"{workspace_path}:/config/workspace",
#             "-e", "PASSWORD=harshal123",
#             "-p", "0:8443",
#             "--name", workspace_id,
#             "--label", f"workspace_id={workspace_id}",
#             "linuxserver/code-server:latest"
#         ]

#         container_id = subprocess.check_output(cmd).decode().strip()
#         port_output = subprocess.check_output(["docker", "port", container_id, "8443"]).decode().strip()
#         host_port = port_output.split(":")[-1]

#         return {
#             "message": "Workspace created",
#             "workspace_url": f"http://localhost:{host_port}",
#             "container_id": container_id
#         }

#     except Exception as e:
#         return JSONResponse(status_code=500, content={"detail": str(e)})

import os, subprocess, uuid, shutil
from fastapi import FastAPI, Form
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
import time

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

def detect_language(path):
    files = os.listdir(path)
    if "package.json" in files:
        return "node"
    elif "requirements.txt" in files or any(f.endswith(".py") for f in files):
        return "python"
    elif "pom.xml" in files:
        return "java"
    elif "main.go" in files:
        return "go"
    elif any(f.endswith(".c") or f.endswith(".cpp") for f in files):
        return "cpp"
    else:
        return "universal"

@app.post("/start-workspace")
def start_workspace(repo: str = Form(...), username: str = Form(...)):
    try:
        workspace_id = f"{username}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        workspace_path = f"/tmp/{workspace_id}"

        # Clean if exists
        if os.path.exists(workspace_path):
            shutil.rmtree(workspace_path)
        os.makedirs(workspace_path, exist_ok=True)

        # Clone Git repo
        subprocess.run(["git", "clone", repo, workspace_path], check=True)

        # Detect Language
        language = detect_language(workspace_path)

        image_map = {
            "node": "ghcr.io/devcontainers/javascript-node:18",
            "python": "ghcr.io/devcontainers/python:3.11",
            "java": "ghcr.io/devcontainers/java:latest",
            "go": "ghcr.io/devcontainers/go:latest",
            "cpp": "ghcr.io/devcontainers/cpp:latest",
            "universal": "mcr.microsoft.com/devcontainers/universal:2"
        }

        image = image_map.get(language, image_map["universal"])

        # Run Docker with code-server
        cmd = [
            "docker", "run", "-d",
            "-v", f"{workspace_path}:/home/vscode/workspace",
            "-p", "0:8443",
            "--name", workspace_id,
            "--label", f"workspace_id={workspace_id}",
            image,
            "code-server",
            "--auth", "password",
            "--password", "harshal123",
            "--bind-addr", "0.0.0.0:8443",
            "/home/vscode/workspace"
        ]

        container_id = subprocess.check_output(cmd).decode().strip()

        port_output = subprocess.check_output(["docker", "port", container_id, "8443"]).decode().strip()
        host_port = port_output.split(":")[-1]

        return {
            "message": "Workspace created",
            "workspace_url": f"http://localhost:{host_port}",
            "container_id": container_id,
            "language": language
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

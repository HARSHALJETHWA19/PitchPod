from fastapi import FastAPI, Form, HTTPException
from datetime import datetime
import os, subprocess, shutil
import json
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or restrict to ['http://localhost'] for safety
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/start-workspace")
def start_workspace(
    repo: str = Form(...),
    language: str = Form(...),
    username: str = Form(...)
):
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    workspace_id = f"{username}_{language}_{timestamp}"
    workspace_path = f"/tmp/{workspace_id}"

    if os.path.exists(workspace_path):
        shutil.rmtree(workspace_path)

    try:
        subprocess.run(["git", "clone", repo, workspace_path], check=True)
    except subprocess.CalledProcessError as e:
        if os.path.exists(workspace_path):
            shutil.rmtree(workspace_path)
        raise HTTPException(status_code=500, detail=f"Git clone failed: {e}")

    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template_path = os.path.join(BASE_DIR, "templates", language)

    try:
        shutil.copytree(template_path, os.path.join(workspace_path, ".devcontainer"), dirs_exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Devcontainer copy failed: {e}")

    # Launch code-server in Docker
    docker_run = [
        "docker", "run", "-d",
        "-v", f"{workspace_path}:/workspace",
        "-p", "0:8443",  # dynamic port
        "-e", "PASSWORD=harshal123",
        "--name", workspace_id,
        "--label", f"workspace_id={workspace_id}",
        "linuxserver/code-server:latest"
    ]

    # try:
    #     result = subprocess.run(docker_run, capture_output=True, text=True, check=True)
    #     container_id = result.stdout.strip()
    # except subprocess.CalledProcessError as e:
    #     raise HTTPException(status_code=500, detail=f"Failed to start container: {e.stderr}")

    try:
        print("Running Docker with command:")
        print(" ".join(docker_run))

        result = subprocess.run(docker_run, capture_output=True, text=True, check=True)
        container_id = result.stdout.strip()
        print(f"Docker container started with ID: {container_id}")

    except subprocess.CalledProcessError as e:
        print("❌ Docker run failed!")
        print("STDOUT:", e.stdout)
        print("STDERR:", e.stderr)
        raise HTTPException(status_code=500, detail=f"Failed to start container: {e.stderr}")

    # Mock workspace URL (replace with real domain + Traefik setup)
    # workspace_url = f"http://localhost:PORT_REPLACE (map to container)"

    inspect_cmd = ["docker", "inspect", workspace_id]
    inspect_output = subprocess.run(inspect_cmd, capture_output=True, text=True, check=True)
    inspect_data = json.loads(inspect_output.stdout)

    # Get host port mapped to container's 8443
    port_info = inspect_data[0]["NetworkSettings"]["Ports"].get("8443/tcp")
    if port_info:
        host_port = port_info[0]["HostPort"]
        workspace_url = f"http://localhost:{host_port}"
    else:
        workspace_url = "Unavailable"

    return {
        "message": "Workspace created",
        "workspace_id": workspace_id,
        "url": workspace_url  # dynamically fetched from docker inspect
    }
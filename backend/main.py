
import os
import json
import shutil
import subprocess
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

load_dotenv()
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")

class WorkspaceRequest(BaseModel):
    repo: str
    language: str
    username: str

@app.get("/login")
def login_with_github():
    github_oauth_url = (
        f"https://github.com/login/oauth/authorize?client_id={GITHUB_CLIENT_ID}&scope=repo"
    )
    return RedirectResponse(github_oauth_url)

@app.get("/callback")
def github_callback(code: str):
    token_url = "https://github.com/login/oauth/access_token"
    headers = {"Accept": "application/json"}
    payload = {
        "client_id": GITHUB_CLIENT_ID,
        "client_secret": GITHUB_CLIENT_SECRET,
        "code": code
    }
    r = requests.post(token_url, headers=headers, data=payload)
    access_token = r.json().get("access_token")

    if not access_token:
        return {"error": "Login failed"}

    user_info = requests.get(
        "https://api.github.com/user",
        headers={"Authorization": f"token {access_token}"}
    ).json()

    return {
        "message": "Logged in successfully!",
        "username": user_info.get("login"),
        "access_token": access_token
    }

@app.post("/start-workspace")
def start_workspace(req: WorkspaceRequest):
    repo = req.repo
    language = req.language
    username = req.username
    workspace_id = f"{username}_{language}_{__import__('time').strftime('%Y%m%d%H%M%S')}"
    workspace_path = f"/tmp/{workspace_id}"
    template_path = f"./templates/{language}"

    try:
        subprocess.run(["git", "clone", repo, workspace_path], check=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Git clone failed: {str(e)}")

    if not os.path.exists(template_path):
        raise HTTPException(status_code=500, detail=f"Devcontainer not found for {language}")

    shutil.copytree(template_path, os.path.join(workspace_path, ".devcontainer"), dirs_exist_ok=True)

    docker_run = [
        "docker", "run", "-d",
        "-v", f"{workspace_path}:/workspace",
        "-p", "0:8443",
        "-e", "PASSWORD=harshal123",
        "--name", workspace_id,
        "--label", f"workspace_id={workspace_id}",
        "linuxserver/code-server:latest"
    ]

    try:
        subprocess.run(docker_run, check=True, capture_output=True, text=True)
        inspect_cmd = ["docker", "inspect", workspace_id]
        inspect_output = subprocess.run(inspect_cmd, capture_output=True, text=True, check=True)
        inspect_data = json.loads(inspect_output.stdout)
        port_info = inspect_data[0]["NetworkSettings"]["Ports"].get("8443/tcp")
        if port_info:
            host_port = port_info[0]["HostPort"]
            workspace_url = f"http://localhost:{host_port}"
        else:
            workspace_url = "Unavailable"
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Failed to start container: {e.stderr}")

    return {"message": "Workspace created", "workspace_id": workspace_id, "url": workspace_url}

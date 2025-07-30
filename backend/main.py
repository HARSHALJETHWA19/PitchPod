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

import os, subprocess, uuid, time, tempfile, textwrap
from fastapi import FastAPI, Form
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
import time

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

IMAGE_CANDIDATES = [
    "ghcr.io/coder/code-server:latest",           # GHCR
    "lscr.io/linuxserver/code-server:latest",     # LinuxServer
    "codercom/code-server:latest",                # Docker Hub (legacy image)
]
FALLBACK_LOCAL_TAG_PREFIX = "local-codeserver-"
PASSWORD = "harshal123"

def sh(cmd:list[str], allow_fail=False) -> str:
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        return out.strip()
    except subprocess.CalledProcessError as e:
        if allow_fail:
            return ""
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{e.output}")

def try_pull_with_retries(image:str, retries=3, sleep_sec=10) -> bool:
    for i in range(1, retries+1):
        try:
            print(f"[pull] Attempt {i}/{retries}: {image}")
            sh(["docker","pull",image])
            return True
        except Exception as err:
            print(f"[pull] {image} failed: {err}")
            time.sleep(sleep_sec)
    return False

def build_local_codeserver_image(tag:str) -> None:
    # Small local build that avoids MCR devcontainers. Uses ubuntu:22.04 (Docker Hub).
    dockerfile = textwrap.dedent(r"""
    FROM ubuntu:22.04
    ENV DEBIAN_FRONTEND=noninteractive
    RUN apt-get update && apt-get install -y curl git ca-certificates gnupg lsb-release build-essential \
        && rm -rf /var/lib/apt/lists/*
    # install code-server
    RUN curl -fsSL https://code-server.dev/install.sh | sh
    EXPOSE 8443
    CMD ["code-server","/config/workspace","--auth","password","--password","harshal123","--bind-addr","0.0.0.0:8443"]
    """)
    with tempfile.TemporaryDirectory() as tmp:
        df = os.path.join(tmp, "Dockerfile")
        with open(df, "w") as f:
            f.write(dockerfile)
        sh(["docker","build","-t", tag, tmp])

def ensure_image_available() -> str:
    # Try pull from several registries first
    for img in IMAGE_CANDIDATES:
        if try_pull_with_retries(img, retries=3, sleep_sec=8):
            return img
    # All pulls failed → build a tiny local image from Ubuntu base
    tag = FALLBACK_LOCAL_TAG_PREFIX + uuid.uuid4().hex[:8]
    build_local_codeserver_image(tag)
    return tag

@app.post("/start-workspace")
def start_workspace(repo: str = Form(...), username: str = Form(...)):
    try:
        # IDs & volumes
        workspace_id = f"{username}_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}".lower()
        tools_volume = f"tools_{username}".lower()
        code_volume  = f"code_{workspace_id}".lower()

        image = ensure_image_available()

        # Start code-server container (no host mounts)
        container_port = "8080"
        container_id = sh([
            "docker","run","-d",
            "-e", f"PASSWORD={PASSWORD}",
            "-p","0:8080",
            "--name", workspace_id,
            "--label", f"workspace_id={workspace_id}",
            "-v", f"{code_volume}:/config/workspace",
            "-v", f"{tools_volume}:/opt/tools",
            image
        ])

        # optional small wait
        time.sleep(1)

        port_line = sh(["docker","port", container_id, container_port])
        host_port = port_line.split(":")[-1].strip()
        workspace_url = f"http://localhost:{host_port}"

        def dexec(script:str) -> str:
            # Run multi-line bash inside the container
            return sh(["docker","exec","-i",container_id,"bash","-lc",script])

        # Prepare PATH to persistent tools
        dexec(r"""
mkdir -p /opt/tools/bin
grep -q '/opt/tools/bin' /etc/profile || echo 'export PATH=/opt/tools/bin:$PATH' >> /etc/profile
grep -q '/opt/tools/bin' ~/.bashrc   || echo 'export PATH=/opt/tools/bin:$PATH' >> ~/.bashrc
export PATH=/opt/tools/bin:$PATH
""")

        # Ensure base deps and clone repo inside the container
        dexec(r"apt-get update || true && apt-get install -y git curl ca-certificates || true")
        # Clean old files if any and clone
        dexec(f"rm -rf /config/workspace/* 2>/dev/null || true; git clone '{repo}' /config/workspace || true")

        # Detect language
        language = dexec(r"""
cd /config/workspace || exit 0
LANGUAGE="unknown"
if [ -f package.json ]; then LANGUAGE="node"; fi
if [ -f requirements.txt ] || ls *.py >/dev/null 2>&1; then LANGUAGE="python"; fi
if [ -f main.go ]; then LANGUAGE="go"; fi
if ls *.java >/dev/null 2>&1 || [ -f pom.xml ]; then LANGUAGE="java"; fi
if ls *.c >/dev/null 2>&1 || ls *.cpp >/dev/null 2>&1; then LANGUAGE="cpp"; fi
echo -n $LANGUAGE
""").strip() or "unknown"

        # Install runtime toolchain (idempotent; cached in /opt/tools)
        if language == "node":
            dexec(r"""
set -e
export PATH=/opt/tools/bin:$PATH
if [ ! -d /opt/tools/nvm ]; then
  git clone https://github.com/nvm-sh/nvm.git /opt/tools/nvm
  cd /opt/tools/nvm && git checkout `git describe --abbrev=0 --tags`
fi
export NVM_DIR=/opt/tools/nvm
. /opt/tools/nvm/nvm.sh
nvm install --lts
nvm use --lts
mkdir -p /opt/tools/bin
ln -sf $(which node) /opt/tools/bin/node || true
ln -sf $(which npm)  /opt/tools/bin/npm  || true
ln -sf $(which npx)  /opt/tools/bin/npx  || true
""")
        elif language == "python":
            dexec(r"""
set -e
export PATH=/opt/tools/bin:$PATH
if [ ! -d /opt/tools/pyenv ]; then
  apt-get update && apt-get install -y make libssl-dev zlib1g-dev libbz2-dev \
    libreadline-dev libsqlite3-dev wget llvm libncursesw5-dev xz-utils tk-dev \
    libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev
  git clone https://github.com/pyenv/pyenv.git /opt/tools/pyenv
fi
export PYENV_ROOT=/opt/tools/pyenv
export PATH="$PYENV_ROOT/bin:$PATH"
if ! pyenv versions | grep -q 3.11.; then pyenv install -s 3.11.9; fi
pyenv global 3.11.9
mkdir -p /opt/tools/bin
ln -sf $(pyenv which python) /opt/tools/bin/python || true
ln -sf $(pyenv which pip)    /opt/tools/bin/pip    || true
""")
        elif language == "go":
            dexec("apt-get update && apt-get install -y golang || true && ln -sf $(which go) /opt/tools/bin/go || true")
        elif language == "java":
            dexec("apt-get update && apt-get install -y openjdk-17-jdk maven || true && ln -sf $(which java) /opt/tools/bin/java || true && ln -sf $(which mvn) /opt/tools/bin/mvn || true")
        elif language == "cpp":
            dexec("apt-get update && apt-get install -y build-essential cmake || true && ln -sf $(which gcc) /opt/tools/bin/gcc || true && ln -sf $(which g++) /opt/tools/bin/g++ || true && ln -sf $(which cmake) /opt/tools/bin/cmake || true")
        # unknown → skip

        # Show mapped port
        port_line = sh(["docker","port",container_id,"8443"])
        host_port = port_line.split(":")[-1].strip()

        return {
            "message": "Workspace created",
            "workspace_url": f"http://localhost:{host_port}",
            "container_id": container_id,
            "language": language,
            "image_used": image if image.startswith(("ghcr.io","lscr.io","codercom/")) else "local build",
            "note": "Toolchains persist in a named volume: tools_<username>. First run may take longer."
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

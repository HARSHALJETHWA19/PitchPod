
import os, subprocess, uuid, time, json, tempfile, sqlite3, threading
from fastapi import FastAPI, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

# ---------------- Config ----------------
PASSWORD = os.getenv("IDE_PASSWORD", "devpass123")
IDLE_MINUTES = int(os.getenv("IDLE_MINUTES", "90"))   # stop after 90 minutes idle
DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")

# ---------------- App ----------------
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# in-memory idle tracker {container_id: epoch_seconds}
last_ping = {}
last_ping_lock = threading.Lock()

# ---------------- Utils ----------------
def sh(cmd, allow_fail=False):
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        return out.strip()
    except subprocess.CalledProcessError as e:
        if allow_fail:
            return e.output
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{e.output}")

def is_codeserver_image(image: str) -> bool:
    img = image.lower()
    return any(x in img for x in [
        "coder/code-server", "ghcr.io/coder/code-server",
        "linuxserver/code-server", "lscr.io/linuxserver/code-server"
    ])

def default_port_for_image(image: str) -> str:
    img = image.lower()
    if "linuxserver/code-server" in img or "lscr.io/linuxserver/code-server" in img:
        return "8443"
    return "8080"

def wait_for_port(container_id: str, container_port: str, timeout_sec: int = 60) -> str:
    start = time.time()
    last_err = None
    while time.time() - start < timeout_sec:
        try:
            pline = sh(["docker", "port", container_id, container_port])
            return pline.split(":")[-1].strip()
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for port mapping on {container_id}/{container_port}: {last_err}")

def resolve_devcontainer(repo_url: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        sh(["git", "clone", "--depth", "1", repo_url, tmp])
        dc_path = os.path.join(tmp, ".devcontainer", "devcontainer.json")
        if os.path.exists(dc_path):
            with open(dc_path) as f:
                return json.load(f)
        files = os.listdir(tmp)
        if "package.json" in files:
            return {"image": "mcr.microsoft.com/devcontainers/javascript-node:1-20-bullseye", "forwardPorts": [3000], "postCreateCommand": "npm ci"}
        if "requirements.txt" in files:
            return {"image": "mcr.microsoft.com/devcontainers/python:3.11", "postCreateCommand": "pip install -r requirements.txt || true"}
        if "pom.xml" in files:
            return {"image": "mcr.microsoft.com/devcontainers/java:17"}
        if "main.go" in files:
            return {"image": "mcr.microsoft.com/devcontainers/go:1.22"}
        return {"image": "mcr.microsoft.com/devcontainers/base:ubuntu"}

# ---------------- SQLite (prebuilds) ----------------
def db_init():
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS prebuilds (
            repo_url TEXT PRIMARY KEY,
            image TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )""")
        con.commit()
    finally:
        con.close()

def db_get_prebuilt(repo_url: str):
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.execute("SELECT image FROM prebuilds WHERE repo_url = ?", (repo_url,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        con.close()

def db_set_prebuilt(repo_url: str, image: str):
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("INSERT INTO prebuilds(repo_url,image,updated_at) VALUES(?,?,?) ON CONFLICT(repo_url) DO UPDATE SET image=excluded.image, updated_at=excluded.updated_at", (repo_url, image, int(time.time())))
        con.commit()
    finally:
        con.close()

# ---------------- Idle Reaper ----------------
def mark_ping(container_id: str):
    with last_ping_lock:
        last_ping[container_id] = time.time()

def reap_idle_loop():
    # background thread
    interval = 60  # check every minute
    while True:
        time.sleep(interval)
        try:
            cutoff = time.time() - IDLE_MINUTES * 60
            to_stop = []
            with last_ping_lock:
                for cid, ts in list(last_ping.items()):
                    if ts < cutoff:
                        to_stop.append(cid)
            for cid in to_stop:
                try:
                    sh(["docker", "rm", "-f", cid])
                except Exception as e:
                    pass
                with last_ping_lock:
                    last_ping.pop(cid, None)
        except Exception:
            # never crash
            pass

@app.on_event("startup")
def on_startup():
    db_init()
    t = threading.Thread(target=reap_idle_loop, daemon=True)
    t.start()

# ---------------- API ----------------
@app.post("/start-workspace")
def start_workspace(
    repo: str = Form(...),
    username: str = Form(...),
    prebuilt_image: str = Form(None)
):
    try:
        ws_id = f"{username}-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}".lower()
        work_vol = f"code_{ws_id}"
        tools_vol = f"tools_{username}".lower()

        # Detect language + get .devcontainer or fallback image
        devc = resolve_devcontainer(repo)
        mapped = prebuilt_image or db_get_prebuilt(repo)
        # base_image = mapped or devc.get("image") or "mcr.microsoft.com/devcontainers/base:ubuntu"

        base_image = mapped or devc.get("image") or "mcr.microsoft.com/devcontainers/base:ubuntu"
        sh(["docker", "pull", base_image])  # PRE-PULL to avoid timeout


        container_port = default_port_for_image(base_image)
        is_code_image = is_codeserver_image(base_image)

        run_cmd = [
            "docker", "run", "-d",
            "-p", f"0:{container_port}",
            "--name", ws_id,
            "--label", f"workspace_id={ws_id}",
            "-v", f"{work_vol}:/workspace",
            "-v", f"{tools_vol}:/opt/tools",
        ]

        if is_code_image:
            run_cmd += ["-e", f"PASSWORD={PASSWORD}", base_image]
            cid = sh(run_cmd)
            # Inject repo manually
            sh([
                "docker", "exec", "-i", cid, "bash", "-lc",
                f"apt-get update || true; apt-get install -y git curl || true; "
                f"rm -rf /workspace/* 2>/dev/null || true; git clone '{repo}' /workspace || true"
            ])
        else:
            run_cmd += [
                base_image, "bash", "-lc", f"""
set -e
apt-get update && apt-get install -y curl git ca-certificates || true
if [ -z "$(ls -A /workspace 2>/dev/null)" ]; then git clone '{repo}' /workspace || true; fi
curl -fsSL https://code-server.dev/install.sh | sh
export PASSWORD='{PASSWORD}'
exec code-server /workspace --auth password --bind-addr 0.0.0.0:{container_port}
"""
            ]
            cid = sh(run_cmd)

        # Optional postCreateCommand from devcontainer.json
        if devc.get("postCreateCommand"):
            sh([
                "docker", "exec", "-i", cid, "bash", "-lc",
                f"cd /workspace && {devc['postCreateCommand']}"
            ], allow_fail=True)

        # Wait until host port is mapped
        host_port = wait_for_port(cid, container_port, timeout_sec=120)
        # host_port = wait_for_port(cid, container_port, timeout_sec=120)
        mark_ping(cid)

        return {
            "message": "Workspace started",
            "workspace_url": f"http://localhost:{host_port}",
            "container_id": cid,
            "image_used": base_image,
            "devcontainer_used": bool(devc),
            "idle_timeout_minutes": IDLE_MINUTES
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})


@app.post("/stop-workspace")
def stop_workspace(container_id: str = Form(...)):
    try:
        sh(["docker", "stop", container_id])
        return {"message": "Stopped", "container_id": container_id}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

@app.post("/start-container")
def start_container(container_id: str = Form(...)):
    try:
        sh(["docker", "start", container_id])
        return {"message": "Started", "container_id": container_id}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})
    
@app.post("/delete-workspace")
def delete_workspace(container_id: str = Form(...)):
    try:
        sh(["docker", "rm", "-f", container_id])
        with last_ping_lock:
            last_ping.pop(container_id, None)
        return {"message": "Deleted", "container_id": container_id}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

@app.get("/workspaces")
def list_workspaces():
    # out = sh(["docker","ps","--filter","label=workspace_id","--format","{{.ID}} {{.Image}} {{.Names}} {{.Ports}}"], allow_fail=True)
    out = sh(["docker","ps","-a","--filter","label=workspace_id","--format","{{.ID}} {{.Image}} {{.Names}} {{.Ports}} {{.Status}}"], allow_fail=True)   
    items = []
    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue
        cid = parts[0]
        image = parts[1] if len(parts) > 1 else ""
        name = parts[2] if len(parts) > 2 else ""
        ports = parts[3] if len(parts) > 3 else ""
        status = " ".join(parts[4:]) if len(parts) > 4 else ""
        with last_ping_lock:
            lp = last_ping.get(cid)
        items.append({
            "id": cid,
            "image": image,
            "name": name,
            "ports": ports,
            "status": status,
            "last_ping": lp
        })

    # for line in out.splitlines():
    #     parts = line.split()
    #     if not parts: 
    #         continue
    #     cid = parts[0]
    #     image = parts[1] if len(parts) > 1 else ""
    #     name = parts[2] if len(parts) > 2 else ""
    #     ports = " ".join(parts[3:]) if len(parts) > 3 else ""
    #     with last_ping_lock:
    #         lp = last_ping.get(cid)
    #     items.append({"id": cid, "image": image, "name": name, "ports": ports, "last_ping": lp})
    return items

@app.get("/logs")
def get_logs(container_id: str = Query(..., alias="id")):
    out = sh(["docker","logs","--tail","200",container_id], allow_fail=True)
    return {"id": container_id, "logs": out}

@app.post("/ping")
def ping(container_id: str = Form(...)):
    # called by frontend every minute to keep workspace alive
    mark_ping(container_id)
    return {"ok": True, "idle_timeout_minutes": IDLE_MINUTES}

# --- Prebuild mapping endpoints ---
@app.post("/prebuild")
def set_prebuild(repo: str = Form(...), image: str = Form(...)):
    try:
        db_set_prebuilt(repo, image)
        return {"message": "Saved", "repo": repo, "image": image}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

@app.get("/prebuild")
def get_prebuild(repo: str):
    img = db_get_prebuilt(repo)
    return {"repo": repo, "image": img}




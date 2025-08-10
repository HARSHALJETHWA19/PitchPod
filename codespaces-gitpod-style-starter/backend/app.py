# backend/app.py
import os, subprocess, uuid, time, json, tempfile, sqlite3, threading, datetime, asyncio
from fastapi import FastAPI, Form, Query, HTTPException, Depends, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from starlette.responses import JSONResponse

# Config
PASSWORD = os.getenv("IDE_PASSWORD", "devpass123")
SECRET_KEY = "super-secret-key"
ALGORITHM = "HS256"
IDLE_MINUTES = 1
DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
AUTH_TOKENS = {}
USERS = {"admin": "devpass123"}
last_ping = {}
last_ping_lock = threading.Lock()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Utils
def sh(cmd, allow_fail=False):
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError as e:
        if allow_fail:
            return e.output
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{e.output}")

def verify_token(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return username
    except JWTError:
        raise HTTPException(status_code=401, detail="Token expired or invalid")

def db_init():
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS workspaces (container_id TEXT PRIMARY KEY, last_ping INTEGER)")
    con.execute("CREATE TABLE IF NOT EXISTS volume_history (username TEXT PRIMARY KEY, volume_name TEXT, updated_at INTEGER)")
    con.execute("CREATE TABLE IF NOT EXISTS prebuilds (repo_url TEXT PRIMARY KEY, image TEXT, updated_at INTEGER)")
    con.commit()
    con.close()

def mark_ping(cid):
    now = int(time.time())
    with last_ping_lock:
        last_ping[cid] = now
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT OR REPLACE INTO workspaces (container_id, last_ping) VALUES (?, ?)", (cid, now))
    con.commit()
    con.close()

async def stop_idle_containers_task():
    while True:
        now = int(datetime.datetime.now().timestamp())
        con = sqlite3.connect(DB_PATH)
        rows = con.execute("SELECT container_id, last_ping FROM workspaces").fetchall()
        con.close()
        for cid, ping in rows:
            if ping is None:
                continue
            if (now - ping) / 60 > IDLE_MINUTES:
                sh(["docker", "stop", cid], allow_fail=True)
        await asyncio.sleep(60)

@app.on_event("startup")
def on_startup():
    db_init()
    threading.Thread(target=lambda: asyncio.run(stop_idle_containers_task()), daemon=True).start()

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    public_paths = ["/login", "/docs", "/openapi.json"]
    if request.url.path in public_paths or request.method == "OPTIONS":
        return await call_next(request)

    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        exp = payload.get("exp")

        if username not in USERS or not exp or datetime.datetime.utcfromtimestamp(exp) < datetime.datetime.utcnow():
            return JSONResponse(status_code=401, content={"detail": "Token expired"})
        return await call_next(request)
    except JWTError:
        return JSONResponse(status_code=401, content={"detail": "Invalid token"})


@app.post("/start-workspace")
def start_workspace(container_id: str = Form(...)):
    try:
        sh(["docker", "start", container_id])
        return {"message": "Started"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

@app.post("/delete-workspace")
def delete_workspace(container_id: str = Form(...)):
    try:
        sh(["docker", "rm", "-f", container_id])
        return {"message": "Deleted"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})


@app.post("/start-workspace")
def start_workspace(
    repo: str = Form(...),
    username: str = Form(...),
    prebuilt_image: str = Form(""),
    reuse_volume: bool = Form(False),
    custom_volume_name: str = Form("")
):
    try:
        # Determine volume name
        volume_name = custom_volume_name or f"{username}-vol"
        if not reuse_volume:
            sh(["docker", "volume", "create", volume_name])

        # Pull or use prebuilt image
        image = prebuilt_image or "mcr.microsoft.com/devcontainers/dotnet:9.0"

        container_name = f"{username}-{uuid.uuid4().hex[:6]}"
        container_id = sh([
            "docker", "run", "-d",
            "-v", f"{volume_name}:/workspace",
            "-p", "0:8080",
            "--label", "workspace_id=1",
            "--name", container_name,
            image
        ])

        mark_ping(container_id)
        return {"id": container_id, "message": "Workspace started."}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

@app.post("/generate-devcontainer")
def generate_devcontainer(
    repo: str = Form(...),
    language: str = Form(...),
    ports: str = Form(""),
    post_create: str = Form("")
):
    config = {
        "name": f"{language}-dev",
        "image": f"myregistry/{language}-dev:latest",
        "appPort": ports,
        "postCreateCommand": post_create,
        "workspaceFolder": "/workspace"
    }
    return {"message": "Generated .devcontainer.json", "config": config}


@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...)):
    if username in USERS and USERS[username] == password:
        token_data = {"sub": username, "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=1)}
        token = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)
        AUTH_TOKENS[username] = token
        return {"token": token}
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/workspaces")
def list_workspaces():
    out = sh(["docker", "ps", "-a", "--filter", "label=workspace_id", "--format", "{{.ID}} {{.Image}} {{.Names}} {{.Ports}} {{.Status}}"])
    result = []
    for line in out.splitlines():
        parts = line.split()
        if not parts: continue
        cid = parts[0]
        image = parts[1]
        name = parts[2]
        ports = parts[3]
        status = " ".join(parts[4:])
        with last_ping_lock:
            lp = last_ping.get(cid)
        result.append({"id": cid, "image": image, "name": name, "ports": ports, "status": status, "last_ping": lp})
    return result

@app.get("/volumes")
def list_volumes():
    vols = sh(["docker", "volume", "ls", "--format", "{{.Name}}"]).splitlines()
    return {"volumes": vols}

@app.post("/delete-volume")
def delete_volume(name: str = Form(...)):
    try:
        sh(["docker", "volume", "rm", name])
        return {"message": f"Deleted {name}"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

@app.post("/ping")
def ping(container_id: str = Form(...)):
    mark_ping(container_id)
    return {"ok": True}

@app.post("/stop-workspace")
def stop_workspace(container_id: str = Form(...)):
    try:
        sh(["docker", "stop", container_id])
        return {"message": "Stopped"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

@app.get("/logs")
def get_logs(container_id: str = Query(..., alias="id")):
    try:
        logs = sh(["docker", "logs", "--tail", "200", container_id], allow_fail=True)
        return {"id": container_id, "logs": logs}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

@app.get("/check-update")
def check_update(container_id: str = Query(...), repo: str = Query(...)):
    try:
        stored_sha = sh(["docker", "exec", "-i", container_id, "bash", "-lc", "cat /workspace/.repo_sha"], allow_fail=True).strip()
        latest_sha = sh(["git", "ls-remote", repo, "HEAD"]).split()[0].strip()
        status = "up_to_date" if stored_sha == latest_sha else "outdated"
        return {"status": status, "stored_sha": stored_sha, "latest_sha": latest_sha}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

@app.post("/prebuild")
def set_prebuild(repo: str = Form(...), image: str = Form(...)):
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT OR REPLACE INTO prebuilds (repo_url, image, updated_at) VALUES (?, ?, ?)", (repo, image, int(time.time())))
    con.commit()
    con.close()
    return {"message": "Saved"}

@app.get("/prebuild")
def get_prebuild(repo: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.execute("SELECT image FROM prebuilds WHERE repo_url = ?", (repo,))
    row = cur.fetchone()
    con.close()
    return {"image": row[0] if row else None}

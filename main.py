import os
import re
import json
import time
import uuid
import sqlite3
import asyncio
import httpx
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from duckduckgo_search import DDGS

app = FastAPI()
DB_FILE = "architect.db"
TASKS = {}
ERROR_LOGS = []

# --- VERİTABANI KURULUMU ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, username TEXT, client_key TEXT UNIQUE, role TEXT, tokens INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS config 
                 (id INTEGER PRIMARY KEY, venice_key TEXT, chat_model TEXT, image_model TEXT, system_prompt TEXT)''')
    
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO users (username, client_key, role, tokens) VALUES (?, ?, ?, ?)", 
                  ("Admin", "admin-key-123", "admin", 999999999))
    
    c.execute("SELECT COUNT(*) FROM config")
    if c.fetchone()[0] == 0:
        default_prompt = (
            "Sen Roblox Studio Baş Geliştiricisi ve Fizik/Oyun Mekaniği Mühendisisin. Yanıtını SADECE geçerli bir JSON olarak ver.\n\n"
            "FIZIK VE OYUN MEKANIGI KURALLARI:\n"
            "1. Zamanlayıcılar ve döngüler için `while wait()` YERİNE DAİMA `RunService.Heartbeat` (DeltaTime) kullan.\n"
            "2. Top (futbol), mermi veya fırlatılan objeler için Position değiştirmek yerine `ApplyImpulse`, `AssemblyLinearVelocity` veya `VectorForce` kullan. Y-eksenindeki yerçekimini ve 3D vektör matematiğini hesapla.\n"
            "3. Güç/Hız barları için `os.clock()` veya DeltaTime ile pürüzsüz artış sağla, anında doldurma.\n"
            "4. Boyut ve konumları dizi olarak ver: Vector3 -> [X, Y, Z], UDim2 -> [XScale, XOffset, YScale, YOffset], Color3 -> [R, G, B].\n\n"
            "DESTEKLENEN AKSİYONLAR:\n"
            "create_instance, edit_instance, delete_instance, write_script, edit_script, create_plugin, run_luau, insert_asset, terrain_fill"
        )
        c.execute("INSERT INTO config (id, venice_key, chat_model, image_model, system_prompt) VALUES (1, '', 'openai-gpt-oss-120b', 'seedream-v5-5', ?)", (default_prompt,))
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# --- YARDIMCI FONKSİYONLAR ---
def estimate_tokens(text: str) -> int:
    return len(text) // 4

def extract_and_repair_json(raw_text: str) -> dict:
    if not raw_text or not raw_text.strip():
        raise ValueError("Yapay zeka boş yanıt döndürdü.")
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.DOTALL)
    candidate = fenced.group(1).strip() if fenced else cleaned[cleaned.find("{"):cleaned.rfind("}")+1]
    
    try:
        parsed = json.loads(candidate, strict=False)
        if isinstance(parsed, dict):
            if "actions" not in parsed: parsed["actions"] = []
            if "thought" not in parsed: parsed["thought"] = "İşlem planlandı."
            return parsed
    except: pass

    try:
        fixed = re.sub(r",\s*([\]}])", r"\1", candidate)
        return json.loads(fixed, strict=False)
    except: pass

    lua_match = re.search(r"```(?:lua|luau)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL)
    code = lua_match.group(1) if lua_match else cleaned
    return {"thought": "Doğrudan kod üretildi.", "actions": [{"type": "fallback_code", "source": code}]}

def get_current_user(client_key: str):
    if not client_key: raise HTTPException(status_code=401, detail="API Key Eksik")
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE client_key = ?", (client_key,)).fetchone()
    conn.close()
    if not user: raise HTTPException(status_code=401, detail="Geçersiz API Key")
    return dict(user)

# --- WEB PANEL (SaaS ARAYÜZÜ) ---
@app.get("/", response_class=HTMLResponse)
async def admin_dashboard():
    return r"""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <title>AI Studio - Yönetim Paneli</title>
    <style>
        :root { --bg: #09090b; --surface: #18181b; --border: #27272a; --accent: #3b82f6; --text: #f4f4f5; --muted: #a1a1aa; --danger: #ef4444; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
        body { background: var(--bg); color: var(--text); display: flex; height: 100vh; overflow: hidden; }
        .sidebar { width: 260px; background: var(--surface); border-right: 1px solid var(--border); padding: 24px; display: flex; flex-direction: column; gap: 8px; }
        .sidebar h2 { font-size: 18px; font-weight: 700; margin-bottom: 24px; letter-spacing: -0.5px; }
        .nav-btn { background: transparent; border: none; color: var(--muted); padding: 12px; text-align: left; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 500; transition: 0.2s; }
        .nav-btn:hover, .nav-btn.active { background: var(--border); color: #fff; }
        .main { flex: 1; padding: 40px; overflow-y: auto; }
        .card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 24px; margin-bottom: 24px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
        .card h3 { font-size: 16px; margin-bottom: 20px; font-weight: 600; border-bottom: 1px solid var(--border); padding-bottom: 12px; }
        .section { display: none; }
        .section.active { display: block; }
        label { display: block; font-size: 13px; color: var(--muted); margin-bottom: 8px; font-weight: 500; }
        input, textarea, select { width: 100%; background: var(--bg); border: 1px solid var(--border); color: #fff; padding: 12px; border-radius: 6px; margin-bottom: 16px; font-family: monospace; font-size: 13px; }
        input:focus, textarea:focus { outline: none; border-color: var(--accent); }
        .btn { background: var(--accent); color: #fff; border: none; padding: 12px 20px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 13px; }
        .btn:hover { opacity: 0.9; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid var(--border); }
        th { color: var(--muted); font-weight: 500; }
        #login-screen { position: fixed; inset: 0; background: var(--bg); display: flex; align-items: center; justify-content: center; z-index: 50; }
        .login-box { background: var(--surface); padding: 40px; border-radius: 8px; border: 1px solid var(--border); width: 400px; }
        .badge { background: var(--border); padding: 4px 8px; border-radius: 4px; font-size: 11px; }
        .admin-only { display: none; }
    </style>
</head>
<body>

<div id="login-screen">
    <div class="login-box">
        <h2 style="margin-bottom: 24px;">AI Studio Giriş</h2>
        <label>Client API Key</label>
        <input type="password" id="auth_key" placeholder="Key giriniz...">
        <button class="btn" style="width: 100%;" onclick="login()">Giriş Yap</button>
        <div id="login-err" style="color:var(--danger); margin-top:12px; font-size:13px; display:none;"></div>
    </div>
</div>

<div class="sidebar" style="display:none;" id="sidebar">
    <h2>AI Studio Pro</h2>
    <div style="font-size:12px; color:var(--muted); margin-bottom:20px;" id="user-info"></div>
    <button class="nav-btn admin-only" onclick="nav('settings', this)">Sistem Ayarları</button>
    <button class="nav-btn admin-only" onclick="nav('users', this)">Kullanıcı Yönetimi</button>
    <button class="nav-btn" onclick="nav('live', this)">Canlı Stream & Loglar</button>
    <button class="nav-btn btn" style="background:var(--danger); margin-top:auto;" onclick="logout()">Çıkış Yap</button>
</div>

<div class="main" style="display:none;" id="main">
    <div id="sec-settings" class="section admin-only">
        <div class="card">
            <h3>Venice API ve Model Yapılandırması</h3>
            <label>Venice API Anahtarı (Admin Only)</label>
            <input type="password" id="venice_key">
            <div style="display:flex; gap:16px;">
                <div style="flex:1;"><label>Sohbet Modeli</label><input type="text" id="chat_model"></div>
                <div style="flex:1;"><label>Görsel Modeli</label><input type="text" id="image_model"></div>
            </div>
            <label>Evrensel Sistem Talimatı (System Prompt)</label>
            <textarea id="system_prompt" rows="12"></textarea>
            <button class="btn" onclick="saveSettings()">Ayarları Kaydet</button>
        </div>
    </div>

    <div id="sec-users" class="section admin-only">
        <div class="card">
            <h3>Kullanıcı & Kredi (Token) Yönetimi</h3>
            <div style="display:flex; gap:16px; margin-bottom:20px;">
                <input type="text" id="new_user_name" placeholder="Kullanıcı Adı" style="margin:0;">
                <input type="text" id="new_user_key" placeholder="Client Key" style="margin:0;">
                <input type="number" id="new_user_tokens" placeholder="Token Limiti" value="50000" style="margin:0;">
                <button class="btn" onclick="addUser()" style="white-space:nowrap;">Kullanıcı Ekle</button>
            </div>
            <table>
                <thead><tr><th>ID</th><th>Kullanıcı Adı</th><th>Client Key</th><th>Rol</th><th>Kalan Token</th><th>İşlem</th></tr></thead>
                <tbody id="users-table"></tbody>
            </table>
        </div>
    </div>

    <div id="sec-live" class="section">
        <div class="card">
            <h3>Canlı Aktivite ve Hata Kayıtları <button class="btn" style="float:right; padding:6px 12px; font-size:11px;" onclick="loadErrors()">Yenile</button></h3>
            <div id="errors-container" style="font-family:monospace; font-size:12px; line-height:1.5;">Kayıt yok.</div>
        </div>
    </div>
</div>

<script>
let currentUser = null;

function nav(id, btn) {
    document.querySelectorAll('.section').forEach(e => e.classList.remove('active'));
    document.querySelectorAll('.nav-btn').forEach(e => e.classList.remove('active'));
    document.getElementById('sec-' + id).classList.add('active');
    btn.classList.add('active');
    if(id === 'users') loadUsers();
    if(id === 'live') loadErrors();
}

async function login() {
    const key = document.getElementById('auth_key').value;
    const res = await fetch('/api/auth/me', { headers: { 'Client-Key': key } });
    if(res.ok) {
        currentUser = await res.json();
        localStorage.setItem('auth_key', key);
        document.getElementById('login-screen').style.display = 'none';
        document.getElementById('sidebar').style.display = 'flex';
        document.getElementById('main').style.display = 'block';
        document.getElementById('user-info').innerText = `Hesap: ${currentUser.username}\nBakiye: ${currentUser.tokens} Token`;
        
        if(currentUser.role === 'admin') {
            document.querySelectorAll('.admin-only').forEach(e => e.style.display = 'block');
            loadSettings();
            nav('settings', document.querySelectorAll('.nav-btn')[0]);
        } else {
            nav('live', document.querySelectorAll('.nav-btn:not(.admin-only)')[0]);
        }
    } else {
        document.getElementById('login-err').style.display = 'block';
        document.getElementById('login-err').innerText = 'Hatalı Key';
    }
}

function logout() {
    localStorage.removeItem('auth_key');
    location.reload();
}

async function req(url, method="GET", body=null) {
    const opts = { method, headers: { 'Client-Key': localStorage.getItem('auth_key') } };
    if(body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return fetch(url, opts);
}

async function loadSettings() {
    const res = await req('/api/admin/config');
    const cfg = await res.json();
    document.getElementById('venice_key').value = cfg.venice_key || '';
    document.getElementById('chat_model').value = cfg.chat_model || '';
    document.getElementById('image_model').value = cfg.image_model || '';
    document.getElementById('system_prompt').value = cfg.system_prompt || '';
}

async function saveSettings() {
    await req('/api/admin/config', 'POST', {
        venice_key: document.getElementById('venice_key').value,
        chat_model: document.getElementById('chat_model').value,
        image_model: document.getElementById('image_model').value,
        system_prompt: document.getElementById('system_prompt').value
    });
    alert('Sistem ayarları kaydedildi!');
}

async function loadUsers() {
    const res = await req('/api/admin/users');
    const users = await res.json();
    document.getElementById('users-table').innerHTML = users.map(u => `
        <tr>
            <td>${u.id}</td><td>${u.username}</td><td><span class="badge">${u.client_key}</span></td>
            <td>${u.role}</td><td>${u.tokens}</td>
            <td>${u.role !== 'admin' ? `<button class="btn" style="padding:4px 8px; font-size:11px;" onclick="delUser(${u.id})">Sil</button>` : ''}</td>
        </tr>
    `).join('');
}

async function addUser() {
    await req('/api/admin/users', 'POST', {
        username: document.getElementById('new_user_name').value,
        client_key: document.getElementById('new_user_key').value,
        tokens: parseInt(document.getElementById('new_user_tokens').value)
    });
    loadUsers();
}

async function delUser(id) {
    await req(`/api/admin/users/${id}`, 'DELETE');
    loadUsers();
}

async function loadErrors() {
    const res = await req('/api/logs/errors');
    const errs = await res.json();
    document.getElementById('errors-container').innerHTML = errs.map(e => `<div style="margin-bottom:12px; padding:12px; background:var(--bg); border-left:3px solid var(--danger); border-radius:4px;">[${e.time}] ${e.type}<br>${e.details}</div>`).join('');
}

if(localStorage.getItem('auth_key')) {
    document.getElementById('auth_key').value = localStorage.getItem('auth_key');
    login();
}
</script>
</body>
</html>"""

# --- API UÇ NOKTALARI ---
@app.get("/api/auth/me")
async def auth_me(client_key: str = Header(None)):
    user = get_current_user(client_key)
    return {"username": user["username"], "role": user["role"], "tokens": user["tokens"]}

@app.get("/api/admin/config")
async def get_conf(client_key: str = Header(None)):
    user = get_current_user(client_key)
    if user["role"] != "admin": raise HTTPException(status_code=403)
    conn = get_db()
    cfg = conn.execute("SELECT * FROM config WHERE id=1").fetchone()
    conn.close()
    return dict(cfg)

@app.post("/api/admin/config")
async def save_conf(request: Request, client_key: str = Header(None)):
    user = get_current_user(client_key)
    if user["role"] != "admin": raise HTTPException(status_code=403)
    data = await request.json()
    conn = get_db()
    conn.execute("UPDATE config SET venice_key=?, chat_model=?, image_model=?, system_prompt=? WHERE id=1",
                 (data["venice_key"], data["chat_model"], data["image_model"], data["system_prompt"]))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/api/admin/users")
async def get_users(client_key: str = Header(None)):
    if get_current_user(client_key)["role"] != "admin": raise HTTPException(status_code=403)
    conn = get_db()
    users = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return [dict(u) for u in users]

@app.post("/api/admin/users")
async def add_user(request: Request, client_key: str = Header(None)):
    if get_current_user(client_key)["role"] != "admin": raise HTTPException(status_code=403)
    data = await request.json()
    conn = get_db()
    conn.execute("INSERT INTO users (username, client_key, role, tokens) VALUES (?, ?, 'user', ?)",
                 (data["username"], data["client_key"], data["tokens"]))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.delete("/api/admin/users/{user_id}")
async def del_user(user_id: int, client_key: str = Header(None)):
    if get_current_user(client_key)["role"] != "admin": raise HTTPException(status_code=403)
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/api/logs/errors")
async def get_errors(client_key: str = Header(None)):
    get_current_user(client_key) # Validate
    return list(reversed(ERROR_LOGS[-30:]))

@app.post("/api/log_error")
async def log_err(request: Request):
    data = await request.json()
    ERROR_LOGS.append({"time": time.strftime("%H:%M:%S"), "type": data.get("type"), "details": data.get("details")})
    return {"status": "ok"}

# --- ASENKRON AI İŞ MOTORU (TIMEOUT KORUMALI) ---
async def process_task(task_id: str, user: dict, payload: dict, prompt_tokens: int):
    conn = get_db()
    cfg = conn.execute("SELECT * FROM config WHERE id=1").fetchone()
    conn.close()
    
    venice_key = cfg["venice_key"]
    if not venice_key:
        TASKS[task_id] = {"status": "failed", "error": "Admin Venice API anahtarını girmemiş."}
        return

    headers = {"Authorization": f"Bearer {venice_key}", "Content-Type": "application/json"}
    venice_payload = {
        "model": cfg["chat_model"],
        "stream": True,
        "messages": [
            {"role": "system", "content": cfg["system_prompt"]},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)[:60000]}
        ]
    }

    collected = []
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream("POST", "https://api.venice.ai/api/v1/chat/completions", json=venice_payload, headers=headers, timeout=180.0) as response:
                if response.status_code != 200:
                    TASKS[task_id] = {"status": "failed", "error": f"Venice HTTP {response.status_code}"}
                    return
                
                async for line in response.aiter_lines():
                    if not line.startswith("data: "): continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]": break
                    try:
                        chunk = json.loads(data_str)
                        content = chunk["choices"][0].get("delta", {}).get("content", "")
                        if content:
                            collected.append(content)
                            TASKS[task_id]["buffer"] = "".join(collected)[-2000:]
                    except: pass

        full_output = "".join(collected)
        output_tokens = estimate_tokens(full_output)
        
        # Token Düşümü
        if user["role"] != "admin":
            conn = get_db()
            conn.execute("UPDATE users SET tokens = tokens - ? WHERE id = ?", (prompt_tokens + output_tokens, user["id"]))
            conn.commit()
            conn.close()

        repaired_json = extract_and_repair_json(full_output)
        
        # Fallback code kontrolü
        actions = repaired_json.get("actions", [])
        if len(actions) == 1 and actions[0].get("type") == "fallback_code":
            selected = payload.get("selected_items", [])
            scripts = [s for s in selected if s.get("Class") in ("Script", "LocalScript")]
            if scripts:
                repaired_json["actions"] = [{"type": "edit_script", "path": scripts[0]["Path"], "source": actions[0]["source"]}]
            else:
                repaired_json["actions"] = [{"type": "write_script", "script_type": "Script", "name": "AI_Generated", "parent": "ServerScriptService", "source": actions[0]["source"]}]

        TASKS[task_id] = {"status": "completed", "result": repaired_json}
    
    except Exception as e:
        TASKS[task_id] = {"status": "failed", "error": str(e)}

@app.post("/api/agent/execute")
async def execute_task(request: Request, client_key: str = Header(None)):
    user = get_current_user(client_key)
    payload = await request.json()
    
    prompt_tokens = estimate_tokens(str(payload))
    if user["role"] != "admin" and user["tokens"] < prompt_tokens + 500:
        return JSONResponse({"error": "Yetersiz Token Bakiyesi"}, status_code=402)
    
    task_id = str(uuid.uuid4())
    TASKS[task_id] = {"status": "processing", "buffer": "Bağlanıyor..."}
    
    # Asenkron işlemi arka planda başlat (Roblox'u 60 saniye bekletmez)
    asyncio.create_task(process_task(task_id, user, payload, prompt_tokens))
    
    return {"task_id": task_id}

@app.get("/api/agent/poll")
async def poll_task(task_id: str, client_key: str = Header(None)):
    get_current_user(client_key)
    if task_id not in TASKS: return JSONResponse({"error": "Görev bulunamadı"}, status_code=404)
    return TASKS[task_id]

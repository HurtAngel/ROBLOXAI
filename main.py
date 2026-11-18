import os
import re
import json
import time
import uuid
import sqlite3
import asyncio
import hashlib
import httpx
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from duckduckgo_search import DDGS

app = FastAPI()
DB_FILE = "architect_v2.db"
TASKS = {}
ERROR_LOGS = []
LIVE_STATE = {"status": "Boşta", "buffer": "", "model": "openai-gpt-oss-120b"}

def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password_hash TEXT,
        client_key TEXT UNIQUE,
        role TEXT,
        tokens INTEGER
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS config (
        id INTEGER PRIMARY KEY,
        venice_key TEXT,
        chat_model TEXT,
        image_model TEXT,
        default_signup_tokens INTEGER,
        input_cost_mult REAL,
        output_cost_mult REAL,
        system_prompt TEXT
    )''')

    c.execute("SELECT COUNT(*) FROM users WHERE username = 'Admin'")
    if c.fetchone()[0] == 0:
        c.execute(
            "INSERT INTO users (username, password_hash, client_key, role, tokens) VALUES (?, ?, ?, ?, ?)",
            ("Admin", hash_pw("1234"), "rbx-admin-" + uuid.uuid4().hex[:12], "admin", 999999999)
        )

    c.execute("SELECT COUNT(*) FROM config")
    if c.fetchone()[0] == 0:
        default_prompt = (
            "Sen Roblox Studio Baş Geliştiricisi ve 3D Fizik/Oyun Mekaniği Mühendisisin. Yanıtını SADECE geçerli bir JSON olarak döndür.\n\n"
            "OYUN MEKANIGI VE 3D FIZIK KURALLARI:\n"
            "1. Döngülerde `while wait()` YERİNE DAİMA `RunService.Heartbeat` (DeltaTime) kullan.\n"
            "2. Top (futbol, basketbol), mermi veya fırlatılan objelerde Position değiştirmek yerine `ApplyImpulse`, `AssemblyLinearVelocity` ve yerçekimi dengelemesi (Y-ekseni kaldırma kuvveti) kullan.\n"
            "3. Şut/Güç barları `os.clock()` veya `DeltaTime` ile yumuşak dolmalı, anında %100 olmamalıdır.\n"
            "4. Boyut ve konumları dizi olarak ver: Vector3 -> [X, Y, Z], UDim2 -> [XScale, XOffset, YScale, YOffset], Color3 -> [R, G, B].\n\n"
            "DESTEKLENEN AKSİYONLAR:\n"
            "create_instance, edit_instance, delete_instance, write_script, edit_script, create_plugin, run_luau, insert_asset, terrain_fill"
        )
        c.execute(
            "INSERT INTO config (id, venice_key, chat_model, image_model, default_signup_tokens, input_cost_mult, output_cost_mult, system_prompt) VALUES (1, '', 'openai-gpt-oss-120b', 'seedream-v5-5', 25000, 1.0, 2.0, ?)",
            (default_prompt,)
        )
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)

def extract_and_repair_json(raw_text: str) -> dict:
    if not raw_text or not raw_text.strip():
        raise ValueError("Yapay zeka boş yanıt döndürdü.")
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    else:
        s, e = cleaned.find("{"), cleaned.rfind("}")
        candidate = cleaned[s:e+1] if (s != -1 and e != -1 and e > s) else cleaned

    try:
        parsed = json.loads(candidate, strict=False)
        if isinstance(parsed, dict):
            parsed.setdefault("actions", [])
            parsed.setdefault("thought", "İşlem tamamlandı.")
            return parsed
    except Exception:
        pass

    try:
        fixed = re.sub(r",\s*([\]}])", r"\1", candidate)
        return json.loads(fixed, strict=False)
    except Exception:
        pass

    lua_match = re.search(r"```(?:lua|luau)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL)
    code = lua_match.group(1) if lua_match else cleaned
    return {"thought": "Doğrudan kod üretildi.", "actions": [{"type": "fallback_code", "source": code}]}

def get_user_by_auth(client_key: str = None, username: str = None, password: str = None):
    conn = get_db()
    user = None
    if client_key:
        user = conn.execute("SELECT * FROM users WHERE client_key = ?", (client_key.strip(),)).fetchone()
    elif username and password:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? AND password_hash = ?",
            (username.strip(), hash_pw(password))
        ).fetchone()
    conn.close()
    if not user:
        raise HTTPException(status_code=401, detail="Geçersiz kullanıcı adı, şifre veya API Key")
    return dict(user)

@app.get("/", response_class=HTMLResponse)
async def web_portal():
    return r"""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Studio Platform</title>
    <style>
        :root { --bg: #09090b; --surface: #121216; --border: #27272a; --accent: #3b82f6; --text: #f4f4f5; --muted: #a1a1aa; --danger: #ef4444; --success: #10b981; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background: var(--bg); color: var(--text); display: flex; height: 100vh; overflow: hidden; }
        .sidebar { width: 270px; background: var(--surface); border-right: 1px solid var(--border); padding: 24px; display: flex; flex-direction: column; gap: 8px; }
        .sidebar h2 { font-size: 18px; font-weight: 700; margin-bottom: 16px; letter-spacing: -0.5px; }
        .profile-card { background: var(--bg); border: 1px solid var(--border); padding: 12px; border-radius: 8px; margin-bottom: 16px; font-size: 12px; }
        .key-box { background: #18181b; padding: 6px 8px; border-radius: 4px; font-family: monospace; color: var(--success); margin-top: 6px; word-break: break-all; cursor: pointer; border: 1px dashed var(--border); }
        .nav-btn { background: transparent; border: none; color: var(--muted); padding: 11px 12px; text-align: left; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 500; transition: 0.15s; }
        .nav-btn:hover, .nav-btn.active { background: var(--border); color: #fff; }
        .main { flex: 1; padding: 36px; overflow-y: auto; }
        .card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 24px; margin-bottom: 24px; }
        .card h3 { font-size: 15px; margin-bottom: 18px; font-weight: 600; border-bottom: 1px solid var(--border); padding-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }
        .section { display: none; }
        .section.active { display: block; }
        label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 6px; font-weight: 500; }
        input, textarea { width: 100%; background: var(--bg); border: 1px solid var(--border); color: #fff; padding: 11px; border-radius: 6px; margin-bottom: 14px; font-family: monospace; font-size: 13px; }
        input:focus, textarea:focus { outline: none; border-color: var(--accent); }
        .btn { background: var(--accent); color: #fff; border: none; padding: 11px 18px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 13px; }
        .btn:hover { opacity: 0.9; }
        .btn-danger { background: var(--danger); padding: 6px 10px; font-size: 11px; }
        .btn-sm { padding: 6px 12px; font-size: 12px; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th, td { padding: 11px; text-align: left; border-bottom: 1px solid var(--border); }
        th { color: var(--muted); font-weight: 500; }
        #auth-screen { position: fixed; inset: 0; background: var(--bg); display: flex; align-items: center; justify-content: center; z-index: 100; }
        .auth-box { background: var(--surface); padding: 36px; border-radius: 10px; border: 1px solid var(--border); width: 400px; }
        .auth-tabs { display: flex; gap: 8px; margin-bottom: 24px; border-bottom: 1px solid var(--border); padding-bottom: 12px; }
        .auth-tab { flex: 1; background: transparent; border: none; color: var(--muted); padding: 8px; cursor: pointer; font-weight: 600; border-radius: 6px; }
        .auth-tab.active { background: var(--border); color: #fff; }
        .admin-only { display: none; }
        .log-box { background: var(--bg); border: 1px solid var(--border); padding: 14px; border-radius: 6px; font-family: monospace; font-size: 12px; white-space: pre-wrap; word-break: break-word; }
    </style>
</head>
<body>

<div id="auth-screen">
    <div class="auth-box">
        <div class="auth-tabs">
            <button class="auth-tab active" id="tab-login-btn" onclick="switchAuth('login')">Giriş Yap</button>
            <button class="auth-tab" id="tab-reg-btn" onclick="switchAuth('register')">Kayıt Ol</button>
        </div>

        <div id="form-login">
            <label>Kullanıcı Adı</label>
            <input type="text" id="login_user">
            <label>Şifre</label>
            <input type="password" id="login_pass">
            <button class="btn" style="width:100%;" onclick="doLogin()">Giriş Yap</button>
        </div>

        <div id="form-register" style="display:none;">
            <label>Yeni Kullanıcı Adı</label>
            <input type="text" id="reg_user">
            <label>Şifre Belirle</label>
            <input type="password" id="reg_pass">
            <button class="btn" style="width:100%; background:var(--success);" onclick="doRegister()">Hesap Oluştur</button>
        </div>

        <div id="auth-msg" style="margin-top:14px; font-size:13px; color:var(--danger); display:none;"></div>
    </div>
</div>

<div class="sidebar" style="display:none;" id="sidebar">
    <h2>AI Studio</h2>
    <div class="profile-card">
        <div id="p-name" style="font-weight:700; font-size:14px;"></div>
        <div id="p-role" style="color:var(--muted); margin-top:2px;"></div>
        <div id="p-tokens" style="margin-top:8px; font-weight:600;"></div>
        <div style="margin-top:8px; color:var(--muted);">Plugin Client Key (Tıkla Kopyala):</div>
        <div class="key-box" id="p-key" onclick="copyKey()"></div>
    </div>

    <button class="nav-btn admin-only" onclick="nav('settings', this)">Sistem & API Ayarları</button>
    <button class="nav-btn admin-only" onclick="nav('users', this)">Kullanıcı & Kredi Yönetimi</button>
    <button class="nav-btn" onclick="nav('live', this)">Canlı Akış & Hata Kayıtları</button>
    <button class="nav-btn btn-danger" style="margin-top:auto; text-align:center;" onclick="logout()">Çıkış Yap</button>
</div>

<div class="main" style="display:none;" id="main">
    <div id="sec-settings" class="section admin-only">
        <div class="card">
            <h3>Venice API, Model ve Token Maliyet Ayarları</h3>
            <label>Venice.ai API Anahtarı</label>
            <input type="password" id="venice_key">
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:14px;">
                <div><label>Aktif Sohbet Modeli</label><input type="text" id="chat_model"></div>
                <div><label>Aktif Görsel Modeli</label><input type="text" id="image_model"></div>
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:14px;">
                <div><label>Yeni Kayıt Başlangıç Kredisi</label><input type="number" id="default_signup_tokens"></div>
                <div><label>Input Token Çarpanı</label><input type="number" step="0.1" id="input_cost_mult"></div>
                <div><label>Output Token Çarpanı</label><input type="number" step="0.1" id="output_cost_mult"></div>
            </div>
            <label>Sistem Talimatı (System Prompt)</label>
            <textarea id="system_prompt" rows="10"></textarea>
            <button class="btn" onclick="saveSettings()">Ayarları Kaydet</button>
        </div>
    </div>

    <div id="sec-users" class="section admin-only">
        <div class="card">
            <h3>Kayıtlı Kullanıcılar & Kredi Düzenleme</h3>
            <table>
                <thead><tr><th>ID</th><th>Kullanıcı Adı</th><th>Rol</th><th>Client API Key</th><th>Kredi (Token)</th><th>İşlem</th></tr></thead>
                <tbody id="users-table"></tbody>
            </table>
        </div>
    </div>

    <div id="sec-live" class="section">
        <div class="card">
            <h3>Aktif Model & Canlı AI Akışı <span id="active-model-badge" style="font-size:12px; color:var(--accent);"></span></h3>
            <div class="log-box" id="live-stream-box" style="min-height:110px; border-left:3px solid var(--success);">Bekleniyor...</div>
        </div>
        <div class="card">
            <h3>Hata Kayıtları <button class="btn btn-sm" onclick="loadLiveAndErrors()">Yenile</button></h3>
            <div id="errors-container"></div>
        </div>
    </div>
</div>

<script>
let currentUser = null;

function switchAuth(mode) {
    document.getElementById('form-login').style.display = mode === 'login' ? 'block' : 'none';
    document.getElementById('form-register').style.display = mode === 'register' ? 'block' : 'none';
    document.getElementById('tab-login-btn').classList.toggle('active', mode === 'login');
    document.getElementById('tab-reg-btn').classList.toggle('active', mode === 'register');
    document.getElementById('auth-msg').style.display = 'none';
}

function showMsg(txt, isErr=true) {
    const m = document.getElementById('auth-msg');
    m.style.display = 'block';
    m.style.color = isErr ? 'var(--danger)' : 'var(--success)';
    m.innerText = txt;
}

async function doLogin() {
    const username = document.getElementById('login_user').value.trim();
    const password = document.getElementById('login_pass').value;
    const res = await fetch('/api/auth/login', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username, password})
    });
    const data = await res.json();
    if (res.ok) {
        localStorage.setItem('client_key', data.client_key);
        initSession(data);
    } else {
        showMsg(data.detail || 'Giriş başarısız');
    }
}

async function doRegister() {
    const username = document.getElementById('reg_user').value.trim();
    const password = document.getElementById('reg_pass').value;
    const res = await fetch('/api/auth/register', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username, password})
    });
    const data = await res.json();
    if (res.ok) {
        localStorage.setItem('client_key', data.client_key);
        initSession(data);
    } else {
        showMsg(data.detail || 'Kayıt başarısız');
    }
}

function initSession(user) {
    currentUser = user;
    document.getElementById('auth-screen').style.display = 'none';
    document.getElementById('sidebar').style.display = 'flex';
    document.getElementById('main').style.display = 'block';

    document.getElementById('p-name').innerText = user.username;
    document.getElementById('p-role').innerText = user.role === 'admin' ? 'Yetki: Yönetici (Sınırsız)' : 'Yetki: Standart Kullanıcı';
    document.getElementById('p-tokens').innerText = user.role === 'admin' ? 'Kredi: ∞ Sınırsız' : `Kalan Kredi: ${user.tokens} Token`;
    document.getElementById('p-key').innerText = user.client_key;

    if (user.role === 'admin') {
        document.querySelectorAll('.admin-only').forEach(e => e.style.display = 'block');
        loadSettings();
        nav('settings', document.querySelectorAll('.nav-btn')[0]);
    } else {
        nav('live', document.querySelectorAll('.nav-btn:not(.admin-only)')[0]);
    }
}

function copyKey() {
    navigator.clipboard.writeText(document.getElementById('p-key').innerText);
    alert('Client API Key kopyalandı!');
}

function logout() {
    localStorage.removeItem('client_key');
    location.reload();
}

function nav(id, btn) {
    document.querySelectorAll('.section').forEach(e => e.classList.remove('active'));
    document.querySelectorAll('.nav-btn').forEach(e => e.classList.remove('active'));
    document.getElementById('sec-' + id).classList.add('active');
    btn.classList.add('active');
    if (id === 'users') loadUsers();
    if (id === 'live') loadLiveAndErrors();
}

async function apiFetch(url, method='GET', body=null) {
    const opts = { method, headers: { 'Client-Key': localStorage.getItem('client_key') } };
    if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return fetch(url, opts);
}

async function loadSettings() {
    const res = await apiFetch('/api/admin/config');
    if (!res.ok) return;
    const cfg = await res.json();
    document.getElementById('venice_key').value = cfg.venice_key || '';
    document.getElementById('chat_model').value = cfg.chat_model || '';
    document.getElementById('image_model').value = cfg.image_model || '';
    document.getElementById('default_signup_tokens').value = cfg.default_signup_tokens || 25000;
    document.getElementById('input_cost_mult').value = cfg.input_cost_mult || 1.0;
    document.getElementById('output_cost_mult').value = cfg.output_cost_mult || 2.0;
    document.getElementById('system_prompt').value = cfg.system_prompt || '';
}

async function saveSettings() {
    await apiFetch('/api/admin/config', 'POST', {
        venice_key: document.getElementById('venice_key').value.trim(),
        chat_model: document.getElementById('chat_model').value.trim(),
        image_model: document.getElementById('image_model').value.trim(),
        default_signup_tokens: parseInt(document.getElementById('default_signup_tokens').value) || 25000,
        input_cost_mult: parseFloat(document.getElementById('input_cost_mult').value) || 1.0,
        output_cost_mult: parseFloat(document.getElementById('output_cost_mult').value) || 2.0,
        system_prompt: document.getElementById('system_prompt').value
    });
    alert('Ayarlar kaydedildi.');
}

async function loadUsers() {
    const res = await apiFetch('/api/admin/users');
    const users = await res.json();
    document.getElementById('users-table').innerHTML = users.map(u => `
        <tr>
            <td>${u.id}</td>
            <td>${u.username}</td>
            <td>${u.role}</td>
            <td style="font-family:monospace;">${u.client_key}</td>
            <td><input type="number" id="tok-${u.id}" value="${u.tokens}" style="width:120px; margin:0; padding:6px;"></td>
            <td>
                <button class="btn btn-sm" onclick="updateTokens(${u.id})">Güncelle</button>
                ${u.role !== 'admin' ? `<button class="btn btn-danger" onclick="delUser(${u.id})">Sil</button>` : ''}
            </td>
        </tr>
    `).join('');
}

async function updateTokens(id) {
    const val = parseInt(document.getElementById('tok-' + id).value);
    await apiFetch(`/api/admin/users/${id}/tokens`, 'POST', { tokens: val });
    alert('Kredi güncellendi.');
}

async function delUser(id) {
    await apiFetch(`/api/admin/users/${id}`, 'DELETE');
    loadUsers();
}

async function loadLiveAndErrors() {
    const res = await apiFetch('/api/status/live');
    if (!res.ok) return;
    const data = await res.json();
    document.getElementById('active-model-badge').innerText = `Aktif Model: ${data.live.model}`;
    document.getElementById('live-stream-box').innerText = `Durum: ${data.live.status}\n\n${data.live.buffer || 'Bekleniyor...'}`;
    document.getElementById('errors-container').innerHTML = data.errors.length
        ? data.errors.map(e => `<div class="log-box" style="margin-bottom:10px; border-left:3px solid var(--danger);">[${e.time}] ${e.type}\n${e.details}</div>`).join('')
        : '<div style="color:var(--muted); font-size:13px;">Kayıtlı hata yok.</div>';
}

(async () => {
    const savedKey = localStorage.getItem('client_key');
    if (savedKey) {
        const res = await fetch('/api/auth/me', { headers: { 'Client-Key': savedKey } });
        if (res.ok) initSession(await res.json());
    }
})();

setInterval(() => {
    if (document.getElementById('sec-live').classList.contains('active')) loadLiveAndErrors();
}, 2500);
</script>
</body>
</html>"""

# --- KİMLİK DOĞRULAMA API ---
@app.post("/api/auth/login")
async def login(request: Request):
    data = await request.json()
    user = get_user_by_auth(username=data.get("username"), password=data.get("password"))
    return {"username": user["username"], "role": user["role"], "tokens": user["tokens"], "client_key": user["client_key"]}

@app.post("/api/auth/register")
async def register(request: Request):
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if len(username) < 3 or len(password) < 4:
        raise HTTPException(status_code=400, detail="Kullanıcı adı en az 3, şifre en az 4 karakter olmalıdır.")

    conn = get_db()
    cfg = conn.execute("SELECT default_signup_tokens FROM config WHERE id=1").fetchone()
    start_tokens = cfg["default_signup_tokens"] if cfg else 25000
    new_key = "rbx-usr-" + uuid.uuid4().hex[:14]

    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, client_key, role, tokens) VALUES (?, ?, ?, 'user', ?)",
            (username, hash_pw(password), new_key, start_tokens)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Bu kullanıcı adı zaten alınmış.")
    conn.close()
    return {"username": username, "role": "user", "tokens": start_tokens, "client_key": new_key}

@app.get("/api/auth/me")
async def auth_me(client_key: str = Header(None)):
    user = get_user_by_auth(client_key=client_key)
    return {"username": user["username"], "role": user["role"], "tokens": user["tokens"], "client_key": user["client_key"]}

# --- ADMIN YÖNETİM API ---
@app.get("/api/admin/config")
async def get_conf(client_key: str = Header(None)):
    if get_user_by_auth(client_key=client_key)["role"] != "admin":
        raise HTTPException(status_code=403)
    conn = get_db()
    cfg = conn.execute("SELECT * FROM config WHERE id=1").fetchone()
    conn.close()
    return dict(cfg)

@app.post("/api/admin/config")
async def save_conf(request: Request, client_key: str = Header(None)):
    if get_user_by_auth(client_key=client_key)["role"] != "admin":
        raise HTTPException(status_code=403)
    d = await request.json()
    conn = get_db()
    conn.execute(
        "UPDATE config SET venice_key=?, chat_model=?, image_model=?, default_signup_tokens=?, input_cost_mult=?, output_cost_mult=?, system_prompt=? WHERE id=1",
        (d["venice_key"], d["chat_model"], d["image_model"], d["default_signup_tokens"], d["input_cost_mult"], d["output_cost_mult"], d["system_prompt"])
    )
    conn.commit()
    conn.close()
    LIVE_STATE["model"] = d["chat_model"]
    return {"status": "ok"}

@app.get("/api/admin/users")
async def get_users(client_key: str = Header(None)):
    if get_user_by_auth(client_key=client_key)["role"] != "admin":
        raise HTTPException(status_code=403)
    conn = get_db()
    users = conn.execute("SELECT id, username, client_key, role, tokens FROM users").fetchall()
    conn.close()
    return [dict(u) for u in users]

@app.post("/api/admin/users/{user_id}/tokens")
async def update_user_tokens(user_id: int, request: Request, client_key: str = Header(None)):
    if get_user_by_auth(client_key=client_key)["role"] != "admin":
        raise HTTPException(status_code=403)
    d = await request.json()
    conn = get_db()
    conn.execute("UPDATE users SET tokens=? WHERE id=?", (d["tokens"], user_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.delete("/api/admin/users/{user_id}")
async def delete_user(user_id: int, client_key: str = Header(None)):
    if get_user_by_auth(client_key=client_key)["role"] != "admin":
        raise HTTPException(status_code=403)
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id=? AND role != 'admin'", (user_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

# --- CANLI DURUM & HATA LOGLARI ---
@app.get("/api/status/live")
async def live_status(client_key: str = Header(None)):
    get_user_by_auth(client_key=client_key)
    conn = get_db()
    cfg = conn.execute("SELECT chat_model FROM config WHERE id=1").fetchone()
    conn.close()
    if cfg:
        LIVE_STATE["model"] = cfg["chat_model"]
    return {"live": LIVE_STATE, "errors": list(reversed(ERROR_LOGS[-30:]))}

@app.post("/api/log_error")
async def log_err(request: Request):
    d = await request.json()
    ERROR_LOGS.append({"time": time.strftime("%H:%M:%S"), "type": d.get("type", "Hata"), "details": d.get("details", "")})
    return {"status": "ok"}

# --- ASENKRON AI MOTORU ---
async def process_task(task_id: str, user: dict, payload: dict, raw_in_tokens: int):
    conn = get_db()
    cfg = dict(conn.execute("SELECT * FROM config WHERE id=1").fetchone())
    conn.close()

    if not cfg["venice_key"]:
        TASKS[task_id] = {"status": "failed", "error": "Yönetici henüz Venice API anahtarını girmemiş."}
        return

    headers = {"Authorization": f"Bearer {cfg['venice_key']}", "Content-Type": "application/json"}
    venice_payload = {
        "model": cfg["chat_model"],
        "stream": True,
        "messages": [
            {"role": "system", "content": cfg["system_prompt"]},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)[:65000]}
        ]
    }

    LIVE_STATE["status"] = f"{user['username']} için üretiliyor..."
    LIVE_STATE["buffer"] = ""
    collected = []

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream("POST", "https://api.venice.ai/api/v1/chat/completions", json=venice_payload, headers=headers, timeout=180.0) as response:
                if response.status_code != 200:
                    err_txt = (await response.aread()).decode("utf-8", errors="ignore")
                    ERROR_LOGS.append({"time": time.strftime("%H:%M:%S"), "type": f"Venice HTTP {response.status_code}", "details": err_txt})
                    TASKS[task_id] = {"status": "failed", "error": f"API Hatası ({response.status_code})"}
                    LIVE_STATE["status"] = "Hata oluştu"
                    return

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        content = chunk["choices"][0].get("delta", {}).get("content", "")
                        if content:
                            collected.append(content)
                            buf = "".join(collected)[-2500:]
                            TASKS[task_id]["buffer"] = buf
                            LIVE_STATE["buffer"] = buf
                    except Exception:
                        pass

        full_output = "".join(collected)
        raw_out_tokens = estimate_tokens(full_output)

        if user["role"] != "admin":
            total_cost = int((raw_in_tokens * cfg["input_cost_mult"]) + (raw_out_tokens * cfg["output_cost_mult"]))
            conn = get_db()
            conn.execute("UPDATE users SET tokens = MAX(0, tokens - ?) WHERE id = ?", (total_cost, user["id"]))
            conn.commit()
            conn.close()

        repaired_json = extract_and_repair_json(full_output)
        actions = repaired_json.get("actions", [])
        if len(actions) == 1 and actions[0].get("type") == "fallback_code":
            selected = payload.get("selected_items", [])
            scripts = [s for s in selected if s.get("Class") in ("Script", "LocalScript", "ModuleScript")]
            if scripts:
                repaired_json["actions"] = [{"type": "edit_script", "path": scripts[0]["Path"], "source": actions[0]["source"]}]
            else:
                repaired_json["actions"] = [{"type": "write_script", "script_type": "Script", "name": "AIGenerated", "parent": "ServerScriptService", "source": actions[0]["source"]}]

        LIVE_STATE["status"] = "Tamamlandı"
        TASKS[task_id] = {"status": "completed", "result": repaired_json}

    except Exception as e:
        ERROR_LOGS.append({"time": time.strftime("%H:%M:%S"), "type": "Stream Hatası", "details": str(e)})
        LIVE_STATE["status"] = "Hata"
        TASKS[task_id] = {"status": "failed", "error": str(e)}

@app.post("/api/agent/execute")
async def execute_task(request: Request, client_key: str = Header(None)):
    user = get_user_by_auth(client_key=client_key)
    payload = await request.json()
    raw_in_tokens = estimate_tokens(str(payload))

    if user["role"] != "admin" and user["tokens"] < 300:
        return JSONResponse({"error": "Yetersiz kredi (Token)."}, status_code=402)

    task_id = str(uuid.uuid4())
    TASKS[task_id] = {"status": "processing", "buffer": "Bağlanılıyor..."}
    asyncio.create_task(process_task(task_id, user, payload, raw_in_tokens))
    return {"task_id": task_id, "role": user["role"], "username": user["username"], "tokens": user["tokens"]}

@app.get("/api/agent/poll")
async def poll_task(task_id: str, client_key: str = Header(None)):
    user = get_user_by_auth(client_key=client_key)
    if task_id not in TASKS:
        return JSONResponse({"error": "Görev bulunamadı"}, status_code=404)
    res = dict(TASKS[task_id])
    res["remaining_tokens"] = "Sınırsız" if user["role"] == "admin" else user["tokens"]
    return res

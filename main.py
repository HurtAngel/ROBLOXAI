import os
import re
import json
import time
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from duckduckgo_search import DDGS

app = FastAPI()
CONFIG_FILE = "config.json"

REQUEST_LOGS = []
ERROR_LOGS = []
LIVE_STREAM_STATE = {"status": "Boşta", "buffer": "", "updated_at": "", "active": False}

def get_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if os.getenv("VENICE_API_KEY") and not data.get("api_key"):
                data["api_key"] = os.getenv("VENICE_API_KEY")
            return data
    return {}

def save_config(new_data: dict):
    current = get_config()
    current.update(new_data)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)

def add_error_log(err_type: str, details: str, raw_output: str = ""):
    ERROR_LOGS.append({
        "time": time.strftime("%H:%M:%S"),
        "type": err_type,
        "details": details,
        "raw": raw_output[:4000]
    })

def extract_and_repair_json(raw_text: str) -> dict:
    if not raw_text or not raw_text.strip():
        raise ValueError("Yapay zeka boş yanıt döndürdü (API anahtarınızı veya model adını kontrol edin).")

    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = cleaned[start:end + 1]
        else:
            candidate = cleaned

    try:
        parsed = json.loads(candidate, strict=False)
        if isinstance(parsed, dict):
            if "actions" not in parsed:
                parsed["actions"] = []
            if "thought" not in parsed:
                parsed["thought"] = "İşlem planlandı."
            return parsed
    except Exception:
        pass

    try:
        fixed_commas = re.sub(r",\s*([\]}])", r"\1", candidate)
        parsed = json.loads(fixed_commas, strict=False)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    lua_match = re.search(r"```(?:lua|luau)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL)
    code_content = lua_match.group(1) if lua_match else cleaned
    return {
        "thought": "Model doğrudan Luau kodu üretti, otomatik olarak script güncelleme aksiyonuna dönüştürüldü.",
        "actions": [{"type": "fallback_code", "source": code_content}]
    }

@app.get("/", response_class=HTMLResponse)
async def admin_dashboard():
    return r"""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Roblox AI Architect Ultimate — Kontrol Merkezi</title>
    <style>
        :root {
            --bg: #090b10; --sidebar: #10131c; --card: #151925; --border: #23293a;
            --accent: #3b82f6; --accent-hover: #2563eb; --success: #10b981;
            --danger: #ef4444; --warning: #f59e0b; --text: #f1f5f9; --muted: #94a3b8;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); display: flex; min-height: 100vh; }
        .sidebar { width: 265px; background: var(--sidebar); border-right: 1px solid var(--border); padding: 22px 14px; display: flex; flex-direction: column; gap: 6px; position: sticky; top: 0; height: 100vh; }
        .brand { font-size: 17px; font-weight: 800; color: #fff; padding: 0 12px 18px; border-bottom: 1px solid var(--border); margin-bottom: 10px; }
        .nav-btn { background: transparent; border: none; color: var(--muted); padding: 12px 14px; text-align: left; border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600; transition: 0.15s; }
        .nav-btn:hover, .nav-btn.active { background: var(--card); color: #fff; border-left: 3px solid var(--accent); }
        .main { flex: 1; padding: 28px 34px; max-width: 1250px; overflow-y: auto; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .top-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; margin-bottom: 24px; }
        .stat-box { background: var(--card); border: 1px solid var(--border); padding: 16px; border-radius: 10px; }
        .stat-title { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
        .stat-val { font-size: 16px; font-weight: 700; margin-top: 6px; color: #fff; word-break: break-all; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 22px; margin-bottom: 22px; }
        .card h3 { font-size: 15px; margin-bottom: 16px; color: #fff; border-bottom: 1px solid var(--border); padding-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
        label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 6px; font-weight: 600; }
        input, textarea { width: 100%; background: var(--bg); border: 1px solid var(--border); color: #fff; padding: 10px 13px; border-radius: 8px; margin-bottom: 14px; font-size: 13px; font-family: monospace; }
        input:focus, textarea:focus { outline: none; border-color: var(--accent); }
        .btn { background: var(--accent); color: #fff; border: none; padding: 10px 18px; border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 13px; transition: 0.15s; }
        .btn:hover { background: var(--accent-hover); }
        .btn-danger { background: var(--danger); padding: 6px 12px; font-size: 12px; }
        .btn-outline { background: transparent; border: 1px solid var(--border); color: var(--text); }
        .btn-outline:hover { background: var(--border); }
        .preset-row { background: var(--bg); border: 1px solid var(--border); padding: 14px; border-radius: 8px; margin-bottom: 12px; }
        .log-item { background: var(--bg); border-left: 3px solid var(--accent); padding: 14px; border-radius: 6px; margin-bottom: 12px; font-family: monospace; font-size: 12px; white-space: pre-wrap; word-break: break-word; }
        .error-item { border-left-color: var(--danger); background: #1a0f14; color: #fecdd3; }
        #toast { position: fixed; bottom: 24px; right: 24px; background: var(--success); color: #fff; padding: 14px 22px; border-radius: 8px; font-weight: 600; display: none; z-index: 9999; box-shadow: 0 8px 25px rgba(0,0,0,0.6); }
    </style>
</head>
<body>
<div class="sidebar">
    <div class="brand">⚡ AI Studio Ultimate</div>
    <button class="nav-btn active" onclick="switchTab('models', this)">🔑 API & Modeller</button>
    <button class="nav-btn" onclick="switchTab('prompts', this)">📋 Şablonlar & System Prompt</button>
    <button class="nav-btn" onclick="switchTab('live', this)">📡 Canlı Stream & İstek Logları</button>
    <button class="nav-btn" onclick="switchTab('errors', this)">⚠️ Hata & Debug Kayıtları</button>
    <button class="nav-btn" onclick="switchTab('texture', this)">🎨 Seedream Görsel Testi</button>
    <button class="nav-btn" onclick="switchTab('tools', this)">🔍 Web & Toolbox Arama Testi</button>
</div>

<div class="main">
    <div class="top-stats">
        <div class="stat-box"><div class="stat-title">Sunucu Adresi</div><div class="stat-val" style="color:var(--success);">robloxai-1ea7.onrender.com</div></div>
        <div class="stat-box"><div class="stat-title">Aktif Sohbet Modeli</div><div class="stat-val" id="stat-chat">openai-gpt-oss-120b</div></div>
        <div class="stat-box"><div class="stat-title">Aktif Görsel Modeli</div><div class="stat-val" id="stat-img">seedream-v5-5</div></div>
        <div class="stat-box"><div class="stat-title">Toplam İstek / Hata</div><div class="stat-val" id="stat-counts">0 / 0</div></div>
    </div>

    <div id="tab-models" class="tab-content active">
        <div class="card">
            <h3>🔑 Venice.ai API & Model Yapılandırması</h3>
            <label>Venice.ai API Anahtarı (Bearer Token)</label>
            <input type="password" id="api_key" placeholder="Venice.ai API anahtarınızı buraya yapıştırın...">
            
            <div class="grid-2">
                <div><label>API Base URL</label><input type="text" id="base_url" value="https://api.venice.ai/api/v1"></div>
                <div><label>Maksimum Girdi Karakter Sınırı</label><input type="number" id="max_input_length" value="80000"></div>
            </div>

            <div class="grid-3">
                <div><label>Sohbet & Kodlama Modeli</label><input type="text" id="chat_model" placeholder="openai-gpt-oss-120b"></div>
                <div><label>Görsel & Texture Modeli</label><input type="text" id="image_model" placeholder="seedream-v5-5"></div>
                <div><label>Görsel Çözünürlüğü (size)</label><input type="text" id="image_size" placeholder="1K"></div>
            </div>

            <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:8px;">
                <button class="btn" onclick="saveAll()">💾 Tüm Ayarları Kaydet</button>
                <button class="btn btn-outline" onclick="testChatAPI()">🧪 Chat Modelini Test Et</button>
                <button class="btn btn-outline" onclick="checkBalance()">💰 Bakiye & Hız Limiti</button>
                <button class="btn btn-outline" onclick="fetchVeniceModels()">🔄 Modelleri Listele</button>
            </div>
            <div id="api-result" style="margin-top:16px; font-family:monospace; font-size:12px; color:var(--warning); white-space:pre-wrap; background:var(--bg); padding:12px; border-radius:8px; display:none;"></div>
        </div>
    </div>

    <div id="tab-prompts" class="tab-content">
        <div class="card">
            <h3>📋 Roblox Studio Eklentisi Açılır Menü Şablonları</h3>
            <div id="presets-container"></div>
            <button class="btn btn-outline" onclick="addPresetRow()">➕ Yeni Görev Şablonu Ekle</button>
            <button class="btn" onclick="saveAll()" style="margin-left:8px;">💾 Şablonları Kaydet</button>
        </div>

        <div class="card">
            <h3>🧠 Evrensel Sistem Talimatı (System Prompt)</h3>
            <textarea id="system_prompt" rows="14"></textarea>
            <button class="btn" onclick="saveAll()">💾 Sistem Talimatını Kaydet</button>
        </div>
    </div>

    <div id="tab-live" class="tab-content">
        <div class="card">
            <h3>⚡ Canlı AI Üretim Akışı <button class="btn btn-outline" onclick="loadLogs()">🔄 Yenile</button></h3>
            <div id="live-stream-box" class="log-item" style="border-left-color:var(--success); min-height:80px;">Bekleniyor...</div>
        </div>
        <div class="card">
            <h3>📡 Studio'dan Gelen Tüm İstekler ve AI Yanıtları</h3>
            <div id="logs-container">Henüz işlem yapılmadı.</div>
        </div>
    </div>

    <div id="tab-errors" class="tab-content">
        <div class="card">
            <h3 style="color:var(--danger);">⚠️ Studio ve Sunucu Hata Kayıtları 
                <div>
                    <button class="btn btn-outline" onclick="loadErrors()">🔄 Yenile</button>
                    <button class="btn btn-danger" onclick="clearErrors()">🗑️ Temizle</button>
                </div>
            </h3>
            <div id="errors-container">Hiçbir hata kaydı yok.</div>
        </div>
    </div>

    <div id="tab-texture" class="tab-content">
        <div class="card">
            <h3>🎨 Venice.ai Görsel Test Stüdyosu</h3>
            <label>Görsel / Texture Promptu</label>
            <input type="text" id="tex_prompt" value="A photorealistic astronaut riding a horse on Mars">
            <button class="btn" onclick="testTexture()">✨ Görsel Üret</button>
            <div id="tex-output" style="margin-top:20px; text-align:center;"></div>
        </div>
    </div>

    <div id="tab-tools" class="tab-content">
        <div class="card">
            <h3>🔍 Web & Roblox Toolbox Arama Testi</h3>
            <div class="grid-2">
                <div>
                    <label>Roblox Toolbox Model Arama</label>
                    <input type="text" id="tb_query" value="medieval sword">
                    <button class="btn btn-outline" onclick="testToolbox()">📦 Toolbox'ta Ara</button>
                </div>
                <div>
                    <label>Web Dokümantasyon Arama</label>
                    <input type="text" id="web_query" value="Roblox Luau TweenService example">
                    <button class="btn btn-outline" onclick="testWebSearch()">🌐 Web'de Ara</button>
                </div>
            </div>
            <div id="tools-output" class="log-item" style="margin-top:16px; display:none;"></div>
        </div>
    </div>
</div>

<div id="toast">✅ Ayarlar Kaydedildi!</div>

<script>
let currentPresets = [];

function switchTab(tabId, btn) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + tabId).classList.add('active');
    btn.classList.add('active');
    if (tabId === 'live') loadLogs();
    if (tabId === 'errors') loadErrors();
}

function showToast(msg) {
    const t = document.getElementById('toast');
    t.innerText = msg;
    t.style.display = 'block';
    setTimeout(() => t.style.display = 'none', 3000);
}

async function loadData() {
    try {
        const res = await fetch('/api/admin/full-config');
        const cfg = await res.json();
        document.getElementById('api_key').value = cfg.api_key || '';
        document.getElementById('base_url').value = cfg.base_url || 'https://api.venice.ai/api/v1';
        document.getElementById('max_input_length').value = cfg.max_input_length || 80000;
        document.getElementById('chat_model').value = cfg.chat_model || 'openai-gpt-oss-120b';
        document.getElementById('image_model').value = cfg.image_model || 'seedream-v5-5';
        document.getElementById('image_size').value = cfg.image_size || '1K';
        document.getElementById('system_prompt').value = cfg.system_prompt || '';
        
        document.getElementById('stat-chat').innerText = cfg.chat_model || 'openai-gpt-oss-120b';
        document.getElementById('stat-img').innerText = cfg.image_model || 'seedream-v5-5';
        document.getElementById('stat-counts').innerText = (cfg.req_count || 0) + ' İstek / ' + (cfg.err_count || 0) + ' Hata';
        
        currentPresets = cfg.presets || [];
        renderPresets();
    } catch (e) {
        console.error('Ayarlar yüklenemedi:', e);
    }
}

function renderPresets() {
    const container = document.getElementById('presets-container');
    container.innerHTML = '';
    currentPresets.forEach((p, idx) => {
        const row = document.createElement('div');
        row.className = 'preset-row';
        
        const header = document.createElement('div');
        header.style.cssText = 'display:flex; justify-content:space-between; margin-bottom:8px;';
        header.innerHTML = '<strong>Şablon #' + (idx + 1) + '</strong>';
        
        const delBtn = document.createElement('button');
        delBtn.className = 'btn btn-danger';
        delBtn.innerText = 'Sil';
        delBtn.onclick = () => removePreset(idx);
        header.appendChild(delBtn);
        
        const titleInput = document.createElement('input');
        titleInput.type = 'text';
        titleInput.value = p.title || '';
        titleInput.onchange = (e) => { currentPresets[idx].title = e.target.value; };
        
        const promptArea = document.createElement('textarea');
        promptArea.rows = 2;
        promptArea.value = p.prompt || '';
        promptArea.onchange = (e) => { currentPresets[idx].prompt = e.target.value; };
        
        row.appendChild(header);
        row.appendChild(titleInput);
        row.appendChild(promptArea);
        container.appendChild(row);
    });
}

function addPresetRow() {
    currentPresets.push({ title: 'Yeni Görev Şablonu', prompt: '' });
    renderPresets();
}

function removePreset(idx) {
    currentPresets.splice(idx, 1);
    renderPresets();
}

async function saveAll() {
    const payload = {
        api_key: document.getElementById('api_key').value.trim(),
        base_url: document.getElementById('base_url').value.trim(),
        max_input_length: parseInt(document.getElementById('max_input_length').value) || 80000,
        chat_model: document.getElementById('chat_model').value.trim(),
        image_model: document.getElementById('image_model').value.trim(),
        image_size: document.getElementById('image_size').value.trim(),
        system_prompt: document.getElementById('system_prompt').value,
        presets: currentPresets
    };
    const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    if (res.ok) {
        showToast('✅ Kaydedildi! Studio eklentisi otomatik güncellenecek.');
        loadData();
    }
}

async function testChatAPI() {
    const out = document.getElementById('api-result');
    out.style.display = 'block';
    out.innerText = '⏳ Sohbet modeli test ediliyor...';
    const res = await fetch('/api/admin/test-chat', { method: 'POST' });
    const data = await res.json();
    out.innerText = JSON.stringify(data, null, 2);
}

async function checkBalance() {
    const out = document.getElementById('api-result');
    out.style.display = 'block';
    out.innerText = '⏳ Sorgulanıyor...';
    const res = await fetch('/api/admin/balance');
    out.innerText = JSON.stringify(await res.json(), null, 2);
}

async function fetchVeniceModels() {
    const out = document.getElementById('api-result');
    out.style.display = 'block';
    out.innerText = '⏳ Modeller çekiliyor...';
    const res = await fetch('/api/admin/models');
    out.innerText = JSON.stringify(await res.json(), null, 2);
}

async function testTexture() {
    const out = document.getElementById('tex-output');
    out.innerHTML = '⏳ Görsel üretiliyor...';
    const res = await fetch('/api/image/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: document.getElementById('tex_prompt').value })
    });
    const data = await res.json();
    if (data.b64_image) {
        out.innerHTML = '<img src="data:image/png;base64,' + data.b64_image + '" style="max-width:480px; border-radius:10px; border:1px solid var(--border);">';
    } else {
        out.innerHTML = '<pre style="text-align:left; color:var(--danger);">' + JSON.stringify(data, null, 2) + '</pre>';
    }
}

async function loadLogs() {
    const res = await fetch('/api/admin/logs');
    const data = await res.json();
    document.getElementById('live-stream-box').innerText = 'Durum: ' + data.live.status + ' (' + data.live.updated_at + ')\n\n' + (data.live.buffer || 'Henüz aktif akış yok.');
    const container = document.getElementById('logs-container');
    container.innerHTML = '';
    if (!data.logs.length) {
        container.innerText = 'Kayıt yok.';
        return;
    }
    data.logs.forEach(l => {
        const div = document.createElement('div');
        div.className = 'log-item';
        div.innerText = '🕒 ' + l.time + ' | Seçili Nesne: ' + l.selected_count + ' | Aksiyon: ' + l.action_count + '\nKomut: ' + l.prompt + '\nAI Planı: ' + l.thought;
        container.appendChild(div);
    });
}

async function loadErrors() {
    const res = await fetch('/api/admin/errors');
    const errs = await res.json();
    const container = document.getElementById('errors-container');
    container.innerHTML = '';
    if (!errs.length) {
        container.innerText = 'Hiçbir hata kaydı yok.';
        return;
    }
    errs.forEach(e => {
        const div = document.createElement('div');
        div.className = 'log-item error-item';
        div.innerText = '[' + e.time + '] ❌ ' + e.type + '\n' + e.details + (e.raw ? '\n--- HAM ÇIKTI ---\n' + e.raw : '');
        container.appendChild(div);
    });
}

async function clearErrors() {
    await fetch('/api/admin/clear-errors', { method: 'POST' });
    loadErrors();
}

async function testToolbox() {
    const out = document.getElementById('tools-output');
    out.style.display = 'block';
    out.innerText = 'Aranıyor...';
    const res = await fetch('/api/toolbox/search?q=' + encodeURIComponent(document.getElementById('tb_query').value));
    out.innerText = JSON.stringify(await res.json(), null, 2);
}

async function testWebSearch() {
    const out = document.getElementById('tools-output');
    out.style.display = 'block';
    out.innerText = 'Web taranıyor...';
    const res = await fetch('/api/search?q=' + encodeURIComponent(document.getElementById('web_query').value));
    out.innerText = JSON.stringify(await res.json(), null, 2);
}

loadData();
setInterval(() => {
    if (document.getElementById('tab-live').classList.contains('active')) loadLogs();
}, 2500);
</script>
</body>
</html>"""

@app.get("/api/admin/full-config")
async def full_config():
    cfg = get_config()
    cfg["req_count"] = len(REQUEST_LOGS)
    cfg["err_count"] = len(ERROR_LOGS)
    return cfg

@app.post("/api/settings")
async def update_settings(request: Request):
    save_config(await request.json())
    return {"status": "ok"}

@app.post("/api/admin/test-chat")
async def test_chat():
    cfg = get_config()
    headers = {"Authorization": f"Bearer {cfg.get('api_key')}", "Content-Type": "application/json"}
    payload = {
        "model": cfg.get("chat_model", "openai-gpt-oss-120b"),
        "messages": [{"role": "user", "content": "Hello! Reply with a short greeting."}]
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{cfg.get('base_url')}/chat/completions", json=payload, headers=headers, timeout=30.0)
        return {"status_code": r.status_code, "response": r.json() if r.status_code == 200 else r.text}

@app.get("/api/admin/balance")
async def get_balance():
    cfg = get_config()
    headers = {"Authorization": f"Bearer {cfg.get('api_key')}"}
    async with httpx.AsyncClient() as client:
        r1 = await client.get(f"{cfg.get('base_url')}/billing/balance", headers=headers, timeout=15.0)
        r2 = await client.get(f"{cfg.get('base_url')}/api_keys/rate_limits", headers=headers, timeout=15.0)
        return {
            "balance": r1.json() if r1.status_code == 200 else r1.text,
            "rate_limits": r2.json() if r2.status_code == 200 else r2.text
        }

@app.get("/api/admin/models")
async def list_models():
    cfg = get_config()
    headers = {"Authorization": f"Bearer {cfg.get('api_key')}"}
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{cfg.get('base_url')}/models", headers=headers, timeout=15.0)
        return [m.get("id") for m in r.json().get("data", [])] if r.status_code == 200 else r.text

@app.get("/api/admin/logs")
async def get_logs():
    return {"live": LIVE_STREAM_STATE, "logs": list(reversed(REQUEST_LOGS[-30:]))}

@app.get("/api/admin/errors")
async def get_errors():
    return list(reversed(ERROR_LOGS[-30:]))

@app.post("/api/admin/clear-errors")
async def clear_errors():
    ERROR_LOGS.clear()
    return {"status": "cleared"}

@app.post("/api/log_error")
async def log_studio_error(request: Request):
    data = await request.json()
    add_error_log(data.get("type", "Studio Hatası"), data.get("details", ""), data.get("raw", ""))
    return {"status": "logged"}

@app.get("/api/config")
async def public_config():
    cfg = get_config()
    return {
        "presets": cfg.get("presets", []),
        "chat_model": cfg.get("chat_model", "openai-gpt-oss-120b"),
        "image_model": cfg.get("image_model", "seedream-v5-5"),
        "has_key": bool(cfg.get("api_key"))
    }

@app.get("/api/search")
async def web_search(q: str):
    try:
        results = DDGS().text(q, max_results=4)
        return {"results": results}
    except Exception as e:
        return {"results": [], "error": str(e)}

@app.get("/api/toolbox/search")
async def search_toolbox(q: str):
    url = f"https://apis.roblox.com/toolbox-service/v1/marketplace/10?keyword={q}&limit=5"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=15.0)
        if r.status_code == 200:
            data = r.json().get("data", [])
            return [{"id": i.get("id")} for i in data if i.get("id")]
    return []

@app.post("/api/image/generate")
async def generate_image(request: Request):
    body = await request.json()
    cfg = get_config()
    headers = {"Authorization": f"Bearer {cfg.get('api_key')}", "Content-Type": "application/json"}
    payload = {
        "model": cfg.get("image_model", "seedream-v5-5"),
        "prompt": body.get("prompt", ""),
        "size": cfg.get("image_size", "1K"),
        "response_format": "b64_json"
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{cfg.get('base_url')}/images/generations", json=payload, headers=headers, timeout=60.0)
        if r.status_code != 200:
            add_error_log("Görsel API Hatası", r.text)
            return {"error": r.text}
        res_json = r.json()
        b64_data = None
        if "data" in res_json and len(res_json["data"]) > 0:
            b64_data = res_json["data"][0].get("b64_json")
        elif "images" in res_json and len(res_json["images"]) > 0:
            b64_data = res_json["images"][0]
        return {"b64_image": b64_data, "raw": res_json}

@app.post("/api/agent/execute")
async def agent_execute(request: Request):
    body = await request.json()
    cfg = get_config()
    
    if not cfg.get("api_key"):
        add_error_log("API Anahtarı Eksik", "Sunucu panelinden Venice.ai API anahtarınızı girmediniz.")
        return JSONResponse({"error": "API Anahtarı eksik! https://robloxai-1ea7.onrender.com adresinden API anahtarınızı kaydedin."}, status_code=400)

    user_prompt = body.get("prompt", "")
    scene_context = body.get("scene_context", [])
    selected_items = body.get("selected_items", [])
    use_web = body.get("use_web", False)

    web_context = ""
    if use_web:
        try:
            search_res = DDGS().text(f"Roblox Luau {user_prompt[:80]}", max_results=3)
            web_context = f"\nWEB ARAMA SONUÇLARI:\n{json.dumps(search_res, ensure_ascii=False)}\n"
        except Exception:
            pass

    full_user_msg = (
        f"KULLANICI KOMUTU:\n{user_prompt}\n"
        f"{web_context}\n"
        f"SEÇİLİ NESNELER VE TAM KODLARI ({len(selected_items)} adet):\n{json.dumps(selected_items, ensure_ascii=False)}\n\n"
        f"MEVCUT 3D SAHNE ÖZETİ:\n{json.dumps(scene_context, ensure_ascii=False)}"
    )

    headers = {
        "Authorization": f"Bearer {cfg.get('api_key')}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": cfg.get("chat_model", "openai-gpt-oss-120b"),
        "stream": True,
        "messages": [
            {"role": "system", "content": cfg.get("system_prompt", "")},
            {"role": "user", "content": full_user_msg[:cfg.get("max_input_length", 80000)]}
        ]
    }

    LIVE_STREAM_STATE["status"] = "Yapay Zeka Yazıyor..."
    LIVE_STREAM_STATE["buffer"] = ""
    LIVE_STREAM_STATE["active"] = True
    LIVE_STREAM_STATE["updated_at"] = time.strftime("%H:%M:%S")

    collected_chunks = []

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream("POST", f"{cfg.get('base_url')}/chat/completions", json=payload, headers=headers, timeout=120.0) as response:
                if response.status_code != 200:
                    err_body = (await response.aread()).decode("utf-8", errors="ignore")
                    add_error_log(f"Venice API HTTP {response.status_code}", err_body)
                    LIVE_STREAM_STATE["status"] = f"Hata ({response.status_code})"
                    LIVE_STREAM_STATE["active"] = False
                    return JSONResponse({"error": f"Venice API Hatası ({response.status_code}): {err_body}"}, status_code=400)

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk["choices"][0].get("delta", {})
                        content_piece = delta.get("content") or ""
                        if content_piece:
                            collected_chunks.append(content_piece)
                            LIVE_STREAM_STATE["buffer"] = "".join(collected_chunks)[-3000:]
                            LIVE_STREAM_STATE["updated_at"] = time.strftime("%H:%M:%S")
                    except Exception:
                        continue
    except Exception as e:
        add_error_log("Stream Bağlantı Hatası", str(e))
        LIVE_STREAM_STATE["status"] = "Bağlantı Kesildi"
        LIVE_STREAM_STATE["active"] = False
        return JSONResponse({"error": f"Sunucu Stream Hatası: {str(e)}"}, status_code=500)

    raw_full_output = "".join(collected_chunks)
    LIVE_STREAM_STATE["status"] = "Tamamlandı"
    LIVE_STREAM_STATE["active"] = False

    try:
        repaired_dict = extract_and_repair_json(raw_full_output)
        actions = repaired_dict.get("actions", [])
        if len(actions) == 1 and actions[0].get("type") == "fallback_code":
            code_str = actions[0].get("source", "")
            script_targets = [i for i in selected_items if i.get("Class") in ("Script", "LocalScript", "ModuleScript")]
            if script_targets:
                repaired_dict["actions"] = [{
                    "type": "edit_script",
                    "path": script_targets[0]["Path"],
                    "source": code_str
                }]
            else:
                repaired_dict["actions"] = [{
                    "type": "write_script",
                    "script_type": "Script",
                    "name": "AIGeneratedScript",
                    "parent": "ServerScriptService",
                    "source": code_str
                }]

        REQUEST_LOGS.append({
            "time": time.strftime("%H:%M:%S"),
            "prompt": user_prompt[:200],
            "selected_count": len(selected_items),
            "action_count": len(repaired_dict.get("actions", [])),
            "thought": repaired_dict.get("thought", "")[:300]
        })
        return JSONResponse(repaired_dict)
    except Exception as e:
        add_error_log("JSON Onarım Hatası", str(e), raw_full_output)
        return JSONResponse({"error": str(e), "raw": raw_full_output[:1500]}, status_code=422)

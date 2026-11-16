import os
import json
import time
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

app = FastAPI()
CONFIG_FILE = "config.json"
RECENT_LOGS = []

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

@app.get("/", response_class=HTMLResponse)
async def admin_dashboard():
    return """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Roblox AI Architect — Yönetici Paneli</title>
    <style>
        :root {
            --bg: #0b0d13; --sidebar: #11141d; --card: #161a24; --border: #252b3b;
            --accent: #3b82f6; --accent-hover: #2563eb; --success: #10b981;
            --danger: #ef4444; --warning: #f59e0b; --text: #f1f5f9; --muted: #94a3b8;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); display: flex; min-height: 100vh; }
        .sidebar { width: 250px; background: var(--sidebar); border-right: 1px solid var(--border); padding: 24px 16px; display: flex; flex-direction: column; gap: 8px; }
        .brand { font-size: 18px; font-weight: 800; color: #fff; padding: 0 12px 20px; border-bottom: 1px solid var(--border); margin-bottom: 12px; }
        .nav-btn { background: transparent; border: none; color: var(--muted); padding: 12px 14px; text-align: left; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 600; transition: 0.2s; }
        .nav-btn:hover, .nav-btn.active { background: var(--card); color: #fff; border-left: 3px solid var(--accent); }
        .main { flex: 1; padding: 32px; max-width: 1100px; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .top-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .stat-box { background: var(--card); border: 1px solid var(--border); padding: 18px; border-radius: 10px; }
        .stat-title { font-size: 12px; color: var(--muted); text-transform: uppercase; }
        .stat-val { font-size: 18px; font-weight: 700; margin-top: 6px; color: #fff; word-break: break-all; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 24px; margin-bottom: 24px; }
        .card h3 { font-size: 16px; margin-bottom: 16px; color: #fff; border-bottom: 1px solid var(--border); padding-bottom: 10px; }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 6px; font-weight: 600; }
        input, textarea { width: 100%; background: var(--bg); border: 1px solid var(--border); color: #fff; padding: 11px 14px; border-radius: 8px; margin-bottom: 16px; font-size: 13px; font-family: monospace; }
        .btn { background: var(--accent); color: #fff; border: none; padding: 11px 20px; border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 13px; }
        .btn:hover { background: var(--accent-hover); }
        .btn-danger { background: var(--danger); padding: 6px 12px; font-size: 12px; }
        .btn-outline { background: transparent; border: 1px solid var(--border); color: var(--text); }
        .preset-row { background: var(--bg); border: 1px solid var(--border); padding: 14px; border-radius: 8px; margin-bottom: 12px; }
        .log-item { background: var(--bg); border-left: 3px solid var(--accent); padding: 12px; border-radius: 6px; margin-bottom: 10px; font-family: monospace; font-size: 12px; }
        #toast { position: fixed; bottom: 24px; right: 24px; background: var(--success); color: #fff; padding: 14px 22px; border-radius: 8px; font-weight: 600; display: none; z-index: 999; }
    </style>
</head>
<body>
<div class="sidebar">
    <div class="brand">⚡ AI Studio Admin</div>
    <button class="nav-btn active" onclick="switchTab('models', this)">🔑 API & Model Ayarları</button>
    <button class="nav-btn" onclick="switchTab('prompts', this)">📋 Hazır Şablonlar & Kurallar</button>
    <button class="nav-btn" onclick="switchTab('texture', this)">🎨 Görsel & Texture Testi</button>
    <button class="nav-btn" onclick="switchTab('logs', this)">📡 Canlı İstek Logları</button>
</div>
<div class="main">
    <div class="top-stats">
        <div class="stat-box"><div class="stat-title">Sunucu Durumu</div><div class="stat-val" style="color: var(--success);">● Çevrimiçi</div></div>
        <div class="stat-box"><div class="stat-title">Aktif Dil Modeli</div><div class="stat-val" id="stat-chat">-</div></div>
        <div class="stat-box"><div class="stat-title">Aktif Görsel Modeli</div><div class="stat-val" id="stat-img">-</div></div>
        <div class="stat-box"><div class="stat-title">Bakiye Durumu</div><div class="stat-val" id="stat-balance">Sorgulanmadı</div></div>
    </div>

    <div id="tab-models" class="tab-content active">
        <div class="card">
            <h3>🔑 API Anahtarı ve Sağlayıcı Bağlantısı</h3>
            <label>API Anahtarı (Venice.ai / OpenRouter / OpenAI)</label>
            <input type="password" id="api_key" placeholder="API anahtarınızı buraya yapıştırın...">
            <div class="grid-2">
                <div><label>API Base URL</label><input type="text" id="base_url"></div>
                <div><label>Maksimum Girdi Karakter Sınırı</label><input type="number" id="max_input_length"></div>
            </div>
            <div style="display:flex; gap:10px; flex-wrap:wrap;">
                <button class="btn" onclick="saveAll()">💾 Ayarları Kaydet</button>
                <button class="btn btn-outline" onclick="checkBalance()">💰 Bakiye & Limit Sorgula</button>
                <button class="btn btn-outline" onclick="fetchVeniceModels()">🔄 Modelleri Listele</button>
            </div>
            <div id="api-result" style="margin-top:14px; font-family:monospace; font-size:12px; color:var(--warning); white-space:pre-wrap;"></div>
        </div>

        <div class="card">
            <h3>🤖 Yapay Zeka Modelleri Seçimi</h3>
            <div class="grid-2">
                <div><label>Dil & Kodlama Modeli</label><input type="text" id="chat_model"></div>
                <div><label>Görsel & Texture Modeli</label><input type="text" id="image_model"></div>
            </div>
            <button class="btn" onclick="saveAll()">💾 Modelleri Kaydet</button>
        </div>
    </div>

    <div id="tab-prompts" class="tab-content">
        <div class="card">
            <h3>📋 Roblox Eklentisi Açılır Menü Şablonları</h3>
            <div id="presets-container"></div>
            <button class="btn btn-outline" onclick="addPresetRow()">➕ Yeni Şablon Ekle</button>
            <button class="btn" onclick="saveAll()" style="margin-left:8px;">💾 Şablonları Kaydet</button>
        </div>
        <div class="card">
            <h3>🧠 Yapay Zeka Sistem Talimatı (System Prompt)</h3>
            <textarea id="system_prompt" rows="10"></textarea>
            <button class="btn" onclick="saveAll()">💾 Sistem Talimatını Kaydet</button>
        </div>
    </div>

    <div id="tab-texture" class="tab-content">
        <div class="card">
            <h3>🎨 AI Texture & Görsel Üretim Testi</h3>
            <input type="text" id="tex_prompt" placeholder="Örn: seamless medieval stone brick wall texture">
            <button class="btn" onclick="testTexture()">✨ Görsel Oluştur</button>
            <div id="tex-output" style="margin-top:20px; text-align:center;"></div>
        </div>
    </div>

    <div id="tab-logs" class="tab-content">
        <div class="card">
            <h3>📡 Son İstekler</h3>
            <button class="btn btn-outline" onclick="loadLogs()" style="margin-bottom:14px;">🔄 Yenile</button>
            <div id="logs-container">Henüz işlem yapılmadı.</div>
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
    if(tabId === 'logs') loadLogs();
}
function showToast(msg) {
    const t = document.getElementById('toast');
    t.innerText = msg; t.style.display = 'block';
    setTimeout(() => t.style.display = 'none', 3000);
}
async function loadData() {
    const res = await fetch('/api/admin/full-config');
    const cfg = await res.json();
    document.getElementById('api_key').value = cfg.api_key || '';
    document.getElementById('base_url').value = cfg.base_url || 'https://api.venice.ai/api/v1';
    document.getElementById('max_input_length').value = cfg.max_input_length || 25000;
    document.getElementById('chat_model').value = cfg.chat_model || '';
    document.getElementById('image_model').value = cfg.image_model || '';
    document.getElementById('system_prompt').value = cfg.system_prompt || '';
    document.getElementById('stat-chat').innerText = cfg.chat_model || '-';
    document.getElementById('stat-img').innerText = cfg.image_model || '-';
    currentPresets = cfg.presets || [];
    renderPresets();
}
function renderPresets() {
    const container = document.getElementById('presets-container');
    container.innerHTML = '';
    currentPresets.forEach((p, index) => {
        container.innerHTML += `
            <div class="preset-row">
                <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                    <strong>Şablon #${index + 1}</strong>
                    <button class="btn btn-danger" onclick="removePreset(${index})">Sil</button>
                </div>
                <input type="text" value="${p.title.replace(/"/g, '&quot;')}" onchange="currentPresets[${index}].title = this.value">
                <textarea rows="2" onchange="currentPresets[${index}].prompt = this.value">${p.prompt}</textarea>
            </div>`;
    });
}
function addPresetRow() { currentPresets.push({title: "Yeni Şablon", prompt: ""}); renderPresets(); }
function removePreset(idx) { currentPresets.splice(idx, 1); renderPresets(); }
async function saveAll() {
    const payload = {
        api_key: document.getElementById('api_key').value.trim(),
        base_url: document.getElementById('base_url').value.trim(),
        max_input_length: parseInt(document.getElementById('max_input_length').value) || 25000,
        chat_model: document.getElementById('chat_model').value.trim(),
        image_model: document.getElementById('image_model').value.trim(),
        system_prompt: document.getElementById('system_prompt').value,
        presets: currentPresets
    };
    const res = await fetch('/api/settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    if(res.ok) { showToast('✅ Kaydedildi! Eklentide Eşitle butonuna basın.'); loadData(); }
}
async function checkBalance() {
    const out = document.getElementById('api-result'); out.innerText = '⏳ Sorgulanıyor...';
    const res = await fetch('/api/admin/balance'); const data = await res.json();
    out.innerText = JSON.stringify(data, null, 2);
}
async function fetchVeniceModels() {
    const out = document.getElementById('api-result'); out.innerText = '⏳ Modeller çekiliyor...';
    const res = await fetch('/api/admin/models'); const data = await res.json();
    out.innerText = JSON.stringify(data, null, 2);
}
async function testTexture() {
    const out = document.getElementById('tex-output'); out.innerHTML = '⏳ Üretiliyor...';
    const res = await fetch('/api/image/generate', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt: document.getElementById('tex_prompt').value}) });
    const data = await res.json();
    if(data.images && data.images[0]) out.innerHTML = `<img src="data:image/png;base64,${data.images[0]}" style="max-width:400px; border-radius:8px;">`;
    else out.innerHTML = `<pre>${JSON.stringify(data, null, 2)}</pre>`;
}
async function loadLogs() {
    const res = await fetch('/api/admin/logs'); const logs = await res.json();
    document.getElementById('logs-container').innerHTML = logs.length ? logs.map(l => `<div class="log-item">🕒 ${l.time} | Seçili: ${l.selected_count}<br><strong>Komut:</strong> ${l.prompt}</div>`).join('') : 'Kayıt yok.';
}
loadData();
</script>
</body>
</html>
    """

@app.get("/api/admin/full-config")
async def full_config():
    return get_config()

@app.post("/api/settings")
async def update_settings(request: Request):
    save_config(await request.json())
    return {"status": "ok"}

@app.get("/api/admin/balance")
async def get_balance():
    cfg = get_config()
    headers = {"Authorization": f"Bearer {cfg.get('api_key')}"}
    async with httpx.AsyncClient() as client:
        r1 = await client.get(f"{cfg.get('base_url')}/billing/balance", headers=headers, timeout=15.0)
        r2 = await client.get(f"{cfg.get('base_url')}/api_keys/rate_limits", headers=headers, timeout=15.0)
        return {"balance": r1.json() if r1.status_code == 200 else r1.text, "rate_limits": r2.json() if r2.status_code == 200 else r2.text}

@app.get("/api/admin/models")
async def list_models():
    cfg = get_config()
    headers = {"Authorization": f"Bearer {cfg.get('api_key')}"}
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{cfg.get('base_url')}/models", headers=headers, timeout=15.0)
        return [m.get("id") for m in r.json().get("data", [])] if r.status_code == 200 else r.text

@app.get("/api/admin/logs")
async def get_logs():
    return list(reversed(RECENT_LOGS[-20:]))

@app.get("/api/config")
async def public_config():
    cfg = get_config()
    return {"presets": cfg.get("presets", []), "chat_model": cfg.get("chat_model"), "image_model": cfg.get("image_model"), "has_key": bool(cfg.get("api_key"))}

@app.get("/api/toolbox/search")
async def search_toolbox(q: str):
    # Güncel Roblox Creator Store / Toolbox API
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
    payload = {"model": cfg.get("image_model", "fluently-xl"), "prompt": body.get("prompt", ""), "width": 512, "height": 512}
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{cfg.get('base_url')}/image/generate", json=payload, headers=headers, timeout=60.0)
        return r.json()

@app.post("/api/agent/stream")
async def agent_stream(request: Request):
    body = await request.json()
    cfg = get_config()
    user_prompt = body.get("prompt", "")
    scene_context = body.get("scene_context", {})
    selected_items = body.get("selected_items", [])
    
    RECENT_LOGS.append({"time": time.strftime("%H:%M:%S"), "prompt": user_prompt[:120], "selected_count": len(selected_items)})
    
    full_message = (
        f"KULLANICI İSTEĞİ: {user_prompt}\n\n"
        f"SEÇİLİ NESNELER VE KODLARI:\n{json.dumps(selected_items, ensure_ascii=False)}\n\n"
        f"SAHNE ÖZETİ:\n{json.dumps(scene_context, ensure_ascii=False)}"
    )

    headers = {"Authorization": f"Bearer {cfg.get('api_key')}", "Content-Type": "application/json"}
    payload = {
        "model": cfg.get("chat_model", "zai-org-glm-5-1"),
        "stream": True,
        "messages": [
            {"role": "system", "content": cfg.get("system_prompt", "")},
            {"role": "user", "content": full_message}
        ]
    }

    async def event_generator():
        async with httpx.AsyncClient() as client:
            async with client.stream("POST", f"{cfg.get('base_url')}/chat/completions", json=payload, headers=headers, timeout=90.0) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]": break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk["choices"][0]["delta"].get("content", "")
                            if delta: yield delta
                        except Exception:
                            continue

    return StreamingResponse(event_generator(), media_type="text/plain")

import os
import json
import time
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from duckduckgo_search import DDGS

app = FastAPI()
CONFIG_FILE = "config.json"
RECENT_LOGS = []
ERROR_LOGS = []

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
    <title>AI Studio Evrensel Admin Paneli</title>
    <style>
        :root { --bg: #0b0d13; --sidebar: #11141d; --card: #161a24; --border: #252b3b; --accent: #3b82f6; --danger: #ef4444; --text: #f1f5f9; --muted: #94a3b8; }
        body { font-family: system-ui, sans-serif; background: var(--bg); color: var(--text); display: flex; min-height: 100vh; margin: 0; }
        .sidebar { width: 250px; background: var(--sidebar); border-right: 1px solid var(--border); padding: 20px; display: flex; flex-direction: column; gap: 8px; }
        .nav-btn { background: transparent; border: none; color: var(--muted); padding: 12px; text-align: left; border-radius: 8px; cursor: pointer; font-weight: 600; }
        .nav-btn.active { background: var(--card); color: #fff; border-left: 3px solid var(--accent); }
        .main { flex: 1; padding: 30px; max-width: 1200px; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 20px; margin-bottom: 20px; }
        input, textarea { width: 100%; background: var(--bg); border: 1px solid var(--border); color: #fff; padding: 10px; border-radius: 6px; margin-bottom: 15px; font-family: monospace; box-sizing: border-box; }
        .btn { background: var(--accent); color: #fff; border: none; padding: 10px 16px; border-radius: 6px; cursor: pointer; font-weight: 600; }
        .log-item { background: var(--bg); border-left: 3px solid var(--accent); padding: 10px; margin-bottom: 8px; font-family: monospace; font-size: 12px; }
        .error-item { border-left-color: var(--danger); color: #fca5a5; }
    </style>
</head>
<body>
<div class="sidebar">
    <h3 style="color:#fff; margin-top:0;">⚡ AI Studio</h3>
    <button class="nav-btn active" onclick="switchTab('settings', this)">⚙️ Ayarlar & Modeller</button>
    <button class="nav-btn" onclick="switchTab('logs', this)">📡 İstek Logları</button>
    <button class="nav-btn" onclick="switchTab('errors', this)">⚠️ Studio Hata Kayıtları</button>
</div>
<div class="main">
    <div id="tab-settings" class="tab-content active">
        <div class="card">
            <h3>API ve Sistem Yapılandırması</h3>
            <label>Venice.ai / OpenAI API Anahtarı</label>
            <input type="password" id="api_key">
            <label>API Base URL</label>
            <input type="text" id="base_url" value="https://api.venice.ai/api/v1">
            <div style="display:flex; gap:10px;">
                <div style="flex:1;"><label>Dil Modeli</label><input type="text" id="chat_model"></div>
                <div style="flex:1;"><label>Görsel Modeli</label><input type="text" id="image_model"></div>
            </div>
            <label>Evrensel Sistem Talimatı (System Prompt)</label>
            <textarea id="system_prompt" rows="8"></textarea>
            <button class="btn" onclick="saveSettings()">💾 Ayarları Kaydet</button>
        </div>
    </div>
    <div id="tab-logs" class="tab-content">
        <div class="card">
            <h3>Son İstekler <button class="btn" style="float:right; padding:5px 10px;" onclick="loadLogs()">Yenile</button></h3>
            <div id="logs-container"></div>
        </div>
    </div>
    <div id="tab-errors" class="tab-content">
        <div class="card">
            <h3 style="color:var(--danger);">Studio Hata Bildirimleri <button class="btn" style="float:right; padding:5px 10px;" onclick="loadErrors()">Yenile</button></h3>
            <div id="errors-container"></div>
        </div>
    </div>
</div>
<script>
function switchTab(id, btn) {
    document.querySelectorAll('.tab-content').forEach(e => e.classList.remove('active'));
    document.querySelectorAll('.nav-btn').forEach(e => e.classList.remove('active'));
    document.getElementById('tab-' + id).classList.add('active');
    btn.classList.add('active');
    if(id==='logs') loadLogs();
    if(id==='errors') loadErrors();
}
async function loadData() {
    const res = await fetch('/api/admin/config');
    const cfg = await res.json();
    document.getElementById('api_key').value = cfg.api_key || '';
    document.getElementById('base_url').value = cfg.base_url || 'https://api.venice.ai/api/v1';
    document.getElementById('chat_model').value = cfg.chat_model || '';
    document.getElementById('image_model').value = cfg.image_model || '';
    document.getElementById('system_prompt').value = cfg.system_prompt || '';
}
async function saveSettings() {
    const payload = {
        api_key: document.getElementById('api_key').value,
        base_url: document.getElementById('base_url').value,
        chat_model: document.getElementById('chat_model').value,
        image_model: document.getElementById('image_model').value,
        system_prompt: document.getElementById('system_prompt').value
    };
    await fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    alert('Kaydedildi!');
}
async function loadLogs() {
    const res = await fetch('/api/admin/logs');
    const logs = await res.json();
    document.getElementById('logs-container').innerHTML = logs.map(l => `<div class="log-item">[${l.time}] ${l.prompt}</div>`).join('');
}
async function loadErrors() {
    const res = await fetch('/api/admin/errors');
    const errs = await res.json();
    document.getElementById('errors-container').innerHTML = errs.map(e => `<div class="log-item error-item">[${e.time}] <b>${e.type}</b><br>${e.details}</div>`).join('');
}
loadData();
</script>
</body>
</html>
    """

@app.get("/api/admin/config")
async def get_full_config():
    return get_config()

@app.get("/api/config")
async def public_config():
    cfg = get_config()
    return {"chat_model": cfg.get("chat_model"), "has_key": bool(cfg.get("api_key"))}

@app.post("/api/settings")
async def update_settings(request: Request):
    save_config(await request.json())
    return {"status": "ok"}

@app.get("/api/admin/logs")
async def get_logs():
    return list(reversed(RECENT_LOGS[-30:]))

@app.get("/api/admin/errors")
async def get_errors():
    return list(reversed(ERROR_LOGS[-30:]))

@app.post("/api/log_error")
async def log_error(request: Request):
    data = await request.json()
    ERROR_LOGS.append({
        "time": time.strftime("%H:%M:%S"),
        "type": data.get("type", "Bilinmeyen Hata"),
        "details": data.get("details", "")
    })
    return {"status": "logged"}

@app.get("/api/search")
async def web_search(q: str):
    try:
        results = DDGS().text(q, max_results=3)
        return {"results": results}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/agent/stream")
async def agent_stream(request: Request):
    body = await request.json()
    cfg = get_config()
    user_prompt = body.get("prompt", "")
    RECENT_LOGS.append({"time": time.strftime("%H:%M:%S"), "prompt": user_prompt[:200]})
    
    headers = {"Authorization": f"Bearer {cfg.get('api_key')}", "Content-Type": "application/json"}
    payload = {
        "model": cfg.get("chat_model", "zai-org-glm-5-1"),
        "stream": True,
        "messages": [
            {"role": "system", "content": cfg.get("system_prompt", "")},
            {"role": "user", "content": f"KULLANICI İSTEĞİ: {user_prompt}\n\nSAHNE VE SEÇİLİ NESNELER:\n{json.dumps(body.get('selected_items', []), ensure_ascii=False)}"}
        ]
    }

    async def event_generator():
        async with httpx.AsyncClient() as client:
            try:
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
            except Exception as e:
                yield f"\n[SUNUCU HATASI]: {str(e)}"

    return StreamingResponse(event_generator(), media_type="text/plain")

import os
import json
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any

app = FastAPI()
CONFIG_FILE = "config.json"

def get_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ortam değişkeni varsa öncelik ver, yoksa panelden kaydedileni kullan
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
async def web_panel():
    cfg = get_config()
    presets_json = json.dumps(cfg.get("presets", []), ensure_ascii=False, indent=2)
    return f"""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <title>Roblox Studio AI Architect - Yönetim Paneli</title>
        <style>
            :root {{ --bg: #0f1117; --card: #181b24; --border: #2a2f3d; --accent: #3b82f6; --text: #e2e8f0; }}
            body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 30px; }}
            .container {{ max-width: 850px; margin: auto; }}
            .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 15px; margin-bottom: 25px; }}
            .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 20px; margin-bottom: 20px; }}
            label {{ display: block; font-size: 13px; color: #94a3b8; margin-bottom: 6px; font-weight: 600; }}
            input, textarea {{ width: 100%; background: #0f1117; border: 1px solid var(--border); color: #fff; padding: 10px; border-radius: 6px; box-sizing: border-box; margin-bottom: 15px; font-family: monospace; }}
            button {{ background: var(--accent); color: white; border: none; padding: 12px 24px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 14px; }}
            button:hover {{ background: #2563eb; }}
            .status {{ padding: 10px; border-radius: 6px; display: none; margin-top: 10px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h2>⚡ Roblox AI Studio — Kontrol Merkezi</h2>
                <span style="color:#10b981; font-size:14px;">● Sunucu Aktif</span>
            </div>
            <div class="card">
                <h3>🔑 API ve Model Yapılandırması</h3>
                <label>API Anahtarı (Venice.ai / OpenAI Uyumlu)</label>
                <input type="password" id="api_key" value="{cfg.get('api_key', '')}" placeholder="sk-...">
                
                <label>API Base URL</label>
                <input type="text" id="base_url" value="{cfg.get('base_url', 'https://api.venice.ai/api/v1')}">
                
                <div style="display:flex; gap:15px;">
                    <div style="flex:1;">
                        <label>Dil & Kodlama Modeli</label>
                        <input type="text" id="chat_model" value="{cfg.get('chat_model', 'zai-org-glm-5-1')}">
                    </div>
                    <div style="flex:1;">
                        <label>Görsel & Texture Modeli</label>
                        <input type="text" id="image_model" value="{cfg.get('image_model', 'fluently-xl')}">
                    </div>
                </div>
            </div>

            <div class="card">
                <h3>🧠 Sistem Komutu ve Hazır Şablonlar (Plugin Otomatik Çeker)</h3>
                <label>Yapay Zeka Sistem Talimatı (System Prompt)</label>
                <textarea id="system_prompt" rows="6">{cfg.get('system_prompt', '')}</textarea>
                
                <label>Açılır Menü Hazır Promptları (JSON Formatı)</label>
                <textarea id="presets" rows="8">{presets_json}</textarea>
                
                <button onclick="saveSettings()">Ayarları Kaydet ve Eklentiyi Güncelle</button>
                <div id="status" class="status"></div>
            </div>
        </div>
        <script>
            async function saveSettings() {{
                const st = document.getElementById('status');
                try {{
                    const payload = {{
                        api_key: document.getElementById('api_key').value,
                        base_url: document.getElementById('base_url').value,
                        chat_model: document.getElementById('chat_model').value,
                        image_model: document.getElementById('image_model').value,
                        system_prompt: document.getElementById('system_prompt').value,
                        presets: JSON.parse(document.getElementById('presets').value)
                    }};
                    const res = await fetch('/api/settings', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify(payload)
                    }});
                    if(res.ok) {{
                        st.style.display = 'block';
                        st.style.background = '#064e3b';
                        st.innerText = '✅ Ayarlar kaydedildi! Roblox Studio eklentisinde Yenile butonuna basmanız yeterli.';
                    }}
                }} catch(e) {{
                    st.style.display = 'block';
                    st.style.background = '#7f1d1d';
                    st.innerText = '❌ JSON formatında hata var: ' + e.message;
                }}
            }}
        </script>
    </body>
    </html>
    """

@app.post("/api/settings")
async def update_settings(request: Request):
    data = await request.json()
    save_config(data)
    return {"status": "ok"}

@app.get("/api/config")
async def public_config():
    cfg = get_config()
    return {
        "presets": cfg.get("presets", []),
        "chat_model": cfg.get("chat_model"),
        "image_model": cfg.get("image_model"),
        "has_key": bool(cfg.get("api_key"))
    }

@app.get("/api/toolbox/search")
async def search_toolbox(q: str):
    # Roblox Katalog API üzerinden ücretsiz model arama
    url = f"https://search.roblox.com/catalog/json?CatalogContext=2&Category=6&Keyword={q}&ResultsPerPage=5"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=15.0)
        if r.status_code == 200:
            items = r.json()
            return [{"id": i.get("AssetId"), "name": i.get("Name")} for i in items if i.get("AssetId")]
    return []

@app.post("/api/image/generate")
async def generate_image(request: Request):
    body = await request.json()
    cfg = get_config()
    headers = {"Authorization": f"Bearer {cfg.get('api_key')}", "Content-Type": "application/json"}
    payload = {
        "model": cfg.get("image_model", "fluently-xl"),
        "prompt": body.get("prompt", ""),
        "width": 512,
        "height": 512
    }
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
    
    full_message = (
        f"KULLANICI İSTEĞİ: {user_prompt}\n\n"
        f"SEÇİLİ NESNELER VE KODLARI:\n{json.dumps(selected_items, ensure_ascii=False)}\n\n"
        f"SAHNE ÖZETİ (3D KONUMLAR VE HİYERARŞİ):\n{json.dumps(scene_context, ensure_ascii=False)}"
    )

    headers = {
        "Authorization": f"Bearer {cfg.get('api_key')}",
        "Content-Type": "application/json"
    }

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
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk["choices"][0]["delta"].get("content", "")
                            if delta:
                                yield delta
                        except Exception:
                            continue

    return StreamingResponse(event_generator(), media_type="text/plain")

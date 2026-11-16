import os
import json
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI()
VENICE_API_KEY = os.getenv("VENICE_API_KEY", "")
VENICE_API_URL = "[https://api.venice.ai/api/v1/chat/completions](https://api.venice.ai/api/v1/chat/completions)"

class PluginRequest(BaseModel):
    mode: str
    prompt: str
    target_code: str = ""

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    config = load_config()
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Roblox AI Plugin - Kontrol Paneli</title>
        <style>
            body {{ font-family: Arial; background: #1e1e24; color: #fff; padding: 20px; }}
            .card {{ background: #2b2b36; padding: 20px; border-radius: 8px; max-width: 600px; margin: auto; }}
            h2 {{ color: #00d2ff; }}
            pre {{ background: #111; padding: 10px; border-radius: 5px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h2>Roblox AI Sunucusu Aktif 🚀</h2>
            <p><strong>Aktif Model:</strong> {config['model_name']}</p>
            <p><strong>Karakter Sınırı:</strong> {config['max_input_length']}</p>
            <h3>Bağlı Prompt Modları:</h3>
            <ul>
                {"".join([f"<li>{m}</li>" for m in config['modes']])}
            </ul>
            <p><i>Not: Bu ayarları GitHub'daki config.json dosyasından değiştirebilirsiniz.</i></p>
        </div>
    </body>
    </html>
    """
    return html_content

@app.get("/api/config")
async def get_config():
    return load_config()

@app.post("/api/generate")
async def generate_code(req: PluginRequest):
    config = load_config()
    
    if len(req.target_code) > config["max_input_length"]:
        raise HTTPException(status_code=400, detail="Kod çok uzun! Güvenlik sınırı aşıldı.")

    user_message = f"İşlem Modu: {req.mode}\nİstek: {req.prompt}\nMevcut Kod/Durum: \n{req.target_code}"
    
    headers = {
        "Authorization": f"Bearer {VENICE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": config["model_name"],
        "messages": [
            {"role": "system", "content": config["system_prompt"]},
            {"role": "user", "content": user_message}
        ]
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(VENICE_API_URL, json=payload, headers=headers, timeout=60.0)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=response.text)
            
        data = response.json()
        ai_response = data["choices"][0]["message"]["content"]
        
        # Fazladan markdown bloklarını temizle
        if ai_response.startswith("```lua"):
            ai_response = ai_response.replace("```lua", "").replace("```", "").strip()
        elif ai_response.startswith("```"):
            ai_response = ai_response.replace("```", "").strip()
            
        return {"result": ai_response}

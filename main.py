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
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prompts_v6 as V6
import agents_v7 as V7

try:
    from duckduckgo_search import DDGS
except Exception:
    DDGS = None

app = FastAPI()
DB_FILE = "architect_v3.db"
REDIS_CFG_FILE = "upstash_conn.json"
TASKS = {}
DISCOVERY_SESSIONS = {}
ERROR_LOGS = []
LIVE_STATE = {"status": "Boşta", "buffer": "", "model": "claude-haiku-5-5", "pipeline": [], "handoffs": []}

DEFAULT_UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "https://selected-chamois-177802.upstash.io").strip()
DEFAULT_UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "gQAAAAAAAraKAAIgcDFhNjM1ZTZhZTIzYTc0NTQyOTMyY2Y2ZDg4Mzk0YTZmMw").strip()

def get_upstash_creds() -> tuple[str, str]:
    url = os.environ.get("UPSTASH_REDIS_REST_URL", "").strip()
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip()
    if os.path.exists(REDIS_CFG_FILE):
        try:
            with open(REDIS_CFG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                url = data.get("url", url).strip() or url
                token = data.get("token", token).strip() or token
        except Exception:
            pass
    return (url or DEFAULT_UPSTASH_URL), (token or DEFAULT_UPSTASH_TOKEN)

def save_upstash_creds(url: str, token: str):
    with open(REDIS_CFG_FILE, "w", encoding="utf-8") as f:
        json.dump({"url": url.strip(), "token": token.strip()}, f)

def upstash_cmd_sync(command_args: list):
    url, token = get_upstash_creds()
    if not url or not token:
        return None
    try:
        with httpx.Client(timeout=12.0) as client:
            r = client.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=command_args
            )
            if r.status_code == 200:
                return r.json().get("result")
    except Exception as e:
        ERROR_LOGS.append({"time": time.strftime("%H:%M:%S"), "type": "Upstash Sync Hatası", "details": str(e)})
    return None

async def upstash_cmd_async(command_args: list, timeout: float = 15.0):
    url, token = get_upstash_creds()
    if not url or not token:
        raise ValueError("Upstash URL veya Token tanımlı değil.")
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=command_args
        )
        if r.status_code != 200:
            raise RuntimeError(f"Upstash HTTP {r.status_code}: {r.text}")
        return r.json().get("result")

def build_full_state_snapshot() -> dict:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cfg_row = conn.execute("SELECT * FROM config WHERE id=1").fetchone()
    cfg = dict(cfg_row) if cfg_row else {}
    if cfg:
        norm_agents = V7.normalize_agents_config(cfg.get("agents_config"), fallback_model=cfg.get("chat_model", ""))
        cfg["agents_config"] = json.dumps(norm_agents, ensure_ascii=False)
    users = [dict(u) for u in conn.execute("SELECT * FROM users").fetchall()]
    memories = [dict(m) for m in conn.execute("SELECT * FROM memory_logs ORDER BY id DESC LIMIT 100").fetchall()]
    conn.close()
    return {
        "backup_version": "7.0-multi-agent-14",
        "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": cfg,
        "users": users,
        "memory_logs": list(reversed(memories))
    }

def apply_full_state_snapshot(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    conn = sqlite3.connect(DB_FILE)
    if "config" in data and isinstance(data["config"], dict) and data["config"]:
        c = data["config"]
        norm_agents = V7.normalize_agents_config(c.get("agents_config"), fallback_model=c.get("chat_model", ""))
        conn.execute(
            "UPDATE config SET venice_key=?, chat_model=?, summarizer_model=?, image_model=?, expander_model=?, default_signup_tokens=?, input_cost_mult=?, output_cost_mult=?, system_prompt=?, expander_prompt=?, agents_config=? WHERE id=1",
            (
                c.get("venice_key", ""),
                c.get("chat_model", "openai-gpt-oss-120b"),
                c.get("summarizer_model", "google-gemma-3-27b-it"),
                c.get("image_model", "seedream-v5-5"),
                c.get("expander_model") or os.environ.get("EXPANDER_MODEL", "claude-haiku-5-5"),
                c.get("default_signup_tokens", 30000),
                c.get("input_cost_mult", 1.0),
                c.get("output_cost_mult", 2.0),
                c.get("system_prompt", ""),
                c.get("expander_prompt") or V6.EXPANDER_PROMPT,
                json.dumps(norm_agents, ensure_ascii=False),
            )
        )
    if "users" in data and isinstance(data["users"], list) and len(data["users"]) > 0:
        conn.execute("DELETE FROM users")
        for u in data["users"]:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, client_key, role, tokens) VALUES (?, ?, ?, ?, ?, ?)",
                (u.get("id"), u["username"], u["password_hash"], u["client_key"], u["role"], u["tokens"])
            )
    if "memory_logs" in data and isinstance(data["memory_logs"], list):
        conn.execute("DELETE FROM memory_logs")
        for m in data["memory_logs"]:
            conn.execute(
                "INSERT INTO memory_logs (user_id, prompt, ai_summary, timestamp) VALUES (?, ?, ?, ?)",
                (m.get("user_id", 1), m.get("prompt", ""), m.get("ai_summary", ""), m.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S")))
            )
    conn.commit()
    conn.close()
    return True

async def auto_push_to_upstash():
    url, token = get_upstash_creds()
    if not url or not token:
        return
    try:
        snapshot = build_full_state_snapshot()
        await upstash_cmd_async(["SET", "ai_studio_v4_state", json.dumps(snapshot, ensure_ascii=False)])
    except Exception as e:
        ERROR_LOGS.append({"time": time.strftime("%H:%M:%S"), "type": "Upstash Oto-Kayıt Hatası", "details": str(e)})

def trigger_bg_sync():
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(auto_push_to_upstash())
    except Exception:
        pass

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
        summarizer_model TEXT,
        image_model TEXT,
        expander_model TEXT,
        default_signup_tokens INTEGER,
        input_cost_mult REAL,
        output_cost_mult REAL,
        system_prompt TEXT,
        expander_prompt TEXT,
        agents_config TEXT
    )''')
    for col, col_type in (("expander_model", "TEXT"), ("expander_prompt", "TEXT"), ("agents_config", "TEXT")):
        try:
            c.execute(f"ALTER TABLE config ADD COLUMN {col} {col_type}")
        except Exception:
            pass
    c.execute('''CREATE TABLE IF NOT EXISTS memory_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        prompt TEXT,
        ai_summary TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute("SELECT COUNT(*) FROM users WHERE username = 'Admin'")
    if c.fetchone()[0] == 0:
        fixed_admin_key = os.environ.get("ADMIN_CLIENT_KEY", "").strip() or ("rbx-admin-" + uuid.uuid4().hex[:12])
        c.execute(
            "INSERT INTO users (username, password_hash, client_key, role, tokens) VALUES (?, ?, ?, ?, ?)",
            ("Admin", hash_pw("1234"), fixed_admin_key, "admin", 999999999)
        )

    default_expander_model = os.environ.get("EXPANDER_MODEL", "claude-haiku-5-5").strip() or "claude-haiku-5-5"
    default_agents_json = json.dumps(V7.get_default_agents_config(), ensure_ascii=False)
    c.execute("SELECT COUNT(*) FROM config")
    if c.fetchone()[0] == 0:
        default_prompt = V6.CORE_RULES
        c.execute(
            "INSERT INTO config (id, venice_key, chat_model, summarizer_model, image_model, expander_model, default_signup_tokens, input_cost_mult, output_cost_mult, system_prompt, expander_prompt, agents_config) VALUES (1, '', 'openai-gpt-oss-120b', 'google-gemma-3-27b-it', 'seedream-v5-5', ?, 30000, 1.0, 2.0, ?, ?, ?)",
            (default_expander_model, default_prompt, V6.EXPANDER_PROMPT, default_agents_json)
        )
    else:
        c.execute("UPDATE config SET expander_model = ? WHERE id=1 AND (expander_model IS NULL OR expander_model = '')", (default_expander_model,))
        c.execute("UPDATE config SET expander_prompt = ? WHERE id=1 AND (expander_prompt IS NULL OR expander_prompt = '')", (V6.EXPANDER_PROMPT,))
        c.execute("UPDATE config SET agents_config = ? WHERE id=1 AND (agents_config IS NULL OR agents_config = '')", (default_agents_json,))
    conn.commit()
    conn.close()

    # Deploy / Başlangıçta Upstash Redis üzerinde kayıtlı veritabanı varsa otomatik geri yükle
    raw_state = upstash_cmd_sync(["GET", "ai_studio_v4_state"])
    if raw_state:
        try:
            parsed_state = json.loads(raw_state) if isinstance(raw_state, str) else raw_state
            if apply_full_state_snapshot(parsed_state):
                print("[Upstash Redis] Kalıcı kullanıcılar, krediler ve 14-Agent ayarları başarıyla geri yüklendi.")
        except Exception as e:
            print(f"[Upstash Redis] Başlangıç geri yükleme hatası: {e}")

init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

async def get_fresh_config() -> dict:
    """Her çağrıda güncel 14-Agent ayarlarını Upstash Redis'ten okur (erişilemezse yerel SQLite'a düşer)."""
    url, token = get_upstash_creds()
    if url and token:
        try:
            raw_state = await upstash_cmd_async(["GET", "ai_studio_v4_state"], timeout=4.5)
            if raw_state:
                parsed = json.loads(raw_state) if isinstance(raw_state, str) else raw_state
                if isinstance(parsed, dict) and isinstance(parsed.get("config"), dict) and parsed["config"]:
                    c = parsed["config"]
                    norm_agents = V7.normalize_agents_config(c.get("agents_config"), fallback_model=c.get("chat_model", ""))
                    conn = get_db()
                    conn.execute(
                        "UPDATE config SET venice_key=?, chat_model=?, summarizer_model=?, image_model=?, expander_model=?, default_signup_tokens=?, input_cost_mult=?, output_cost_mult=?, system_prompt=?, expander_prompt=?, agents_config=? WHERE id=1",
                        (
                            c.get("venice_key", ""),
                            c.get("chat_model", "openai-gpt-oss-120b"),
                            c.get("summarizer_model", "google-gemma-3-27b-it"),
                            c.get("image_model", "seedream-v5-5"),
                            c.get("expander_model") or os.environ.get("EXPANDER_MODEL", "claude-haiku-5-5"),
                            c.get("default_signup_tokens", 30000),
                            c.get("input_cost_mult", 1.0),
                            c.get("output_cost_mult", 2.0),
                            c.get("system_prompt", ""),
                            c.get("expander_prompt") or V6.EXPANDER_PROMPT,
                            json.dumps(norm_agents, ensure_ascii=False),
                        )
                    )
                    conn.commit()
                    conn.close()
        except Exception:
            pass

    conn = get_db()
    cfg = dict(conn.execute("SELECT * FROM config WHERE id=1").fetchone())
    conn.close()
    cfg["agents"] = V7.normalize_agents_config(cfg.get("agents_config"), fallback_model=cfg.get("chat_model", ""))
    cfg["agents_config"] = json.dumps(cfg["agents"], ensure_ascii=False)
    if not cfg.get("expander_model"):
        cfg["expander_model"] = os.environ.get("EXPANDER_MODEL", "claude-haiku-5-5").strip() or "claude-haiku-5-5"
    if not cfg.get("expander_prompt"):
        cfg["expander_prompt"] = V6.EXPANDER_PROMPT
    return cfg

def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)

# --- DİNAMİK KURAL ENJEKSİYONU (DIRECTIVE INJECTOR) ---
def build_dynamic_directives(prompt_text: str, directives_cfg: dict) -> list[str]:
    cfg = directives_cfg or {}
    auto_mode = cfg.get("auto_mode", True)
    p = (prompt_text or "").lower()

    req_anim = bool(cfg.get("req_anim", False))
    req_cam = bool(cfg.get("req_cam", False))
    req_vfx = bool(cfg.get("req_vfx", False))
    req_weapon = bool(cfg.get("req_weapon", False))
    req_rig = any(k in p for k in [
        "rig", "karakter", "character", "npc", "dummy", "boss", "düşman", "enemy",
        "insan", "humanoid", "r6", "r15", "animasyon", "animation", "animate",
        "canavar", "monster", "fighter", "warrior", "savaşçı", "adam", "asker", "avatar"
    ])

    if auto_mode:
        if any(k in p for k in ["combat", "slash", "combo", "punch", "kick", "anime", "boss", "yetenek", "dövüş", "savaş", "saldırı", "animasyon", "ability", "glitch", "skill"]):
            req_anim = True
            req_vfx = True
        if any(k in p for k in ["camera", "kamera", "fov", "shake", "sarsıntı", "cinematic", "sinematik", "showcase", "combat", "aksiyon", "hitstop"]):
            req_cam = True
        if any(k in p for k in ["sword", "kılıç", "blade", "weapon", "silah", "katana", "scythe", "staff", "asa", "gun", "tüfek", "ekipman", "spear", "mızrak"]):
            req_weapon = True

    rules = []
    if req_rig:
        rules.append(
            "[RIG]: Every humanoid character/NPC must be created with the `spawn_rig` action (official rig, colors, "
            "walk/run/idle animations and movement brain are automatic). Never build characters from Parts or run_luau. "
            "Moving NPCs are never anchored; use behavior 'static' only for statues/mannequins."
        )
    if req_anim:
        rules.append(
            "[ANIMATION]: Create animations with `create_animation` using a `preset` whenever possible "
            "(idle, walk, run, jump, kick, throw, swing, jab, block, wave, sit, dance, celebrate). "
            "Play them in scripts ONLY with AIKit.PlayAnim(model, name). Weapons: Model welded to the hand, never a Tool."
        )
    if req_cam:
        rules.append(
            "[CAMERA]: Use `setup_camera` (third_person / follow / top_down / side_scroller / broadcast ...) for gameplay cameras, "
            "`create_cutscene` for cinematic shots, and AIKit.Shake / FovPunch / Blur for hit effects. Do not write camera scripts."
        )
    if req_vfx:
        rules.append(
            "[VFX]: Use ParticleEmitter (rbxassetid://243098098 glow, rbxassetid://241650934 spark, rbxassetid://243660364 smoke), "
            "Trail, Beam, PointLight and TweenService fades — never spray raw cube Parts as effects."
        )
    if req_weapon:
        rules.append(
            "[EQUIPMENT]: Build multi-part equipment (grip, guard, body, details) welded to the hand with WeldConstraint/Motor6D; reuse scene models if present."
        )
    if len(p.strip()) < 220 and not V6.needs_blueprint(prompt_text, {}):
        rules.append(
            "[POLISH]: This is a short tweak. Keep all working systems; change only what was asked."
        )
    return rules

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
            parsed.setdefault("need_files", [])
            parsed.setdefault("search_toolbox", [])
            parsed.setdefault("thought", "İşlem tamamlandı.")
            return parsed
    except Exception:
        pass

    try:
        fixed = re.sub(r",\s*([\]}])", r"\1", candidate)
        parsed = json.loads(fixed, strict=False)
        if isinstance(parsed, dict):
            parsed.setdefault("actions", [])
            parsed.setdefault("need_files", [])
            parsed.setdefault("search_toolbox", [])
            parsed.setdefault("thought", "İşlem tamamlandı.")
            return parsed
    except Exception:
        pass

    lua_match = re.search(r"```(?:lua|luau)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL)
    code = lua_match.group(1) if lua_match else cleaned
    return {"thought": "Doğrudan kod üretildi.", "need_files": [], "search_toolbox": [], "actions": [{"type": "fallback_code", "source": code}]}

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
        raise HTTPException(status_code=401, detail="Geçersiz kullanıcı adı, şifre veya Client Key")
    return dict(user)

# --- ROBLOX TOOLBOX / CREATOR STORE 3D MODEL ARAMA MOTORU ---
async def search_roblox_toolbox(keyword: str, limit: int = 6) -> list:
    results = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                "https://apis.roblox.com/toolbox-service/v1/marketplace/10",
                params={"keyword": keyword, "num": limit, "sortType": "Relevance", "includeOnlyVerifiedCreators": "false"}
            )
            if r.status_code == 200:
                data = r.json()
                ids = [str(item["id"]) for item in data.get("data", []) if "id" in item][:limit]
                if ids:
                    det = await client.get(
                        "https://apis.roblox.com/toolbox-service/v1/items/details",
                        params={"assetIds": ",".join(ids)}
                    )
                    if det.status_code == 200:
                        for item in det.json().get("data", []):
                            asset = item.get("asset", {})
                            creator = item.get("creator", {})
                            voting = item.get("voting", {})
                            results.append({
                                "asset_id": asset.get("id"),
                                "name": asset.get("name", "Model"),
                                "creator": creator.get("name", "Unknown"),
                                "up_votes": voting.get("upVotes", 0),
                                "down_votes": voting.get("downVotes", 0)
                            })
                        return results
                    else:
                        return [{"asset_id": int(i), "name": f"{keyword} Model #{idx+1}"} for idx, i in enumerate(ids)]
    except Exception as e:
        ERROR_LOGS.append({"time": time.strftime("%H:%M:%S"), "type": "Toolbox Arama Hatası", "details": str(e)})
    return results

@app.get("/", response_class=HTMLResponse)
async def web_portal():
    return r"""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Studio V4 — Kontrol Merkezi</title>
    <style>
        :root { --bg: #09090b; --surface: #121216; --border: #27272a; --accent: #3b82f6; --text: #f4f4f5; --muted: #a1a1aa; --danger: #ef4444; --success: #10b981; --amber: #f59e0b; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background: var(--bg); color: var(--text); display: flex; height: 100vh; overflow: hidden; }
        .sidebar { width: 275px; background: var(--surface); border-right: 1px solid var(--border); padding: 24px; display: flex; flex-direction: column; gap: 8px; flex-shrink: 0; }
        .sidebar h2 { font-size: 18px; font-weight: 700; margin-bottom: 14px; letter-spacing: -0.5px; }
        .profile-card { background: var(--bg); border: 1px solid var(--border); padding: 12px; border-radius: 8px; margin-bottom: 14px; font-size: 12px; }
        .key-box { background: #18181b; padding: 7px 9px; border-radius: 4px; font-family: monospace; color: var(--success); margin-top: 6px; word-break: break-all; cursor: pointer; border: 1px dashed var(--border); }
        .nav-btn { background: transparent; border: none; color: var(--muted); padding: 11px 12px; text-align: left; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 500; transition: 0.15s; width: 100%; }
        .nav-btn:hover, .nav-btn.active { background: var(--border); color: #fff; }
        .main { flex: 1; padding: 34px; overflow-y: auto; }
        .card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 24px; margin-bottom: 24px; }
        .card h3 { font-size: 15px; margin-bottom: 18px; font-weight: 600; border-bottom: 1px solid var(--border); padding-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }
        .section { display: none; }
        .section.active { display: block; }
        label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 6px; font-weight: 500; }
        input, textarea, select { width: 100%; background: var(--bg); border: 1px solid var(--border); color: #fff; padding: 11px; border-radius: 6px; margin-bottom: 14px; font-family: monospace; font-size: 13px; }
        input:focus, textarea:focus, select:focus { outline: none; border-color: var(--accent); }
        .btn { background: var(--accent); color: #fff; border: none; padding: 10px 18px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 13px; }
        .btn:hover { opacity: 0.9; }
        .btn-danger { background: var(--danger); padding: 6px 10px; font-size: 11px; }
        .btn-success { background: var(--success); }
        .btn-amber { background: var(--amber); color: #000; }
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
        .img-preview { max-width: 100%; max-height: 420px; border-radius: 8px; border: 1px solid var(--border); margin-top: 14px; display: none; }
        .phase-heading { font-size: 12px; text-transform: uppercase; letter-spacing: 0.7px; color: var(--accent); margin: 18px 0 10px 0; font-weight: 700; display: flex; align-items: center; gap: 8px; }
        .agent-acc { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 10px; overflow: hidden; }
        .agent-acc-head { display: flex; justify-content: space-between; align-items: center; padding: 12px 14px; cursor: pointer; user-select: none; background: #141419; gap: 10px; }
        .agent-acc-head:hover { background: #1b1b22; }
        .agent-acc-title { font-size: 13px; font-weight: 600; color: #fff; }
        .agent-acc-desc { font-size: 11px; color: var(--muted); margin-top: 2px; }
        .agent-model-pill { font-family: monospace; font-size: 11px; padding: 3px 8px; border-radius: 999px; background: #1f2937; color: #93c5fd; border: 1px solid #334155; white-space: nowrap; }
        .agent-acc-body { display: none; padding: 14px; border-top: 1px solid var(--border); }
        .agent-acc.open .agent-acc-body { display: block; }
        .pipe-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; margin-bottom: 14px; }
        .pipe-chip { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; font-size: 11px; }
        .pipe-chip.running { border-color: var(--accent); background: #0f1c33; }
        .pipe-chip.done { border-color: var(--success); background: #0c241c; }
        .pipe-chip.pass { opacity: 0.55; border-style: dashed; }
        .pipe-chip.error { border-color: var(--danger); background: #2a1215; }
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
            <button class="btn btn-success" style="width:100%;" onclick="doRegister()">Hesap Oluştur</button>
        </div>

        <div id="auth-msg" style="margin-top:14px; font-size:13px; color:var(--danger); display:none;"></div>
    </div>
</div>

<div class="sidebar" style="display:none;" id="sidebar">
    <h2>AI Studio V4</h2>
    <div class="profile-card">
        <div id="p-name" style="font-weight:700; font-size:14px;"></div>
        <div id="p-role" style="color:var(--muted); margin-top:2px;"></div>
        <div id="p-tokens" style="margin-top:8px; font-weight:600;"></div>
        <div style="margin-top:8px; color:var(--muted);">Client API Key (Tıkla Kopyala):</div>
        <div class="key-box" id="p-key" onclick="copyKey()"></div>
    </div>

    <button class="nav-btn admin-only" onclick="nav('settings', this)">Sistem & Model Ayarları</button>
    <button class="nav-btn admin-only" onclick="nav('users', this)">Kullanıcı & Kredi Yönetimi</button>
    <button class="nav-btn admin-only" onclick="nav('backup', this)">Upstash Redis & Yedekleme</button>
    <button class="nav-btn" onclick="nav('images', this)">Görsel & UI Asset Üretici</button>
    <button class="nav-btn" onclick="nav('live', this)">Canlı Akış & Hata Kayıtları</button>
    <button class="nav-btn btn-danger" style="margin-top:auto; text-align:center;" onclick="logout()">Çıkış Yap</button>
</div>

<div class="main" style="display:none;" id="main">
    <div id="sec-settings" class="section admin-only">
        <div class="card">
            <h3>
                <span>Venice API, Genel Modeller & Maliyet Ayarları</span>
                <button class="btn btn-success btn-sm" onclick="saveSettings()">💾 Tüm Ayarları Kaydet (Upstash'e Eşitle)</button>
            </h3>
            <label>Venice.ai API Anahtarı</label>
            <input type="password" id="venice_key">
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:14px;">
                <div><label>Yedek / Varsayılan Kodlayıcı Model</label><input type="text" id="chat_model"></div>
                <div><label>Prompt Zenginleştirici Model (Expander)</label><input type="text" id="expander_model" placeholder="claude-haiku-5-5"></div>
                <div><label>Özetleyici Model (Indexer)</label><input type="text" id="summarizer_model"></div>
                <div><label>Görsel Modeli</label><input type="text" id="image_model"></div>
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:14px;">
                <div><label>Yeni Kayıt Başlangıç Kredisi</label><input type="number" id="default_signup_tokens"></div>
                <div><label>Input Token Çarpanı</label><input type="number" step="0.1" id="input_cost_mult"></div>
                <div><label>Output Token Çarpanı</label><input type="number" step="0.1" id="output_cost_mult"></div>
            </div>
            <details style="margin-bottom:8px;">
                <summary style="cursor:pointer; font-size:12px; color:var(--muted); margin-bottom:10px;">Ek Genel Sistem Talimatı & Prompt Zenginleştirici Talimatı (Aç/Kapat)</summary>
                <label>Ek Evrensel Sistem Talimatı (System Prompt)</label>
                <textarea id="system_prompt" rows="6"></textarea>
                <label>Prompt Zenginleştirici Sistem Talimatı (Expander Prompt)</label>
                <textarea id="expander_prompt" rows="8"></textarea>
            </details>
        </div>

        <div class="card">
            <h3>
                <span>14 Agent Orkestrasyon Ayarları (1 Ana Orkestratör + 13 Uzman Alt Agent)</span>
                <div style="display:flex; gap:8px; align-items:center;">
                    <button class="btn btn-sm" style="background:#27272a;" onclick="toggleAllAccordions(true)">Tümünü Aç</button>
                    <button class="btn btn-sm" style="background:#27272a;" onclick="toggleAllAccordions(false)">Tümünü Kapat</button>
                </div>
            </h3>
            <p style="font-size:12px; color:var(--muted); margin-bottom:14px;">
                Her agent için bağımsız Model ID (ör. <code>claude-haiku-5-5</code>, <code>qwen3-235b-a22b-instruct-2507</code>, <code>mistral-small-3-2-24b-instruct</code>) ve evrensel sistem talimatı atayabilirsin. Kaydettiğinde tüm 14 agent ayarı anında Upstash Redis'e yazılır ve her çağrıda oradan okunur.
            </p>
            <div style="display:flex; gap:10px; align-items:center; margin-bottom:16px; background:var(--bg); padding:10px 12px; border-radius:6px; border:1px solid var(--border);">
                <input type="text" id="bulk_agent_model" placeholder="Toplu Model ID (ör. claude-haiku-5-5)" style="margin:0; flex:1;">
                <button class="btn btn-amber btn-sm" onclick="applyBulkAgentModel()">Tüm 14 Agent'a Uygula</button>
            </div>
            <div id="agents-accordion-container">Yükleniyor...</div>
            <button class="btn btn-success" style="width:100%; margin-top:12px; padding:12px;" onclick="saveSettings()">💾 Ayarları Kaydet (ve Upstash'e Eşitle)</button>
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

    <div id="sec-backup" class="section admin-only">
        <div class="card">
            <h3>Upstash Redis Kalıcı Veritabanı Bağlantısı <span id="redis-badge" style="font-size:12px; color:var(--muted);"></span></h3>
            <p style="font-size:13px; color:var(--muted); margin-bottom:14px;">Kullanıcılar, krediler, Client Key'ler ve sistem ayarları otomatik olarak Upstash Redis'e yazılır ve her Render deploy sonrasında buradan otomatik yüklenir.</p>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:14px;">
                <div>
                    <label>Upstash Redis REST URL</label>
                    <input type="text" id="upstash_url" placeholder="https://selected-chamois-177802.upstash.io">
                </div>
                <div>
                    <label>Upstash Redis REST Token</label>
                    <input type="password" id="upstash_token" placeholder="Upstash REST Token yapıştırın">
                </div>
            </div>
            <div style="display:flex; flex-wrap:wrap; gap:10px; margin-bottom:14px;">
                <button class="btn" onclick="saveUpstashConfig()">💾 Bağlantıyı Kaydet</button>
                <button class="btn btn-success" onclick="pushToUpstash()">⬆️ Verileri Upstash'e Yazdır (Force Push)</button>
                <button class="btn btn-amber" onclick="pullFromUpstash()">⬇️ Upstash'ten Verileri Çek (Force Pull)</button>
            </div>
            <div class="log-box" id="redis-status-box" style="min-height:48px;">Durum bekleniyor...</div>
        </div>

        <div class="card">
            <h3>Manuel Redis Anahtar / Veri Yazdırma & Çekme (SET / GET)</h3>
            <div style="display:grid; grid-template-columns:1fr 2fr; gap:14px;">
                <div>
                    <label>Redis Key (Anahtar)</label>
                    <input type="text" id="manual_redis_key" value="ai_studio_v4_state">
                </div>
                <div>
                    <label>Redis Value (Değer - Yazdırmak için)</label>
                    <input type="text" id="manual_redis_val" placeholder="Manuel yazılacak veri...">
                </div>
            </div>
            <div style="display:flex; gap:10px; margin-bottom:14px;">
                <button class="btn btn-success" onclick="manualRedisSet()">📝 Manuel Veri Yazdır (SET)</button>
                <button class="btn" onclick="manualRedisGet()">🔍 Manuel Veri Çek (GET)</button>
            </div>
            <div class="log-box" id="manual-redis-output" style="max-height:220px; overflow-y:auto;">Sonuç burada görünecek...</div>
        </div>

        <div class="card">
            <h3>Yerel JSON Dosya Yedeği (Export / Import)</h3>
            <div style="display:flex; gap:12px; margin-bottom:20px;">
                <button class="btn btn-success" onclick="downloadBackup()">⬇️ Tüm Veritabanını JSON İndir</button>
            </div>
            <label>JSON Yedek Dosyasından Geri Yükle (.json seçin)</label>
            <input type="file" id="backup_file" accept=".json" style="padding:8px;">
            <button class="btn" onclick="uploadBackup()">⬆️ Yedeği Geri Yükle (Import)</button>
        </div>
    </div>

    <div id="sec-images" class="section">
        <div class="card">
            <h3>Venice AI Görsel, UI Arka Planı & İkon Üretici</h3>
            <label>Görsel İsteği (İngilizce yazmanız daha yüksek kalite verir)</label>
            <textarea id="img_prompt" rows="3" placeholder="Örn: Sleek dark sci-fi game shop UI panel background, neon blue borders, clean flat vector icon style"></textarea>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:14px;">
                <div><label>Genişlik (Width)</label><input type="number" id="img_width" value="1024"></div>
                <div><label>Yükseklik (Height)</label><input type="number" id="img_height" value="1024"></div>
            </div>
            <button class="btn" id="img_gen_btn" onclick="generateImage()">🎨 Görsel Üret</button>
            <button class="btn btn-success" id="img_dl_btn" style="display:none; margin-left:8px;" onclick="downloadGeneratedImg()">⬇️ PNG İndir (Studio'ya Yükle)</button>
            <div id="img_status" style="margin-top:10px; font-size:12px; color:var(--muted);"></div>
            <img id="img_output" class="img-preview" alt="Generated UI Asset">
        </div>
    </div>

    <div id="sec-live" class="section">
        <div class="card">
            <h3>Aktif Model & 14 Agent Canlı Boru Hattı <span id="active-model-badge" style="font-size:12px; color:var(--accent);"></span></h3>
            <div id="live-pipeline-grid" class="pipe-grid"></div>
            <div class="log-box" id="live-stream-box" style="min-height:140px; max-height:460px; overflow:auto; border-left:3px solid var(--success);">Bekleniyor...</div>
        </div>
        <div class="card">
            <h3>Hata Kayıtları <button class="btn btn-sm" onclick="loadLiveAndErrors()">Yenile</button></h3>
            <div id="errors-container"></div>
        </div>
    </div>
</div>

<script>
let currentUser = null;
let lastGeneratedDataUrl = "";

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
    document.getElementById('p-role').innerText = user.role === 'admin' ? 'Yetki: Yönetici (Sınırsız)' : 'Yetki: Kullanıcı';
    document.getElementById('p-tokens').innerText = user.role === 'admin' ? 'Kredi: ∞ Sınırsız' : `Kalan Kredi: ${user.tokens} Token`;
    document.getElementById('p-key').innerText = user.client_key;

    if (user.role === 'admin') {
        document.querySelectorAll('.nav-btn.admin-only').forEach(e => e.style.display = 'block');
        loadSettings();
        loadUpstashStatus();
        nav('settings', document.querySelectorAll('.nav-btn')[0]);
    } else {
        nav('live', document.querySelectorAll('.nav-btn:not(.admin-only)')[1]);
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
    if (btn) btn.classList.add('active');
    if (id === 'users') loadUsers();
    if (id === 'backup') loadUpstashStatus();
    if (id === 'live') loadLiveAndErrors();
}

async function apiFetch(url, method='GET', body=null) {
    const opts = { method, headers: { 'Client-Key': localStorage.getItem('client_key') } };
    if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return fetch(url, opts);
}

let loadedAgentsMeta = [];

function escHtml(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function renderAgentAccordions(metaList, agentsCfg) {
    loadedAgentsMeta = metaList || [];
    const container = document.getElementById('agents-accordion-container');
    if (!container) return;
    let html = '';
    let lastPhase = null;
    for (const m of loadedAgentsMeta) {
        if (m.phase !== lastPhase) {
            html += `<div class="phase-heading"><span>▸ ${escHtml(m.phase_label)}</span></div>`;
            lastPhase = m.phase;
        }
        const cur = (agentsCfg && agentsCfg[m.id]) || {};
        const modelVal = cur.model || m.default_model || 'claude-haiku-5-5';
        const promptVal = cur.prompt || m.default_prompt || '';
        const openCls = m.id === 'main_agent' ? ' open' : '';
        html += `
        <div class="agent-acc${openCls}" id="acc-${m.id}">
            <div class="agent-acc-head" onclick="document.getElementById('acc-${m.id}').classList.toggle('open')">
                <div>
                    <div class="agent-acc-title">${escHtml(m.title)} <span style="font-size:11px; color:var(--muted); font-family:monospace;">(${escHtml(m.id)})</span></div>
                    <div class="agent-acc-desc">${escHtml(m.description)}</div>
                </div>
                <span class="agent-model-pill" id="pill-${m.id}">${escHtml(modelVal)}</span>
            </div>
            <div class="agent-acc-body">
                <div style="display:flex; gap:10px; align-items:flex-end; margin-bottom:10px;">
                    <div style="flex:1;">
                        <label>Model ID</label>
                        <input type="text" id="ag-model-${m.id}" value="${escHtml(modelVal)}" placeholder="claude-haiku-5-5 / qwen3-235b-a22b-instruct-2507 / mistral-small-3-2-24b-instruct" style="margin-bottom:0;" oninput="document.getElementById('pill-${m.id}').innerText = this.value.trim() || 'varsayılan'">
                    </div>
                    <button class="btn btn-sm" style="background:#27272a; height:38px;" onclick="resetAgentDefault('${m.id}')">Varsayılana Sıfırla</button>
                </div>
                <label>Evrensel Sistem Talimatı (System Prompt)</label>
                <textarea id="ag-prompt-${m.id}" rows="9" style="margin-bottom:0;">${escHtml(promptVal)}</textarea>
            </div>
        </div>`;
    }
    container.innerHTML = html;
}

function toggleAllAccordions(open) {
    document.querySelectorAll('.agent-acc').forEach(el => el.classList.toggle('open', open));
}

function applyBulkAgentModel() {
    const val = document.getElementById('bulk_agent_model').value.trim();
    if (!val) return alert('Lütfen bir Model ID girin.');
    for (const m of loadedAgentsMeta) {
        const inp = document.getElementById('ag-model-' + m.id);
        const pill = document.getElementById('pill-' + m.id);
        if (inp) inp.value = val;
        if (pill) pill.innerText = val;
    }
}

function resetAgentDefault(agentId) {
    const meta = loadedAgentsMeta.find(x => x.id === agentId);
    if (!meta) return;
    const mEl = document.getElementById('ag-model-' + agentId);
    const pEl = document.getElementById('ag-prompt-' + agentId);
    const pill = document.getElementById('pill-' + agentId);
    if (mEl) mEl.value = meta.default_model;
    if (pEl) pEl.value = meta.default_prompt;
    if (pill) pill.innerText = meta.default_model;
}

async function loadSettings() {
    const res = await apiFetch('/api/admin/config');
    if (!res.ok) return;
    const cfg = await res.json();
    document.getElementById('venice_key').value = cfg.venice_key || '';
    document.getElementById('chat_model').value = cfg.chat_model || '';
    document.getElementById('expander_model').value = cfg.expander_model || 'claude-haiku-5-5';
    document.getElementById('summarizer_model').value = cfg.summarizer_model || '';
    document.getElementById('image_model').value = cfg.image_model || '';
    document.getElementById('default_signup_tokens').value = cfg.default_signup_tokens || 30000;
    document.getElementById('input_cost_mult').value = cfg.input_cost_mult || 1.0;
    document.getElementById('output_cost_mult').value = cfg.output_cost_mult || 2.0;
    document.getElementById('system_prompt').value = cfg.system_prompt || '';
    document.getElementById('expander_prompt').value = cfg.expander_prompt || '';
    renderAgentAccordions(cfg.agents_meta || [], cfg.agents || {});
}

async function saveSettings() {
    const agentsPayload = {};
    for (const m of loadedAgentsMeta) {
        const mEl = document.getElementById('ag-model-' + m.id);
        const pEl = document.getElementById('ag-prompt-' + m.id);
        agentsPayload[m.id] = {
            model: (mEl ? mEl.value.trim() : '') || m.default_model,
            prompt: (pEl ? pEl.value : '') || m.default_prompt
        };
    }
    const res = await apiFetch('/api/admin/config', 'POST', {
        venice_key: document.getElementById('venice_key').value.trim(),
        chat_model: document.getElementById('chat_model').value.trim(),
        expander_model: document.getElementById('expander_model').value.trim() || 'claude-haiku-5-5',
        summarizer_model: document.getElementById('summarizer_model').value.trim(),
        image_model: document.getElementById('image_model').value.trim(),
        default_signup_tokens: parseInt(document.getElementById('default_signup_tokens').value) || 30000,
        input_cost_mult: parseFloat(document.getElementById('input_cost_mult').value) || 1.0,
        output_cost_mult: parseFloat(document.getElementById('output_cost_mult').value) || 2.0,
        system_prompt: document.getElementById('system_prompt').value,
        expander_prompt: document.getElementById('expander_prompt').value,
        agents: agentsPayload
    });
    if (res.ok) {
        alert('Tüm 14 Agent ve sistem ayarları kaydedildi, Upstash Redis ile eşitlendi.');
    } else {
        alert('Ayarlar kaydedilirken hata oluştu.');
    }
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
    alert('Kredi güncellendi ve Upstash Redis ile eşitlendi.');
}

async function delUser(id) {
    await apiFetch(`/api/admin/users/${id}`, 'DELETE');
    loadUsers();
}

// --- UPSTASH REDIS FONKSİYONLARI ---
async function loadUpstashStatus() {
    const res = await apiFetch('/api/admin/redis/status');
    if (!res.ok) return;
    const d = await res.json();
    document.getElementById('upstash_url').value = d.url || '';
    document.getElementById('upstash_token').value = d.token || '';
    const badge = document.getElementById('redis-badge');
    const box = document.getElementById('redis-status-box');
    if (d.connected) {
        badge.innerText = '● Bağlı (Aktif)';
        badge.style.color = 'var(--success)';
        box.innerText = `Upstash Redis bağlantısı aktif.\nSunucu Yanıtı: ${d.ping}\nKayıtlı Snapshot Mevcut: ${d.has_snapshot ? 'Evet' : 'Henüz Yok'}`;
    } else {
        badge.innerText = '● Bağlı Değil';
        badge.style.color = 'var(--danger)';
        box.innerText = `Bağlantı yok veya Token girilmedi: ${d.error || 'Token bekleniyor'}`;
    }
}

async function saveUpstashConfig() {
    const url = document.getElementById('upstash_url').value.trim();
    const token = document.getElementById('upstash_token').value.trim();
    const res = await apiFetch('/api/admin/redis/config', 'POST', { url, token });
    const d = await res.json();
    if (res.ok) {
        alert('Upstash bağlantı bilgileri kaydedildi!');
        loadUpstashStatus();
    } else {
        alert('Hata: ' + (d.detail || 'Kaydedilemedi'));
    }
}

async function pushToUpstash() {
    const box = document.getElementById('redis-status-box');
    box.innerText = "Tüm veriler Upstash Redis'e yazdırılıyor...";
    const res = await apiFetch('/api/admin/redis/push', 'POST');
    const d = await res.json();
    if (res.ok) {
        box.innerText = `✅ Başarılı! ${d.users_count} kullanıcı, krediler ve ayarlar Upstash Redis'e yazdırıldı (${d.synced_at}).`;
    } else {
        box.innerText = `❌ Hata: ${d.detail || 'Yazdırma başarısız'}`;
    }
}

async function pullFromUpstash() {
    const box = document.getElementById('redis-status-box');
    box.innerText = "Upstash Redis'ten veriler çekiliyor...";
    const res = await apiFetch('/api/admin/redis/pull', 'POST');
    const d = await res.json();
    if (res.ok) {
        box.innerText = `✅ Başarılı! ${d.users_count} kullanıcı ve ayarlar geri yüklendi. Sayfa yenileniyor...`;
        setTimeout(() => location.reload(), 1000);
    } else {
        box.innerText = `❌ Hata: ${d.detail || 'Veri çekme başarısız'}`;
    }
}

async function manualRedisSet() {
    const key = document.getElementById('manual_redis_key').value.trim();
    const value = document.getElementById('manual_redis_val').value;
    if (!key) return alert('Anahtar (Key) zorunludur.');
    const res = await apiFetch('/api/admin/redis/manual', 'POST', { action: 'set', key, value });
    const d = await res.json();
    document.getElementById('manual-redis-output').innerText = res.ok
        ? `✅ SET [${key}] -> ${JSON.stringify(d.result)}`
        : `❌ Hata: ${d.detail}`;
}

async function manualRedisGet() {
    const key = document.getElementById('manual_redis_key').value.trim();
    if (!key) return alert('Anahtar (Key) zorunludur.');
    const res = await apiFetch('/api/admin/redis/manual', 'POST', { action: 'get', key });
    const d = await res.json();
    document.getElementById('manual-redis-output').innerText = res.ok
        ? `🔍 GET [${key}]:\n${typeof d.result === 'string' ? d.result : JSON.stringify(d.result, null, 2)}`
        : `❌ Hata: ${d.detail}`;
}

async function downloadBackup() {
    const res = await apiFetch('/api/admin/backup/export');
    if (!res.ok) return alert('Yedek alınamadı');
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `ai_studio_backup_${new Date().toISOString().slice(0,10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
}

async function uploadBackup() {
    const fileInput = document.getElementById('backup_file');
    if (!fileInput.files.length) return alert('Lütfen bir .json yedek dosyası seçin.');
    const text = await fileInput.files[0].text();
    let parsed;
    try { parsed = JSON.parse(text); } catch { return alert('Geçersiz JSON dosyası.'); }
    const res = await apiFetch('/api/admin/backup/import', 'POST', parsed);
    if (res.ok) {
        alert('Yedek başarıyla geri yüklendi ve Upstash Redis ile eşitlendi! Sayfa yenileniyor...');
        location.reload();
    } else {
        alert('Geri yükleme hatası.');
    }
}

async function generateImage() {
    const prompt = document.getElementById('img_prompt').value.trim();
    if (!prompt) return alert('Lütfen bir görsel açıklaması girin.');
    const btn = document.getElementById('img_gen_btn');
    const status = document.getElementById('img_status');
    btn.disabled = true;
    status.innerText = 'Görsel üretiliyor...';

    const res = await apiFetch('/api/image/generate', 'POST', {
        prompt,
        width: parseInt(document.getElementById('img_width').value) || 1024,
        height: parseInt(document.getElementById('img_height').value) || 1024
    });
    btn.disabled = false;
    const data = await res.json();
    if (res.ok && data.image_b64) {
        lastGeneratedDataUrl = 'data:image/png;base64,' + data.image_b64;
        const img = document.getElementById('img_output');
        img.src = lastGeneratedDataUrl;
        img.style.display = 'block';
        document.getElementById('img_dl_btn').style.display = 'inline-block';
        status.innerText = 'Görsel hazır! İndirip Roblox Studio Asset Manager üzerinden oyuna aktarabilirsiniz.';
    } else {
        status.innerText = 'Hata: ' + (data.detail || data.error || 'Görsel üretilemedi');
    }
}

function downloadGeneratedImg() {
    if (!lastGeneratedDataUrl) return;
    const a = document.createElement('a');
    a.href = lastGeneratedDataUrl;
    a.download = `roblox_ui_asset_${Date.now()}.png`;
    a.click();
}

async function loadLiveAndErrors() {
    const res = await apiFetch('/api/status/live');
    if (!res.ok) return;
    const data = await res.json();
    document.getElementById('active-model-badge').innerText = `Aktif Model: ${data.live.model}`;
    const pipeGrid = document.getElementById('live-pipeline-grid');
    if (pipeGrid) {
        const pipe = data.live.pipeline || [];
        const labelMap = { running: '🔄 ÇALIŞIYOR', done: '✅ TAMAM', pass: '⏭️ PASS', active: '⏳ AKTİF', pending: '· BEKLİYOR', error: '❌ HATA' };
        pipeGrid.innerHTML = pipe.map(p => `
            <div class="pipe-chip ${escHtml(p.status || 'pending')}">
                <div style="font-weight:600; color:#fff;">${escHtml(p.title || p.id)}</div>
                <div style="margin-top:3px; color:var(--muted);">${labelMap[p.status] || escHtml(p.status)}${p.actions_count ? ` (${p.actions_count} aks.)` : ''}</div>
                ${p.model && p.status !== 'pass' ? `<div style="margin-top:2px; font-family:monospace; color:#93c5fd; font-size:10px;">${escHtml(p.model)}</div>` : ''}
            </div>
        `).join('');
    }
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

# --- AUTH API ---
@app.post("/api/auth/login")
async def login(request: Request):
    d = await request.json()
    u = get_user_by_auth(username=d.get("username"), password=d.get("password"))
    return {"username": u["username"], "role": u["role"], "tokens": u["tokens"], "client_key": u["client_key"]}

@app.post("/api/auth/register")
async def register(request: Request):
    d = await request.json()
    username = (d.get("username") or "").strip()
    password = d.get("password") or ""
    if len(username) < 3 or len(password) < 4:
        raise HTTPException(status_code=400, detail="Kullanıcı adı en az 3, şifre en az 4 karakter olmalıdır.")
    conn = get_db()
    cfg = conn.execute("SELECT default_signup_tokens FROM config WHERE id=1").fetchone()
    start_tokens = cfg["default_signup_tokens"] if cfg else 30000
    new_key = "rbx-usr-" + uuid.uuid4().hex[:14]
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, client_key, role, tokens) VALUES (?, ?, ?, 'user', ?)",
            (username, hash_pw(password), new_key, start_tokens)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Bu kullanıcı adı zaten mevcut.")
    conn.close()
    trigger_bg_sync()
    return {"username": username, "role": "user", "tokens": start_tokens, "client_key": new_key}

@app.get("/api/auth/me")
async def auth_me(client_key: str = Header(None)):
    u = get_user_by_auth(client_key=client_key)
    return {"username": u["username"], "role": u["role"], "tokens": u["tokens"], "client_key": u["client_key"]}

# --- ADMIN CONFIG & USERS ---
@app.get("/api/admin/config")
async def get_conf(client_key: str = Header(None)):
    if get_user_by_auth(client_key=client_key)["role"] != "admin":
        raise HTTPException(status_code=403)
    cfg = await get_fresh_config()
    cfg["agents_meta"] = V7.get_agents_meta()
    return cfg

@app.post("/api/admin/config")
async def save_conf(request: Request, client_key: str = Header(None)):
    if get_user_by_auth(client_key=client_key)["role"] != "admin":
        raise HTTPException(status_code=403)
    d = await request.json()
    expander_model = (d.get("expander_model") or "").strip() or os.environ.get("EXPANDER_MODEL", "claude-haiku-5-5").strip() or "claude-haiku-5-5"
    expander_prompt = d.get("expander_prompt") or V6.EXPANDER_PROMPT
    raw_agents = d.get("agents") if isinstance(d.get("agents"), dict) else d.get("agents_config")
    norm_agents = V7.normalize_agents_config(raw_agents, fallback_model=d.get("chat_model", ""))
    agents_json = json.dumps(norm_agents, ensure_ascii=False)
    conn = get_db()
    conn.execute(
        "UPDATE config SET venice_key=?, chat_model=?, summarizer_model=?, image_model=?, expander_model=?, default_signup_tokens=?, input_cost_mult=?, output_cost_mult=?, system_prompt=?, expander_prompt=?, agents_config=? WHERE id=1",
        (
            d["venice_key"], d["chat_model"], d["summarizer_model"], d["image_model"], expander_model,
            d["default_signup_tokens"], d["input_cost_mult"], d["output_cost_mult"],
            d["system_prompt"], expander_prompt, agents_json
        )
    )
    conn.commit()
    conn.close()
    LIVE_STATE["model"] = norm_agents["main_agent"]["model"]
    await auto_push_to_upstash()
    return {"status": "ok", "agents_count": len(norm_agents)}

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
    trigger_bg_sync()
    return {"status": "ok"}

@app.delete("/api/admin/users/{user_id}")
async def delete_user(user_id: int, client_key: str = Header(None)):
    if get_user_by_auth(client_key=client_key)["role"] != "admin":
        raise HTTPException(status_code=403)
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id=? AND role != 'admin'", (user_id,))
    conn.commit()
    conn.close()
    trigger_bg_sync()
    return {"status": "ok"}

# --- UPSTASH REDIS YÖNETİM & MANUEL VERİ YAZDIRMA/ÇEKME API ---
@app.get("/api/admin/redis/status")
async def redis_status(client_key: str = Header(None)):
    if get_user_by_auth(client_key=client_key)["role"] != "admin":
        raise HTTPException(status_code=403)
    url, token = get_upstash_creds()
    if not url or not token:
        return {"connected": False, "url": url, "token": "", "error": "Upstash Token henüz girilmemiş."}
    try:
        ping_res = await upstash_cmd_async(["PING"])
        exists_res = await upstash_cmd_async(["EXISTS", "ai_studio_v4_state"])
        return {
            "connected": True,
            "url": url,
            "token": token,
            "ping": ping_res,
            "has_snapshot": bool(exists_res)
        }
    except Exception as e:
        return {"connected": False, "url": url, "token": token, "error": str(e)}

@app.post("/api/admin/redis/config")
async def redis_save_config(request: Request, client_key: str = Header(None)):
    if get_user_by_auth(client_key=client_key)["role"] != "admin":
        raise HTTPException(status_code=403)
    d = await request.json()
    url = (d.get("url") or "").strip()
    token = (d.get("token") or "").strip()
    if not url or not token:
        raise HTTPException(status_code=400, detail="Upstash URL ve Token alanları boş olamaz.")
    save_upstash_creds(url, token)
    try:
        await upstash_cmd_async(["PING"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Bağlantı testi başarısız: {str(e)}")
    return {"status": "connected"}

@app.post("/api/admin/redis/push")
async def redis_force_push(client_key: str = Header(None)):
    if get_user_by_auth(client_key=client_key)["role"] != "admin":
        raise HTTPException(status_code=403)
    try:
        snapshot = build_full_state_snapshot()
        await upstash_cmd_async(["SET", "ai_studio_v4_state", json.dumps(snapshot, ensure_ascii=False)])
        return {"status": "pushed", "users_count": len(snapshot["users"]), "synced_at": snapshot["synced_at"]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/admin/redis/pull")
async def redis_force_pull(client_key: str = Header(None)):
    if get_user_by_auth(client_key=client_key)["role"] != "admin":
        raise HTTPException(status_code=403)
    try:
        raw_state = await upstash_cmd_async(["GET", "ai_studio_v4_state"])
        if not raw_state:
            raise HTTPException(status_code=404, detail="Upstash Redis içinde henüz kayıtlı veri bulunamadı.")
        parsed = json.loads(raw_state) if isinstance(raw_state, str) else raw_state
        apply_full_state_snapshot(parsed)
        return {"status": "pulled", "users_count": len(parsed.get("users", []))}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/admin/redis/manual")
async def redis_manual_op(request: Request, client_key: str = Header(None)):
    if get_user_by_auth(client_key=client_key)["role"] != "admin":
        raise HTTPException(status_code=403)
    d = await request.json()
    action = d.get("action")
    key = (d.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Key zorunludur.")
    try:
        if action == "set":
            val = d.get("value", "")
            res = await upstash_cmd_async(["SET", key, val])
            if key == "ai_studio_v4_state":
                try:
                    apply_full_state_snapshot(json.loads(val))
                except Exception:
                    pass
            return {"status": "ok", "result": res}
        elif action == "get":
            res = await upstash_cmd_async(["GET", key])
            return {"status": "ok", "result": res}
        else:
            raise HTTPException(status_code=400, detail="Geçersiz işlem.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# --- HAFIZA TEMİZLEME VE SON ADIMI GERİ ALMA (ROLLBACK) API ---
@app.delete("/api/agent/memory")
async def clear_memory(client_key: str = Header(None)):
    user = get_user_by_auth(client_key=client_key)
    conn = get_db()
    conn.execute("DELETE FROM memory_logs WHERE user_id=?", (user["id"],))
    conn.commit()
    conn.close()
    trigger_bg_sync()
    return {"status": "ok"}

@app.post("/api/agent/rollback")
async def rollback_last_memory(client_key: str = Header(None)):
    user = get_user_by_auth(client_key=client_key)
    conn = get_db()
    last_row = conn.execute("SELECT id FROM memory_logs WHERE user_id=? ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
    deleted = False
    if last_row:
        conn.execute("DELETE FROM memory_logs WHERE id=?", (last_row["id"],))
        conn.commit()
        deleted = True
    conn.close()
    if deleted:
        trigger_bg_sync()
    return {"status": "ok", "deleted_last_turn": deleted}

# --- TAM JSON YEDEKLEME (EXPORT / IMPORT) ---
@app.get("/api/admin/backup/export")
async def export_backup(client_key: str = Header(None)):
    if get_user_by_auth(client_key=client_key)["role"] != "admin":
        raise HTTPException(status_code=403)
    snap = build_full_state_snapshot()
    snap["exported_at"] = snap["synced_at"]
    return snap

@app.post("/api/admin/backup/import")
async def import_backup(request: Request, client_key: str = Header(None)):
    if get_user_by_auth(client_key=client_key)["role"] != "admin":
        raise HTTPException(status_code=403)
    data = await request.json()
    apply_full_state_snapshot(data)
    trigger_bg_sync()
    return {"status": "restored"}

# --- VENICE GÖRSEL / UI ASSET ÜRETİM API ---
@app.post("/api/image/generate")
async def generate_image_api(request: Request, client_key: str = Header(None)):
    user = get_user_by_auth(client_key=client_key)
    if user["role"] != "admin" and user["tokens"] < 1500:
        raise HTTPException(status_code=402, detail="Görsel üretimi için yetersiz kredi (min 1500 Token).")

    d = await request.json()
    conn = get_db()
    cfg = dict(conn.execute("SELECT * FROM config WHERE id=1").fetchone())
    conn.close()

    if not cfg["venice_key"]:
        raise HTTPException(status_code=400, detail="Venice API anahtarı tanımlı değil.")

    headers = {"Authorization": f"Bearer {cfg['venice_key']}", "Content-Type": "application/json"}
    body = {
        "model": cfg.get("image_model") or "seedream-v5-5",
        "prompt": d.get("prompt", ""),
        "width": min(1280, max(512, int(d.get("width", 1024)))),
        "height": min(1280, max(512, int(d.get("height", 1024)))),
        "format": "png",
        "hide_watermark": True
    }

    async with httpx.AsyncClient(timeout=90.0) as client:
        r = await client.post("https://api.venice.ai/api/v1/image/generate", json=body, headers=headers)
        if r.status_code != 200:
            ERROR_LOGS.append({"time": time.strftime("%H:%M:%S"), "type": f"Venice Image HTTP {r.status_code}", "details": r.text})
            raise HTTPException(status_code=400, detail=f"Görsel API Hatası ({r.status_code})")
        resp_json = r.json()
        images = resp_json.get("images", [])
        if not images:
            raise HTTPException(status_code=500, detail="Görsel yanıtı boş döndü.")

    if user["role"] != "admin":
        conn = get_db()
        conn.execute("UPDATE users SET tokens = MAX(0, tokens - 1500) WHERE id = ?", (user["id"],))
        conn.commit()
        conn.close()
        trigger_bg_sync()

    return {"image_b64": images[0]}

# --- DOĞRUDAN TOOLBOX ARAMA ENDPOINTİ ---
@app.get("/api/toolbox/search")
async def api_toolbox_search(q: str, client_key: str = Header(None)):
    get_user_by_auth(client_key=client_key)
    return {"results": await search_roblox_toolbox(q, limit=8)}

# --- ÖZETLEYİCİ MODEL (CODEBASE INDEXER) ---
@app.post("/api/agent/summarize")
async def summarize_scripts(request: Request, client_key: str = Header(None)):
    user = get_user_by_auth(client_key=client_key)
    d = await request.json()
    scripts = d.get("scripts", [])
    if not scripts:
        return {"summaries": {}}

    conn = get_db()
    cfg = dict(conn.execute("SELECT * FROM config WHERE id=1").fetchone())
    conn.close()

    if not cfg["venice_key"]:
        raise HTTPException(status_code=400, detail="Venice API anahtarı tanımlı değil.")

    sys_msg = (
        "Sen bir Roblox Luau Kod İndeksleyicisisin. Sana verilen her script için ne işe yaradığını, hangi tuşları/eventleri kullandığını "
        "1-2 kısa Türkçe cümleyle özetle. SADECE şu JSON formatında yanıt ver: {\"summaries\": {\"Dosya.Yolu\": \"Kısa özet\"}}"
    )
    user_msg = json.dumps(scripts, ensure_ascii=False)[:45000]

    headers = {"Authorization": f"Bearer {cfg['venice_key']}", "Content-Type": "application/json"}
    payload = {
        "model": cfg.get("summarizer_model") or "google-gemma-3-27b-it",
        "messages": [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg}
        ]
    }

    async with httpx.AsyncClient() as client:
        r = await client.post("https://api.venice.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=90.0)
        if r.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Özetleyici API Hatası: {r.text}")
        raw_content = r.json()["choices"][0]["message"]["content"]
        parsed = extract_and_repair_json(raw_content)

    if user["role"] != "admin":
        cost = int(estimate_tokens(user_msg) * 0.5)
        conn = get_db()
        conn.execute("UPDATE users SET tokens = MAX(0, tokens - ?) WHERE id = ?", (cost, user["id"]))
        conn.commit()
        conn.close()
        trigger_bg_sync()

    return {"summaries": parsed.get("summaries", {})}

# --- CANLI DURUM & HATA LOGLARI ---
@app.get("/api/status/live")
async def live_status(client_key: str = Header(None)):
    get_user_by_auth(client_key=client_key)
    conn = get_db()
    cfg = conn.execute("SELECT chat_model, agents_config FROM config WHERE id=1").fetchone()
    conn.close()
    if cfg and not LIVE_STATE.get("model"):
        agents = V7.normalize_agents_config(cfg["agents_config"], fallback_model=cfg["chat_model"])
        LIVE_STATE["model"] = agents["main_agent"]["model"]
    return {"live": LIVE_STATE, "errors": list(reversed(ERROR_LOGS[-30:]))}

@app.post("/api/log_error")
async def log_err(request: Request):
    d = await request.json()
    ERROR_LOGS.append({"time": time.strftime("%H:%M:%S"), "type": d.get("type", "Hata"), "details": d.get("details", "")})
    return {"status": "ok"}

# --- ASENKRON ÇOKLU AGENT MOTORU (V7: 1 ANA ORKESTRATÖR + 13 ALT AGENT) ---
async def stream_venice_call(
    cfg: dict,
    messages: list,
    task_id: str,
    thinking_mode: bool = False,
    stage_prefix: str = "",
    model_override: str | None = None,
    buffer_builder=None,
) -> str:
    headers = {"Authorization": f"Bearer {cfg['venice_key']}", "Content-Type": "application/json"}
    primary_model = (model_override or "").strip() or cfg["chat_model"]
    fallback_model = (cfg.get("chat_model") or "").strip() or primary_model
    models_to_try = [primary_model] if fallback_model == primary_model else [primary_model, fallback_model]

    collected = []
    reasoning_collected = []
    prefix = f"{stage_prefix}\n" if stage_prefix else ""
    prior_live = (LIVE_STATE.get("buffer") or "").strip()
    stage_header = (prior_live + "\n\n" + ("=" * 48) + "\n") if prior_live else ""

    def build_stage_full_text() -> str:
        parts = []
        if stage_prefix:
            parts.append(stage_prefix)
        if reasoning_collected:
            parts.append("[GİZLİ DÜŞÜNCE / REASONING]:\n" + "".join(reasoning_collected))
        if collected:
            parts.append("[AÇIK ÇIKTI / OUTPUT]:\n" + "".join(collected))
        return "\n\n".join(parts)

    last_err = ""
    async with httpx.AsyncClient() as client:
        for mdl_idx, mdl in enumerate(models_to_try):
            LIVE_STATE["model"] = mdl
            base_payload = {
                "model": mdl,
                "stream": True,
                "max_tokens": 16384,
                "messages": messages,
            }
            if thinking_mode:
                candidate_payloads = [base_payload]
            else:
                p1 = {
                    **base_payload,
                    "reasoning": {"enabled": False},
                    "venice_parameters": {"disable_thinking": True, "strip_thinking_response": True},
                }
                p2 = {
                    **base_payload,
                    "reasoning_effort": "low",
                    "venice_parameters": {"disable_thinking": True, "strip_thinking_response": True},
                }
                p3 = {
                    **base_payload,
                    "venice_parameters": {"disable_thinking": True, "strip_thinking_response": True},
                }
                candidate_payloads = [p1, p2, p3, base_payload]

            succeeded = False
            for idx, venice_payload in enumerate(candidate_payloads):
                async with client.stream("POST", "https://api.venice.ai/api/v1/chat/completions", json=venice_payload, headers=headers, timeout=180.0) as response:
                    if response.status_code in (400, 404, 422) and idx < len(candidate_payloads) - 1:
                        last_err = (await response.aread()).decode("utf-8", errors="ignore")
                        continue
                    if response.status_code != 200:
                        last_err = (await response.aread()).decode("utf-8", errors="ignore")
                        if mdl_idx < len(models_to_try) - 1:
                            break
                        ERROR_LOGS.append({"time": time.strftime("%H:%M:%S"), "type": f"Venice HTTP {response.status_code} ({mdl})", "details": last_err[:400]})
                        raise RuntimeError(f"API Hatası ({response.status_code} - {mdl})")

                    succeeded = True
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content") or ""
                            reasoning_piece = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thinking") or ""
                            updated = False
                            if reasoning_piece:
                                reasoning_collected.append(reasoning_piece)
                                updated = True
                            if content:
                                collected.append(content)
                                updated = True
                            if updated:
                                full_stage = build_stage_full_text()
                                LIVE_STATE["buffer"] = (stage_header + full_stage)[-200000:]
                                if task_id and task_id in TASKS:
                                    live_tail = "".join(collected)[-1200:] if collected else ("[Düşünüyor...] " + "".join(reasoning_collected)[-800:])
                                    if buffer_builder:
                                        TASKS[task_id]["buffer"] = buffer_builder(prefix + live_tail)
                                    else:
                                        TASKS[task_id]["buffer"] = prefix + live_tail
                        except Exception:
                            pass
                    break
            if succeeded:
                break
        else:
            ERROR_LOGS.append({"time": time.strftime("%H:%M:%S"), "type": "Venice Model Hatası", "details": last_err[:400]})
            raise RuntimeError(f"API Hatası: {last_err[:200] or 'Model yanıt vermedi'}")
    return "".join(collected)


async def call_venice_json(cfg: dict, messages: list, model_name: str, max_tokens: int = 3072) -> tuple[str, str]:
    """Non-streaming Venice AI call with automatic fallback model."""
    headers = {"Authorization": f"Bearer {cfg['venice_key']}", "Content-Type": "application/json"}
    primary_model = (model_name or "").strip() or cfg["chat_model"]
    fallback_model = (cfg.get("chat_model") or "").strip() or primary_model
    models_to_try = [primary_model] if fallback_model == primary_model else [primary_model, fallback_model]
    last_err = ""
    async with httpx.AsyncClient(timeout=75.0) as client:
        for mdl in models_to_try:
            LIVE_STATE["model"] = mdl
            payloads = [
                {
                    "model": mdl,
                    "max_tokens": max_tokens,
                    "messages": messages,
                    "reasoning": {"enabled": False},
                    "venice_parameters": {"disable_thinking": True, "strip_thinking_response": True},
                },
                {
                    "model": mdl,
                    "max_tokens": max_tokens,
                    "messages": messages,
                },
            ]
            for pld in payloads:
                try:
                    r = await client.post("https://api.venice.ai/api/v1/chat/completions", json=pld, headers=headers)
                    if r.status_code == 200:
                        data = r.json()
                        msg = (data.get("choices") or [{}])[0].get("message") or {}
                        content = str(msg.get("content") or "").strip()
                        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                        if content:
                            return content, mdl
                    else:
                        last_err = f"HTTP {r.status_code}: {r.text[:250]}"
                except Exception as exc:
                    last_err = str(exc)
    raise RuntimeError(last_err or "Model boş yanıt döndürdü.")


def mask_system_prompt_tools(sys_prompt: str, forbidden_types: set[str]) -> str:
    """Physically strips forbidden action schema lines from the system prompt so the model never sees them."""
    kept = []
    for line in (sys_prompt or "").splitlines():
        if any(f'"type": "{ft}"' in line or f"'type': '{ft}'" in line for ft in forbidden_types):
            continue
        kept.append(line)
    return "\n".join(kept)


def build_rig_type_hints(payload: dict) -> list[str]:
    hints = []
    seen = set()
    candidates = []
    sh = payload.get("scene_hierarchy") or {}
    if isinstance(sh, dict):
        candidates.extend(sh.get("Workspace") or [])
    candidates.extend(payload.get("selected_items") or [])
    for item in candidates:
        if not isinstance(item, dict):
            continue
        rt = str(item.get("RigType") or "").strip().upper()
        path = item.get("Path") or item.get("Name")
        if rt in ("R6", "R15") and path and path not in seen:
            seen.add(path)
            if rt == "R6":
                hints.append(
                    f"[RIG TİPİ - {path}]: Bu rig R6'dır! SADECE şu eklemleri kullan: "
                    "HumanoidRootPart, Torso, Head, Left Arm, Right Arm, Left Leg, Right Leg. "
                    "ASLA UpperTorso, LowerTorso, LeftUpperLeg, RightUpperArm gibi R15 isimleri yazma!"
                )
            else:
                hints.append(
                    f"[RIG TİPİ - {path}]: Bu rig R15'tir! SADECE şu eklemleri kullan: "
                    "HumanoidRootPart, LowerTorso, UpperTorso, Head, LeftUpperArm, LeftLowerArm, LeftHand, "
                    "RightUpperArm, RightLowerArm, RightHand, LeftUpperLeg, LeftLowerLeg, LeftFoot, "
                    "RightUpperLeg, RightLowerLeg, RightFoot."
                )
    return hints


# =====================================================================
# ADIM A: ETKİLEŞİMLİ KEŞİF & NOT ALMA (POST /agent/discover)
# =====================================================================
@app.post("/agent/discover")
@app.post("/api/agent/discover")
async def discover_project(request: Request, client_key: str = Header(None)):
    user = get_user_by_auth(client_key=client_key)
    body = await request.json()
    user_msg = str(body.get("prompt") or body.get("answer") or "").strip()
    if not user_msg:
        return JSONResponse({"error": "Boş istek analiz edilemez."}, status_code=400)

    cfg = await get_fresh_config()
    if not cfg.get("venice_key"):
        return JSONResponse({"error": "Yönetici henüz Venice API anahtarını girmemiş."}, status_code=400)

    session_id = str(body.get("session_id") or "").strip() or str(uuid.uuid4())
    session = DISCOVERY_SESSIONS.get(session_id) or {
        "session_id": session_id,
        "user_id": user["id"],
        "initial_prompt": user_msg,
        "conversation": [],
        "project_notes": {},
        "round": 0,
    }

    if isinstance(body.get("project_notes"), dict) and body["project_notes"]:
        session["project_notes"] = body["project_notes"]
    if isinstance(body.get("conversation"), list) and body["conversation"]:
        session["conversation"] = body["conversation"]

    session["conversation"].append({"role": "user", "content": user_msg})
    session["round"] = int(session.get("round", 0)) + 1

    main_cfg = cfg["agents"]["main_agent"]
    scene_compact = V6.compact_scene(body.get("scene_hierarchy", {}), limit=18000)

    LIVE_STATE["status"] = f"{user['username']} | Main Agent Keşif & Not Alma (Tur {session['round']})..."
    discover_payload = {
        "MODE": "DISCOVER",
        "ROUND": session["round"],
        "INITIAL_PROMPT": session["initial_prompt"],
        "LATEST_USER_MESSAGE": user_msg,
        "CONVERSATION_HISTORY": session["conversation"][-8:],
        "EXISTING_PROJECT_NOTES": session["project_notes"],
        "AVAILABLE_13_SUBAGENTS": [
            {"id": a["id"], "title": a["title"], "phase": a["phase"], "description": a["description"]}
            for a in V7.SUB_AGENTS
        ],
        "SCENE_HIERARCHY": scene_compact,
    }

    discover_sys = (
        main_cfg["prompt"]
        + "\n\n[AKTİF MOD: DISCOVER (KEŞİF & NOT ALMA)]\n"
        + "- SADECE geçerli tek bir JSON objesi döndür (markdown/kod bloğu yok).\n"
        + "- Tur 1'de 3–5 adet kısa, net, maddeli açıklayıcı soru sor (kamera, NPC/bot isteği, tuş/mekanik, atmosfer/tema, kazanma/kaybetme vb.) ve ilk mesajdan çıkan kesin kuralları + negatif kısıtlamaları `project_notes` içine kaydet.\n"
        + "- Tur 2 ve sonrasında kullanıcının cevaplarını `project_notes` içine işle; kritik belirsizlik kalmadıysa `\"status\": \"ready\"` ve `\"questions\": []` döndür (gerekirse en fazla 1 kısa takip sorusu sor).\n"
        + "- Türkçe iletişim kur."
    )

    try:
        raw_out, used_model = await call_venice_json(
            cfg,
            [
                {"role": "system", "content": discover_sys},
                {"role": "user", "content": json.dumps(discover_payload, ensure_ascii=False)[:60000]},
            ],
            model_name=main_cfg["model"],
            max_tokens=3072,
        )
        parsed = extract_and_repair_json(raw_out)
    except Exception as exc:
        ERROR_LOGS.append({"time": time.strftime("%H:%M:%S"), "type": "Keşif Hatası", "details": str(exc)})
        return JSONResponse({"error": f"Keşif başarısız: {exc}"}, status_code=502)

    status = str(parsed.get("status") or ("ready" if session["round"] >= 2 else "needs_clarification")).strip().lower()
    if status not in ("needs_clarification", "ready"):
        status = "needs_clarification" if parsed.get("questions") else "ready"

    questions = parsed.get("questions") if isinstance(parsed.get("questions"), list) else []
    questions = [str(q).strip() for q in questions if str(q).strip()][:5]
    if status == "ready":
        questions = []

    notes = parsed.get("project_notes") if isinstance(parsed.get("project_notes"), dict) else {}
    if notes:
        session["project_notes"] = notes
    else:
        session["project_notes"].setdefault("core_concept", session["initial_prompt"])

    # Negatif NPC kısıtlamasını keşif aşamasında bile preview listesine yansıt
    preview_active = parsed.get("preview_active_agents") if isinstance(parsed.get("preview_active_agents"), list) else []
    preview_skipped = parsed.get("preview_skipped_agents") if isinstance(parsed.get("preview_skipped_agents"), dict) else {}
    if V7.user_forbids_npc(user_msg + " " + session["initial_prompt"], session["project_notes"]):
        preview_active = [x for x in preview_active if x != "agent_ai_npc"]
        preview_skipped["agent_ai_npc"] = "Kullanıcı kısıtlaması (NPC istenmedi)"

    assistant_msg = str(parsed.get("assistant_message") or parsed.get("summary") or "Proje notları güncellendi.").strip()
    session["conversation"].append({"role": "assistant", "content": assistant_msg + ("\n" + "\n".join(questions) if questions else "")})
    DISCOVERY_SESSIONS[session_id] = session

    LIVE_STATE["status"] = f"Keşif Tur {session['round']} ({status})"
    LIVE_STATE["buffer"] = (
        f"[🧭 Main Agent Keşif ({used_model}) — Tur {session['round']}]\n"
        f"Durum: {status}\n"
        f"Özet: {assistant_msg}\n\n"
        + ("Sorular:\n" + "\n".join(questions) + "\n\n" if questions else "")
        + "PROJECT_NOTES:\n" + json.dumps(session["project_notes"], ensure_ascii=False, indent=2)
    )

    return {
        "session_id": session_id,
        "round": session["round"],
        "status": status,
        "model": used_model,
        "summary": str(parsed.get("summary") or assistant_msg),
        "assistant_message": assistant_msg,
        "questions": questions,
        "project_notes": session["project_notes"],
        "conversation": session["conversation"],
        "preview_active_agents": preview_active,
        "preview_skipped_agents": preview_skipped,
    }


# =====================================================================
# ADIM B: SEÇİCİ YÖNLENDİRME + 4 FAZLI 13 ALT AGENT BORU HATTI
# =====================================================================
async def process_task(task_id: str, user: dict, payload: dict, raw_in_tokens: int):
    cfg = await get_fresh_config()
    agents_cfg = cfg["agents"]

    conn = get_db()
    mem_records = conn.execute("SELECT prompt, ai_summary FROM memory_logs WHERE user_id = ? ORDER BY id DESC LIMIT 4", (user["id"],)).fetchall()
    conn.close()

    memory_list = [{"Kullanıcı_Talebi": r["prompt"], "Yaptığın_Düzenlemeler": r["ai_summary"]} for r in reversed(mem_records)]
    raw_tm = payload.get("thinking_mode", False)
    thinking_mode = raw_tm if isinstance(raw_tm, bool) else str(raw_tm).strip().lower() in ("true", "1", "yes", "on")
    directives_cfg = payload.get("directives", {})
    prompt_text = str(payload.get("prompt") or "").strip()
    active_directives = build_dynamic_directives(prompt_text, directives_cfg)
    rig_type_hints = build_rig_type_hints(payload)

    session_id = str(payload.get("session_id") or "").strip()
    session_data = DISCOVERY_SESSIONS.get(session_id) if session_id else None
    project_notes = payload.get("project_notes") if isinstance(payload.get("project_notes"), dict) else {}
    if not project_notes and session_data:
        project_notes = dict(session_data.get("project_notes") or {})
    discovery_conv = payload.get("discovery_conversation") if isinstance(payload.get("discovery_conversation"), list) else (
        session_data.get("conversation", []) if session_data else []
    )
    if session_data and session_data.get("initial_prompt") and session_data["initial_prompt"] not in prompt_text:
        prompt_text = f"{session_data['initial_prompt']}\n\n[Keşif Yanıtları]:\n{prompt_text}" if prompt_text else session_data["initial_prompt"]

    mode_instruction = (
        "[DERİN DÜŞÜNME MODU AÇIK]: Mimariyi, isim sözleşmelerini, fizik/animasyon zamanlamalarını ve tüm kenar durumlarını eksiksiz planlayarak üret."
        if thinking_mode else
        "[HIZLI MOD AÇIK]: Gereksiz uzatmadan doğrudan en temiz, çalışan JSON aksiyonlarını ve manifest güncellemelerini üret."
    )

    if not cfg.get("venice_key"):
        TASKS[task_id] = {"status": "failed", "error": "Yönetici henüz Venice API anahtarını girmemiş."}
        return

    is_syntax_repair = bool(payload.get("is_syntax_repair", False))
    scene_compact = V6.compact_scene(payload.get("scene_hierarchy", {}))

    # 14 Agent Canlı Boru Hattı Durumu
    pipeline: list[dict] = [
        {
            "id": a["id"],
            "order": a["order"],
            "phase": a["phase"],
            "title": a["title"],
            "short_name": a["short_name"],
            "status": "pending",
            "model": agents_cfg[a["id"]]["model"],
            "reason": "",
            "actions_count": 0,
        }
        for a in V7.AGENT_REGISTRY
    ]
    pipe_by_id = {p["id"]: p for p in pipeline}
    handoffs: list[str] = []

    def sync_live(detail_text: str = ""):
        report = V7.format_pipeline_report(pipeline, handoffs, detail_text)
        LIVE_STATE["pipeline"] = [dict(p) for p in pipeline]
        LIVE_STATE["handoffs"] = list(handoffs)
        if task_id in TASKS:
            TASKS[task_id]["agent_pipeline"] = [dict(p) for p in pipeline]
            TASKS[task_id]["handoff_logs"] = list(handoffs)
            TASKS[task_id]["buffer"] = report
        return report

    LIVE_STATE["status"] = f"{user['username']} | V7 14-Agent Orkestrasyon başlıyor..."
    LIVE_STATE["buffer"] = ""
    sync_live("Main Agent başlatılıyor...")

    def summarize_actions(acts):
        out = []
        for a in acts:
            item = {"type": a.get("type")}
            for k in ("name", "path", "parent", "class", "rig_type", "behavior", "preset", "mode", "target_path", "script_type"):
                if a.get(k) not in (None, ""):
                    item[k] = a.get(k)
            props = a.get("properties") or {}
            if isinstance(props, dict):
                for k in ("Position", "Size"):
                    if k in props:
                        item[k] = props[k]
            if a.get("position") is not None:
                item["position"] = a.get("position")
            out.append(item)
        return out

    try:
        total_out_tokens = 0
        repaired_json = {}

        if is_syntax_repair:
            repair_model = agents_cfg["agent_combat_skills"]["model"]
            repair_sys = V7.build_subagent_system_prompt(
                "agent_combat_skills",
                agents_cfg["agent_combat_skills"]["prompt"],
                mode_instruction,
                ["SYNTAX ONARIMI MODU: Sadece sözdizimi hatalı scriptleri `write_script` / `edit_script` ile düzelt."],
            )
            messages = [
                {"role": "system", "content": repair_sys},
                {"role": "user", "content": json.dumps({
                    "KULLANICI_KOMUTU": prompt_text,
                    "SCENE_HIERARCHY": scene_compact,
                    "SELECTED_ITEMS": payload.get("selected_items", []),
                    "REQUESTED_FILES_CONTENT": payload.get("requested_files_content", {}),
                }, ensure_ascii=False)[:100000]},
            ]
            full_output = await stream_venice_call(
                cfg, messages, task_id, thinking_mode=thinking_mode,
                stage_prefix="[Syntax Onarımı]", model_override=repair_model,
            )
            total_out_tokens += estimate_tokens(full_output)
            repaired_json = extract_and_repair_json(full_output)
        else:
            combined_actions: list[dict] = []
            combined_thoughts: list[str] = []
            agent_summaries: list[dict] = []
            shared_manifest = V7.create_empty_manifest(str(project_notes.get("game_title") or ""))

            # =========================================================
            # FAZ 0: MAIN AGENT — SEÇİCİ YÖNLENDİRME & MANIFEST BAŞLATMA
            # =========================================================
            main_cfg = agents_cfg["main_agent"]
            pipe_by_id["main_agent"]["status"] = "running"
            LIVE_STATE["status"] = f"{user['username']} | Main Agent yönlendirme planı hazırlıyor..."
            sync_live("Main Agent 13 alt agent arasından sadece gerekenleri seçiyor...")

            route_sys = (
                main_cfg["prompt"]
                + "\n\n[AKTİF MOD: ROUTE (SEÇİCİ AGENT YÖNLENDİRME & SHARED_MANIFEST)]\n"
                + "- 13 alt agent'tan YALNIZCA gerçekten gerekli olanlara `\"status\": \"ACTIVE\"` ve özel `task_input` ver; gereksiz veya kullanıcının reddettiği tüm agent'lara `\"status\": \"PASS\"` ve kısa `reason` yaz.\n"
                + "- SADECE geçerli tek bir JSON objesi döndür (markdown/kod bloğu yok)."
            )
            route_payload = {
                "MODE": "ROUTE",
                "KULLANICI_KOMUTU": prompt_text,
                "PROJECT_NOTES": project_notes,
                "DISCOVERY_CONVERSATION": discovery_conv[-8:],
                "GECMIS_HAFIZA": memory_list,
                "ACTIVE_QUALITY_DIRECTIVES": active_directives,
                "AVAILABLE_13_SUBAGENTS": [
                    {"id": a["id"], "order": a["order"], "phase": a["phase"], "title": a["title"], "description": a["description"], "allowed_actions": a["allowed_actions"]}
                    for a in V7.SUB_AGENTS
                ],
                "SCENE_HIERARCHY": scene_compact,
                "SELECTED_ITEMS": payload.get("selected_items", []),
            }

            route_out = await stream_venice_call(
                cfg,
                [
                    {"role": "system", "content": route_sys},
                    {"role": "user", "content": json.dumps(route_payload, ensure_ascii=False)[:90000]},
                ],
                task_id,
                thinking_mode=thinking_mode,
                stage_prefix=f"[0/13 Main Agent Yönlendirme — {main_cfg['model']}]",
                model_override=main_cfg["model"],
                buffer_builder=lambda tail: V7.format_pipeline_report(pipeline, handoffs, tail),
            )
            total_out_tokens += estimate_tokens(route_out)
            route_json = extract_and_repair_json(route_out)

            if isinstance(route_json.get("project_notes"), dict) and route_json["project_notes"]:
                project_notes = {**project_notes, **route_json["project_notes"]}
            if isinstance(route_json.get("shared_manifest"), dict):
                shared_manifest = V7.merge_manifest(shared_manifest, route_json["shared_manifest"], "main_agent", [])

            routing = V7.normalize_routing(route_json.get("routing"), prompt_text, project_notes)
            pipe_by_id["main_agent"]["status"] = "done"

            active_names = []
            pass_names = []
            for a in V7.SUB_AGENTS:
                aid = a["id"]
                dec = routing[aid]
                if dec["status"] == "ACTIVE":
                    pipe_by_id[aid]["status"] = "active"
                    pipe_by_id[aid]["reason"] = dec["reason"]
                    active_names.append(a["short_name"])
                else:
                    pipe_by_id[aid]["status"] = "pass"
                    pipe_by_id[aid]["reason"] = dec["reason"]
                    pass_names.append(a["short_name"])

            handoffs.append(f"Main Agent → AKTİF ({len(active_names)}): {', '.join(active_names)}")
            if pass_names:
                handoffs.append(f"Main Agent → PASS ({len(pass_names)}): {', '.join(pass_names)}")
            if route_json.get("thought"):
                combined_thoughts.append("[Main Agent] " + str(route_json["thought"]))
            sync_live("Seçici yönlendirme tamamlandı, Faz 1 başlatılıyor...")

            # =========================================================
            # FAZ 1 → FAZ 2 → FAZ 3 → FAZ 4 (ZORUNLU SIRAYLA 1..13)
            # =========================================================
            need_files_result = None
            for meta in V7.SUB_AGENTS:
                aid = meta["id"]
                dec = routing[aid]
                if dec["status"] != "ACTIVE":
                    continue

                a_cfg = agents_cfg[aid]
                a_model = a_cfg["model"]
                allowed_set = set(meta["allowed_actions"])
                pipe_by_id[aid]["status"] = "running"
                pipe_by_id[aid]["model"] = a_model
                LIVE_STATE["status"] = f"{user['username']} | Faz {meta['phase']} • {meta['title']} ({a_model})..."

                # Ajanlar arası zorunlu SHARED_MANIFEST veri aktarımları
                extra_notes: list[str] = []
                if cfg.get("system_prompt") and not V6.is_legacy_prompt(cfg["system_prompt"]):
                    extra_notes.append("ADMIN EK KURALI: " + cfg["system_prompt"][:1500])

                if aid in ("agent_animation", "agent_movement", "agent_combat_skills", "agent_ai_npc") and rig_type_hints:
                    extra_notes.extend(rig_type_hints)

                if aid in ("agent_movement", "agent_combat_skills", "agent_ai_npc") and shared_manifest.get("animations"):
                    anim_list_str = ", ".join(
                        (f"{x.get('name')} ({x.get('trigger') or x.get('preset') or 'action'})" if isinstance(x, dict) else str(x))
                        for x in shared_manifest["animations"]
                    )
                    extra_notes.append(
                        f"ZORUNLU AGENT 5 ANİMASYON AKTARIMI: Agent 5 şu animasyonları üretti -> [{anim_list_str}]. "
                        "Bunları BİREBİR aynı isimlerle `AIKit.PlayAnim(target, \"TamIsim\")` üzerinden ilgili tuşlara/durumlara bağla!"
                    )
                    handoffs.append(f"Agent 5 (Animasyon) → {meta['title']}: {len(shared_manifest['animations'])} animasyon aktarıldı")

                if aid in ("agent_movement", "agent_combat_skills", "agent_interaction", "agent_inventory", "agent_economy") and shared_manifest.get("ui_elements"):
                    ui_preview = [
                        (x.get("path") or x.get("name") if isinstance(x, dict) else str(x))
                        for x in shared_manifest["ui_elements"][:30]
                    ]
                    extra_notes.append("ZORUNLU AGENT 6 UI AKTARIMI: Mevcut UI elemanları -> " + ", ".join(ui_preview) + ". `ScreenGui.Enabled = true` yap ve birebir aynı isimlerle güncelle.")
                    handoffs.append(f"Agent 6 (UI/HUD) → {meta['title']}: {len(shared_manifest['ui_elements'])} UI elemanı aktarıldı")

                if aid in ("agent_combat_skills", "agent_movement") and shared_manifest.get("vfx"):
                    vfx_preview = [
                        (x.get("path") or x.get("name") if isinstance(x, dict) else str(x))
                        for x in shared_manifest["vfx"][:20]
                    ]
                    extra_notes.append("AGENT 4 VFX ŞABLONLARI: " + ", ".join(vfx_preview))
                    handoffs.append(f"Agent 4 (VFX) → {meta['title']}: {len(shared_manifest['vfx'])} VFX şablonu aktarıldı")

                if aid == "agent_camera" and project_notes.get("camera_preference"):
                    extra_notes.append(f"KULLANICI KAMERA TERCİHİ: {project_notes['camera_preference']}")

                sync_live(f"{meta['title']} çalışıyor ({a_model})...")

                sub_sys = V7.build_subagent_system_prompt(aid, a_cfg["prompt"], mode_instruction, extra_notes)
                sub_payload = {
                    "AGENT_ID": aid,
                    "AGENT_ROLE": meta["title"],
                    "GOREV_EMRI": dec["task_input"] or prompt_text,
                    "KULLANICI_KOMUTU": prompt_text,
                    "PROJECT_NOTES": project_notes,
                    "SHARED_MANIFEST": shared_manifest,
                    "ONCEKI_AGENT_OZETLERI": agent_summaries[-6:],
                    "SCENE_HIERARCHY": scene_compact,
                    "SELECTED_ITEMS": payload.get("selected_items", []),
                }
                if meta["phase"] in (3, 4):
                    sub_payload["PROJECT_INDEX"] = payload.get("project_shortened_index", [])
                    sub_payload["REQUESTED_FILES_CONTENT"] = payload.get("requested_files_content", {})
                    sub_payload["GECMIS_HAFIZA"] = memory_list

                sub_messages = [
                    {"role": "system", "content": sub_sys},
                    {"role": "user", "content": json.dumps(sub_payload, ensure_ascii=False)[:105000]},
                ]

                try:
                    sub_json = {}
                    max_turns = 2 if aid in ("agent_architecture", "agent_environment") else 1
                    for _turn in range(max_turns):
                        sub_out = await stream_venice_call(
                            cfg,
                            sub_messages,
                            task_id,
                            thinking_mode=thinking_mode,
                            stage_prefix=f"[Faz {meta['phase']} • {meta['title']} — {a_model}]",
                            model_override=a_model,
                            buffer_builder=lambda tail: V7.format_pipeline_report(pipeline, handoffs, tail),
                        )
                        total_out_tokens += estimate_tokens(sub_out)
                        sub_json = extract_and_repair_json(sub_out)

                        if sub_json.get("need_files") and not combined_actions:
                            need_files_result = sub_json
                            break

                        queries = sub_json.get("search_toolbox") or []
                        if queries and isinstance(queries, list) and _turn == 0 and max_turns == 2:
                            LIVE_STATE["status"] = f"{meta['title']} Toolbox arıyor: {', '.join(map(str, queries[:4]))}"
                            toolbox_results = {}
                            for q in queries[:4]:
                                toolbox_results[str(q)] = await search_roblox_toolbox(str(q), limit=6)
                            sub_messages.append({"role": "assistant", "content": sub_out})
                            sub_messages.append({
                                "role": "user",
                                "content": "TOOLBOX_ARAMA_SONUCLARI:\n" + json.dumps(toolbox_results, ensure_ascii=False) + "\nŞimdi uygun AssetId'leri seçip JSON aksiyonlarını ve manifest_updates alanını döndür. Tekrar search_toolbox çağırma.",
                            })
                            continue
                        break

                    if need_files_result:
                        break

                    filtered_actions = []
                    for a in (sub_json.get("actions") or []):
                        if not isinstance(a, dict):
                            continue
                        t = a.get("type")
                        if t == "fallback_code" and a.get("source") and "write_script" in allowed_set:
                            a = {"type": "write_script", "script_type": "Script", "name": f"{meta['short_name'].replace(' ', '')}Script", "parent": "ServerScriptService", "source": a["source"]}
                            t = "write_script"
                        if t in allowed_set:
                            filtered_actions.append(a)

                    combined_actions.extend(filtered_actions)
                    shared_manifest = V7.merge_manifest(shared_manifest, sub_json.get("manifest_updates"), aid, filtered_actions)

                    pipe_by_id[aid]["status"] = "done"
                    pipe_by_id[aid]["actions_count"] = len(filtered_actions)

                    if sub_json.get("thought"):
                        combined_thoughts.append(f"[{meta['title']}] " + str(sub_json["thought"]))

                    if aid == "agent_animation" and shared_manifest.get("animations"):
                        anames = [x.get("name") if isinstance(x, dict) else str(x) for x in shared_manifest["animations"]]
                        handoffs.append(f"Agent 5 (Animasyon) Manifeste Yazdı ({len(anames)}): {', '.join(anames[:15])}")
                    elif aid == "agent_ui" and shared_manifest.get("ui_elements"):
                        handoffs.append(f"Agent 6 (UI/HUD) Manifeste Yazdı: {len(shared_manifest['ui_elements'])} UI elemanı")
                    elif aid == "agent_architecture" and shared_manifest.get("world_objects"):
                        handoffs.append(f"Agent 1 (Mimari) Manifeste Yazdı: {len(shared_manifest['world_objects'])} sahne yapısı")

                    agent_summaries.append({
                        "agent": aid,
                        "title": meta["title"],
                        "thought": sub_json.get("thought", ""),
                        "actions_summary": summarize_actions(filtered_actions)[:80],
                    })
                    sync_live(f"{meta['title']} tamamlandı ({len(filtered_actions)} aksiyon).")

                except Exception as sub_err:
                    pipe_by_id[aid]["status"] = "error"
                    pipe_by_id[aid]["reason"] = str(sub_err)[:120]
                    ERROR_LOGS.append({"time": time.strftime("%H:%M:%S"), "type": f"{meta['title']} Hatası", "details": str(sub_err)})
                    handoffs.append(f"⚠️ {meta['title']} hata aldı: {str(sub_err)[:80]}")
                    sync_live(f"{meta['title']} hata aldı, sonraki faza geçiliyor...")

            if need_files_result:
                repaired_json = need_files_result
            else:
                # Güvenlik ağları: kamera, AIKit, varsayılan sinematik atmosfer
                p_low = (prompt_text + " " + json.dumps(project_notes, ensure_ascii=False)).lower()
                wants_cam = any(k in p_low for k in ["3. şahıs", "3.şahıs", "üçüncü şahıs", "third person", "3rd person", "tps", "omuz", "shoulder", "kamera takip", "follow cam"])
                has_cam = any(a.get("type") in ("setup_camera", "create_cutscene") for a in combined_actions)
                if wants_cam and not has_cam:
                    wants_shoulder = any(k in p_low for k in ["omuz", "shoulder", "tps", "shooter"])
                    combined_actions.append({"type": "setup_camera", "mode": "third_person", "distance": 11, "shoulder_offset": 2.0 if wants_shoulder else 0, "sprint": True})

                is_scene_build = pipe_by_id["agent_architecture"]["actions_count"] > 0 or len(combined_actions) > 0
                uses_kit = any("AIKit" in str(a.get("source", "")) for a in combined_actions)
                has_kit_action = any(a.get("type") in ("spawn_rig", "create_animation", "setup_camera", "install_kit") for a in combined_actions)
                if (uses_kit or is_scene_build) and not has_kit_action:
                    combined_actions.insert(0, {"type": "install_kit"})

                has_custom_lighting = any(
                    a.get("type") == "setup_atmosphere"
                    or str(a.get("parent") or "") == "Lighting"
                    or str(a.get("path") or "").startswith("Lighting")
                    for a in combined_actions
                )
                if is_scene_build and not has_custom_lighting and not V6.wants_custom_atmosphere(p_low):
                    combined_actions.insert(0, {"type": "setup_atmosphere"})

                repaired_json = {
                    "thought": "\n".join(combined_thoughts) if combined_thoughts else "V7 14-Agent üretimi tamamlandı.",
                    "actions": combined_actions,
                    "agent_pipeline": pipeline,
                    "handoff_logs": handoffs,
                    "shared_manifest": shared_manifest,
                    "project_notes": project_notes,
                }

        if user["role"] != "admin":
            total_cost = int((raw_in_tokens * cfg["input_cost_mult"]) + (total_out_tokens * cfg["output_cost_mult"]))
            conn = get_db()
            conn.execute("UPDATE users SET tokens = MAX(0, tokens - ?) WHERE id = ?", (total_cost, user["id"]))
            conn.commit()
            conn.close()

        actions = repaired_json.get("actions", [])

        if not repaired_json.get("need_files") and not payload.get("is_syntax_repair", False):
            act_descs = [f"{a.get('type')}: {a.get('name') or a.get('path') or a.get('asset_id') or 'nesne'}" for a in actions[:40]]
            ai_summary = repaired_json.get("thought", "Düzenlemeler yapıldı.")
            if act_descs:
                ai_summary += " (Değişenler: " + ", ".join(act_descs) + ")"

            conn = get_db()
            conn.execute("INSERT INTO memory_logs (user_id, prompt, ai_summary) VALUES (?, ?, ?)", (user["id"], prompt_text[:500], ai_summary[:1000]))
            conn.commit()
            conn.close()

        trigger_bg_sync()

        if len(actions) == 1 and actions[0].get("type") == "fallback_code":
            selected = payload.get("selected_items", [])
            scripts = [s for s in selected if s.get("Class") in ("Script", "LocalScript", "ModuleScript")]
            if scripts:
                repaired_json["actions"] = [{"type": "edit_script", "path": scripts[0]["Path"], "source": actions[0]["source"]}]
            else:
                repaired_json["actions"] = [{"type": "write_script", "script_type": "Script", "name": "AIGenerated", "parent": "ServerScriptService", "source": actions[0]["source"]}]

        final_report = sync_live("Üretim tamamlandı! Aksiyonlar Roblox Studio'ya aktarılıyor...")
        LIVE_STATE["status"] = "Tamamlandı"
        TASKS[task_id] = {
            "status": "completed",
            "result": repaired_json,
            "agent_pipeline": pipeline,
            "handoff_logs": handoffs,
            "buffer": final_report,
        }

    except Exception as e:
        ERROR_LOGS.append({"time": time.strftime("%H:%M:%S"), "type": "V7 Orkestrasyon Hatası", "details": str(e)})
        LIVE_STATE["status"] = "Hata"
        TASKS[task_id] = {
            "status": "failed",
            "error": str(e),
            "agent_pipeline": pipeline,
            "handoff_logs": handoffs,
        }


@app.post("/agent/execute")
@app.post("/api/agent/execute")
async def execute_task(request: Request, client_key: str = Header(None)):
    user = get_user_by_auth(client_key=client_key)
    payload = await request.json()
    raw_in_tokens = estimate_tokens(str(payload))

    if user["role"] != "admin" and user["tokens"] < 300:
        return JSONResponse({"error": "Yetersiz kredi (Token)."}, status_code=402)

    task_id = str(uuid.uuid4())
    TASKS[task_id] = {"status": "processing", "buffer": "V7 14-Agent Orkestratör başlatılıyor...", "agent_pipeline": [], "handoff_logs": []}
    asyncio.create_task(process_task(task_id, user, payload, raw_in_tokens))
    return {"task_id": task_id, "role": user["role"], "username": user["username"]}


@app.get("/agent/poll")
@app.get("/api/agent/poll")
async def poll_task(task_id: str, client_key: str = Header(None)):
    user = get_user_by_auth(client_key=client_key)
    if task_id not in TASKS:
        return JSONResponse({"error": "Görev bulunamadı"}, status_code=404)
    res = dict(TASKS[task_id])
    res["remaining_tokens"] = "Sınırsız" if user["role"] == "admin" else user["tokens"]
    return res


EXPANDER_MODEL = os.environ.get("EXPANDER_MODEL", "claude-haiku-5-5").strip() or "claude-haiku-5-5"


@app.post("/expand-prompt")
async def expand_prompt_endpoint(request: Request, client_key: str = Header(None)):
    user = get_user_by_auth(client_key=client_key)
    body = await request.json()
    raw_prompt = str(body.get("prompt") or "").strip()
    if not raw_prompt:
        return JSONResponse({"error": "Boş prompt genişletilemez."}, status_code=400)

    cfg = await get_fresh_config()
    if not cfg.get("venice_key"):
        return JSONResponse({"error": "Yönetici henüz Venice API anahtarını girmemiş."}, status_code=400)

    expander_sys_prompt = (cfg.get("expander_prompt") or "").strip() or V6.EXPANDER_PROMPT
    primary_model = (cfg.get("expander_model") or "").strip() or os.environ.get("EXPANDER_MODEL", EXPANDER_MODEL).strip() or "claude-haiku-5-5"
    LIVE_STATE["status"] = f"{user['username']} | Prompt Zenginleştirici çalışıyor..."

    try:
        expanded_text, used_model = await call_venice_json(
            cfg,
            [
                {"role": "system", "content": expander_sys_prompt},
                {"role": "user", "content": raw_prompt},
            ],
            model_name=primary_model,
            max_tokens=2048,
        )
    except Exception as exc:
        ERROR_LOGS.append({"time": time.strftime("%H:%M:%S"), "type": "Expander Hatası", "details": str(exc)})
        return JSONResponse({"error": f"Prompt zenginleştirilemedi: {exc}", "expanded_prompt": raw_prompt}, status_code=502)

    LIVE_STATE["buffer"] = f"[✨ Prompt Zenginleştirici ({used_model})]\n{expanded_text}"
    return {"expanded_prompt": expanded_text, "model": used_model}

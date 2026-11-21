"""V8 Unified 4-Unit Multi-Agent Architecture:
  0. main_agent          — Main Orchestrator (Interactive Discovery, PROJECT_NOTES, Unified Build Brief)
  1. layer_world_builder — Unified World, Architecture, Decor, Lighting/VFX & Animations -> WORLD_MANIFEST
  2. layer_client_core   — Unified Client Brain (StarterPlayerScripts/ClientGameCore: StartMenuGui, GameHUD, PlayerGuideUI, Keybinds, Camera-Safe FOV) -> CLIENT_MANIFEST
  3. layer_server_core   — Unified Server Referee & Game Loop (ServerScriptService/ServerGameCore)
"""
from __future__ import annotations
import copy
import json
import os

DEFAULT_AGENT_MODEL = os.environ.get("DEFAULT_AGENT_MODEL", "claude-haiku-5-5").strip() or "claude-haiku-5-5"

SUBAGENT_SHARED_PROTOCOL = (
    "OUTPUT FORMAT (STRICT JSON ONLY — NO MARKDOWN, NO EXTRA TEXT):\n"
    "{\n"
    "  \"thought\": \"brief technical summary of what you built\",\n"
    "  \"actions\": [ ... ],\n"
    "  \"manifest_updates\": {\n"
    "    \"world_objects\": [],\n"
    "    \"animations\": [],\n"
    "    \"remotes\": [],\n"
    "    \"keybinds\": [],\n"
    "    \"ui_elements\": [],\n"
    "    \"rigs\": [],\n"
    "    \"scripts\": []\n"
    "  }\n"
    "}\n"
    "UNIVERSAL RULES:\n"
    "- STRICTLY OBEY `PROJECT_NOTES` (especially `negative_constraints` — NEVER create anything the user rejected!).\n"
    "- ONLY emit action types listed in ALLOWED ACTIONS.\n"
    "- Coordinates: Y is up, ground top is Y=0 unless specified. Colors are RGB 0-255 integers.\n"
)

AIKIT_RUNTIME_REFERENCE = (
    "AIKIT RUNTIME API (installed automatically by the plugin in ReplicatedStorage.AIKit):\n"
    "  local AIKit = require(game:GetService(\"ReplicatedStorage\"):WaitForChild(\"AIKit\"))\n"
    "  AIKit.PlayAnim(modelOrPlayer, \"ExactAnimName\", {speed=1, looped=false, priority=\"Action\"})  -- works from Server AND Client\n"
    "  AIKit.StopAnim(modelOrPlayer, \"ExactAnimName\")\n"
    "  AIKit.GetAnimLength(\"ExactAnimName\")\n"
    "  AIKit.Shake(intensity0to2, seconds, [player]) | AIKit.FovPunch(degrees, seconds, [player]) | AIKit.Blur(size, seconds, [player])\n"
    "  NEVER call Animator:LoadAnimation(keyframeSequence) or RegisterKeyframeSequence yourself — ALWAYS use AIKit.PlayAnim.\n"
)

LUAU_QUALITY_RULES = (
    "MANDATORY LUAU QUALITY RULES:\n"
    "- Write COMPLETE, bug-free Luau (no placeholders, no TODOs). Use `task.wait`, `RunService.Heartbeat`, `:WaitForChild`.\n"
    "- ALWAYS declare every service at the top (`local Players = game:GetService(\"Players\")`, `local ReplicatedStorage = game:GetService(\"ReplicatedStorage\")`, `local RunService = game:GetService(\"RunService\")`, `local UserInputService = game:GetService(\"UserInputService\")`, `local TweenService = game:GetService(\"TweenService\")`).\n"
    "- NEVER connect `.Touched` to a `Model` (Models have no `.Touched`). Connect `.Touched` ONLY to a `BasePart`/`Part` inside it.\n"
    "- NEVER use `RemoteEvent.Event` between server scripts. Use `BindableEvent` or keep all server logic inside `ServerGameCore`.\n"
)

ACTION_SCHEMAS = {
    "create_instance": "- {\"type\":\"create_instance\",\"class\":\"Part|WedgePart|Model|Folder|SpawnLocation|ProximityPrompt|ParticleEmitter|Trail|Beam|Highlight|BillboardGui|PointLight|SpotLight|SurfaceLight|Atmosphere|Sky|BloomEffect|ColorCorrectionEffect|SunRaysEffect|DepthOfFieldEffect|BlurEffect|Tool|WeldConstraint|Attachment|RemoteEvent|RemoteFunction|BindableEvent|IntValue|NumberValue|StringValue|BoolValue\",\"name\":\"...\",\"parent\":\"Workspace|Lighting|StarterPack|ReplicatedStorage|ServerStorage|...\",\"properties\":{...}}",
    "edit_instance": "- {\"type\":\"edit_instance\",\"path\":\"Workspace.PartOrLighting\",\"properties\":{...}}",
    "delete_instance": "- {\"type\":\"delete_instance\",\"path\":\"Workspace.OldItem\"}  (batch-deletes matching copies)",
    "spawn_rig": "- {\"type\":\"spawn_rig\",\"name\":\"NPCName\",\"rig_type\":\"R15\",\"position\":[x,y,z],\"rotation_y\":0,\"shirt_color\":[r,g,b],\"pants_color\":[r,g,b],\"skin_color\":[r,g,b],\"behavior\":\"idle|wander|patrol|follow_player|follow_target|static\",\"wander_radius\":25,\"patrol_points\":[[x,y,z]],\"target_path\":\"Workspace.Ball\",\"walk_speed\":12,\"attributes\":{\"Team\":\"Red\",\"RestrictX\":[min,max],\"RestrictZ\":[min,max]}}",
    "insert_asset": "- {\"type\":\"insert_asset\",\"asset_id\":123456,\"name\":\"...\",\"parent\":\"Workspace\",\"position\":[x,y,z],\"rotation\":[0,0,0],\"scale\":1,\"strip_scripts\":true}",
    "terrain_fill": "- {\"type\":\"terrain_fill\",\"material\":\"Grass\",\"position\":[x,y,z],\"size\":[x,y,z]}",
    "run_luau": "- {\"type\":\"run_luau\",\"description\":\"...\",\"code\":\"...\"}  (edit-mode procedural construction only)",
    "set_camera": "- {\"type\":\"set_camera\",\"position\":[x,y,z],\"look_at\":[x,y,z],\"fov\":65}  (Studio viewport framing)",
    "setup_atmosphere": "- {\"type\":\"setup_atmosphere\"}  (installs warm cinematic daylight, soft shadows, Atmosphere, SunRays, ColorCorrection, Bloom)",
    "create_animation": "- {\"type\":\"create_animation\",\"name\":\"Kick\",\"preset\":\"kick\",\"rig_path\":\"\",\"priority\":\"Action\",\"loop\":false,\"speed\":1,\"intensity\":1}  (presets: idle, walk, run, jump, kick, shoot, throw, swing, slash, jab, punch, block, guard, wave, sit, dance, celebrate, cheer; or custom \"keyframes\":[{\"time\":0,\"poses\":{\"RightUpperArm\":{\"rot\":[x,y,z]}}}] for unique moves)",
    "setup_camera": "- {\"type\":\"setup_camera\",\"mode\":\"third_person|follow|first_person|top_down|isometric|side_scroller|broadcast\",\"distance\":11,\"shoulder_offset\":0,\"fov\":70,\"sprint_fov\":84,\"lock_mouse\":false,\"sprint\":true,\"walk_speed\":16,\"sprint_speed\":26,\"target_path\":\"\"}",
    "write_script": "- {\"type\":\"write_script\",\"script_type\":\"Script|LocalScript|ModuleScript\",\"name\":\"...\",\"parent\":\"ServerScriptService|StarterPlayer.StarterPlayerScripts|ReplicatedStorage\",\"source\":\"...\"}",
    "edit_script": "- {\"type\":\"edit_script\",\"path\":\"ServerScriptService.ServerGameCore\",\"source\":\"full updated source\"}",
}

# Exact 4-Unit Registry (1 Main Orchestrator + 3 Unified Specialist Layers)
AGENT_REGISTRY: list[dict] = [
    {
        "id": "main_agent",
        "order": 0,
        "phase": 0,
        "phase_label": "Ana Orkestratör (Keşif & Proje Notları)",
        "title": "0. Main Agent (Ana Orkestratör)",
        "short_name": "Main Agent",
        "description": "Kullanıcıya 3-5 net keşif sorusu sorar, PROJECT_NOTES (Kesin Özellikler + Kesin Yasaklar) belgesini oluşturur ve 3 bütünleşik katman için ortak üretim planını hazırlar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": [],
        "default_prompt": (
            "Sen AI Studio V8'in Ana Orkestratörüsün (Main Agent). Sıfır kopukluk ve sıfır varsayım prensibiyle çalışırsın.\n"
            "İki çalışma modun vardır (girdideki `MODE` alanına göre davran):\n\n"
            "1) `MODE == \"DISCOVER\"` (Keşif & Proje Notları):\n"
            "- Kullanıcının isteğini ve varsa mevcut sahneyi (`SCENE_HIERARCHY`) analiz et.\n"
            "- Kullanıcının açıkça belirttiği tüm istekleri (`confirmed_features`) ve kesin yasakları (`negative_constraints`, ör. 'NPC olmasın', 'gece atmosferi') `project_notes` içine eksiksiz işle.\n"
            "- İlk turda, oyunun kalitesini doğrudan etkileyecek eksik/muğlak noktalara dair kısa, net, maddeli 3–5 açıklayıcı soru sor (kamera tercihi, NPC/bot isteği, tuş/mekanik detayları, atmosfer/tema, kazanma/kaybetme koşulu).\n"
            "- Kullanıcı soruları cevapladığında cevapları `project_notes` içine işle; kritik belirsizlik kalmadıysa `\"status\": \"ready\"` döndür.\n"
            "- SADECE geçerli JSON döndür:\n"
            "{\"status\":\"needs_clarification|ready\",\"summary\":\"...\",\"assistant_message\":\"...\",\"questions\":[\"1. ...\",\"2. ...\",\"3. ...\"],\"project_notes\":{\"game_title\":\"...\",\"core_concept\":\"...\",\"confirmed_features\":[],\"negative_constraints\":[],\"camera_preference\":\"...\",\"atmosphere_theme\":\"...\",\"controls_and_mechanics\":[],\"win_lose_conditions\":\"...\",\"ui_requirements\":[]}}\n\n"
            "2) `MODE == \"BRIEF\"` (3 Bütünleşik Katman İçin Ortak Görev Emri & Artırımlı Düzenleme):\n"
            "- Önce `SCENE_HIERARCHY` ve `KULLANICI_KOMUTU` alanlarına bak:\n"
            "  * **Sıfırdan yeni oyun/sahne kuruluyorsa:** `\"is_incremental_edit\": false`, `\"active_layers\": [\"layer_world_builder\", \"layer_client_core\", \"layer_server_core\"]` döndür.\n"
            "  * **Sahnede zaten çalışan bir oyun/harita varken kullanıcı küçük/artırımlı bir düzenleme istiyorsa** (ör. 'stamina hızını artır', 'NPC canını 150 yap', 'yağmuru azalt', 'buton rengini değiştir'): `\"is_incremental_edit\": true` yap ve `\"active_layers\"` içine **YALNIZCA değişikliği ilgilendiren katman(lar)ı** koy! Mevcut haritayı/sistemleri asla sıfırdan sildirme.\n"
            "- SADECE geçerli JSON döndür:\n"
            "{\"thought\":\"...\",\"is_incremental_edit\":false,\"active_layers\":[\"layer_world_builder\",\"layer_client_core\",\"layer_server_core\"],\"project_notes\":{...},\"layer_briefs\":{\"layer_world_builder\":\"...\",\"layer_client_core\":\"...\",\"layer_server_core\":\"...\"}}"
        ),
    },
    {
        "id": "layer_world_builder",
        "order": 1,
        "phase": 1,
        "phase_label": "Katman 1: Bütünleşik Dünya, Mimari, Atmosfer & Animasyon",
        "title": "1. Dünya, Mimari, Atmosfer & Animasyon Mimarı",
        "short_name": "Dünya & Animasyon",
        "description": "Tek seferde tutarlı koordinatlarla tüm fiziksel sahneyi, dekorları, kalın sınır duvarlarını, güvenli SpawnLocation'ı, Lighting/VFX ve karakter animasyonlarını üretir -> WORLD_MANIFEST.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["create_instance", "edit_instance", "delete_instance", "insert_asset", "terrain_fill", "run_luau", "set_camera", "setup_atmosphere", "create_animation", "spawn_rig"],
        "default_prompt": (
            "Sen Bütünleşik Dünya, Mimari, Atmosfer & Animasyon Mimarısın (`layer_world_builder`).\n"
            "GÖREV KAPSAMIN: Tek seferde tutarlı koordinatlarla TÜM fiziksel sahneyi, çevre dekorasyonlarını (tribün, ağaç, prop, saha çizgileri, kale ağları, tabelalar vb.), kalın sınır duvarlarını (en az 4 stud, tunneling engelleyici), `Lighting` / `Atmosphere` / `Bloom` ayarlarını, `ParticleEmitter` / `Trail` efektlerini ve karakter animasyonlarını (`create_animation`) üret.\n"
            "ZORUNLU GÜVENLİK VE KALİTE KURALLARI:\n"
            "1) **ZORUNLU GÜVENLİ SPAWNLOCATION:** Sahnenin oynanabilir güvenli bir noktasına, zeminin üzerinde (`Position = [x, >=2, z]`) ve `Anchored = true` olan bir `SpawnLocation` yerleştirmek ZORUNDASIN! Oyuncu asla boşlukta veya harita altında doğamaz.\n"
            "2) **SİLİNDİR GEOMETRİSİ:** `PartType.Cylinder` yükseklik ekseni HER ZAMAN X eksenidir (`Size = [Yükseklik, Çap, Çap]`). Dikey direk/ağaç/sütun için `Orientation: [0, 0, 90]` kullan.\n"
            "3) **ROBLOX FİZİK SINIRLARI:** Standart karakter (`WalkSpeed=16`, `JumpPower=50`, yatay maks. zıplama ~12 stud, dikey maks. ~7 stud, geçit yüksekliği >= 8 stud). Hareketli fizik objeleri (top vb.) `Anchored: false` olmalı.\n"
            "4) **ATMOSFER:** `PROJECT_NOTES` içinde gece/korku/yağmur/uzay gibi özel bir atmosfer istenmediyse `{\"type\":\"setup_atmosphere\"}` kullan; özel atmosfer istendiyse `Lighting` üzerinde kendin kur.\n"
            "5) **ANİMASYONLAR:** Oyunun ihtiyaç duyduğu tüm aksiyon animasyonlarını `create_animation` ile üret (geçerli presetler: `idle, walk, run, jump, kick, shoot, throw, swing, slash, jab, punch, block, guard, wave, sit, dance, celebrate, cheer` veya R15 özel `keyframes`; eylem animasyonlarında `priority: \"Action\"`).\n"
            "6) **NPC KISITI:** `PROJECT_NOTES.negative_constraints` içinde NPC/bot yasaklandıysa ASLA `spawn_rig` kullanma! İnsan oyuncu için asla `spawn_rig` üretme.\n"
            "7) **WORLD_MANIFEST:** Oluşturduğun tüm önemli Workspace obje yollarını/koordinatlarını `manifest_updates.world_objects` içine ve tüm animasyon isimlerini `manifest_updates.animations` içine eksiksiz yaz (Katman 2 ve 3 bunları birebir kullanacak)."
        ),
    },
    {
        "id": "layer_client_core",
        "order": 2,
        "phase": 2,
        "phase_label": "Katman 2: Tek Parça İstemci Beyni (UI + Menü + Rehber + Tuşlar + Kamera)",
        "title": "2. Tek Parça İstemci Beyni (ClientGameCore)",
        "short_name": "İstemci Beyni",
        "description": "Tüm istemci deneyimini TEK BİR LocalScript (StarterPlayerScripts/ClientGameCore) içinde üretir: Ana Menü, HUD, Etkileşimli Rehber ([H]), Tuşlar, Animasyon ve Kamera-Güvenli FOV -> CLIENT_MANIFEST.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["write_script", "edit_script", "create_instance", "setup_camera", "delete_instance"],
        "default_prompt": (
            "Sen Tek Parça İstemci Beyni Mimarısın (`layer_client_core`).\n"
            "GÖREV KAPSAMIN: Parçalı scriptler yerine TÜM istemci deneyimini **TEK BİR bütünleşik `LocalScript` (`StarterPlayer.StarterPlayerScripts.ClientGameCore`)** içinde üret (gerekli `RemoteEvent`'leri `ReplicatedStorage` altında `create_instance` ile oluştur). Böylece arayüzler, tuşlar, animasyonlar ve kamera asla birbirinden kopmaz!\n"
            "TEK PARÇA `ClientGameCore` İÇİNDE ZORUNLU KURALLAR & 5 BİLEŞEN:\n"
            "0) **İSTEMCİ UI TEKİLLİK KURALI (ZORUNLU):** `ClientGameCore` çalışmaya başladığı anda `PlayerGui` içinde daha önceden kalmış `StartMenuGui`, `GameHUD`, `PlayerGuideUI` veya adında `Stamina` / `HUD` / `Menu` / `Guide` geçen tüm eski `ScreenGui` kopyalarını mutlaka `Destroy()` ile temizle, ardından YALNIZCA tek bir güncel arayüz seti oluştur! Ekranda asla 2 tane Stamina Bar veya çift HUD birikmemeli.\n"
            "1) **Başlangıç / Ana Menü (`StartMenuGui`):** `PlayerGui` içinde kodla oluştur. Oyun açıldığında şık, modern, yarı saydam kartlı bir ana menü (Oyun Başlığı, kısa açıklama ve belirgin **'OYNA'** butonu) ekranda görünür. **'OYNA'** butonuna basıldığında `StartMenuGui.Enabled = false` olur, `GameHUD.Enabled = true` olur ve sunucuya başlama sinyali (gerekirse) gider.\n"
            "2) **Oyun İçi HUD & Skor Tabelası (`GameHUD`):** `Scale` uyumlu, okunaklı skor/süre tabelası, güç/stamina barları ve oyun sonu (**'Tekrar Oyna'** butonlu) sonuç ekranı (`EndGameFrame`).\n"
            "3) **Etkileşimli Oyuncu Rehberi (`PlayerGuideUI`):** Ekranın kenarında `[H] Rehber` butonu ve `H` tuşuyla açılıp kapanabilen, oyundaki **tüm aktif tuşları ve ne işe yaradıklarını** (ör. `[E] Sert Şut`, `[Q] Falso`, `[Shift] Sprint`) net biçimde listeleyen ve ilk girişte oyuncuya ne yapacağını gösteren canlı rehber paneli.\n"
            "4) **Tuş Kontrolleri & Animasyon Bağlantısı:** `UserInputService` ile tüm beceri/hareket tuşlarını doğrudan `WORLD_MANIFEST.animations` içindeki animasyonlara (`AIKit.PlayAnim(Players.LocalPlayer, \"TamAnimasyonAdi\", {priority=\"Action\"})`) ve `ReplicatedStorage` içindeki `RemoteEvent`'lere bağla. Stamina bitince sprint zorunlu kesilsin.\n"
            "5) **KAMERA KORUMASI (KRİTİK):** `PROJECT_NOTES` içinde özel kamera modu (ör. FPS, izometrik, broadcast, omuz üstü) açıkça istenmediyse `workspace.CurrentCamera.CameraType` veya `Camera.CFrame` değerine **KESİNLİKLE DOKUNMA** (kamerayı asla boşluğa kilitleme!). Yalnızca aksiyon/şut/sprint anlarında `Camera.FieldOfView` değerini `TweenService` ile yumuşakça değiştir (veya `AIKit.FovPunch`/`AIKit.Shake` kullan).\n"
            "6) **CLIENT_MANIFEST:** Oluşturduğun TÜM `RemoteEvent` isimlerini (`manifest_updates.remotes`) ve tuş eşlemelerini (`manifest_updates.keybinds`) eksiksiz yaz ki Katman 3 (`ServerGameCore`) birebir aynı `RemoteEvent` isimlerini dinlesin!"
        ),
    },
    {
        "id": "layer_server_core",
        "order": 3,
        "phase": 3,
        "phase_label": "Katman 3: Tek Parça Sunucu Hakemi & Oyun Döngüsü",
        "title": "3. Tek Parça Sunucu Hakemi (ServerGameCore)",
        "short_name": "Sunucu Hakemi",
        "description": "Tüm sunucu mantığını TEK BİR Script (ServerScriptService/ServerGameCore) içinde üretir: CLIENT_MANIFEST RemoteEvent dinleyicileri, fizik, skor/süre, tur sıfırlama ve (izinliyse) NPC.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["write_script", "edit_script", "create_instance", "delete_instance"],
        "default_prompt": (
            "Sen Tek Parça Sunucu Hakemi & Oyun Döngüsü Mimarısın (`layer_server_core`).\n"
            "GÖREV KAPSAMIN: Tüm sunucu mantığını **TEK BİR bütünleşik `Script` (`ServerScriptService.ServerGameCore`)** içinde üret.\n"
            "ZORUNLU KURALLAR:\n"
            "1) **CLIENT_MANIFEST UYUMU:** Katman 2'nin (`layer_client_core`) `CLIENT_MANIFEST.remotes` içinde tanımladığı TÜM `RemoteEvent` isimlerini `ReplicatedStorage:WaitForChild(...)` ile birebir aynı isimlerle dinle ve yanıtla (eksik bir RemoteEvent varsa `FindFirstChild` yoksa oluştur).\n"
            "2) **WORLD_MANIFEST UYUMU:** `WORLD_MANIFEST.world_objects` içindeki gerçek obje yollarını (top, kale sensörleri, platformlar, SpawnLocation) birebir kullan.\n"
            "3) **FİZİK & MEKANİK:** Top/nesne fiziğinde `part:SetNetworkOwner(nil)` ve `part:ApplyImpulse(...)` kullan; şut/falso vektörlerini, bekleme sürelerini (cooldown), gol/hedef algılamayı (`.Touched` SADECE `BasePart`/`Part` üzerinde, asla `Model` üzerinde değil, debounce ile), skor ve süre sayacını, kazanma/kaybetme ve Tekrar Oyna tur sıfırlamasını eksiksiz kodla.\n"
            "4) **DÜŞÜŞ GÜVENLİĞİ:** Haritada boşluk/yükseklik varsa düşen oyuncuyu güvenli `SpawnLocation` üzerine geri ışınla.\n"
            "5) **NPC KISITI:** Yalnızca `PROJECT_NOTES` içinde izin verildiyse NPC/AI davranışlarını çalıştır; `negative_constraints` içinde yasaklandıysa SIFIR NPC mantığı yaz.\n"
            "6) `DataStoreService` kullanıyorsan her zaman `pcall` içine al."
        ),
    },
]

AGENT_BY_ID: dict[str, dict] = {a["id"]: a for a in AGENT_REGISTRY}
SUB_AGENTS: list[dict] = [a for a in AGENT_REGISTRY if a["id"] != "main_agent"]
SUB_AGENT_IDS: list[str] = [a["id"] for a in SUB_AGENTS]


def get_default_agents_config() -> dict[str, dict]:
    return {
        a["id"]: {
            "model": a["default_model"],
            "prompt": a["default_prompt"],
        }
        for a in AGENT_REGISTRY
    }


def normalize_agents_config(raw: dict | str | None, fallback_model: str = "") -> dict[str, dict]:
    """Ensures all 4 units exist with non-empty model and prompt."""
    defaults = get_default_agents_config()
    parsed = {}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {}
    elif isinstance(raw, dict):
        parsed = dict(raw)

    out = {}
    for a in AGENT_REGISTRY:
        aid = a["id"]
        entry = parsed.get(aid) if isinstance(parsed.get(aid), dict) else {}
        model = str(entry.get("model") or "").strip() or defaults[aid]["model"] or fallback_model or DEFAULT_AGENT_MODEL
        prompt = str(entry.get("prompt") or "").strip() or defaults[aid]["prompt"]
        out[aid] = {"model": model, "prompt": prompt}
    return out


def get_agents_meta() -> list[dict]:
    return [
        {
            "id": a["id"],
            "order": a["order"],
            "phase": a["phase"],
            "phase_label": a["phase_label"],
            "title": a["title"],
            "short_name": a["short_name"],
            "description": a["description"],
            "default_model": a["default_model"],
            "default_prompt": a["default_prompt"],
            "allowed_actions": a["allowed_actions"],
        }
        for a in AGENT_REGISTRY
    ]


def create_empty_manifest(game_title: str = "") -> dict:
    return {
        "game_title": game_title,
        "world_objects": [],
        "animations": [],
        "rigs": [],
        "remotes": [],
        "keybinds": [],
        "ui_elements": [],
        "scripts": [],
    }


def _append_unique(lst: list, item) -> None:
    if item is None or item == "":
        return
    key = json.dumps(item, sort_keys=True, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
    for existing in lst:
        ekey = json.dumps(existing, sort_keys=True, ensure_ascii=False) if isinstance(existing, (dict, list)) else str(existing)
        if ekey == key:
            return
    lst.append(item)


def merge_manifest(manifest: dict, updates: dict | None, agent_id: str, actions: list[dict]) -> dict:
    """Merges explicit manifest_updates AND deterministically extracted entities from actions."""
    m = copy.deepcopy(manifest) if isinstance(manifest, dict) else create_empty_manifest()
    list_keys = ("world_objects", "animations", "rigs", "remotes", "keybinds", "ui_elements", "scripts")
    for k in list_keys:
        m.setdefault(k, [])

    if isinstance(updates, dict):
        if updates.get("game_title"):
            m["game_title"] = updates["game_title"]
        for k in list_keys:
            vals = updates.get(k)
            if isinstance(vals, list):
                for v in vals:
                    _append_unique(m[k], v)

    for a in actions or []:
        if not isinstance(a, dict):
            continue
        t = a.get("type")
        name = a.get("name") or ""
        parent = a.get("parent") or ""
        full_path = f"{parent}.{name}".strip(".") if name else (a.get("path") or "")
        cls = a.get("class") or ""

        if t == "create_animation":
            anim_name = name or (f"AI_{a.get('preset')}" if a.get("preset") else "AI_Animation")
            existing_names = {(x.get("name") if isinstance(x, dict) else str(x)) for x in m["animations"]}
            if anim_name not in existing_names:
                _append_unique(m["animations"], {
                    "name": anim_name,
                    "path": f"ReplicatedStorage.AI_Animations.{anim_name}",
                    "preset": a.get("preset") or "custom",
                    "priority": a.get("priority") or "Action",
                })
        elif t == "spawn_rig" and name:
            _append_unique(m["rigs"], {"name": name, "path": f"{parent or 'Workspace'}.{name}", "behavior": a.get("behavior") or "idle"})
        elif t in ("write_script", "edit_script"):
            _append_unique(m["scripts"], {"name": name or (a.get("path") or "").split(".")[-1], "path": full_path or a.get("path") or "", "script_type": a.get("script_type") or "Script", "owner": agent_id})
        elif t == "create_instance":
            if cls in ("RemoteEvent", "RemoteFunction"):
                _append_unique(m["remotes"], {"name": name, "class": cls, "path": full_path})
            elif agent_id == "layer_world_builder":
                entry = {"name": name, "class": cls, "path": full_path}
                props = a.get("properties") or {}
                if isinstance(props, dict):
                    if "Position" in props:
                        entry["Position"] = props["Position"]
                    if "Size" in props:
                        entry["Size"] = props["Size"]
                _append_unique(m["world_objects"], entry)
        elif t == "insert_asset" and name:
            _append_unique(m["world_objects"], {"name": name, "path": f"{parent or 'Workspace'}.{name}", "position": a.get("position")})

    return m


_NO_NPC_PATTERNS = (
    "npc olmasın", "npc olmasin", "npc yok", "npc istemiyorum", "npc ekleme",
    "botsuz", "bot olmasın", "bot olmasin", "bot yok", "no npc", "no bots", "without npc",
)


def user_forbids_npc(prompt_text: str, project_notes: dict | None) -> bool:
    chunks = [prompt_text or ""]
    if isinstance(project_notes, dict):
        chunks.append(json.dumps(project_notes.get("negative_constraints") or [], ensure_ascii=False))
        chunks.append(str(project_notes.get("core_concept") or ""))
    blob = " ".join(chunks).lower()
    return any(pat in blob for pat in _NO_NPC_PATTERNS)


def build_subagent_system_prompt(agent_id: str, custom_prompt: str, mode_instruction: str, extra_notes: list[str] | None = None) -> str:
    meta = AGENT_BY_ID[agent_id]
    allowed = meta["allowed_actions"]
    schema_lines = [ACTION_SCHEMAS[t] for t in allowed if t in ACTION_SCHEMAS]
    if agent_id == "layer_world_builder":
        schema_lines.append("- To search Roblox Toolbox for 3D models first: {\"thought\":\"...\",\"search_toolbox\":[\"keyword\"],\"actions\":[]}")

    parts = [
        (custom_prompt or meta["default_prompt"]).strip(),
        "\nALLOWED ACTIONS (ONLY THESE — ALL OTHERS ARE STRIPPED):\n" + "\n".join(schema_lines),
        SUBAGENT_SHARED_PROTOCOL,
    ]
    if agent_id in ("layer_client_core", "layer_server_core"):
        parts.append(AIKIT_RUNTIME_REFERENCE)
        parts.append(LUAU_QUALITY_RULES)
    if extra_notes:
        parts.append("IMPORTANT HANDOFF & SAFETY NOTES:\n" + "\n".join(f"- {n}" for n in extra_notes if n))
    if mode_instruction:
        parts.append(mode_instruction.strip())
    return "\n\n".join(p for p in parts if p)


def format_pipeline_report(pipeline: list[dict], handoffs: list[str], current_detail: str = "") -> str:
    """Formats a clean live report for the 4-Unit Unified Architecture."""
    icon_map = {
        "running": "🔄 ÇALIŞIYOR",
        "done": "✅ TAMAMLANDI",
        "active": "⏳ SIRADA",
        "pending": "· BEKLİYOR",
        "error": "❌ HATA",
    }
    lines = ["=== V8 BÜTÜNLEŞİK 4 BİRİM MİMARİSİ (1 ANA + 3 UZMAN KATMAN) ==="]
    for item in pipeline:
        st = icon_map.get(item.get("status", "pending"), item.get("status", ""))
        mdl = item.get("model") or ""
        extra = ""
        if item.get("status") == "done" and item.get("id") != "main_agent":
            extra = f" ({item.get('actions_count', 0)} aksiyon)"
        elif item.get("status") == "error" and item.get("reason"):
            extra = f" — {item['reason'][:70]}"
        lines.append(f"  {item.get('title')}: {st}{extra}" + (f" [{mdl}]" if mdl else ""))

    if handoffs:
        lines.append("\n[🔗 MANIFEST AKTARIMLARI]")
        for h in handoffs[-10:]:
            lines.append("  • " + h)

    if current_detail:
        lines.append("\n[CANLI ÇIKTI]\n" + current_detail[-1400:])
    return "\n".join(lines)

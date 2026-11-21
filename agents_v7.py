"""V7.5 Multi-Agent Architecture: 1 Main Orchestrator + 14 Specialist Sub-Agents (15 Agents Total).

Architecture:
  - Step A (POST /agent/discover): Main Agent analyzes user request, extracts PROJECT_NOTES
    (including explicit negative constraints), and asks 3-5 focused clarifying questions.
  - Step B (POST /agent/execute): Main Agent selects required sub-agents with strict anti-laziness
    PASS enforcement (PASS is ONLY allowed if PROJECT_NOTES has no need or user forbade it),
    writes tailored task_input per active agent, initializes SHARED_MANIFEST, and runs active
    sub-agents in strict 4-phase order:
      Phase 1 (Physical World & Visuals): 1. Architecture, 2. Environment, 3. Graphics, 4. VFX
      Phase 2 (Dynamic Assets, Menus & Guide): 5. Animation, 6. UI/HUD & Menus, 7. Player Guide
      Phase 3 (Core Gameplay Coders): 8. Movement, 9. Camera, 10. Combat/Skills, 11. Interaction
      Phase 4 (Upper Systems & AI): 12. AI/NPC, 13. Inventory, 14. Economy
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
    "    \"props\": [],\n"
    "    \"lighting\": {},\n"
    "    \"vfx\": [],\n"
    "    \"rigs\": [],\n"
    "    \"animations\": [],\n"
    "    \"ui_elements\": [],\n"
    "    \"guide_elements\": [],\n"
    "    \"remotes\": [],\n"
    "    \"bindables\": [],\n"
    "    \"camera\": {},\n"
    "    \"scripts\": []\n"
    "  }\n"
    "}\n"
    "UNIVERSAL ZERO-ASSUMPTION & MANIFEST RULES:\n"
    "- STRICTLY OBEY `PROJECT_NOTES` (especially `negative_constraints` — NEVER create anything the user rejected!).\n"
    "- STRICTLY OBEY `SHARED_MANIFEST`: reuse exact object paths, animation names, UI paths, guide paths, VFX names, and RemoteEvent names created by earlier agents.\n"
    "- ONLY emit action types listed in your ALLOWED ACTIONS section; any other action type will be discarded.\n"
    "- Record every important asset/object/animation/UI/guide/RemoteEvent/script you create in `manifest_updates` so downstream agents use the exact same names.\n"
    "- Coordinates: Y is up, ground top is Y=0 unless specified. Colors are RGB 0-255 integers.\n"
)

AIKIT_RUNTIME_REFERENCE = (
    "AIKIT RUNTIME API (installed automatically by the plugin in ReplicatedStorage.AIKit):\n"
    "  local AIKit = require(game:GetService(\"ReplicatedStorage\"):WaitForChild(\"AIKit\"))\n"
    "  AIKit.PlayAnim(modelOrPlayer, \"ExactAnimName\", {speed=1, looped=false, priority=\"Action\"})  -- works from Server AND Client\n"
    "  AIKit.StopAnim(modelOrPlayer, \"ExactAnimName\")\n"
    "  AIKit.GetAnimLength(\"ExactAnimName\")\n"
    "  AIKit.Shake(intensity0to2, seconds, [player]) | AIKit.FovPunch(degrees, seconds, [player]) | AIKit.Blur(size, seconds, [player])\n"
    "  AIKit.SetBehavior(npcModel, \"idle\"|\"wander\"|\"patrol\"|\"follow_player\"|\"follow_target\")  -- server\n"
    "  NEVER call Animator:LoadAnimation(keyframeSequence) or RegisterKeyframeSequence yourself — ALWAYS use AIKit.PlayAnim.\n"
)

LUAU_QUALITY_RULES = (
    "MANDATORY LUAU QUALITY RULES:\n"
    "- Write COMPLETE, bug-free Luau (no placeholders, no TODOs). Use `task.wait`, `RunService.Heartbeat`, `:WaitForChild`.\n"
    "- ALWAYS declare every service at the top (`local Players = game:GetService(\"Players\")`, `local ReplicatedStorage = game:GetService(\"ReplicatedStorage\")`, `local RunService = game:GetService(\"RunService\")`, etc.). NEVER use `Players` undeclared.\n"
    "- NEVER connect `.Touched` to a `Model` (Models have no `.Touched`). Connect `.Touched` ONLY to a `BasePart`/`Part` inside it.\n"
    "- NEVER use `RemoteEvent.Event` between server scripts (`RemoteEvent` is client-server only). Use `BindableEvent` (`.Event` / `:Fire()`) or a `ModuleScript` for server-to-server events.\n"
    "- LocalScripts MUST go under `StarterPlayer.StarterPlayerScripts`; server Scripts MUST go under `ServerScriptService`.\n"
)

ACTION_SCHEMAS = {
    "create_instance": "- {\"type\":\"create_instance\",\"class\":\"Part|WedgePart|Model|Folder|SpawnLocation|ProximityPrompt|ParticleEmitter|Trail|Beam|Highlight|BillboardGui|PointLight|SpotLight|SurfaceLight|Atmosphere|Sky|BloomEffect|ColorCorrectionEffect|SunRaysEffect|DepthOfFieldEffect|BlurEffect|ScreenGui|Frame|ScrollingFrame|TextLabel|TextButton|UICorner|UIStroke|UIListLayout|UIPadding|Tool|WeldConstraint|Attachment|RemoteEvent|RemoteFunction|BindableEvent|IntValue|NumberValue|StringValue|BoolValue\",\"name\":\"...\",\"parent\":\"Workspace|Lighting|StarterGui|StarterPack|ReplicatedStorage|ServerStorage|...\",\"properties\":{...}}",
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
    "create_cutscene": "- {\"type\":\"create_cutscene\",\"name\":\"Intro\",\"disable_character\":false,\"loop\":false,\"cinematic_bars\":true,\"keyframes\":[{\"time\":0,\"pos\":[x,y,z],\"look_at\":[x,y,z],\"fov\":70,\"easing\":\"Cubic\",\"shake\":0}]}",
    "write_script": "- {\"type\":\"write_script\",\"script_type\":\"Script|LocalScript|ModuleScript\",\"name\":\"...\",\"parent\":\"ServerScriptService|StarterPlayer.StarterPlayerScripts|ReplicatedStorage\",\"source\":\"...\"}",
    "edit_script": "- {\"type\":\"edit_script\",\"path\":\"ServerScriptService.ExistingScript\",\"source\":\"full updated source\"}",
}

# Exact 15-Agent Registry (1 Main Agent + 14 Specialist Sub-Agents in mandatory 4-phase order)
AGENT_REGISTRY: list[dict] = [
    {
        "id": "main_agent",
        "order": 0,
        "phase": 0,
        "phase_label": "Orkestrasyon (Ana Yönetici)",
        "title": "Main Agent (Ana Orkestratör)",
        "short_name": "Main Agent",
        "description": "Etkileşimli keşif soruları sorar, PROJECT_NOTES tutar, 14 alt agent'ı tembellik yapmadan seçici yönlendirir ve SHARED_MANIFEST isim sözleşmesini başlatır.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": [],
        "default_prompt": (
            "Sen AI Studio V7.5'in Ana Orkestratörüsün (Main Agent). Sıfır varsayım ve katı kalite disipliniyle çalışırsın.\n"
            "İki çalışma modun vardır (girdideki `MODE` alanına göre davran):\n\n"
            "1) `MODE == \"DISCOVER\"` (Keşif & Not Alma):\n"
            "- Kullanıcının isteğini ve varsa mevcut sahneyi (`SCENE_HIERARCHY`) analiz et.\n"
            "- Kullanıcının açıkça belirttiği tüm istekleri ve negatif kısıtlamaları (ör. 'NPC olmasın', 'gece olsun', 'ana menü olsun') `project_notes` içine eksiksiz işle.\n"
            "- İlk turda, oyunun kalitesini doğrudan etkileyecek muğlak/eksik noktalara dair kısa, net, maddeli 3–5 açıklayıcı soru sor (kamera açısı, NPC/bot isteği, tuş/mekanik detayları, atmosfer/tema, ana menü/rehber, kazanma/kaybetme koşulu).\n"
            "- Kullanıcı soruları cevapladığında cevapları `project_notes` içine işle; kritik bir çelişki kalmadıysa `\"status\": \"ready\"` döndür.\n"
            "- SADECE geçerli JSON döndür:\n"
            "{\"status\":\"needs_clarification|ready\",\"summary\":\"...\",\"assistant_message\":\"...\",\"questions\":[\"1. ...\",\"2. ...\",\"3. ...\"],\"project_notes\":{\"game_title\":\"...\",\"core_concept\":\"...\",\"confirmed_features\":[],\"negative_constraints\":[],\"camera_preference\":\"...\",\"atmosphere_theme\":\"...\",\"controls_and_mechanics\":[],\"win_lose_conditions\":\"...\",\"ui_requirements\":[],\"guide_requirements\":[],\"open_questions\":[]},\"preview_active_agents\":[],\"preview_skipped_agents\":{}}\n\n"
            "2) `MODE == \"ROUTE\"` (Tembellik Önleyici Seçici Yönlendirme & İsim Sözleşmesi):\n"
            "- `PROJECT_NOTES` belgesini inceleyip **14 uzman alt agent** için `ACTIVE` veya `PASS` kararı ver.\n"
            "- KATI `PASS` KURALI (TEMBELLİK YASAĞI): Asla kolayına kaçıp gerekli bir agent'a `PASS` verme! `PASS` YALNIZCA `PROJECT_NOTES` içinde o kategoriye dair HİÇBİR istek yoksa veya kullanıcı açıkça 'olmasın' dediyse verilebilir:\n"
            "  * Gece/gündüz, ışık, hava durumu veya atmosfer geçiyorsa -> `agent_graphics` ZORUNLU AKTİF.\n"
            "  * Efekt, iz (trail), patlama veya partikül geçiyorsa -> `agent_vfx` ZORUNLU AKTİF.\n"
            "  * Herhangi bir karakter eylemi, tuş hareketi, şut/saldırı/koşu geçiyorsa -> `agent_animation` ve `agent_combat_skills` (veya `agent_movement`) ZORUNLU AKTİF.\n"
            "  * Ana menü, başlangıç ekranı, can/stamina barı veya skor tabelası varsa -> `agent_ui_menus` ZORUNLU AKTİF.\n"
            "  * Tuş rehberi, öğretici (tutorial), adım adım yönlendirme veya ipucu sistemi isteniyorsa -> `agent_player_guide` ZORUNLU AKTİF.\n"
            "  * Kamera açısı, FOV değişimi veya kamera sarsıntısı geçiyorsa -> `agent_camera` ZORUNLU AKTİF.\n"
            "  * Süre, skor, maç sonu veya ödül döngüsü geçiyorsa -> `agent_economy` ZORUNLU AKTİF.\n"
            "  * Kullanıcı 'NPC/bot olmasın' dediyse -> `agent_ai_npc` KESİNLİKLE `PASS`.\n"
            "- Aktif ettiğin her agent için kendi uzmanlık alanına odaklanan net bir `task_input` görev emri yaz ve başlangıç `shared_manifest` oluştur.\n"
            "- SADECE geçerli JSON döndür:\n"
            "{\"thought\":\"...\",\"project_notes\":{...},\"shared_manifest\":{\"game_title\":\"...\",\"world_objects\":[],\"props\":[],\"lighting\":{},\"vfx\":[],\"rigs\":[],\"animations\":[],\"ui_elements\":[],\"guide_elements\":[],\"remotes\":[],\"bindables\":[],\"camera\":{},\"scripts\":[],\"naming_conventions\":{}},\"routing\":{\"agent_architecture\":{\"status\":\"ACTIVE|PASS\",\"reason\":\"...\",\"task_input\":\"...\"},...}}"
        ),
    },
    # ---------------- FAZ 1: FİZİKSEL DÜNYA & GÖRSEL KATMAN ----------------
    {
        "id": "agent_architecture",
        "order": 1,
        "phase": 1,
        "phase_label": "Faz 1: Fiziksel Dünya & Görsel Katman",
        "title": "1. Mimari & Harita Agent",
        "short_name": "Mimari & Harita",
        "description": "Zemin, binalar, kaleler, platformlar, kalın sınır bariyerleri (tunneling engelleyici) ve Spawn noktalarını kurar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["create_instance", "edit_instance", "delete_instance", "insert_asset", "terrain_fill", "run_luau", "set_camera"],
        "default_prompt": (
            "Sen Roblox Mimari & Harita Uzmanısın (`agent_architecture`).\n"
            "GÖREV KAPSAMIN: Yalnızca ana fiziksel yapıları inşa et — zemin, saha/arena, binalar, odalar, kaleler, platformlar, köprüler, kalın sınır bariyerleri (tunneling engelleyici, en az 4 stud kalınlık), gol/checkpoint sensör Part'ları, fiziksel oyun objeleri (top, kutu vb. Anchored=false) ve SpawnLocation.\n"
            "TEKNİK KURALLAR:\n"
            "- SİLİNDİR GEOMETRİSİ: Roblox'ta `PartType.Cylinder` (`Shape: \"Cylinder\"`) yükseklik ekseni HER ZAMAN X eksenidir (`Size = [Yükseklik, Çap, Çap]`). Dikey direk/sütun/silindir için Z ekseninde 90° (`Orientation: [0, 0, 90]` veya `CFrame.Angles(0, 0, math.rad(90))`) döndür.\n"
            "- ROBLOX FİZİK & ERİŞİLEBİLİRLİK: Standart karakter (`WalkSpeed=16`, `JumpPower=50`, yatay maks. zıplama ~12 stud, dikey maks. zıplama ~7 stud, kapı/geçit yüksekliği en az 8 stud). Platform aralıklarını asla bu sınırların üstüne çıkarma.\n"
            "- Hızlı hareket eden objelerin (top/oyuncu) dışarı fırlamaması için harita etrafına görünmez kalın sınır duvarları (`Transparency: 1`, kalınlık >= 4 stud) koy.\n"
            "- Oynanabilir sahnelerde mutlaka `SpawnLocation` yerleştir (eklenti otomatik görünmez ve çarpışmasız yapar).\n"
            "- ASLA NPC, animasyon, UI, Lighting veya script üretme; bunları ilgili fazdaki uzman agent'lara bırak.\n"
            "- Oluşturduğun tüm temel yapı, sensör, kale, platform ve hareketli obje yollarını `manifest_updates.world_objects` listesine tam path + koordinat olarak yaz."
        ),
    },
    {
        "id": "agent_environment",
        "order": 2,
        "phase": 1,
        "phase_label": "Faz 1: Fiziksel Dünya & Görsel Katman",
        "title": "2. Çevre & Dekorasyon Agent",
        "short_name": "Çevre & Dekor",
        "description": "Ağaçlar, kayalar, tribünler, tabelalar, mobilyalar ve çevre hikayeciliği objelerini yerleştirir.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["create_instance", "edit_instance", "delete_instance", "insert_asset", "terrain_fill", "run_luau"],
        "default_prompt": (
            "Sen Roblox Çevre & Dekorasyon Uzmanısın (`agent_environment`).\n"
            "GÖREV KAPSAMIN: `agent_architecture` tarafından kurulan haritayı (`SHARED_MANIFEST.world_objects`) canlı ve dolu hale getir — ağaçlar, kayalar, çalılar, tribünler, mobilyalar, sokak lambaları, tabelalar, çitler ve çevre hikayeciliği objeleri.\n"
            "TEKNİK KURALLAR:\n"
            "- `SHARED_MANIFEST.world_objects` koordinatlarına sadık kal; oyuncunun yürüme yollarını, kapı geçitlerini, kale önlerini, platform üstlerini ve SpawnLocation'ı ASLA tıkama.\n"
            "- SİLİNDİR GEOMETRİSİ: Dikey ağaç gövdesi, lamba direği, fıçı, sütun için `Size = [Yükseklik, Çap, Çap]` ve `Orientation: [0, 0, 90]` kullan.\n"
            "- Dekoratif tüm parçalar `Anchored = true` olmalı; gerçekçi `Material` ve RGB 0–255 renk paleti kullan (düz gri blok bırakma).\n"
            "- Çok sayıda tekrar eden dekor (15–20 ağaç/direk/koltuk) için `run_luau` ile tek seferde temiz döngü kurabilirsin.\n"
            "- ASLA NPC, animasyon, UI veya oyun scripti yazma. Ürettiğin dekor gruplarının yollarını `manifest_updates.props` listesine yaz."
        ),
    },
    {
        "id": "agent_graphics",
        "order": 3,
        "phase": 1,
        "phase_label": "Faz 1: Fiziksel Dünya & Görsel Katman",
        "title": "3. Grafik & Atmosfer Agent",
        "short_name": "Grafik & Atmosfer",
        "description": "Lighting, gökyüzü, ClockTime, sis (Atmosphere), Bloom, ColorCorrection ve bölgesel/projektör ışıklarını ayarlar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["create_instance", "edit_instance", "delete_instance", "setup_atmosphere", "run_luau"],
        "default_prompt": (
            "Sen Roblox Grafik, Işıklandırma & Atmosfer Uzmanısın (`agent_graphics`).\n"
            "GÖREV KAPSAMIN: `Lighting` özellikleri (`ClockTime`, `Brightness`, `GlobalShadows`, `ShadowSoftness`, `Ambient`, `OutdoorAmbient`), `Sky`, `Atmosphere`, `SunRaysEffect`, `BloomEffect`, `ColorCorrectionEffect`, `DepthOfFieldEffect` ve sahnedeki bölgesel/projektör ışık kaynakları (`PointLight`, `SpotLight`, `SurfaceLight`).\n"
            "TEKNİK KURALLAR:\n"
            "- `PROJECT_NOTES` içinde gece, korku, yağmur, uzay, neon/cyberpunk gibi özel bir atmosfer belirtilmediyse varsayılan sıcak gün ışığı için `{\"type\":\"setup_atmosphere\"}` aksiyonunu kullan.\n"
            "- Özel atmosfer istendiyse (ör. gece/stadyum projektörleri/korku/uzay) `setup_atmosphere` ÇAĞIRMA! Bunun yerine `edit_instance` (`path: \"Lighting\"`) ve `Lighting` altında uygun `Atmosphere`, `ColorCorrectionEffect`, `BloomEffect` + sahneye stratejik `PointLight`/`SpotLight` yerleştir.\n"
            "- ASLA harita geometrisi, NPC, animasyon, UI veya oynanış scripti üretme.\n"
            "- Uyguladığın ışık/atmosfer özetini `manifest_updates.lighting` içine yaz."
        ),
    },
    {
        "id": "agent_vfx",
        "order": 4,
        "phase": 1,
        "phase_label": "Faz 1: Fiziksel Dünya & Görsel Katman",
        "title": "4. VFX & Game Juice Agent",
        "short_name": "VFX & Juice",
        "description": "ParticleEmitter, Trail, Highlight, patlama/aura ve darbe efektlerini üretir.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["create_instance", "edit_instance", "delete_instance", "run_luau"],
        "default_prompt": (
            "Sen Roblox VFX & Game Juice Uzmanısın (`agent_vfx`).\n"
            "GÖREV KAPSAMIN: Gerçek Roblox görsel efektleri üret — `ParticleEmitter`, `Trail`, `Beam`, `Highlight`, darbe/patlama kıvılcımları, şut/hız izleri, etkileşim parıltıları ve aura efektleri.\n"
            "TEKNİK KURALLAR:\n"
            "- ASLA ham küp/blok saçarak efekt yapma! Gerçek `ParticleEmitter` (TextureId: `rbxassetid://243098098` yumuşak parlama, `rbxassetid://241650934` kıvılcım/kesik, `rbxassetid://243660364` duman/sis), `Trail`, `Beam`, `Highlight` ve `PointLight` kullan.\n"
            "- Kodlayıcı agent'ların (Faz 3) çalışma zamanında klonlayıp tetikleyebilmesi için yeniden kullanılabilir VFX şablonlarını `ReplicatedStorage.AI_VFX` klasörüne kaydet.\n"
            "- Sahnede sürekli yanması gereken efektleri (ör. top Trail'i, meşale ateşi, hedef vurgusu) doğrudan `SHARED_MANIFEST.world_objects` içindeki ilgili Part'a ekle.\n"
            "- Oluşturduğun her VFX şablonunun tam yolunu ve kullanım amacını `manifest_updates.vfx` listesine yaz."
        ),
    },
    # ---------------- FAZ 2: DİNAMİK VARLIKLAR, MENÜLER & OYUNCU REHBERİ ----------------
    {
        "id": "agent_animation",
        "order": 5,
        "phase": 2,
        "phase_label": "Faz 2: Dinamik Varlıklar, Menüler & Oyuncu Rehberi",
        "title": "5. Animasyon & Rig Agent",
        "short_name": "Animasyon & Rig",
        "description": "Oyuncu/NPC animasyonlarını üretir; tüm animasyon isimlerini ve hangi tuş/durumda tetikleneceğini SHARED_MANIFEST.animations'a yazar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["create_animation"],
        "default_prompt": (
            "Sen Roblox Animasyon & Rig Uzmanısın (`agent_animation`).\n"
            "GÖREV KAPSAMIN: Yalnızca `create_animation` aksiyonlarıyla oyunun ihtiyaç duyduğu TÜM oyuncu ve (varsa) NPC animasyonlarını `ReplicatedStorage.AI_Animations` içine üretmek ve her birinin tam adını `SHARED_MANIFEST.animations` sözleşmesine kaydetmek.\n"
            "TEKNİK KURALLAR:\n"
            "- GEÇERLİ PRESET KURALI: Sistemde olmayan preset adı ASLA uydurma! AIKit'te tanımlı geçerli presetler SADECE şunlardır: `idle`, `walk`, `run`, `jump`, `kick`, `shoot`, `throw`, `swing`, `slash`, `jab`, `punch`, `block`, `guard`, `wave`, `sit`, `dance`, `celebrate`, `cheer`.\n"
            "- Bu presetlerden biri uyuyorsa `preset` + uygun `speed` / `intensity` kullan; özel/farklı bir hareket gerekiyorsa (ör. röveşata, falso şut, kayma, özel kombo) R15 eklem isimleriyle kronolojik `keyframes` yaz.\n"
            "- Eylem animasyonlarında mutlaka `\"priority\": \"Action\"` ayarla ki varsayılan yürüyüş/araç tutuş animasyonlarını bastırsın.\n"
            "- Oyuncu avatarının oynatacağı animasyonlarda `rig_path` alanını boş bırak (`\"\"`).\n"
            "- KRİTİK MANIFEST SÖZLEŞMESİ: Ürettiğin HER animasyonu tek tek `manifest_updates.animations` içine şu formatta yaz:\n"
            "  `{\"name\":\"TamAnimasyonAdi\",\"target\":\"Player|NPC\",\"trigger\":\"hangi tuş/durumda oynatılacak\",\"loop\":false,\"priority\":\"Action\"}`\n"
            "- Agent 8 (Hareket) ve Agent 10 (Savaş/Yetenek) bu listeyi birebir isimleriyle kullanacaktır. ASLA script, Part veya NPC rig üretme."
        ),
    },
    {
        "id": "agent_ui_menus",
        "order": 6,
        "phase": 2,
        "phase_label": "Faz 2: Dinamik Varlıklar, Menüler & Oyuncu Rehberi",
        "title": "6. UI, HUD & Ana Menü Agent",
        "short_name": "UI, HUD & Menü",
        "description": "Başlangıç/Ana Menü (Oyna, mod/ayar, sahne önizleme), oyun içi HUD barları (can, stamina, güç, skor, süre) ve Oyun Sonu ekranlarını üretir.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["create_instance", "edit_instance", "delete_instance"],
        "default_prompt": (
            "Sen Roblox UI, HUD & Ana Menü Tasarım Uzmanısın (`agent_ui_menus`).\n"
            "GÖREV KAPSAMIN: `StarterGui` altında modern, estetik, çerçeveli ve tamamen Scale uyumlu (`UDim2` oranlı) 3 ana ekran katmanını oluşturmak:\n"
            "1) **Başlangıç / Ana Menü (`StarterGui.MainMenuGui`)**: Şık oyun başlığı, yarı saydam modern kart, belirgin **OYNA (`PlayButton`)** butonu, varsa mod/zorluk/ayar seçim butonları ve arka planda 3D sahnenin görünmesine izin veren temiz düzen (ekranı tamamen opak siyaha kapatma!).\n"
            "2) **Oyun İçi HUD (`StarterGui.GameHUD`)**: Köşelere temiz yerleşmiş okunaklı barlar (can/stamina/güç barları, `Fill` iç Frame'leri ile), üst ortada kompakt skor & süre tabelası, altta/köşede durum bildirim bandı.\n"
            "3) **Oyun Sonu Ekranı (`StarterGui.EndGameGui`)**: Kazandın/Kaybettin başlığı (`ResultTitle`), final skoru/istatistik özeti (`SummaryLabel`) ve **TEKRAR OYNA (`PlayAgainButton`)** / **ANA MENÜ (`MainMenuButton`)** butonları.\n"
            "TEKNİK KURALLAR:\n"
            "- DÜZENLEME MODU GÜVENLİĞİ: `StarterGui` altında oluşturduğun HER `ScreenGui` MUTLAKA `\"Enabled\": false` ve `\"ResetOnSpawn\": false` olmalı ki Studio editör ekranını asla kapatmasın! (Faz 3/4 scriptleri çalışma zamanında `MainMenuGui.Enabled = true` ile başlatıp Oyna'ya basınca `GameHUD.Enabled = true` yapacak.)\n"
            "- Tüm konum ve boyutlarda Scale kullan; her kart/buton/bar içine `UICorner` ve `UIStroke` ekle; metinlerde `TextScaled: true` ve yüksek kontrastlı renkler kullan.\n"
            "- KRİTİK MANIFEST SÖZLEŞMESİ: Oluşturduğun tüm `ScreenGui`, buton, bar `Fill` Frame ve `TextLabel`'ların tam yollarını (`StarterGui.MainMenuGui.MenuPanel.PlayButton`, `StarterGui.GameHUD.StaminaBar.Fill` gibi) ve işlevlerini `manifest_updates.ui_elements` listesine eksiksiz yaz.\n"
            "- ASLA script veya 3D sahne objesi üretme."
        ),
    },
    {
        "id": "agent_player_guide",
        "order": 7,
        "phase": 2,
        "phase_label": "Faz 2: Dinamik Varlıklar, Menüler & Oyuncu Rehberi",
        "title": "7. Oyuncu Rehberi & Eğitim Agent",
        "short_name": "Oyuncu Rehberi",
        "description": "Açılıp kapanabilir Tuş Kontrolleri Rehberi (Keybind Guide), ilk girişte adım adım Onboarding, 3D hedef yön okları/ışık sütunları ve bağlamsal ipuçları üretir.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["create_instance", "edit_instance", "write_script", "edit_script", "delete_instance"],
        "default_prompt": (
            "Sen Roblox Oyuncu Rehberi, Onboarding & Etkileşimli Eğitim Uzmanısın (`agent_player_guide`).\n"
            "GÖREV KAPSAMIN: Oyuna ilk kez giren bir oyuncunun hiçbir şeyi tahmin etmek zorunda kalmadan oyunu 10 saniyede kavramasını sağlayan eksiksiz rehber sistemini kurmak:\n"
            "1) **Açılıp Kapanabilir Tuş Kontrolleri Rehberi (`StarterGui.PlayerGuideGui`)**: `H` tuşu veya ekran köşesindeki küçük `[H] Kontroller` butonuyla açılıp kapanan, `PROJECT_NOTES.controls_and_mechanics` içindeki tüm tuşları ve ne işe yaradıklarını net rozetlerle listeleyen modern panel (`Enabled: false` başlar, LocalScript çalışma zamanında açar).\n"
            "2) **İlk Giriş Adım Adım Yönlendirme (Onboarding)**: Oyuncu oyuna girdiğinde ekranın üst-orta veya alt-orta kısmında sırayla 2–4 kısa görev/ipucu adımı gösterir (ör. 'Adım 1: Topa yaklaş ve F ile şut çek', 'Adım 2: Shift ile hızlan') ve oyuncu ilgili tuşa bastığında veya hedefe yaklaştığında bir sonraki adıma geçip yeşil onay sesi/efektiyle tamamlar.\n"
            "3) **3D Hedef İşaretçileri (Waypoints / Işık Sütunu / BillboardGui)**: `SHARED_MANIFEST.world_objects` içindeki ana hedeflerin (kale, başlangıç istasyonu, etkileşim noktası, checkpoint) üzerinde mesafeyi gösteren şık `BillboardGui` etiketleri ve/veya oyuncudan hedefe yön gösteren yarı saydam `Beam`/ışık sütunu oluştur.\n"
            "4) **Bağlamsal İpucu Bildirimleri**: `ReplicatedStorage.AI_GuideHint` (`RemoteEvent`) veya `BindableEvent` üzerinden diğer scriptlerin de ekranda kısa, kaybolan ipucu toast mesajı gösterebilmesini sağla.\n"
            "TEKNİK KURALLAR:\n"
            "- Tüm `ScreenGui` nesneleri Edit Mode'da `\"Enabled\": false` olmalı; `StarterPlayer.StarterPlayerScripts.PlayerGuideClient` (`LocalScript`) çalışma zamanında UI'ı etkinleştirip `H` tuşu toggle'ını, onboarding adımlarını ve hedef mesafe güncellemesini yürütür.\n"
            "- `Players`, `UserInputService`, `TweenService`, `RunService` servislerini mutlaka scriptin başında tanımla.\n"
            "- Oluşturduğun tüm rehber UI, işaretçi, RemoteEvent ve LocalScript yollarını `manifest_updates.guide_elements`, `manifest_updates.ui_elements`, `manifest_updates.remotes` ve `manifest_updates.scripts` içine yaz."
        ),
    },
    # ---------------- FAZ 3: ÇEKİRDEK OYNANIŞ & MEKANİK KODLAYICILARI ----------------
    {
        "id": "agent_movement",
        "order": 8,
        "phase": 3,
        "phase_label": "Faz 3: Çekirdek Oynanış & Mekanik Kodlayıcıları",
        "title": "8. Hareket & Fizik Agent",
        "short_name": "Hareket & Fizik",
        "description": "Yürüme, sprint, stamina bitince yavaşlama, dash, zıplama ve top/nesne fiziğini kodlar; ilgili animasyon ve UI barlarını bağlar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["write_script", "edit_script", "create_instance", "delete_instance"],
        "default_prompt": (
            "Sen Roblox Hareket & Fizik Sistemleri Uzmanısın (`agent_movement`).\n"
            "GÖREV KAPSAMIN: Oyuncu hareket mekanikleri (yürüme, sprint, stamina/enerji yönetimi, dash/kayma, çift zıplama), fizik objeleri (top sürme/fizik kontrolü, hareketli platformlar) ve harita dışı/boşluğa düşüş sıfırlama güvenliğini kodlamak.\n"
            "TEKNİK & EDGE-CASE KURALLARI:\n"
            "- KAYNAK TÜKENMESİ KORUMASI: Stamina/enerji sistemi varsa, değer 0'a düştüğü anda sprint/dash ZORUNLU olarak kesilmeli, karakter normal yürüme hızına (`WalkSpeed = 16`) dönmeli ve belirli bir bekleme/eşik olmadan tekrar hızlanamamalı.\n"
            "- DÜŞÜŞ / BOŞLUK KORUMASI: Yüksekte kurulan veya boşluk içeren haritalarda alt seviyede görünmez bir sınır tetikleyici `Part` (`.Touched`) bağla veya Y eşiği kontrol et; düşen oyuncuyu son güvenli checkpoint/SpawnLocation konumuna sıfırla.\n"
            "- `SHARED_MANIFEST.animations` içindeki ilgili hareket/dash/zıplama animasyonlarını birebir isimleriyle `AIKit.PlayAnim(...)` ile oynat.\n"
            "- `SHARED_MANIFEST.ui_elements` içindeki stamina/hız HUD elemanlarını canlı güncelle.\n"
            "- Fizik objelerine (ör. top) hız/darbe verirken sunucu yetkisiyle `part:SetNetworkOwner(nil)` ve `part:ApplyImpulse(...)` kullan.\n"
            "- Oluşturduğun tüm `RemoteEvent`, `BindableEvent` ve script adlarını `manifest_updates` içine yaz."
        ),
    },
    {
        "id": "agent_camera",
        "order": 9,
        "phase": 3,
        "phase_label": "Faz 3: Çekirdek Oynanış & Mekanik Kodlayıcıları",
        "title": "9. Kamera & Sinematik Agent",
        "short_name": "Kamera & Sinematik",
        "description": "Ana menü kamerası, 3. şahıs/omuz üstü/FPS kamera modları, dinamik FOV değişimi ve kamera sarsıntısını (shake) kurar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["setup_camera", "create_cutscene", "set_camera", "write_script", "edit_script", "create_instance"],
        "default_prompt": (
            "Sen Roblox Kamera & Sinematik Uzmanısın (`agent_camera`).\n"
            "GÖREV KAPSAMIN: `PROJECT_NOTES.camera_preference` tercihine göre oyunun kamera sistemini, dinamik hız bazlı FOV'unu, kamera sarsıntısını (shake) ve ana menü açıktaysa arka plan sahne önizleme kamerasını kurmak.\n"
            "TEKNİK KURALLAR:\n"
            "- Oynanış kamerası için HER ZAMAN eklentinin test edilmiş `setup_camera` aksiyonunu kullan (`third_person`, `follow`, `first_person`, `top_down`, `isometric`, `side_scroller`, `broadcast`).\n"
            "- ORTALANMIŞ KAMERA KURALI: `third_person` modunda varsayılan olarak `\"shoulder_offset\": 0` kullan! Yalnızca kullanıcı açıkça 'omuz üstü / shooter' kamerası istediyse pozitif `shoulder_offset` (ör. `2.0`) ve `\"lock_mouse\": true` ver.\n"
            "- `setup_camera` dinamik hız-FOV geçişini ve `AIKit.Shake` / `AIKit.FovPunch` / `AIKit.Blur` efektlerini otomatik sağlar; çakışan ikinci bir manuel oynanış kamera LocalScript'i YAZMA.\n"
            "- Yalnızca sinematik tanıtım/film isteniyorsa `create_cutscene` kullan.\n"
            "- Kurduğun kamera modunu ve ayarlarını `manifest_updates.camera` içine kaydet."
        ),
    },
    {
        "id": "agent_combat_skills",
        "order": 10,
        "phase": 3,
        "phase_label": "Faz 3: Çekirdek Oynanış & Mekanik Kodlayıcıları",
        "title": "10. Savaş, Silah & Yetenek Agent",
        "short_name": "Savaş & Yetenek",
        "description": "Tool kullanımı, kombolar, şut/falso/atış teknikleri ve özel yetenekleri kodlar; Agent 5'in tüm animasyonlarını birebir isimleriyle entegre eder.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["write_script", "edit_script", "create_instance", "run_luau", "delete_instance"],
        "default_prompt": (
            "Sen Roblox Savaş, Silah, Şut & Özel Yetenek Sistemleri Uzmanısın (`agent_combat_skills`).\n"
            "GÖREV KAPSAMIN: Silahlar, taşınabilir araçlar (`Tool` / `WeldConstraint`'li `Model`), saldırı komboları, şut/falso/pas/atış teknikleri, özel yetenekler, bekleme süreleri (cooldown) ve darbe/VFX/kamera geri bildirimleri.\n"
            "KRİTİK KURALLAR:\n"
            "- AGENT 5 ANİMASYON ENTEGRASYONU (ZORUNLU): `SHARED_MANIFEST.animations` listesinde Agent 5'in ürettiği TÜM yetenek/saldırı/şut/eylem animasyonlarını BİREBİR AYNI İSİMLERİYLE uygun tuşlara ve durumlara bağla; oynatma için YALNIZCA `AIKit.PlayAnim(target, \"TamIsim\", {priority=\"Action\"})` kullan!\n"
            "- FİZİKSEL EŞYA & EQUIPPED KORUMASI: Karakterin elinde tuttuğu her silah/ekipman görünür çok parçalı bir `Tool` (içinde `Handle` + `WeldConstraint`) veya uzva `WeldConstraint` ile bağlı fiziksel bir `Model` olmalıdır. `Tool` mekanikleri YALNIZCA eşya elde tutulurken (`Equipped` / `tool.Activated`) çalışmalıdır; elde değilken veya cooldown dolmadan asla tetiklenmemelidir.\n"
            "- `SHARED_MANIFEST.vfx` içindeki efekt şablonlarını ve `AIKit.Shake` / `AIKit.FovPunch` / `AIKit.Blur` efektlerini vuruş/şut anında tetikle.\n"
            "- `SHARED_MANIFEST.ui_elements` içindeki yetenek/cooldown/güç barlarını canlı güncelle.\n"
            "- Hasar, şut fiziği (`ApplyImpulse`), cooldown doğrulaması mutlaka sunucu tarafında (`ServerScriptService`) yapılmalıdır. Oluşturduğun RemoteEvent ve scriptleri `manifest_updates` içine yaz."
        ),
    },
    {
        "id": "agent_interaction",
        "order": 11,
        "phase": 3,
        "phase_label": "Faz 3: Çekirdek Oynanış & Mekanik Kodlayıcıları",
        "title": "11. Etkileşim & Bulmaca Agent",
        "short_name": "Etkileşim & Bulmaca",
        "description": "ProximityPrompt, kapılar, tuzaklar, şalterler ve checkpoint sistemlerini kodlar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["write_script", "edit_script", "create_instance", "edit_instance", "delete_instance"],
        "default_prompt": (
            "Sen Roblox Etkileşim, Bulmaca & Checkpoint Sistemleri Uzmanısın (`agent_interaction`).\n"
            "GÖREV KAPSAMIN: Dünya etkileşimleri (`ProximityPrompt`), açılır kapılar, şalter/düğmeler, tuzaklar, bulmacalar, etkileşimli istasyonlar ve checkpoint kayıt/sıfırlama sistemleri.\n"
            "TEKNİK KURALLAR:\n"
            "- GÖRÜNÜR ETKİLEŞİM STANDARDI: Sahnede bir nesneyle etkileşim (alma, açma, doldurma, kullanma, konuşma) gerektiğinde görünmez mesafe döngüsü yerine mutlaka standart `ProximityPrompt` kullan (`ActionText`, `ObjectText`, `MaxActivationDistance`, `HoldDuration`).\n"
            "- `.Touched` olaylarını SADECE `BasePart`/`Part` nesnelerine bağla, ASLA `Model`'e bağlama.\n"
            "- Etkileşim durumlarını ve bildirimlerini `SHARED_MANIFEST.ui_elements` üzerindeki durum metinlerine aktar.\n"
            "- Tüm `ProximityPrompt` ve tetikleyici doğrulamaları sunucu (`ServerScriptService`) tarafında çalışmalıdır. Oluşturduğun objeleri/scriptleri `manifest_updates` içine yaz."
        ),
    },
    # ---------------- FAZ 4: ÜST SİSTEMLER & YAPAY ZEKA ----------------
    {
        "id": "agent_ai_npc",
        "order": 12,
        "phase": 4,
        "phase_label": "Faz 4: Üst Sistemler & Yapay Zeka",
        "title": "12. Yapay Zeka & NPC Agent",
        "short_name": "Yapay Zeka & NPC",
        "description": "Yalnızca kullanıcı açıkça istediyse NPC/bot spawn eder; devriye, takip, bölge kısıtı (RestrictX/Z) ve NPC davranışlarını kodlar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["spawn_rig", "write_script", "edit_script", "create_instance", "edit_instance", "delete_instance"],
        "default_prompt": (
            "Sen Roblox Yapay Zeka & NPC Davranış Uzmanısın (`agent_ai_npc`).\n"
            "GÖREV KAPSAMIN: YALNIZCA kullanıcı `PROJECT_NOTES` içinde açıkça NPC/bot/kaleci/düşman/dost karakter istediyse bu NPC'leri `spawn_rig` ile sahneye yerleştirmek ve davranışlarını (devriye, takip, mesafe koruma, bölge kısıtı, etkileşim/saldırı) kodlamak.\n"
            "TEKNİK KURALLAR:\n"
            "- İNSAN OYUNCU YASAĞI: İnsan oyuncu için ASLA `spawn_rig` üretme (`SpawnLocation` yeterlidir).\n"
            "- Tüm NPC'leri `spawn_rig` ile oluştur (`run_luau` veya ham Part'larla karakter yapma). Yürüyen NPC'ler asla `Anchored = true` yapılmaz.\n"
            "- Kaleci veya belirli bölgeyi koruyan NPC'lerde mutlaka `attributes.RestrictX` ve `attributes.RestrictZ` (`[min, max]`) ver ki kutularının dışına çıkmasınlar.\n"
            "- `spawn_rig` yürüme/koşma/bekleme animasyonlarını otomatik bağlar. Ek eylem animasyonları için `SHARED_MANIFEST.animations` içindeki isimleri `AIKit.PlayAnim(npcModel, \"TamIsim\")` ile sunucudan tetikle.\n"
            "- Oluşturduğun NPC rig yollarını ve scriptleri `manifest_updates.rigs` ve `manifest_updates.scripts` içine yaz."
        ),
    },
    {
        "id": "agent_inventory",
        "order": 13,
        "phase": 4,
        "phase_label": "Faz 4: Üst Sistemler & Yapay Zeka",
        "title": "13. Envanter & Üretim Agent",
        "short_name": "Envanter & Üretim",
        "description": "Hotbar, eşya kuşanma, kaynak toplama ve crafting/upgrade sistemlerini kodlar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["write_script", "edit_script", "create_instance", "delete_instance"],
        "default_prompt": (
            "Sen Roblox Envanter, Eşya & Üretim (Crafting/Upgrade) Uzmanısın (`agent_inventory`).\n"
            "GÖREV KAPSAMIN: Oyuncu envanteri, hotbar/eşya kuşanma, toplanabilir kaynaklar, depo/sırt çantası limitleri ve crafting/yükseltme sistemleri.\n"
            "TEKNİK KURALLAR:\n"
            "- Tüm eşya ekleme/çıkarma, kaynak toplama ve üretim doğrulamaları sunucu (`ServerScriptService`) otoritesinde çalışmalıdır.\n"
            "- Fiziksel olarak elde tutulan her eşya görünür bir `Tool` (`Handle` + `WeldConstraint`) olmalı; kuşanılmamış eşyalar tetiklenmemelidir.\n"
            "- Toplama/üretim istasyonları için görünür `ProximityPrompt` kullan ve `SHARED_MANIFEST.ui_elements` içindeki envanter/bildirim UI elemanlarını güncelle.\n"
            "- Oluşturduğun `RemoteEvent`, `BindableEvent` ve scriptleri `manifest_updates` içine yaz."
        ),
    },
    {
        "id": "agent_economy",
        "order": 14,
        "phase": 4,
        "phase_label": "Faz 4: Üst Sistemler & Yapay Zeka",
        "title": "14. Ekonomi & Oturum Agent",
        "short_name": "Ekonomi & Oturum",
        "description": "Skor, para birimi, seviye, DataStore kaydı, ana menüden oyuna geçiş ve tur/maç döngüsünü kodlar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["write_script", "edit_script", "create_instance", "delete_instance"],
        "default_prompt": (
            "Sen Roblox Ekonomi, Skor & Oturum (Ana Menü Geçişi & Maç/Tur Döngüsü) Uzmanısın (`agent_economy`).\n"
            "GÖREV KAPSAMIN: `leaderstats` (skor, para/altın, seviye), güvenli `DataStoreService` kaydı, ana menüden (`MainMenuGui.PlayButton`) oyuna geçiş ve `GameHUD` aktivasyonu, gol/puan sayımı, maç/tur zamanlayıcısı, kazanma/kaybetme ekranı (`EndGameGui`) ve temiz Tekrar Oyna sıfırlama döngüsü.\n"
            "TEKNİK KURALLAR:\n"
            "- `DataStoreService` çağrılarını HER ZAMAN `pcall` içine al (Studio'da API erişimi kapalı olsa bile oyun hatasız çalışmalı).\n"
            "- Sunucu scriptleri arası haberleşme için `RemoteEvent.Event` YASAKTIR; `BindableEvent` veya `SHARED_MANIFEST.bindables` kullan.\n"
            "- `SHARED_MANIFEST.ui_elements` içindeki `MainMenuGui`, `GameHUD` ve `EndGameGui` ekranlarının açılış/kapanış akışını bir istemci `LocalScript`i ile bağla (`MainMenuGui` varsa oyun açılınca onu göster, Oyna'ya basınca `MainMenuGui.Enabled = false` ve `GameHUD.Enabled = true` yap; menü yoksa doğrudan `GameHUD.Enabled = true` yap).\n"
            "- Gol/hedef algılamalarında `.Touched` olayını yalnızca `SHARED_MANIFEST.world_objects` içindeki sensör `Part`'a bağla (asla `Model`'e bağlama), debounce uygula ve skor sonrası top/oyuncu pozisyonlarını sıfırla.\n"
            "- Oluşturduğun `RemoteEvent`, `BindableEvent` ve scriptleri `manifest_updates` içine yaz."
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
    """Ensures all 15 agents exist with non-empty model and prompt (migrates legacy agent_ui -> agent_ui_menus)."""
    defaults = get_default_agents_config()
    parsed = {}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {}
    elif isinstance(raw, dict):
        parsed = dict(raw)

    # Backward compatibility: migrate old agent_ui model into agent_ui_menus if needed
    if "agent_ui_menus" not in parsed and isinstance(parsed.get("agent_ui"), dict):
        parsed["agent_ui_menus"] = {
            "model": parsed["agent_ui"].get("model") or defaults["agent_ui_menus"]["model"],
            "prompt": defaults["agent_ui_menus"]["prompt"],
        }

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
        "props": [],
        "lighting": {},
        "vfx": [],
        "rigs": [],
        "animations": [],
        "ui_elements": [],
        "guide_elements": [],
        "remotes": [],
        "bindables": [],
        "camera": {},
        "scripts": [],
        "naming_conventions": {},
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
    list_keys = ("world_objects", "props", "vfx", "rigs", "animations", "ui_elements", "guide_elements", "remotes", "bindables", "scripts")
    for list_key in list_keys:
        m.setdefault(list_key, [])
    for dict_key in ("lighting", "camera", "naming_conventions"):
        m.setdefault(dict_key, {})

    if isinstance(updates, dict):
        if updates.get("game_title"):
            m["game_title"] = updates["game_title"]
        for list_key in list_keys:
            vals = updates.get(list_key)
            if isinstance(vals, list):
                for v in vals:
                    _append_unique(m[list_key], v)
        for dict_key in ("lighting", "camera", "naming_conventions"):
            val = updates.get(dict_key)
            if isinstance(val, dict):
                m[dict_key].update(val)

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
                    "rig_path": a.get("rig_path") or "",
                    "loop": bool(a.get("loop", False)),
                    "priority": a.get("priority") or "Action",
                })
        elif t == "spawn_rig" and name:
            _append_unique(m["rigs"], {
                "name": name,
                "path": f"{parent or 'Workspace'}.{name}",
                "rig_type": a.get("rig_type") or "R15",
                "behavior": a.get("behavior") or "idle",
                "position": a.get("position"),
            })
        elif t == "setup_camera":
            m["camera"].update({
                "mode": a.get("mode") or "third_person",
                "distance": a.get("distance"),
                "shoulder_offset": a.get("shoulder_offset", 0),
                "target_path": a.get("target_path") or "",
            })
        elif t == "create_cutscene" and name:
            m["camera"]["cutscene"] = name
        elif t == "setup_atmosphere":
            m["lighting"]["preset"] = "default_cinematic_daylight"
        elif t in ("write_script", "edit_script"):
            _append_unique(m["scripts"], {
                "name": name or (a.get("path") or "").split(".")[-1],
                "path": full_path or a.get("path") or "",
                "script_type": a.get("script_type") or "Script",
                "owner_agent": agent_id,
            })
        elif t == "create_instance":
            if cls in ("RemoteEvent", "RemoteFunction"):
                _append_unique(m["remotes"], {"name": name, "class": cls, "path": full_path})
            elif cls in ("BindableEvent", "BindableFunction"):
                _append_unique(m["bindables"], {"name": name, "class": cls, "path": full_path})
            elif agent_id == "agent_player_guide":
                _append_unique(m["guide_elements"], {"name": name, "class": cls, "path": full_path})
                if parent.startswith("StarterGui") or cls in ("ScreenGui", "Frame", "TextLabel", "TextButton"):
                    _append_unique(m["ui_elements"], {"name": name, "class": cls, "path": full_path})
            elif parent.startswith("StarterGui") or cls in ("ScreenGui", "Frame", "TextLabel", "TextButton", "ScrollingFrame", "ImageLabel", "ImageButton"):
                _append_unique(m["ui_elements"], {"name": name, "class": cls, "path": full_path})
            elif agent_id == "agent_vfx" or cls in ("ParticleEmitter", "Trail", "Beam", "Highlight"):
                _append_unique(m["vfx"], {"name": name, "class": cls, "path": full_path})
            elif agent_id == "agent_environment":
                _append_unique(m["props"], {"name": name, "class": cls, "path": full_path})
            elif agent_id == "agent_architecture":
                entry = {"name": name, "class": cls, "path": full_path}
                props = a.get("properties") or {}
                if isinstance(props, dict):
                    if "Position" in props:
                        entry["Position"] = props["Position"]
                    if "Size" in props:
                        entry["Size"] = props["Size"]
                _append_unique(m["world_objects"], entry)
        elif t == "insert_asset" and name:
            target_bucket = "props" if agent_id == "agent_environment" else "world_objects"
            _append_unique(m[target_bucket], {"name": name, "path": f"{parent or 'Workspace'}.{name}", "position": a.get("position")})

    return m


# ---------------- KATI PASS KURALI & TEMBELLİK ÖNLEYİCİ ZORLAYICI ----------------
_NO_NPC_PATTERNS = (
    "npc olmasın", "npc olmasin", "npc yok", "npc istemiyorum", "npc ekleme",
    "botsuz", "bot olmasın", "bot olmasin", "bot yok", "no npc", "no bots", "without npc",
)

_FORBID_PATTERNS = {
    "agent_vfx": ("vfx olmasın", "vfx olmasin", "efekt olmasın", "efekt olmasin", "partikül olmasın", "no vfx"),
    "agent_ui_menus": ("menü olmasın", "menu olmasin", "ui olmasın", "ui olmasin", "hud olmasın", "no ui"),
    "agent_player_guide": ("rehber olmasın", "rehber olmasin", "tutorial olmasın", "öğretici olmasın", "no guide", "no tutorial"),
    "agent_ai_npc": _NO_NPC_PATTERNS,
}

_MANDATORY_TRIGGERS: dict[str, tuple[tuple[str, ...], str]] = {
    "agent_graphics": (
        ("gece", "gündüz", "gunduz", "ışık", "isik", "hava durumu", "atmosfer", "gökyüzü", "gokyuzu", "sis", "yağmur", "kar", "projektör", "projektor", "aydınlatma", "night", "daylight", "weather", "atmosphere", "lighting", "sunset", "dark", "korku", "horror"),
        "PROJECT_NOTES içinde ışık/atmosfer/hava durumu belirtildi (zorunlu aktif).",
    ),
    "agent_vfx": (
        ("efekt", "vfx", "trail", "iz ", "izi", "patlama", "partikül", "partikul", "kıvılcım", "kivilcim", "aura", "parıltı", "parilti", "şok dalgası", "particle", "explosion", "spark"),
        "PROJECT_NOTES içinde efekt/trail/partikül belirtildi (zorunlu aktif).",
    ),
    "agent_animation": (
        ("animasyon", "animation", "şut", "sut", "falso", "saldırı", "saldiri", "kombo", "vuruş", "vurus", "koşu", "kosu", "sprint", "dash", "kayma", "kılıç", "kilic", "yumruk", "tekme", "atış", "atis", "kick", "shoot", "slash", "punch", "combat", "tuş", "tus"),
        "PROJECT_NOTES içinde karakter eylemi/tuş hareketi/şut/saldırı/koşu geçti (zorunlu aktif).",
    ),
    "agent_ui_menus": (
        ("ana menü", "ana menu", "başlangıç ekranı", "baslangic ekrani", "oyna butonu", "menü", "menu", "hud", "can barı", "can bari", "stamina", "güç barı", "guc bari", "skor tabelası", "skor tabelasi", "skor", "oyun sonu", "win/lose", "scoreboard", "health bar"),
        "PROJECT_NOTES içinde ana menü/HUD/bar/skor tabelası geçti (zorunlu aktif).",
    ),
    "agent_player_guide": (
        ("tuş rehberi", "tus rehberi", "öğretici", "ogretici", "tutorial", "rehber", "adım adım", "adim adim", "yönlendirme", "yonlendirme", "ipucu", "keybind", "guide", "onboarding", "kontroller ekranı", "nasıl oynanır"),
        "PROJECT_NOTES içinde tuş rehberi/öğretici/yönlendirme/ipucu istendi (zorunlu aktif).",
    ),
    "agent_camera": (
        ("kamera", "camera", "3. şahıs", "3.şahıs", "üçüncü şahıs", "third person", "omuz üstü", "shoulder", "fps", "first person", "izometrik", "isometric", "fov", "sarsıntı", "sarsinti", "shake", "sinematik", "broadcast"),
        "PROJECT_NOTES içinde kamera açısı/FOV/sarsıntı belirtildi (zorunlu aktif).",
    ),
    "agent_combat_skills": (
        ("şut", "sut", "falso", "pas", "saldırı", "saldiri", "savaş", "savas", "silah", "kılıç", "kilic", "kombo", "yetenek", "skill", "ability", "tool", "atış", "atis", "kick", "shoot", "combat", "weapon", "sword", "gun"),
        "PROJECT_NOTES içinde şut/saldırı/silah/yetenek mekaniği geçti (zorunlu aktif).",
    ),
    "agent_movement": (
        ("koşu", "kosu", "sprint", "stamina", "dash", "kayma", "zıplama", "ziplama", "parkur", "obby", "top fiziği", "top fizigi", "hareket", "movement", "double jump"),
        "PROJECT_NOTES içinde hareket/sprint/stamina/fizik mekaniği geçti (zorunlu aktif).",
    ),
    "agent_economy": (
        ("süre", "sure", "zamanlayıcı", "zamanlayici", "timer", "skor", "score", "gol", "goal", "maç", "mac", "tur", "round", "ödül", "odul", "para", "altın", "altin", "coin", "seviye", "level", "kazanma", "kaybetme", "datastore", "maç sonu"),
        "PROJECT_NOTES içinde süre/skor/maç sonu/ödül döngüsü geçti (zorunlu aktif).",
    ),
}


def _notes_blob(prompt_text: str, project_notes: dict | None) -> tuple[str, str]:
    """Returns (positive_blob, negative_blob) lowercased."""
    pos_parts = [prompt_text or ""]
    neg_parts = []
    if isinstance(project_notes, dict):
        neg = project_notes.get("negative_constraints") or []
        neg_parts.append(json.dumps(neg, ensure_ascii=False))
        for k, v in project_notes.items():
            if k == "negative_constraints":
                continue
            pos_parts.append(json.dumps(v, ensure_ascii=False))
    return " ".join(pos_parts).lower(), (" ".join(neg_parts) + " " + (prompt_text or "")).lower()


def user_forbids_npc(prompt_text: str, project_notes: dict | None) -> bool:
    _, neg_blob = _notes_blob(prompt_text, project_notes)
    return any(pat in neg_blob for pat in _NO_NPC_PATTERNS)


def _user_forbids_agent(agent_id: str, neg_blob: str) -> bool:
    pats = _FORBID_PATTERNS.get(agent_id)
    if not pats:
        return False
    return any(p in neg_blob for p in pats)


def normalize_routing(raw_routing: dict | None, prompt_text: str, project_notes: dict | None) -> dict[str, dict]:
    """Enforces 14 sub-agent routing + strict anti-laziness mandatory activations + negative constraints."""
    raw = raw_routing if isinstance(raw_routing, dict) else {}
    # Migrate legacy agent_ui routing key if Main Agent used old name
    if "agent_ui_menus" not in raw and "agent_ui" in raw:
        raw["agent_ui_menus"] = raw["agent_ui"]

    pos_blob, neg_blob = _notes_blob(prompt_text, project_notes)
    normalized = {}

    for a in SUB_AGENTS:
        aid = a["id"]
        entry = raw.get(aid)
        if isinstance(entry, dict):
            st = str(entry.get("status") or "PASS").strip().upper()
            st = "ACTIVE" if st in ("ACTIVE", "AKTIF", "AKTİF", "ON", "YES", "TRUE") else "PASS"
            reason = str(entry.get("reason") or "").strip()
            task_input = str(entry.get("task_input") or "").strip()
        elif isinstance(entry, bool):
            st = "ACTIVE" if entry else "PASS"
            reason = ""
            task_input = ""
        else:
            st = "PASS"
            reason = "Bu görev için gerekli görülmedi."
            task_input = ""

        # 1. Hard negative constraint check (e.g., "NPC olmasın" -> agent_ai_npc MUST be PASS)
        if _user_forbids_agent(aid, neg_blob):
            st = "PASS"
            reason = "Kullanıcı kısıtlaması (açıkça istenmedi)."
            task_input = ""
        # 2. Anti-laziness mandatory trigger check: never let Main Agent lazily PASS required agents
        elif st == "PASS" and aid in _MANDATORY_TRIGGERS:
            keywords, auto_reason = _MANDATORY_TRIGGERS[aid]
            if any(kw in pos_blob for kw in keywords):
                st = "ACTIVE"
                reason = auto_reason
                task_input = task_input or f"PROJECT_NOTES doğrultusunda {a['title']} görevini eksiksiz uygula."

        normalized[aid] = {
            "status": st,
            "reason": reason or ("Gerekli uzmanlık" if st == "ACTIVE" else "Bu görev için gerekmiyor"),
            "task_input": task_input,
        }

    # Safety: if nothing was activated, enable architecture + movement
    if not any(v["status"] == "ACTIVE" for v in normalized.values()):
        normalized["agent_architecture"] = {"status": "ACTIVE", "reason": "Varsayılan temel sahne", "task_input": prompt_text}
        normalized["agent_movement"] = {"status": "ACTIVE", "reason": "Varsayılan temel mekanik", "task_input": prompt_text}

    return normalized


def build_subagent_system_prompt(agent_id: str, custom_prompt: str, mode_instruction: str, extra_notes: list[str] | None = None) -> str:
    meta = AGENT_BY_ID[agent_id]
    allowed = meta["allowed_actions"]
    schema_lines = [ACTION_SCHEMAS[t] for t in allowed if t in ACTION_SCHEMAS]
    if agent_id in ("agent_architecture", "agent_environment"):
        schema_lines.append("- To search Roblox Toolbox for 3D models first: {\"thought\":\"...\",\"search_toolbox\":[\"keyword\"],\"actions\":[]}")

    parts = [
        (custom_prompt or meta["default_prompt"]).strip(),
        "\nALLOWED ACTIONS (ONLY THESE — ALL OTHERS ARE STRIPPED):\n" + "\n".join(schema_lines),
        SUBAGENT_SHARED_PROTOCOL,
    ]
    if meta["phase"] in (3, 4) or agent_id == "agent_player_guide":
        parts.append(AIKIT_RUNTIME_REFERENCE)
        parts.append(LUAU_QUALITY_RULES)
    if extra_notes:
        parts.append("IMPORTANT HANDOFF & SCENE NOTES:\n" + "\n".join(f"- {n}" for n in extra_notes if n))
    if mode_instruction:
        parts.append(mode_instruction.strip())
    return "\n\n".join(p for p in parts if p)


def format_pipeline_report(pipeline: list[dict], handoffs: list[str], current_detail: str = "") -> str:
    """Formats a crisp live report for Studio & Web showing ACTIVE / RUNNING / DONE / PASS status of all 15 agents."""
    icon_map = {
        "running": "🔄 ÇALIŞIYOR",
        "done": "✅ TAMAMLANDI",
        "pass": "⏭️ PASS",
        "active": "⏳ AKTİF",
        "pending": "· BEKLİYOR",
        "error": "❌ HATA",
    }
    lines = ["=== V7.5 ÇOKLU AGENT BORU HATTI (1 ANA + 14 ALT AGENT = 15 AGENT) ==="]
    last_phase = None
    for item in pipeline:
        ph = item.get("phase")
        if ph != last_phase and ph in (1, 2, 3, 4):
            phase_names = {
                1: "\n[FAZ 1: FİZİKSEL DÜNYA & GÖRSEL KATMAN]",
                2: "\n[FAZ 2: DİNAMİK VARLIKLAR, MENÜLER & OYUNCU REHBERİ]",
                3: "\n[FAZ 3: ÇEKİRDEK OYNANIŞ & MEKANİK]",
                4: "\n[FAZ 4: ÜST SİSTEMLER & YAPAY ZEKA]",
            }
            lines.append(phase_names[ph])
            last_phase = ph
        st = icon_map.get(item.get("status", "pending"), item.get("status", ""))
        mdl = item.get("model") or ""
        extra = ""
        if item.get("status") == "done":
            extra = f" ({item.get('actions_count', 0)} aksiyon)"
        elif item.get("status") == "pass" and item.get("reason"):
            extra = f" — {item['reason'][:70]}"
        elif item.get("status") == "error" and item.get("reason"):
            extra = f" — {item['reason'][:70]}"
        lines.append(f"  {item.get('title')}: {st}{extra}" + (f" [{mdl}]" if mdl and item.get("status") != "pass" else ""))

    if handoffs:
        lines.append("\n[🔗 SHARED_MANIFEST VERİ AKTARIMLARI]")
        for h in handoffs[-14:]:
            lines.append("  • " + h)

    if current_detail:
        lines.append("\n[CANLI ÇIKTI]\n" + current_detail[-1400:])
    return "\n".join(lines)

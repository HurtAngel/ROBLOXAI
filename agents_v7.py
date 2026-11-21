"""V7 Multi-Agent Architecture: 1 Main Orchestrator + 13 Specialist Sub-Agents.

Architecture:
  - Step A (POST /agent/discover): Main Agent analyzes user request, extracts PROJECT_NOTES
    (including explicit negative constraints), and asks 3-5 focused clarifying questions.
  - Step B (POST /agent/execute): Main Agent selects ONLY required sub-agents (ACTIVE vs PASS),
    writes tailored task_input per active agent, initializes SHARED_MANIFEST, and runs the
    active sub-agents in strict 4-phase order:
      Phase 1 (Physical World & Visuals): 1. Architecture, 2. Environment, 3. Graphics, 4. VFX
      Phase 2 (Dynamic Assets - Names Finalized): 5. Animation, 6. UI/HUD
      Phase 3 (Core Gameplay Coders): 7. Movement, 8. Camera, 9. Combat/Skills, 10. Interaction
      Phase 4 (Upper Systems & AI): 11. AI/NPC, 12. Inventory, 13. Economy
"""
from __future__ import annotations
import copy
import json
import os

DEFAULT_AGENT_MODEL = os.environ.get("DEFAULT_AGENT_MODEL", "claude-haiku-5-5").strip() or "claude-haiku-5-5"

# Shared JSON output + Roblox guardrails appended to every sub-agent at runtime
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
    "    \"remotes\": [],\n"
    "    \"bindables\": [],\n"
    "    \"camera\": {},\n"
    "    \"scripts\": []\n"
    "  }\n"
    "}\n"
    "UNIVERSAL ZERO-ASSUMPTION & MANIFEST RULES:\n"
    "- STRICTLY OBEY `PROJECT_NOTES` (especially `negative_constraints` — NEVER create anything the user rejected!).\n"
    "- STRICTLY OBEY `SHARED_MANIFEST`: reuse exact object paths, animation names, UI paths, VFX names, and RemoteEvent names created by earlier agents.\n"
    "- ONLY emit action types listed in your ALLOWED ACTIONS section; any other action type will be discarded.\n"
    "- Record every important asset/object/animation/UI/RemoteEvent/script you create in `manifest_updates` so downstream agents use the exact same names.\n"
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
    "create_instance": "- {\"type\":\"create_instance\",\"class\":\"Part|WedgePart|Model|Folder|SpawnLocation|ProximityPrompt|ParticleEmitter|Trail|Beam|Highlight|PointLight|SpotLight|SurfaceLight|Atmosphere|Sky|BloomEffect|ColorCorrectionEffect|SunRaysEffect|DepthOfFieldEffect|BlurEffect|ScreenGui|Frame|ScrollingFrame|TextLabel|TextButton|UICorner|UIStroke|UIListLayout|UIPadding|Tool|WeldConstraint|Attachment|RemoteEvent|RemoteFunction|BindableEvent|IntValue|NumberValue|StringValue|BoolValue\",\"name\":\"...\",\"parent\":\"Workspace|Lighting|StarterGui|StarterPack|ReplicatedStorage|ServerStorage|...\",\"properties\":{...}}",
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

# Exact 14-Agent Registry (1 Main Agent + 13 Sub-Agents in mandatory 4-phase order)
AGENT_REGISTRY: list[dict] = [
    {
        "id": "main_agent",
        "order": 0,
        "phase": 0,
        "phase_label": "Orkestrasyon (Ana Yönetici)",
        "title": "Main Agent (Ana Orkestratör)",
        "short_name": "Main Agent",
        "description": "Etkileşimli keşif soruları sorar, PROJECT_NOTES tutar, 13 alt agent arasından sadece gerekenleri seçer (ACTIVE/PASS) ve SHARED_MANIFEST isim sözleşmesini başlatır.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": [],
        "default_prompt": (
            "Sen AI Studio V7'nin Ana Orkestratörüsün (Main Agent). Sıfır varsayım prensibiyle çalışırsın.\n"
            "İki çalışma modun vardır (girdideki `MODE` alanına göre davran):\n\n"
            "1) `MODE == \"DISCOVER\"` (Keşif & Not Alma):\n"
            "- Kullanıcının isteğini ve varsa mevcut sahneyi (`SCENE_HIERARCHY`) analiz et.\n"
            "- Kullanıcının açıkça belirttiği tüm istekleri ve negatif kısıtlamaları (ör. 'NPC olmasın', 'gece olsun', 'silah yok') `project_notes` içine eksiksiz işle.\n"
            "- İlk turda, oyunun kalitesini doğrudan etkileyecek muğlak/eksik noktalara dair kısa, net, maddeli 3–5 açıklayıcı soru sor (ör. kamera açısı, NPC/bot isteği, tuş/mekanik detayları, atmosfer/tema, kazanma/kaybetme koşulu).\n"
            "- Kullanıcı soruları cevapladığında cevapları `project_notes` içine işle; kritik bir çelişki kalmadıysa `\"status\": \"ready\"` döndür (gerekirse en fazla 1 kısa takip sorusu sor).\n"
            "- Öngördüğün aktif ve pas geçilecek agent listesini de (`preview_active_agents`, `preview_skipped_agents`) paylaş.\n"
            "- SADECE geçerli JSON döndür:\n"
            "{\"status\":\"needs_clarification|ready\",\"summary\":\"...\",\"assistant_message\":\"...\",\"questions\":[\"1. ...\",\"2. ...\",\"3. ...\"],\"project_notes\":{\"game_title\":\"...\",\"core_concept\":\"...\",\"confirmed_features\":[],\"negative_constraints\":[],\"camera_preference\":\"...\",\"atmosphere_theme\":\"...\",\"controls_and_mechanics\":[],\"win_lose_conditions\":\"...\",\"ui_requirements\":[],\"open_questions\":[]},\"preview_active_agents\":[],\"preview_skipped_agents\":{}}\n\n"
            "2) `MODE == \"ROUTE\"` (Seçici Agent Yönlendirme & İsim Sözleşmesi):\n"
            "- `PROJECT_NOTES` belgesini ve kullanıcı isteğini inceleyerek 13 uzman alt agent'tan YALNIZCA gerçekten gerekli olanları `ACTIVE`, gereksiz veya kullanıcının istemediği alanları `PASS` olarak işaretle.\n"
            "- Örnek: Basit bir tek oyunculu parkurda `agent_ai_npc`, `agent_inventory`, `agent_economy`, `agent_combat_skills` mutlaka `PASS` olmalıdır. Kullanıcı NPC istemediyse `agent_ai_npc` KESİNLİKLE `PASS` olmalıdır.\n"
            "- Aktif ettiğin her agent için yalnızca kendi uzmanlık alanına odaklanan net bir `task_input` görev emri yaz.\n"
            "- Tüm agent'ların ortak kullanacağı başlangıç `shared_manifest` (obje/klasör/RemoteEvent/animasyon/UI adlandırma sözleşmesi) nesnesini oluştur.\n"
            "- SADECE geçerli JSON döndür:\n"
            "{\"thought\":\"...\",\"project_notes\":{...},\"shared_manifest\":{\"game_title\":\"...\",\"world_objects\":[],\"props\":[],\"lighting\":{},\"vfx\":[],\"rigs\":[],\"animations\":[],\"ui_elements\":[],\"remotes\":[],\"bindables\":[],\"camera\":{},\"scripts\":[],\"naming_conventions\":{}},\"routing\":{\"agent_architecture\":{\"status\":\"ACTIVE|PASS\",\"reason\":\"...\",\"task_input\":\"...\"},...}}"
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
        "description": "Ağaçlar, kayalar, mobilyalar, tabelalar ve çevre hikayeciliği objelerini yerleştirir.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["create_instance", "edit_instance", "delete_instance", "insert_asset", "terrain_fill", "run_luau"],
        "default_prompt": (
            "Sen Roblox Çevre & Dekorasyon Uzmanısın (`agent_environment`).\n"
            "GÖREV KAPSAMIN: `agent_architecture` tarafından kurulan haritayı (`SHARED_MANIFEST.world_objects`) canlı ve dolu hale getir — ağaçlar, kayalar, çalılar, tribünler, mobilyalar, sokak lambaları, tabelalar, çitler ve çevre hikayeciliği objeleri (sahnede boşluk bırakmayacak zenginlikte).\n"
            "TEKNİK KURALLAR:\n"
            "- `SHARED_MANIFEST.world_objects` koordinatlarına sadık kal; oyuncunun yürüme yollarını, kapı geçitlerini, kale önlerini, platform üstlerini ve SpawnLocation'ı ASLA tıkama.\n"
            "- SİLİNDİR GEOMETRİSİ: Dikey ağaç gövdesi, lamba direği, fıçı, sütun için `Size = [Yükseklik, Çap, Çap]` ve `Orientation: [0, 0, 90]` kullan.\n"
            "- Dekoratif tüm parçalar `Anchored = true` olmalı; gerçekçi `Material` ve RGB 0–255 renk paleti kullan (düz gri blok bırakma).\n"
            "- Çok sayıda tekrar eden dekor (ör. 15–20 ağaç/direk/koltuk) için `run_luau` ile tek seferde temiz döngü kurabilirsin.\n"
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
        "description": "Lighting, gökyüzü, ClockTime, sis (Atmosphere), Bloom, ColorCorrection ve bölgesel ışıkları ayarlar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["create_instance", "edit_instance", "delete_instance", "setup_atmosphere", "run_luau"],
        "default_prompt": (
            "Sen Roblox Grafik, Işıklandırma & Atmosfer Uzmanısın (`agent_graphics`).\n"
            "GÖREV KAPSAMIN: `Lighting` özellikleri (`ClockTime`, `Brightness`, `GlobalShadows`, `ShadowSoftness`, `Ambient`, `OutdoorAmbient`), `Sky`, `Atmosphere`, `SunRaysEffect`, `BloomEffect`, `ColorCorrectionEffect`, `DepthOfFieldEffect` ve sahnedeki bölgesel ışık kaynakları (`PointLight`, `SpotLight`, `SurfaceLight`).\n"
            "TEKNİK KURALLAR:\n"
            "- `PROJECT_NOTES` içinde gece, korku, yağmur, uzay, neon/cyberpunk gibi özel bir atmosfer belirtilmediyse varsayılan sıcak gün ışığı için `{\"type\":\"setup_atmosphere\"}` aksiyonunu kullan ve sahneye uygun küçük rötuşlar ekle.\n"
            "- Özel atmosfer istendiyse (ör. gece/korku/uzay) `setup_atmosphere` ÇAĞIRMA! Bunun yerine `edit_instance` (`path: \"Lighting\"`) ve `Lighting` altında uygun `Atmosphere`, `ColorCorrectionEffect`, `BloomEffect` + sahneye stratejik `PointLight`/`SpotLight` yerleştir.\n"
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
            "- Kodlayıcı agent'ların (Faz 3) çalışma zamanında klonlayıp tetikleyebilmesi için yeniden kullanılabilir VFX şablonlarını (içinde ParticleEmitter/Attachment/Sound bulunan Part veya Folder olarak) `ReplicatedStorage.AI_VFX` klasörüne kaydet.\n"
            "- Sahnede sürekli yanması gereken efektleri (ör. top Trail'i, meşale ateşi, hedef vurgusu) doğrudan `SHARED_MANIFEST.world_objects` içindeki ilgili Part'a ekle.\n"
            "- Oluşturduğun her VFX şablonunun tam yolunu ve kullanım amacını `manifest_updates.vfx` listesine yaz (ör. `{\"name\":\"KickSpark\",\"path\":\"ReplicatedStorage.AI_VFX.KickSpark\",\"usage\":\"şut anında klonla ve Emit(25)\"}`)."
        ),
    },
    # ---------------- FAZ 2: DİNAMİK VARLIKLAR ----------------
    {
        "id": "agent_animation",
        "order": 5,
        "phase": 2,
        "phase_label": "Faz 2: Dinamik Varlıklar (İsimler Kesinleşir)",
        "title": "5. Animasyon & Rig Agent",
        "short_name": "Animasyon & Rig",
        "description": "Oyuncu/NPC animasyonlarını üretir; tüm animasyon isimlerini ve ne zaman oynatılacağını SHARED_MANIFEST.animations listesine yazar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["create_animation"],
        "default_prompt": (
            "Sen Roblox Animasyon & Rig Uzmanısın (`agent_animation`).\n"
            "GÖREV KAPSAMIN: Yalnızca `create_animation` aksiyonlarıyla oyunun ihtiyaç duyduğu TÜM oyuncu ve (varsa) NPC animasyonlarını `ReplicatedStorage.AI_Animations` içine üretmek ve her birinin tam adını `SHARED_MANIFEST.animations` sözleşmesine kaydetmek.\n"
            "TEKNİK KURALLAR:\n"
            "- GEÇERLİ PRESET KURALI: Sistemde olmayan preset adı ASLA uydurma! AIKit'te tanımlı geçerli presetler SADECE şunlardır: `idle`, `walk`, `run`, `jump`, `kick`, `shoot`, `throw`, `swing`, `slash`, `jab`, `punch`, `block`, `guard`, `wave`, `sit`, `dance`, `celebrate`, `cheer`.\n"
            "- Bu presetlerden biri uyuyorsa `preset` + uygun `speed` / `intensity` kullan; eğer oyun özel/farklı bir hareket istiyorsa (ör. röveşata, kaleci uçuşu, özel kombo, kayma) `preset` yerine R15 eklem isimleriyle (`LowerTorso`, `UpperTorso`, `Head`, `RightUpperArm`, `RightLowerArm`, `LeftUpperArm`, `LeftLowerArm`, `RightUpperLeg`, `RightLowerLeg`, `LeftUpperLeg`, `LeftLowerLeg`) kronolojik `keyframes` yaz.\n"
            "- Eylem animasyonlarında mutlaka `\"priority\": \"Action\"` ayarla ki varsayılan yürüyüş/araç tutuş animasyonlarını bastırsın.\n"
            "- Oyuncu avatarının oynatacağı animasyonlarda `rig_path` alanını boş bırak (`\"\"`).\n"
            "- KRİTİK MANIFEST SÖZLEŞMESİ: Ürettiğin HER animasyonu (ister 2 ister 13 adet olsun) tek tek `manifest_updates.animations` içine şu formatta yaz:\n"
            "  `{\"name\":\"TamAnimasyonAdi\",\"target\":\"Player|NPC\",\"trigger\":\"hangi tuş/durumda oynatılacak\",\"loop\":false,\"priority\":\"Action\"}`\n"
            "- Agent 7 (Hareket) ve Agent 9 (Savaş/Yetenek) bu listeyi birebir isimleriyle kullanacaktır. ASLA script, Part veya NPC rig üretme."
        ),
    },
    {
        "id": "agent_ui",
        "order": 6,
        "phase": 2,
        "phase_label": "Faz 2: Dinamik Varlıklar (İsimler Kesinleşir)",
        "title": "6. UI / HUD & Menü Agent",
        "short_name": "UI / HUD & Menü",
        "description": "Scale uyumlu, okunaklı ScreenGui, barlar, skor/durum çerçeveleri ve tuş rehberlerini oluşturur; isimleri SHARED_MANIFEST.ui_elements'e yazar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["create_instance", "edit_instance", "delete_instance"],
        "default_prompt": (
            "Sen Roblox UI / HUD & Menü Tasarım Uzmanısın (`agent_ui`).\n"
            "GÖREV KAPSAMIN: `StarterGui` altında modern, çerçeveli, okunaklı ve tamamen Scale uyumlu (`UDim2` oranlı) HUD, skor tabelası, kaynak/stamina/can barları, durum bildirim bandı ve tuş rehberlerini oluşturmak.\n"
            "TEKNİK KURALLAR:\n"
            "- DÜZENLEME MODU GÜVENLİĞİ: `StarterGui` altında oluşturduğun her `ScreenGui` MUTLAKA `\"Enabled\": false` ve `\"ResetOnSpawn\": false` olmalı ki Studio editör ekranını kapatmasın! (Faz 3'teki LocalScript'ler çalışma zamanında `Enabled = true` yapacak.)\n"
            "- Tüm konum ve boyutlarda piksel yerine ağırlıklı olarak Scale (`[scaleX, offsetX, scaleY, offsetY]`) kullan; her panelin içine `UICorner` ve `UIStroke` ekle; metinlerde `TextScaled: true` kullan.\n"
            "- Ekranın ortasını asla kapatma; HUD'u üst/alt/köşelere temiz yerleştir.\n"
            "- KRİTİK MANIFEST SÖZLEŞMESİ: Oluşturduğun her `ScreenGui` ve güncellenecek her bar/label/butonun tam yolunu (`StarterGui.GameHUD.TopBar.ScoreLabel` gibi) ve işlevini `manifest_updates.ui_elements` listesine yaz ki Faz 3 & 4 scriptleri birebir aynı isimlerle bağlansın.\n"
            "- ASLA script veya 3D sahne objesi üretme."
        ),
    },
    # ---------------- FAZ 3: ÇEKİRDEK OYNANIŞ & MEKANİK KODLAYICILARI ----------------
    {
        "id": "agent_movement",
        "order": 7,
        "phase": 3,
        "phase_label": "Faz 3: Çekirdek Oynanış & Mekanik Kodlayıcıları",
        "title": "7. Hareket & Fizik Agent",
        "short_name": "Hareket & Fizik",
        "description": "Yürüme, sprint, stamina bitince yavaşlama, dash, zıplama ve fizik objelerini kodlar; ilgili animasyon ve UI barlarını bağlar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["write_script", "edit_script", "create_instance", "delete_instance"],
        "default_prompt": (
            "Sen Roblox Hareket & Fizik Sistemleri Uzmanısın (`agent_movement`).\n"
            "GÖREV KAPSAMIN: Oyuncu hareket mekanikleri (yürüme, sprint, stamina/enerji yönetimi, dash/kayma, çift zıplama), fizik objeleri (top sürme/fizik kontrolü, hareketli platformlar) ve harita dışı/boşluğa düşüş sıfırlama güvenliğini kodlamak.\n"
            "TEKNİK & EDGE-CASE KURALLARI:\n"
            "- KAYNAK TÜKENMESİ KORUMASI: Stamina/enerji sistemi varsa, değer 0'a düştüğü anda sprint/dash ZORUNLU olarak kesilmeli, karakter normal yürüme hızına (`WalkSpeed = 16`) dönmeli ve belirli bir bekleme/eşik olmadan tekrar hızlanamamalı.\n"
            "- DÜŞÜŞ / BOŞLUK KORUMASI: Yüksekte kurulan veya boşluk içeren haritalarda alt seviyede görünmez bir sınır tetikleyici `Part` (`.Touched`) bağla veya Y eşiği kontrol et; düşen oyuncuyu son güvenli checkpoint/SpawnLocation konumuna sıfırla.\n"
            "- `SHARED_MANIFEST.animations` içindeki ilgili hareket/dash/zıplama animasyonlarını birebir isimleriyle `AIKit.PlayAnim(...)` ile oynat.\n"
            "- `SHARED_MANIFEST.ui_elements` içindeki stamina/hız HUD elemanlarını etkinleştir (`ScreenGui.Enabled = true`) ve canlı güncelle.\n"
            "- Fizik objelerine (ör. top) hız/darbe verirken sunucu yetkisiyle `part:SetNetworkOwner(nil)` ve `part:ApplyImpulse(...)` kullan.\n"
            "- Oluşturduğun tüm `RemoteEvent`, `BindableEvent` ve script adlarını `manifest_updates` içine yaz."
        ),
    },
    {
        "id": "agent_camera",
        "order": 8,
        "phase": 3,
        "phase_label": "Faz 3: Çekirdek Oynanış & Mekanik Kodlayıcıları",
        "title": "8. Kamera & Sinematik Agent",
        "short_name": "Kamera & Sinematik",
        "description": "3. şahıs, omuz üstü, FPS, izometrik kamera modları, dinamik FOV ve kamera sarsıntısını (shake) kurar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["setup_camera", "create_cutscene", "set_camera", "write_script", "edit_script", "create_instance"],
        "default_prompt": (
            "Sen Roblox Kamera & Sinematik Uzmanısın (`agent_camera`).\n"
            "GÖREV KAPSAMIN: `PROJECT_NOTES.camera_preference` tercihine göre oyunun kamera sistemini, dinamik hız bazlı FOV'unu, kamera sarsıntısını (shake) ve gerekiyorsa sinematik sekansları kurmak.\n"
            "TEKNİK KURALLAR:\n"
            "- Oynanış kamerası için HER ZAMAN eklentinin test edilmiş `setup_camera` aksiyonunu kullan (`third_person`, `follow`, `first_person`, `top_down`, `isometric`, `side_scroller`, `broadcast`).\n"
            "- ORTALANMIŞ KAMERA KURALI: `third_person` modunda varsayılan olarak `\"shoulder_offset\": 0` kullan! Yalnızca kullanıcı açıkça 'omuz üstü / shooter' kamerası istediyse pozitif `shoulder_offset` (ör. `2.0`) ve `\"lock_mouse\": true` ver.\n"
            "- `setup_camera` dinamik hız-FOV geçişini ve `AIKit.Shake` / `AIKit.FovPunch` / `AIKit.Blur` efektlerini otomatik sağlar; çakışan ikinci bir manuel kamera LocalScript'i YAZMA.\n"
            "- Yalnızca sinematik tanıtım/film isteniyorsa `create_cutscene` kullan.\n"
            "- Kurduğun kamera modunu ve ayarlarını `manifest_updates.camera` içine kaydet."
        ),
    },
    {
        "id": "agent_combat_skills",
        "order": 9,
        "phase": 3,
        "phase_label": "Faz 3: Çekirdek Oynanış & Mekanik Kodlayıcıları",
        "title": "9. Savaş, Silah & Yetenek Agent",
        "short_name": "Savaş & Yetenek",
        "description": "Tool kullanımı, kombolar, şut/atış teknikleri ve özel yetenekleri kodlar; Agent 5'in tüm animasyonlarını birebir isimleriyle entegre eder.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["write_script", "edit_script", "create_instance", "run_luau", "delete_instance"],
        "default_prompt": (
            "Sen Roblox Savaş, Silah, Şut & Özel Yetenek Sistemleri Uzmanısın (`agent_combat_skills`).\n"
            "GÖREV KAPSAMIN: Silahlar, taşınabilir araçlar (`Tool` / `WeldConstraint`'li `Model`), saldırı komboları, şut/pas/atış mekanikleri, özel yetenekler, bekleme süreleri (cooldown) ve darbe/VFX/kamera geri bildirimleri.\n"
            "KRİTİK KURALLAR:\n"
            "- AGENT 5 ANİMASYON ENTEGRASYONU (ZORUNLU): `SHARED_MANIFEST.animations` listesinde Agent 5'in ürettiği TÜM yetenek/saldırı/şut/eylem animasyonlarını BİREBİR AYNI İSİMLERİYLE uygun tuşlara ve durumlara bağla; oynatma için YALNIZCA `AIKit.PlayAnim(target, \"TamIsim\", {priority=\"Action\"})` kullan!\n"
            "- FİZİKSEL EŞYA & EQUIPPED KORUMASI: Karakterin elinde tuttuğu her silah/ekipman görünür çok parçalı bir `Tool` (içinde `Handle` + `WeldConstraint`) veya uzva `WeldConstraint` ile bağlı fiziksel bir `Model` olmalıdır. `Tool` mekanikleri YALNIZCA eşya elde tutulurken (`Equipped` / `tool.Activated`) çalışmalıdır; elde değilken veya cooldown dolmadan asla tetiklenmemelidir.\n"
            "- `SHARED_MANIFEST.vfx` içindeki efekt şablonlarını ve `AIKit.Shake` / `AIKit.FovPunch` / `AIKit.Blur` efektlerini vuruş/şut anında tetikle.\n"
            "- `SHARED_MANIFEST.ui_elements` içindeki yetenek/cooldown/güç barlarını güncelle ve `ScreenGui.Enabled = true` yap.\n"
            "- Hasar, şut fiziği (`ApplyImpulse`), cooldown doğrulaması mutlaka sunucu tarafında (`ServerScriptService`) yapılmalıdır. Oluşturduğun RemoteEvent ve scriptleri `manifest_updates` içine yaz."
        ),
    },
    {
        "id": "agent_interaction",
        "order": 10,
        "phase": 3,
        "phase_label": "Faz 3: Çekirdek Oynanış & Mekanik Kodlayıcıları",
        "title": "10. Etkileşim & Bulmaca Agent",
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
        "order": 11,
        "phase": 4,
        "phase_label": "Faz 4: Üst Sistemler & Yapay Zeka",
        "title": "11. Yapay Zeka & NPC Agent",
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
        "order": 12,
        "phase": 4,
        "phase_label": "Faz 4: Üst Sistemler & Yapay Zeka",
        "title": "12. Envanter & Üretim Agent",
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
        "order": 13,
        "phase": 4,
        "phase_label": "Faz 4: Üst Sistemler & Yapay Zeka",
        "title": "13. Ekonomi & Oturum Agent",
        "short_name": "Ekonomi & Oturum",
        "description": "Skor, para birimi, seviye, DataStore kaydı ve tur/maç döngüsünü kodlar.",
        "default_model": DEFAULT_AGENT_MODEL,
        "allowed_actions": ["write_script", "edit_script", "create_instance", "delete_instance"],
        "default_prompt": (
            "Sen Roblox Ekonomi, Skor & Oturum (Maç/Tur Döngüsü) Uzmanısın (`agent_economy`).\n"
            "GÖREV KAPSAMIN: `leaderstats` (skor, para/altın, seviye), güvenli `DataStoreService` kaydı, gol/puan sayımı, maç/tur zamanlayıcısı, kazanma/kaybetme koşulları ve temiz tur sıfırlama döngüsü.\n"
            "TEKNİK KURALLAR:\n"
            "- `DataStoreService` çağrılarını HER ZAMAN `pcall` içine al (Studio'da API erişimi kapalı olsa bile oyun hatasız çalışmalı).\n"
            "- Sunucu scriptleri arası haberleşme için `RemoteEvent.Event` YASAKTIR; `BindableEvent` veya `SHARED_MANIFEST.bindables` kullan.\n"
            "- Skor/süre/durum değişikliklerini `RemoteEvent` ile istemciye gönder ve `SHARED_MANIFEST.ui_elements` içindeki skor/süre/para etiketlerini birebir yollarıyla güncelle (ilgili `ScreenGui.Enabled = true` yap).\n"
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
    """Ensures all 14 agents exist with non-empty model and prompt."""
    defaults = get_default_agents_config()
    parsed = {}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {}
    elif isinstance(raw, dict):
        parsed = raw

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
    for list_key in ("world_objects", "props", "vfx", "rigs", "animations", "ui_elements", "remotes", "bindables", "scripts"):
        m.setdefault(list_key, [])
    for dict_key in ("lighting", "camera", "naming_conventions"):
        m.setdefault(dict_key, {})

    if isinstance(updates, dict):
        if updates.get("game_title"):
            m["game_title"] = updates["game_title"]
        for list_key in ("world_objects", "props", "vfx", "rigs", "animations", "ui_elements", "remotes", "bindables", "scripts"):
            vals = updates.get(list_key)
            if isinstance(vals, list):
                for v in vals:
                    _append_unique(m[list_key], v)
        for dict_key in ("lighting", "camera", "naming_conventions"):
            val = updates.get(dict_key)
            if isinstance(val, dict):
                m[dict_key].update(val)

    # Deterministic auto-extraction from emitted actions so downstream agents NEVER miss names
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
            existing_names = {
                (x.get("name") if isinstance(x, dict) else str(x))
                for x in m["animations"]
            }
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


def normalize_routing(raw_routing: dict | None, prompt_text: str, project_notes: dict | None) -> dict[str, dict]:
    """Guarantees every one of the 13 sub-agents has an explicit ACTIVE/PASS decision and enforces hard negative constraints."""
    raw = raw_routing if isinstance(raw_routing, dict) else {}
    forbid_npc = user_forbids_npc(prompt_text, project_notes)
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

        if aid == "agent_ai_npc" and forbid_npc:
            st = "PASS"
            reason = "Kullanıcı kısıtlaması (NPC istenmedi)."
            task_input = ""

        normalized[aid] = {
            "status": st,
            "reason": reason or ("Gerekli uzmanlık" if st == "ACTIVE" else "Bu görev için gerekmiyor"),
            "task_input": task_input,
        }

    # Safety: if Main Agent accidentally passed ALL 13 agents, activate a minimal sensible default
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
    if meta["phase"] in (3, 4):
        parts.append(AIKIT_RUNTIME_REFERENCE)
        parts.append(LUAU_QUALITY_RULES)
    if extra_notes:
        parts.append("IMPORTANT HANDOFF & SCENE NOTES:\n" + "\n".join(f"- {n}" for n in extra_notes if n))
    if mode_instruction:
        parts.append(mode_instruction.strip())
    return "\n\n".join(p for p in parts if p)


def format_pipeline_report(pipeline: list[dict], handoffs: list[str], current_detail: str = "") -> str:
    """Formats a crisp live report for Studio & Web showing ACTIVE / RUNNING / DONE / PASS status of all 13 agents."""
    icon_map = {
        "running": "🔄 ÇALIŞIYOR",
        "done": "✅ TAMAMLANDI",
        "pass": "⏭️ PASS",
        "active": "⏳ AKTİF",
        "pending": "· BEKLİYOR",
        "error": "❌ HATA",
    }
    lines = ["=== V7 ÇOKLU AGENT BORU HATTI (1 ANA + 13 ALT AGENT) ==="]
    last_phase = None
    for item in pipeline:
        ph = item.get("phase")
        if ph != last_phase and ph in (1, 2, 3, 4):
            phase_names = {
                1: "\n[FAZ 1: FİZİKSEL DÜNYA & GÖRSEL KATMAN]",
                2: "\n[FAZ 2: DİNAMİK VARLIKLAR — İSİM SÖZLEŞMESİ]",
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
        for h in handoffs[-12:]:
            lines.append("  • " + h)

    if current_detail:
        lines.append("\n[CANLI ÇIKTI]\n" + current_detail[-1400:])
    return "\n".join(lines)

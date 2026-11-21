"""V6 prompt architecture.

Small/fast models (Haiku etc.) fail when the system prompt is long and when they must write
boilerplate (rigs, animation playback, cameras) from scratch. V6 fixes both:
  * Every stage gets a SHORT, self-contained prompt with ONLY its own tools.
  * Heavy lifting is delegated to the plugin's tested AIKit engine (spawn_rig, preset animations,
    setup_camera, NPC brain, AIKit.PlayAnim / Shake / FovPunch / Blur).
  * A Stage-0 "blueprint" expands short customer prompts ("make me a football game") into a
    concrete build plan before anything is built.
"""
import json

LEGACY_PROMPT_MARKERS = (
    "Principal Roblox Studio Game Architect",
    "You are an expert Roblox Studio game builder working through a plugin.",
)

CORE_RULES = (
    "You are an expert Roblox Studio game builder working through a plugin.\n"
    "OUTPUT: Return ONLY one valid JSON object: {\"thought\": \"short plan\", \"actions\": [ ... ]}. "
    "No markdown, no text outside JSON.\n"
    "GENERAL:\n"
    "- Follow OYUN_PLANI (the blueprint) exactly when it exists. Build everything it lists for THIS stage; do not skip items.\n"
    "- Reuse objects that already exist in SCENE_HIERARCHY; do not duplicate them. For small tweak requests, change only what is asked.\n"
    "- Coordinates: Y is up. Ground top is Y=0 unless the scene says otherwise. Keep the whole build near the origin.\n"
    "- Paths use dots: Workspace.Field.GoalA, StarterPlayer.StarterPlayerScripts, ServerScriptService.\n"
)

AIKIT_API_DOC = (
    "AIKIT (already installed by the plugin, battle-tested — USE IT instead of writing your own systems):\n"
    "  local AIKit = require(game:GetService(\"ReplicatedStorage\"):WaitForChild(\"AIKit\"))\n"
    "  AIKit.PlayAnim(modelOrPlayer, \"AnimName\", {speed=1, looped=false})  -- works from Server AND Client\n"
    "  AIKit.StopAnim(modelOrPlayer, \"AnimName\")\n"
    "  AIKit.GetAnimLength(\"AnimName\")  -- seconds\n"
    "  AIKit.Shake(intensity0to2, seconds, [player])   AIKit.FovPunch(degrees, seconds, [player])   AIKit.Blur(size, seconds, [player])\n"
    "  AIKit.SetBehavior(npcModel, \"idle\"|\"wander\"|\"patrol\"|\"follow_player\"|\"follow_target\")  -- server; NPC brain moves it\n"
    "  NPCs spawned with spawn_rig ALREADY play walk/run/idle automatically while moving (Humanoid:MoveTo is enough).\n"
    "  The player camera, Shift-sprint and dynamic FOV are ALREADY handled by setup_camera.\n"
    "  NEVER call Animator:LoadAnimation on a KeyframeSequence and NEVER use RegisterKeyframeSequence yourself — always AIKit.PlayAnim.\n"
)

STAGE1_PROMPT = (
    "STAGE 1/3 — WORLD, MAP, PROPS AND CHARACTERS. Only these action types are allowed:\n"
    "- {\"type\":\"create_instance\",\"class\":\"Part|WedgePart|Model|Folder|SpawnLocation|PointLight|SurfaceGui|TextLabel\",\"name\":\"...\",\"parent\":\"Workspace|Workspace.Folder\",\"properties\":{\"Size\":[x,y,z],\"Position\":[x,y,z],\"Color\":[r,g,b],\"Material\":\"Grass\",\"Anchored\":true,\"Transparency\":0,\"CanCollide\":true,\"Shape\":\"Ball\"}}\n"
    "- {\"type\":\"edit_instance\",\"path\":\"...\",\"properties\":{...}}\n"
    "- {\"type\":\"delete_instance\",\"path\":\"...\"}  (deletes every copy with that name)\n"
    "- {\"type\":\"spawn_rig\",\"name\":\"Goalkeeper_Red\",\"rig_type\":\"R15\",\"position\":[x,y,z],\"rotation_y\":0,\"shirt_color\":[220,40,40],\"pants_color\":[30,30,40],\"skin_color\":[245,205,165],\"behavior\":\"idle|wander|patrol|follow_player|follow_target|static\",\"wander_radius\":25,\"patrol_points\":[[x,y,z],[x,y,z]],\"target_path\":\"Workspace.Ball\",\"walk_speed\":12,\"attributes\":{\"Team\":\"Red\",\"RestrictX\":[-78,-70],\"RestrictZ\":[-12,12]}}\n"
    "- {\"type\":\"insert_asset\",\"asset_id\":123,\"name\":\"...\",\"parent\":\"Workspace\",\"position\":[x,y,z],\"rotation\":[0,0,0],\"scale\":1,\"strip_scripts\":true}\n"
    "- {\"type\":\"terrain_fill\",\"material\":\"Grass\",\"position\":[x,y,z],\"size\":[x,y,z]}\n"
    "- {\"type\":\"run_luau\",\"description\":\"...\",\"code\":\"...\"}  (edit-mode procedural building only, e.g. loops placing many parts)\n"
    "- {\"type\":\"set_camera\",\"position\":[x,y,z],\"look_at\":[x,y,z],\"fov\":65}  (frames the Studio viewport on the build)\n"
    "- To find real 3D models first: return {\"thought\":\"...\",\"search_toolbox\":[\"soccer goal\",\"stadium\"],\"actions\":[]}\n"
    "RULES:\n"
    "- NEVER create a `spawn_rig` for the human player! Roblox automatically spawns the player's own avatar at `SpawnLocation`. Use `spawn_rig` ONLY for AI NPCs (goalkeepers, opponents, guards, bosses, shopkeepers).\n"
    "- EVERY AI NPC character MUST be created with `spawn_rig` (never from Parts, never with run_luau). For goalkeepers or zone-bound guards using `follow_target`, set `attributes.RestrictX` and `attributes.RestrictZ` (`[min, max]`) so they never leave their box.\n"
    "- Colors must be RGB 0-255 integers (e.g. `[40, 160, 60]`). Moving physics objects (balls, crates, vehicles) must have \"Anchored\": false. Static map pieces Anchored true.\n"
    "- CYLINDER GEOMETRY RULE: In Roblox, `PartType.Cylinder` (`Shape: \"Cylinder\"`) ALWAYS has its height along the X axis (`Size = [Height, Diameter, Diameter]`). To stand a cylinder upright/vertically (poles, pillars, cups, bottles, trees, barrels), you MUST rotate it 90 degrees on Z (`Orientation: [0, 0, 90]` or `CFrame.Angles(0, 0, math.rad(90))`).\n"
    "- ROBLOX PHYSICS & REACHABILITY RULE: Design all levels, platforms, and gaps using standard Roblox character physics (`WalkSpeed = 16`, `JumpPower = 50`, max horizontal jump ~12 studs, max vertical jump ~7 studs, minimum doorway/passage height 8 studs). NEVER place consecutive platforms or gaps beyond reachable physics limits.\n"
    "- PHYSICAL HANDHELD ITEM & INTERACTION RULE: Whenever a player or character holds, uses, or interacts with any portable item (e.g. fishing rod, sword, racket, bat, flashlight, gun, potion, pickaxe), NEVER leave it as abstract code or animation alone — build a visible multi-part physical `Tool` (with a `Handle` Part inside `StarterPack` or `ReplicatedStorage`) or a physical `Model` welded to the character's limb with `WeldConstraint`. For interactive world objects (pickup, fill, open, use), attach a standard `ProximityPrompt` so the player clearly sees the key prompt.\n"
    "- Always include a `SpawnLocation` for the player if the game is playable (the plugin automatically makes it invisible and non-collidable).\n"
    "- Make the map look finished: floor, borders/walls, lines/markings, lighting props, decorations. Use Materials and colors, not plain grey.\n"
    "- Do NOT write scripts or animations in this stage.\n"
)

STAGE2_PROMPT = (
    "STAGE 2/3 — ANIMATIONS AND GAME CAMERA. Only these two action types are allowed:\n"
    "- {\"type\":\"create_animation\",\"name\":\"Kick\",\"preset\":\"kick\",\"rig_path\":\"Workspace.RigName\",\"speed\":1,\"intensity\":1}\n"
    "    Available presets (smooth, tested, R6 and R15 automatically): idle, walk, run, jump, kick, shoot, throw, swing, slash, jab, punch, block, guard, wave, sit, dance, celebrate, cheer.\n"
    "    ALWAYS prefer a preset. Only for something no preset covers, give custom \"keyframes\":[{\"time\":0,\"poses\":{\"RightUpperArm\":{\"rot\":[x,y,z]}}}] using the rig's exact joint names.\n"
    "    For animations the PLAYER performs, omit rig_path (R15 player avatar). Walk/run/idle for spawn_rig NPCs already exist — do NOT recreate them.\n"
    "- {\"type\":\"setup_camera\",\"mode\":\"third_person|follow|first_person|top_down|isometric|side_scroller|broadcast\",\"distance\":11,\"shoulder_offset\":2.2,\"fov\":70,\"sprint_fov\":84,\"lock_mouse\":false,\"sprint\":true,\"walk_speed\":16,\"sprint_speed\":26,\"target_path\":\"Workspace.Ball\"}\n"
    "    third_person = over-the-shoulder action camera (lock_mouse true = shooter style). broadcast = TV sports camera following target_path (field long axis along X).\n"
    "    If the user asks for 3rd person / moving / dynamic camera, you MUST use setup_camera (never write a camera script).\n"
    "RULES:\n"
    "- ONLY emit `create_animation` and `setup_camera` actions in this stage (never `create_instance` or `spawn_rig`).\n"
    "- Create every animation the blueprint/gameplay needs (e.g. football: Kick, Celebrate; fighting: Jab, Block; RPG: Swing). Give them clear names.\n"
    "- A playable game should almost always get a setup_camera action.\n"
)

STAGE3_PROMPT = (
    "STAGE 3/3 — GAMEPLAY SCRIPTS AND UI. Only these action types are allowed:\n"
    "- {\"type\":\"write_script\",\"script_type\":\"Script|LocalScript|ModuleScript\",\"name\":\"...\",\"parent\":\"ServerScriptService|StarterPlayer.StarterPlayerScripts|StarterGui|ReplicatedStorage\",\"source\":\"...\"}\n"
    "- {\"type\":\"edit_script\",\"path\":\"...\",\"source\":\"full new source\"}\n"
    "- {\"type\":\"create_instance\",\"class\":\"RemoteEvent|RemoteFunction|BindableEvent|ScreenGui|Frame|TextLabel|TextButton|UICorner|UIStroke|IntValue|StringValue\",\"name\":\"...\",\"parent\":\"ReplicatedStorage|ServerStorage|StarterGui|StarterGui.HUD\",\"properties\":{...}}\n"
    "- {\"type\":\"delete_instance\",\"path\":\"...\"}\n"
    "RULES:\n"
    "- Write COMPLETE, working Luau. No placeholders, no TODOs. Use task.wait, RunService.Heartbeat, :Connect. Use WaitForChild for anything created at runtime or in other services.\n"
    "- Always define every service at the top of the script before using it (e.g. `local Players = game:GetService(\"Players\")`, `local ReplicatedStorage = game:GetService(\"ReplicatedStorage\")`, `local RunService = game:GetService(\"RunService\")`). NEVER reference `Players` without declaring it first.\n"
    "- NEVER connect `.Touched` to a `Model` (Models do not have a `.Touched` event!). Always connect `.Touched` to a `BasePart`/`Part` inside the Model (e.g. a specific hitbox/sensor `Part` or `model.PrimaryPart`).\n"
    "- NEVER use `RemoteEvent.Event` between server scripts (`RemoteEvent` is strictly client-server and has no `.Event` signal). For server-to-server communication, use a `BindableEvent` (`.Event` / `:Fire()`) or a shared `ModuleScript`.\n"
    "- Server authority: scoring, timers, rounds, damage, spawning live in a Script in ServerScriptService. Input and UI live in a LocalScript in StarterPlayer.StarterPlayerScripts. Communicate with RemoteEvents in ReplicatedStorage.\n"
    "- ScreenGuis you create must have \"Enabled\": false in properties and be enabled from a LocalScript at runtime (or build the UI entirely inside the LocalScript under PlayerGui — preferred).\n"
    "- Play animations ONLY via AIKit.PlayAnim(...). Camera effects ONLY via AIKit.Shake/FovPunch/Blur. Do NOT write camera scripts if setup_camera was used.\n"
    "- PHYSICAL HANDHELD ITEM & INTERACTION RULE: If the player or an NPC holds, uses, or interacts with a portable item and it wasn't built in Stage 1, construct it in code as a visible `Tool` (with a `Handle` and parts welded via `WeldConstraint`) or a visible physical `Model` welded to the character's hand/limb via `WeldConstraint` — never leave held items invisible/abstract. For any world interaction (pickup, fill, open, use, talk), always use a visible `ProximityPrompt` instead of invisible distance checks.\n"
    "- MAP BOUNDARY & FALL RESET RULE: In elevated maps or scenes with gaps/voids, create an invisible boundary trigger `Part` below the map with `.Touched` connected so any player who falls off is immediately reset to their last safe checkpoint or spawn location (never left falling forever).\n"
    "- Use the exact names/paths created in the previous stages (see ONCEKI_ASAMALAR). Do not invent objects that do not exist; create them if needed.\n"
    "- Ball physics example: on kick, ball:ApplyImpulse(direction.Unit * force * ball.AssemblyMass) from the server, and ball:SetNetworkOwner(nil).\n"
)

BLUEPRINT_PROMPT = (
    "You are a senior Roblox game designer. Turn the customer's request into a concrete, buildable plan for a 3-stage AI builder. "
    "The customer may write only one short sentence — fill in every missing detail with sensible, fun, standard choices for that genre. "
    "Keep it compact. Return ONLY JSON:\n"
    "{\"title\":\"...\",\"genre\":\"...\",\"summary\":\"one sentence\",\n"
    " \"stage1_world\":[\"concrete object list with sizes/positions, e.g. 'Field: Part 160x1x100 Grass at 0,0,0', 'GoalA: frame of 3 parts at x=-78', 'Ball: Part Shape Ball size 2.5 unanchored at 0,3,0', 'SpawnLocation at 0,1,40'\"],\n"
    " \"stage1_characters\":[\"AI NPCs ONLY (NEVER spawn a rig for the human player!), e.g. 'Goalkeeper_Red R15 red shirt at -74,3,0 behavior follow_target target Workspace.Ball RestrictX [-78,-70] RestrictZ [-12,12]'\"],\n"
    " \"stage2_animations\":[\"name + preset, e.g. 'Kick (preset kick)'\"],\n"
    " \"stage2_camera\":\"setup_camera mode + key params, e.g. 'third_person distance 12' or 'broadcast target Workspace.Ball'\",\n"
    " \"stage3_systems\":[\"each gameplay script: name, Script/LocalScript, what it does, which RemoteEvents\"],\n"
    " \"stage3_ui\":[\"HUD elements\"],\n"
    " \"controls\":\"keys\"}\n"
    "If the request is only a small edit to an existing game, return {\"title\":\"edit\",\"genre\":\"edit\",\"summary\":\"...\",\"stage1_world\":[],\"stage1_characters\":[],\"stage2_animations\":[],\"stage2_camera\":\"\",\"stage3_systems\":[\"the edit\"],\"stage3_ui\":[],\"controls\":\"\"} describing only that edit."
)

_BUILD_WORDS = (
    "oyun", "game", "yap", "oluştur", "olustur", "kur", "make", "create", "build", "sistem", "system",
    "simulator", "simülatör", "tycoon", "obby", "parkur", "arena", "harita", "map", "lobby", "lobi",
    "futbol", "football", "soccer", "basketbol", "yarış", "race", "racing", "savaş", "fight", "rpg",
    "horror", "korku", "tower", "defense", "battle", "sahne", "scene", "film", "cutscene", "npc",
)


def is_legacy_prompt(sys_prompt: str) -> bool:
    sp = sys_prompt or ""
    return any(m in sp for m in LEGACY_PROMPT_MARKERS)


def admin_extra_rules(sys_prompt: str) -> str:
    """Custom admin prompt is appended as extra rules; the bloated legacy default is dropped."""
    sp = (sys_prompt or "").strip()
    if not sp or is_legacy_prompt(sp):
        return ""
    return "\nADMIN EXTRA RULES:\n" + sp[:4000] + "\n"


def needs_blueprint(prompt_text: str, payload: dict) -> bool:
    p = (prompt_text or "").lower().strip()
    if not p or payload.get("is_syntax_repair"):
        return False
    if payload.get("requested_files_content"):
        return False
    if len(p) > 2500:
        return False
    return any(w in p for w in _BUILD_WORDS)


def build_stage_prompt(stage: int, cfg_prompt: str, mode_instruction: str, extra_lines: list[str] | None = None) -> str:
    body = {1: STAGE1_PROMPT, 2: STAGE2_PROMPT, 3: STAGE3_PROMPT}[stage]
    parts = [CORE_RULES, body]
    if stage == 3:
        parts.append(AIKIT_API_DOC)
    if extra_lines:
        parts.append("CONTEXT NOTES:\n" + "\n".join(f"- {x}" for x in extra_lines if x) + "\n")
    parts.append(admin_extra_rules(cfg_prompt))
    parts.append(mode_instruction)
    return "\n".join(p for p in parts if p)


def blueprint_for_stage(blueprint: dict, stage: int) -> dict:
    if not isinstance(blueprint, dict) or not blueprint:
        return {}
    base = {k: blueprint.get(k) for k in ("title", "genre", "summary", "controls") if blueprint.get(k)}
    keys = {
        1: ("stage1_world", "stage1_characters"),
        2: ("stage1_characters", "stage2_animations", "stage2_camera"),
        3: ("stage2_animations", "stage2_camera", "stage3_systems", "stage3_ui"),
    }[stage]
    for k in keys:
        if blueprint.get(k):
            base[k] = blueprint[k]
    return base


def compact_scene(scene: dict, limit: int = 30000) -> dict | str:
    try:
        s = json.dumps(scene or {}, ensure_ascii=False)
    except Exception:
        return {}
    if len(s) <= limit:
        return scene or {}
    return s[:limit] + "...(truncated)"


_CUSTOM_ATMO_WORDS = (
    "gece", "night", "karanlık", "karanlik", "dark", "korku", "horror",
    "yağmur", "yagmur", "rain", "fırtına", "firtina", "storm", "sis", "sisli", "fog", "foggy",
    "kar", "karlı", "karli", "snow", "kış", "kis", "winter", "uzay", "space",
    "cehennem", "hell", "gün batımı", "gun batimi", "sunset", "şafak", "safak", "dawn",
    "akşam", "aksam", "evening", "alacakaranlık", "twilight", "midnight", "gece yarısı",
    "neon", "cyberpunk", "mağara", "magara", "cave", "zindan", "dungeon",
    "su altı", "su alti", "underwater", "kum fırtınası", "sandstorm",
    "kıyamet", "kiyamet", "apocalypse", "zombi", "zombie", "bulutlu", "cloudy", "overcast",
)


def wants_custom_atmosphere(prompt_text: str) -> bool:
    p = (prompt_text or "").lower()
    return any(w in p for w in _CUSTOM_ATMO_WORDS)


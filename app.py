import asyncio, os, ssl, certifi, subprocess, webbrowser
os.environ["SSL_CERT_FILE"] = certifi.where()

import json, re
from datetime import datetime
import requests
import gradio as gr
from g4f.client import AsyncClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

llm = AsyncClient()
WORKSPACE_DIR = os.path.expanduser("~/genz-life-os/workspace")
MEMORY_FILE = os.path.join(WORKSPACE_DIR, "my_memory.json")
os.makedirs(WORKSPACE_DIR, exist_ok=True)

def load_memory() -> str:
    if not os.path.exists(MEMORY_FILE):
        return "*No saved goals yet. Run an action to build your memory!*"
    with open(MEMORY_FILE, "r") as f:
        items = json.load(f)
    return "\n".join(f"- {item}" for item in items[-15:])

def save_memory(fact: str):
    items = []
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as f:
            items = json.load(f)
    if fact not in items:
        items.append(f"[{datetime.now().strftime('%Y-%m-%d')}] {fact}")
        with open(MEMORY_FILE, "w") as f:
            json.dump(items, f, indent=2)

def list_workspace_files() -> str:
    files = sorted(os.listdir(WORKSPACE_DIR))
    if not files:
        return "*No files generated yet. Trigger any pillar to create files!*"
    return "\n".join(
        f"- 📄 **`{fn}`** · `{os.path.getsize(os.path.join(WORKSPACE_DIR, fn))} B`"
        for fn in files
    )

def read_workspace_file(filename: str) -> str:
    if not filename:
        return "*Type a file name above (e.g. `reel_scripts.md`) and click Preview.*"
    fpath = os.path.join(WORKSPACE_DIR, os.path.basename(filename.strip()))
    if not os.path.exists(fpath):
        return f"⚠️ File `{filename}` not found."
    with open(fpath, "r", encoding="utf-8") as f:
        return f"### 📄 `{filename}`\n\n```text\n{f.read()}\n```"

def fetch_live_jobs() -> str:
    try:
        r = requests.get("https://remoteok.com", headers={"User-Agent": "GenZLifeOS/1.0"}, timeout=10)
        return "\n".join(
            f"- **{j.get('position')}** at {j.get('company')} | Tags: {', '.join(j.get('tags', [])[:4])} | Link: {j.get('url')}"
            for j in r.json()[1:4]
        )
    except Exception as e:
        return f"RemoteOK feed error: {e}"

def fetch_github_bounties() -> str:
    try:
        r = requests.get(
            "https://github.com",
            headers={"User-Agent": "GenZLifeOS/1.0"}, timeout=10
        )
        items = r.json().get("items", [])
        return "\n".join(f"- **{it.get('title')}** | Link: {it.get('html_url')}" for it in items) or "No open bounties right now."
    except Exception as e:
        return f"GitHub API error: {e}"

def fetch_live_opportunities() -> str:
    try:
        ids = requests.get("https://firebaseio.com", timeout=8).json()[:5]
        out = []
        for sid in ids:
            it = requests.get(f"https://firebaseio.com{sid}.json", timeout=5).json()
            if it and "title" in it:
                out.append(f"- **{it['title']}** | Link: {it.get('url', f'https://ycombinator.com{sid}')}")
        return "\n".join(out)
    except Exception as e:
        return f"HN API error: {e}"

async def fetch_url_via_mcp(url: str) -> str:
    with open("mcp_config.json") as f:
        cfg = json.load(f)["mcpServers"]["fetch"]
    server_params = StdioServerParameters(command=cfg["command"], args=cfg["args"])
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await asyncio.wait_for(session.call_tool("fetch", {"url": url, "max_length": 3500}), timeout=12.0)
            return "\n".join(c.text for c in res.content if hasattr(c, "text"))

def execute_system_actions(ai_text: str):
    logs = []
    for fact in re.findall(r'\[REMEMBER:\s*(.*?)\]', ai_text):
        save_memory(fact.strip())
        logs.append(f"🧠 **Memory Updated:** *{fact.strip()}*")
    ai_text = re.sub(r'\[REMEMBER:\s*.*?\]', '', ai_text)

    pattern = r'\[SAVE_FILE:\s*([^\]]+)\](.*?)\[/SAVE_FILE\]'
    for filename, content in re.findall(pattern, ai_text, flags=re.DOTALL):
        safe_name = os.path.basename(filename.strip())
        filepath = os.path.join(WORKSPACE_DIR, safe_name)
        mode = "a" if (safe_name.endswith(".csv") and os.path.exists(filepath)) else "w"
        with open(filepath, mode, encoding="utf-8") as f:
            f.write(content.strip() + "\n")
        logs.append(f"💾 **Saved to Workspace:** `workspace/{safe_name}`")
    ai_text = re.sub(pattern, '', ai_text, flags=re.DOTALL)

    clip_match = re.search(r'\[COPY_CLIPBOARD\](.*?)\[/COPY_CLIPBOARD\]', ai_text, flags=re.DOTALL)
    if clip_match:
        clip_text = clip_match.group(1).strip()
        try:
            subprocess.run(["pbcopy"], input=clip_text.encode("utf-8"))
            logs.append("📋 **Copied Pitch/Script to Clipboard!**")
        except Exception:
            pass
    ai_text = re.sub(r'\[COPY_CLIPBOARD\].*?\[/COPY_CLIPBOARD\]', '', ai_text, flags=re.DOTALL)
    return ai_text.strip(), logs

async def execute_goal(prompt):
    if not prompt.strip():
        return "⚠️ Enter a command or tap one of the 8 Action Cards above.", list_workspace_files(), load_memory()
    live_data = ""
    lp = prompt.lower()
    if "[live_jobs]" in lp or "find jobs" in lp:
        live_data += f"\n\n[LIVE REMOTE JOBS]:\n{fetch_live_jobs()}"
    if "[live_bounties]" in lp or "bounties" in lp:
        live_data += f"\n\n[LIVE GITHUB BOUNTIES]:\n{fetch_github_bounties()}"
    if "[live_opps]" in lp or "opportunities" in lp:
        live_data += f"\n\n[LIVE OPPORTUNITIES]:\n{fetch_live_opportunities()}"

    urls = re.findall(r'https?://[^\s]+', prompt)
    if urls:
        try:
            page_text = await fetch_url_via_mcp(urls.rstrip(".,)"))
            live_data += f"\n\n[LIVE MCP FETCH]:\n{page_text[:3000]}"
        except Exception:
            pass

    sys_prompt = (
        f"You are an Action AI for Gen Z. USER MEMORY:\n{load_memory()}\n\n"
        "SYSTEM ACTION COMMANDS:\n"
        "1. Save files: [SAVE_FILE: filename.md]\n<content>\n[/SAVE_FILE] (use .csv for budgets)\n"
        "2. Save facts: [REMEMBER: short fact]\n"
        "3. Copy pitch/script: [COPY_CLIPBOARD]\n<text>\n[/COPY_CLIPBOARD]\n\n"
        f"GOAL: {prompt}{live_data}"
    )

    for m in ["", "gpt-4o-mini", "gpt-4o"]:
        try:
            res = await asyncio.wait_for(
                llm.chat.completions.create(model=m, messages=[{"role": "user", "content": sys_prompt}]),
                timeout=55.0
            )
            raw = res.choices.pop(0).message.content
            if raw:
                clean, logs = execute_system_actions(raw)
                banner = ("\n\n---\n### ✅ Executed System Actions\n" + "\n".join(f"- {x}" for x in logs)) if logs else ""
                return clean + banner, list_workspace_files(), load_memory()
        except Exception:
            continue
    return "⚠️ Free providers are momentarily busy—tap **⚡ Execute Action** once more.", list_workspace_files(), load_memory()

CUSTOM_CSS = """
@import url('https://googleapis.com');
body, .gradio-container { background-color: #09090b !important; color: #f4f4f5 !important; font-family: 'Plus Jakarta Sans', sans-serif !important; }
.gradio-container { max-width: 880px !important; margin: 0 auto !important; padding: 1.25rem !important; }
.hero-card { background: linear-gradient(135deg, #18181b 0%, #09090b 100%); border: 1px solid #27272a; border-radius: 20px; padding: 24px; margin-bottom: 18px; }
.badge-pill { display: inline-block; background: rgba(16, 185, 129, 0.14); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.35); font-size: 11px; font-weight: 800; letter-spacing: 0.08em; padding: 4px 12px; border-radius: 999px; margin-bottom: 10px; }
.hero-title { font-size: 28px !important; font-weight: 800 !important; margin: 0 0 6px 0 !important; color: #ffffff !important; }
.hero-sub { color: #a1a1aa !important; font-size: 14px !important; margin: 0 !important; }
.bento-btn { background: #18181b !important; border: 1px solid #27272a !important; color: #e4e4e7 !important; border-radius: 14px !important; padding: 12px 10px !important; font-weight: 600 !important; font-size: 13px !important; }
.bento-btn:hover { border-color: #10b981 !important; background: #1f2421 !important; }
.primary-run { background: linear-gradient(135deg, #10b981 0%, #059669 100%) !important; color: #09090b !important; font-weight: 800 !important; border: none !important; border-radius: 14px !important; padding: 14px !important; font-size: 15px !important; }
.output-panel { background: #121215 !important; border: 1px solid #27272a !important; border-radius: 18px !important; padding: 20px !important; margin-top: 12px !important; }
footer { display: none !important; }
"""

theme = gr.themes.Base(primary_hue="emerald", neutral_hue="zinc").set(body_background_fill="#09090b", block_background_fill="#18181b", block_border_color="#27272a")

with gr.Blocks() as demo:
    gr.HTML('<div class="hero-card"><span class="badge-pill">● LIVE 24/7 · 100% OPEN-SOURCE MCP ENGINE</span><h1 class="hero-title">⚡ Gen Z Life OS</h1><p class="hero-sub">Not a chatbot that just talks—an autonomous Action Agent that scrapes live jobs, hunts bounties, writes scripts, and builds files.</p></div>')
    with gr.Tabs():
        with gr.Tab("⚡ Command Center"):
            goal_input = gr.Textbox(label="Command / Goal", placeholder="Tap any Bento card below or type a custom action...", lines=2)
            run_btn = gr.Button("⚡ Execute Action", elem_classes="primary-run")
            with gr.Row():
                b1 = gr.Button("💼 Find Jobs", elem_classes="bento-btn")
                b2 = gr.Button("💸 Earn Money", elem_classes="bento-btn")
                b3 = gr.Button("🚀 Opportunities", elem_classes="bento-btn")
                b4 = gr.Button("🎬 Create Content", elem_classes="bento-btn")
            with gr.Row():
                b5 = gr.Button("📊 Manage Money", elem_classes="bento-btn")
                b6 = gr.Button("🏗️ Build Business", elem_classes="bento-btn")
                b7 = gr.Button("🧠 Learn Skills", elem_classes="bento-btn")
                b8 = gr.Button("⚡ Lifestyle", elem_classes="bento-btn")
            with gr.Group(elem_classes="output-panel"):
                output_box = gr.Markdown("*Ready. Select a pillar or type an action above to begin.*")
        with gr.Tab("📂 Saved Files & Vault"):
            files_box = gr.Markdown(value=list_workspace_files)
            with gr.Row():
                file_name_in = gr.Textbox(label="File Name", placeholder="e.g. live_jobs.md", scale=3)
                view_btn = gr.Button("👁️ Open File", elem_classes="bento-btn", scale=1)
            file_preview = gr.Markdown()
        with gr.Tab("🧠 AI Memory"):
            memory_box = gr.Markdown(value=load_memory)

    run_btn.click(execute_goal, inputs=goal_input, outputs=[output_box, files_box, memory_box])
    view_btn.click(read_workspace_file, inputs=file_name_in, outputs=file_preview)
    b1.click(lambda: "[LIVE_JOBS] Pick top 3 remote jobs with links, put a ready-to-send pitch in [COPY_CLIPBOARD], and save all to live_jobs.md", outputs=goal_input)
    b2.click(lambda: "[LIVE_BOUNTIES] Pick top 3 GitHub paid bounties with links, put a proposal comment in [COPY_CLIPBOARD], and save to live_bounties.md", outputs=goal_input)
    b3.click(lambda: "[LIVE_OPPS] Extract top 4 live tech opportunities with links and save to live_opportunities.md", outputs=goal_input)
    b4.click(lambda: "Write 3 viral 30s Reel scripts on free AI tools, put the best script in [COPY_CLIPBOARD], and save all to reel_scripts.md", outputs=goal_input)
    b5.click(lambda: "Create or update a monthly student budget CSV (Date,Category,Item,Amount_INR) and save to budget_tracker.csv", outputs=goal_input)
    b6.click(lambda: "Create a 48-hour zero-cost digital product launch plan and save to business_launch.md", outputs=goal_input)
    b7.click(lambda: "Remember my goal to master AI automation, and save a 7-day skill roadmap to skill_roadmap.md", outputs=goal_input)
    b8.click(lambda: "Build a high-focus daily routine for deep work, fitness, and study and save to daily_routine.md", outputs=goal_input)

demo.launch(server_name="0.0.0.0", server_port=int(os.environ.get("PORT", 7860)), theme=theme, css=CUSTOM_CSS)

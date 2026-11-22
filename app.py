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
        return "No saved profile facts yet."
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
        return "*No files created yet.*"
    return "\n".join(
        f"- 📄 **`{fn}`** ({os.path.getsize(os.path.join(WORKSPACE_DIR, fn))} bytes)"
        for fn in files
    )

def read_workspace_file(filename: str) -> str:
    if not filename:
        return "Select a file name above to preview its contents."
    fpath = os.path.join(WORKSPACE_DIR, os.path.basename(filename.strip()))
    if not os.path.exists(fpath):
        return f"File `{filename}` not found in workspace."
    with open(fpath, "r", encoding="utf-8") as f:
        return f"### 📄 Preview: `{filename}`\n\n```text\n{f.read()}\n```"

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
        ids = requests.get("https://firebaseio.com", timeout=8).json()[:8]
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
    # 1. Memory Saves
    for fact in re.findall(r'\[REMEMBER:\s*(.*?)\]', ai_text):
        save_memory(fact.strip())
        logs.append(f"🧠 Saved to Memory: *{fact.strip()}*")
    ai_text = re.sub(r'\[REMEMBER:\s*.*?\]', '', ai_text)

    # 2. File Saves
    pattern = r'\[SAVE_FILE:\s*([^\]]+)\](.*?)\[/SAVE_FILE\]'
    for filename, content in re.findall(pattern, ai_text, flags=re.DOTALL):
        safe_name = os.path.basename(filename.strip())
        filepath = os.path.join(WORKSPACE_DIR, safe_name)
        mode = "a" if (safe_name.endswith(".csv") and os.path.exists(filepath)) else "w"
        with open(filepath, mode, encoding="utf-8") as f:
            f.write(content.strip() + "\n")
        logs.append(f"💾 Saved File: **`workspace/{safe_name}`**")
    ai_text = re.sub(pattern, '', ai_text, flags=re.DOTALL)

    # 3. Copy Pitch/Script to Mac Clipboard
    clip_match = re.search(r'\[COPY_CLIPBOARD\](.*?)\[/COPY_CLIPBOARD\]', ai_text, flags=re.DOTALL)
    if clip_match:
        clip_text = clip_match.group(1).strip()
        try:
            subprocess.run(["pbcopy"], input=clip_text.encode("utf-8"))
        except Exception:
            pass
        logs.append("📋 **Copied ready-to-send pitch/script to your Mac Clipboard!** (Just press `Cmd+V`)")
    ai_text = re.sub(r'\[COPY_CLIPBOARD\].*?\[/COPY_CLIPBOARD\]', '', ai_text, flags=re.DOTALL)

    # 4. Open Best Action URL in Browser
    for url in re.findall(r'\[OPEN_URL:\s*(https?://[^\]]+)\]', ai_text):
        webbrowser.open(url.strip())
        logs.append(f"🌐 **Opened in Browser:** `{url.strip()}`")
    ai_text = re.sub(r'\[OPEN_URL:\s*.*?\]', '', ai_text)

    return ai_text.strip(), logs

async def execute_goal(prompt):
    if not prompt.strip():
        return "Please type a goal or click a pillar button!", list_workspace_files(), load_memory()
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
        "SYSTEM ACTION COMMANDS YOU CAN TRIGGER:\n"
        "1. Save outputs: [SAVE_FILE: filename.md]\n<content>\n[/SAVE_FILE] (use .csv for budgets)\n"
        "2. Save facts: [REMEMBER: short fact]\n"
        "3. Copy best pitch/script to clipboard: [COPY_CLIPBOARD]\n<ready-to-paste text>\n[/COPY_CLIPBOARD]\n"
        "4. If user says 'open best', launch the top URL: [OPEN_URL: https://...]\n\n"
        f"GOAL: {prompt}{live_data}"
    )

    # Try auto-routed default provider first (most reliable), then fallbacks
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
        except Exception as e:
            print(f"Attempt ({m or 'auto'}) failed: {e}")
            continue
    return "⚠️ Providers timed out—click **🚀 Execute & Save Action** once more.", list_workspace_files(), load_memory()

with gr.Blocks() as demo:
    gr.Markdown("# ⚡ Gen Z Life OS — Complete 8-Pillar Action Agent")
    goal_input = gr.Textbox(label="What should your AI execute today?", lines=2)
    run_btn = gr.Button("🚀 Execute & Save Action", variant="primary")
    with gr.Row():
        b1 = gr.Button("💼 Find Jobs (Live)")
        b2 = gr.Button("💸 Earn Money (Bounties)")
        b3 = gr.Button("🚀 Opportunities")
        b4 = gr.Button("🎬 Create Content")
    with gr.Row():
        b5 = gr.Button("📊 Manage Money")
        b6 = gr.Button("🏗️ Build Business")
        b7 = gr.Button("🧠 Learn Skills")
        b8 = gr.Button("⚡ Lifestyle")
    output_box = gr.Markdown()
    with gr.Row():
        with gr.Column():
            gr.Markdown("### 📂 Workspace Files")
            files_box = gr.Markdown(value=list_workspace_files)
            file_name_in = gr.Textbox(label="Preview a file (e.g. budget_tracker.csv)", placeholder="reel_scripts.md")
            view_btn = gr.Button("👁️ Read File")
            file_preview = gr.Markdown()
        with gr.Column():
            gr.Markdown("### 🧠 Persistent Memory")
            memory_box = gr.Markdown(value=load_memory)

    run_btn.click(execute_goal, inputs=goal_input, outputs=[output_box, files_box, memory_box])
    view_btn.click(read_workspace_file, inputs=file_name_in, outputs=file_preview)
    b1.click(lambda: "[LIVE_JOBS] Pick top 3 remote jobs with links, put a ready-to-send pitch in [COPY_CLIPBOARD], and save all to live_jobs.md", outputs=goal_input)
    b2.click(lambda: "[LIVE_BOUNTIES] Pick top 3 GitHub paid bounties with links, put a proposal comment in [COPY_CLIPBOARD], and save to live_bounties.md", outputs=goal_input)
    b3.click(lambda: "[LIVE_OPPS] Extract top 4 live tech opportunities with links and save to live_opportunities.md", outputs=goal_input)
    b4.click(lambda: "Write 3 viral 30s Reel scripts on free AI tools, put the best script in [COPY_CLIPBOARD], and save all to reel_scripts.md", outputs=goal_input)
    b5.click(lambda: "Add today's expenses (₹150 lunch, ₹99 mobile data) to budget_tracker.csv and summarize my spending.", outputs=goal_input)
    b6.click(lambda: "Create a 48-hour zero-cost digital product launch plan and save to business_launch.md", outputs=goal_input)
    b7.click(lambda: "Remember my goal to master AI automation, and save a 7-day skill roadmap to skill_roadmap.md", outputs=goal_input)
    b8.click(lambda: "Build a daily focus & fitness schedule and save to daily_routine.md", outputs=goal_input)

demo.launch(server_name="0.0.0.0", server_port=int(os.environ.get("PORT", 7860)), theme=gr.themes.Soft(primary_hue="emerald", neutral_hue="zinc"))

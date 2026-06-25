import os, re, json, asyncio, time, random, subprocess, requests
from playwright.async_api import async_playwright

BASE_URL = "https://www.ting13.cc"
BOOK_KEY = os.environ.get("BOOK_KEY", "赘婿")
# 目录页 URL（已更新）
BOOK_URLS = {
    "赘婿": f"{BASE_URL}/tingdirs/uiPlHh/cbbhASacUDuhOsFb.html"
}
BASE_DIR_URL = BOOK_URLS[BOOK_KEY]
MAX_PER_RUN = 60                    # 每次下载一整页（60集）
PROGRESS_FILE = "progress.json"
PRIVATE_REPO = os.environ["PRIVATE_REPO"]
ACCESS_TOKEN = os.environ["ACCESS_TOKEN"]
TARGET_DIR = f"public/{BOOK_KEY}"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"

def load_progress():
    if not os.path.exists(PROGRESS_FILE):
        return {}
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_progress(prog):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False, indent=2)

def sanitize_filename(title):
    name = re.sub(r'[\\/*?:"<>|]', "", title)
    return name[:80].strip()

# ===== 登录 =====
async def login(playwright):
    raw = os.environ.get("TING13", "")
    if "-----" not in raw:
        raise RuntimeError("TING13 格式错误")
    username, password = raw.split("-----", 1)

    browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-http2", "--disable-gpu"])
    context = await browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 720})
    page = await context.new_page()

    print("🔐 正在打开登录页面...")
    await page.goto(f"{BASE_URL}/user/public/login.html", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_selector("#slider", state="visible", timeout=15000)
    await asyncio.sleep(2)

    await page.fill('input[name="username"]', username)
    await page.fill('input[name="password"]', password)

    print("  使用 JS 完成滑块验证...")
    await page.evaluate('''() => {
        const slider = document.getElementById('slider');
        const container = slider.parentElement;
        slider.style.left = (container.offsetWidth - slider.offsetWidth) + 'px';
        document.getElementById('sliderText').innerText = '验证通过';
        const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
        let token = '';
        for (let i = 0; i < 16; i++) token += chars.charAt(Math.floor(Math.random() * chars.length));
        document.getElementById('verificationToken').value = token;
        document.getElementById('loginButton').disabled = false;
        window._loginToken = token;
    }''')
    token = await page.evaluate("() => window._loginToken")
    await page.evaluate('''async (token) => {
        await fetch('/user/public/store_token.html', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({token: token})
        });
    }''', token)

    print("  提交登录表单...")
    async with page.expect_navigation(url="**/user/index/index.html", wait_until="domcontentloaded", timeout=30000):
        await page.evaluate("document.getElementById('frmpassedit').submit()")
    print("✅ 登录成功")

    cookies = await context.cookies()
    cookie_dict = {c['name']: c['value'] for c in cookies}
    await page.close()
    await context.close()
    return browser, cookie_dict

# ===== 获取指定页的章节列表（带重试和限流检测） =====
async def fetch_page_chapters(browser, cookies_dict, page_num):
    context = await browser.new_context(user_agent=USER_AGENT)
    await context.add_cookies([
        {"name": k, "value": v, "domain": ".ting13.cc", "path": "/"}
        for k, v in cookies_dict.items()
    ])
    page = await context.new_page()

    url = f"{BASE_DIR_URL}?page={page_num}&sort=asc"
    for attempt in range(3):
        try:
            await asyncio.sleep(random.uniform(2, 4))
            print(f"  请求目录页: {url} (尝试 {attempt+1}/3)")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if "请求过于频繁" in await page.title():
                print("  ⚠️ 被限流，等待 30 秒后重试...")
                await asyncio.sleep(30)
                continue
            await page.wait_for_selector("#playlist", state="visible", timeout=15000)
            chapters = await page.evaluate('''() => {
                const items = document.querySelectorAll("#playlist ul li a");
                return Array.from(items).map(a => ({
                    title: a.getAttribute("title") || a.innerText.trim(),
                    url: a.href
                }));
            }''')
            await page.close()
            await context.close()
            return chapters
        except Exception as e:
            print(f"  失败: {e}")
            await asyncio.sleep(5)
    await page.close()
    await context.close()
    raise RuntimeError(f"无法获取第 {page_num} 页章节")

# ===== 音频地址获取 =====
async def fetch_audio_url(browser, play_url, cookies_dict):
    context = await browser.new_context(user_agent=USER_AGENT)
    await context.add_cookies([
        {"name": k, "value": v, "domain": ".ting13.cc", "path": "/"}
        for k, v in cookies_dict.items()
    ])
    page = await context.new_page()
    captured = {}

    async def on_response(resp):
        if "/api/mapi/play" in resp.url and resp.status == 200 and not captured:
            try:
                data = await resp.json()
                if data.get("status") == 200:
                    captured["name"] = data.get("name")
                    captured["url"] = data.get("url")
            except:
                pass

    page.on("response", on_response)
    try:
        await page.goto(play_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        await page.evaluate("() => { const btn = document.querySelector('.play-btn,#playButton,.audio-play'); if(btn) btn.click(); }")
        await asyncio.sleep(3)
    except Exception as e:
        print(f"    播放页异常: {e}")
    finally:
        page.remove_listener("response", on_response)
        await page.close()
        await context.close()
    return captured.get("name", ""), captured.get("url", "")

# ===== 下载音频 =====
def download_audio(url, filepath):
    if os.path.exists(filepath):
        os.remove(filepath)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=120)
    resp.raise_for_status()
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(8192):
            f.write(chunk)
    return True

# ===== Git 操作 =====
def clone_private_repo():
    subprocess.run(["rm", "-rf", "/tmp/private_repo"], check=False)
    subprocess.run(["git", "clone", "--depth", "1", f"https://{ACCESS_TOKEN}@github.com/{PRIVATE_REPO}.git", "/tmp/private_repo"], check=True)
    return "/tmp/private_repo"

def commit_and_push(repo_path, msg):
    subprocess.run(["git", "-C", repo_path, "config", "user.email", "actions@github.com"], check=True)
    subprocess.run(["git", "-C", repo_path, "config", "user.name", "GitHub Actions"], check=True)
    subprocess.run(["git", "-C", repo_path, "add", "."], check=True)
    status = subprocess.run(["git", "-C", repo_path, "status", "--porcelain"], capture_output=True, text=True).stdout
    if status.strip():
        subprocess.run(["git", "-C", repo_path, "commit", "-m", msg], check=True)
        subprocess.run(["git", "-C", repo_path, "push", "origin", "main"], check=True)
        print("✅ 私有仓库更新已推送")
    else:
        print("ℹ️ 无变更")

def update_index_json(repo_path, entries):
    idx_dir = os.path.join(repo_path, TARGET_DIR)
    os.makedirs(idx_dir, exist_ok=True)
    idx_path = os.path.join(idx_dir, "index.json")
    existing = []
    if os.path.exists(idx_path):
        with open(idx_path, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
            except:
                pass
    # 移除旧 title
    for item in existing:
        item.pop("title", None)

    eps = {e["episode"] for e in existing}
    for e in entries:
        if e["episode"] not in eps:
            existing.append(e)
            eps.add(e["episode"])

    existing.sort(key=lambda x: x["episode"])
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f"📄 index.json 更新至 {len(existing)} 集")

# ===== 主流程 =====
async def main():
    # 确保 progress.json 存在
    if not os.path.exists(PROGRESS_FILE):
        save_progress({})

    progress = load_progress()
    bp = progress.get(BOOK_KEY, {"last_index": 0})
    start = bp["last_index"] + 1
    print(f"📖 {BOOK_KEY} 已爬 {bp['last_index']} 集，从第 {start} 集开始")

    async with async_playwright() as p:
        browser, cookies = await login(p)

        # 计算当前页码（每页60集）
        page_num = (start - 1) // 60 + 1
        print(f"⚡ 本次将抓取第 {page_num} 页（60集）")

        try:
            page_chapters = await fetch_page_chapters(browser, cookies, page_num)
        except Exception as e:
            print(f"❌ 获取目录失败: {e}")
            await browser.close()
            return

        if not page_chapters:
            print("❌ 未获取到章节数据（可能已到达最后一页）")
            await browser.close()
            return

        # 整页下载（从该页第1集开始，到最后一集）
        start_ep = (page_num - 1) * 60 + 1
        end_ep = start_ep + len(page_chapters) - 1
        print(f"📥 将下载第 {start_ep} ~ {end_ep} 集")

        repo = clone_private_repo()
        entries = []
        for i, ch in enumerate(page_chapters):
            ep = start_ep + i
            print(f"\n🎯 第{ep}集: {ch['title']}")
            name, url = await fetch_audio_url(browser, ch["url"], cookies)
            if not url:
                print("   ⚠️ 未获取到音频链接")
                continue
            base_name = sanitize_filename(name or ch['title'])
            fname = base_name + ".m4a"
            dest = os.path.join(repo, TARGET_DIR, fname)
            try:
                download_audio(url, dest)
                print(f"   ✅ 下载成功: {fname}")
            except Exception as e:
                print(f"   ❌ 下载失败: {e}")
                if os.path.exists(dest):
                    os.remove(dest)
                continue
            entries.append({
                "name": base_name,
                "episode": ep,
                "url": f"{BOOK_KEY}/{fname}"
            })
            await asyncio.sleep(random.uniform(1, 2))

        if entries:
            update_index_json(repo, entries)
            commit_and_push(repo, f"抓取 {BOOK_KEY} 第{start_ep}-{end_ep}集")
            # 更新进度为最后一集
            bp["last_index"] = end_ep
            progress[BOOK_KEY] = bp
            save_progress(progress)
            print(f"📈 进度已更新: last_index={end_ep}")
        else:
            print("ℹ️ 无新文件，进度未更新")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())

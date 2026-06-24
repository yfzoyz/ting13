import os, re, json, asyncio, time, subprocess, requests
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

BASE_URL = "https://www.ting13.cc"
BOOK_KEY = os.environ.get("BOOK_KEY", "赘婿")
BOOK_URLS = {"赘婿": f"{BASE_URL}/tingdirs/uiPlHh/cbbhASacUDuaQoFc.html"}
BASE_DIR_URL = BOOK_URLS[BOOK_KEY]
MAX_PER_RUN = 50
PROGRESS_FILE = "progress.json"
PRIVATE_REPO = os.environ["PRIVATE_REPO"]
ACCESS_TOKEN = os.environ["ACCESS_TOKEN"]
TARGET_DIR = f"public/{BOOK_KEY}"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"

def load_progress():
    if not os.path.exists(PROGRESS_FILE): return {}
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f: return json.load(f)

def save_progress(prog):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f: json.dump(prog, f, ensure_ascii=False, indent=2)

def sanitize_filename(title):
    name = re.sub(r'[\\/*?:"<>|]', "", title)
    return name[:80].strip()

# ===== 登录 (Playwright)，返回 browser 和 cookies =====
async def login(playwright):
    raw = os.environ.get("TING13", "")
    if "-----" not in raw: raise RuntimeError("TING13 格式错误")
    username, password = raw.split("-----", 1)

    browser = await playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-http2", "--disable-gpu"]
    )
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1280, "height": 720}
    )
    page = await context.new_page()

    print("🔐 正在打开登录页面...")
    await page.goto(f"{BASE_URL}/user/public/login.html", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_selector("#slider", state="visible", timeout=15000)
    await asyncio.sleep(2)

    await page.fill('input[name="username"]', username)
    await page.fill('input[name="password"]', password)

    # 滑块验证（直接使用 JS 强制通过）
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
        // 存储 token 供后续 store_token 使用
        window._loginToken = token;
    }''')
    await asyncio.sleep(0.5)
    token = await page.evaluate("() => window._loginToken")

    # 发送 store_token
    await page.evaluate('''async (token) => {
        await fetch('/user/public/store_token.html', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({token: token})
        });
    }''', token)

    # 提交登录表单
    print("  提交登录表单...")
    async with page.expect_navigation(url="**/user/index/index.html", wait_until="domcontentloaded", timeout=30000):
        await page.evaluate("document.getElementById('frmpassedit').submit()")
    print("✅ 登录成功")

    # 获取 cookies 字典（备用）
    cookies = await context.cookies()
    cookie_dict = {c['name']: c['value'] for c in cookies}
    # 不关闭 browser，直接返回 browser 和 context（context 会自动共享 Cookie）
    return browser, cookie_dict

# ===== 目录抓取（使用 Playwright 页面） =====
async def fetch_chapters(browser, cookies_dict):
    """使用已登录的 browser 创建新页面，抓取所有章节"""
    context = await browser.new_context(user_agent=USER_AGENT)  # 新建上下文，会自动继承 browser 的 Cookie？不，需要手动设置
    # 更安全：手动添加 cookies
    await context.add_cookies([
        {"name": k, "value": v, "domain": ".ting13.cc", "path": "/"}
        for k, v in cookies_dict.items()
    ])
    page = await context.new_page()

    chapters = []
    max_page = 1

    print("正在获取首页目录...")
    await page.goto(f"{BASE_DIR_URL}?page=1&sort=asc", wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_selector("#playlist", timeout=15000)

    # 解析总页数
    page_links = await page.query_selector_all(".chapter-list-block li a")
    for a in page_links:
        href = await a.get_attribute("href")
        if href and (m := re.search(r"page=(\d+)", href)):
            max_page = max(max_page, int(m.group(1)))
    print(f"📖 共 {max_page} 页")

    # 解析当前页章节
    async def parse_current_page():
        items = await page.query_selector_all("#playlist ul li a")
        chs = []
        for a in items:
            title = await a.get_attribute("title") or ""
            url = await a.get_attribute("href") or ""
            if url:
                chs.append({"title": title.strip(), "url": BASE_URL + url})
        return chs

    chs = await parse_current_page()
    chapters.extend(chs)
    print(f"  第1页获取 {len(chs)} 集")

    for pg in range(2, max_page + 1):
        print(f"  抓取第 {pg}/{max_page} 页...", end=" ")
        try:
            await page.goto(f"{BASE_DIR_URL}?page={pg}&sort=asc", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_selector("#playlist", timeout=10000)
            chs = await parse_current_page()
            chapters.extend(chs)
            print(f"获取 {len(chs)} 集")
        except Exception as e:
            print(f"失败: {e}")
        await asyncio.sleep(1)

    await page.close()
    await context.close()
    return chapters

# ===== 音频地址获取 =====
async def fetch_audio_url(browser, play_url, cookies_dict):
    """创建一个新的页面，拦截 API"""
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

def download_audio(url, filepath):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=120)
    resp.raise_for_status()
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(8192): f.write(chunk)
    return True

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
            try: existing = json.load(f)
            except: pass
    eps = {e["episode"] for e in existing}
    for e in entries:
        if e["episode"] not in eps:
            existing.append(e)
            eps.add(e["episode"])
    existing.sort(key=lambda x: x["episode"])
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f"📄 index.json 更新至 {len(existing)} 集")

async def main():
    progress = load_progress()
    bp = progress.get(BOOK_KEY, {"last_index": 0, "total_chapters": 0})
    start = bp["last_index"] + 1
    print(f"📖 {BOOK_KEY} 已爬 {bp['last_index']} 集，从第 {start} 集开始")

    async with async_playwright() as p:
        browser, cookies = await login(p)

        # 目录抓取
        chapters = await fetch_chapters(browser, cookies)
        total = len(chapters)
        print(f"📚 共获取 {total} 章节")
        if not total:
            await browser.close()
            return
        bp["total_chapters"] = total
        end = min(start + MAX_PER_RUN - 1, total)
        if start > total:
            print("✅ 已全部爬完")
            await browser.close()
            return
        print(f"⚡ 本次: 第{start}-{end}集")

        repo = clone_private_repo()
        entries = []
        for i in range(start - 1, end):
            ch = chapters[i]; ep = i + 1
            print(f"\n🎯 第{ep}集: {ch['title']}")
            name, url = await fetch_audio_url(browser, ch["url"], cookies)
            if not url:
                print("   ⚠️ 未获取到音频链接"); continue
            fname = sanitize_filename(name or ch['title']) + ".m4a"
            dest = os.path.join(repo, TARGET_DIR, fname)
            try:
                download_audio(url, dest)
                print(f"   ✅ 下载成功: {fname}")
            except Exception as e:
                print(f"   ❌ 下载失败: {e}"); continue
            entries.append({
                "name": fname[:-4],
                "title": name or ch['title'],
                "episode": ep,
                "url": f"{BOOK_KEY}/{fname}"
            })
            await asyncio.sleep(1)

        if entries:
            update_index_json(repo, entries)
            commit_and_push(repo, f"抓取 {BOOK_KEY} 第{start}-{end}集")
            bp["last_index"] = end
            progress[BOOK_KEY] = bp
            save_progress(progress)
            print(f"📈 进度已更新: last_index={end}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())

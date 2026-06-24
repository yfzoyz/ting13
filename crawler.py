import os, re, json, asyncio, time, subprocess, requests
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

# ===== 配置 =====
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

# ===== Playwright 登录 =====
async def login(playwright):
    raw = os.environ.get("TING13", "")
    if "-----" not in raw: raise RuntimeError("TING13 格式错误，应为 账号-----密码")
    username, password = raw.split("-----", 1)

    browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-http2", "--disable-gpu"])
    context = await browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 720})
    page = await context.new_page()

    print("🔐 正在打开登录页面...")
    await page.goto(f"{BASE_URL}/user/public/login.html", wait_until="networkidle")
    await asyncio.sleep(2)

    await page.fill('input[name="username"]', username)
    await page.fill('input[name="password"]', password)

    # 滑块验证
    slider = page.locator("#slider")
    container = page.locator(".slider-container")
    await slider.wait_for(state="visible")

    # 多次尝试拖拽，直到显示“验证通过”
    for attempt in range(3):
        slider_box = await slider.bounding_box()
        cont_box = await container.bounding_box()
        if not slider_box or not cont_box:
            raise RuntimeError("未找到滑块元素")

        start_x = slider_box['x'] + slider_box['width'] / 2
        start_y = slider_box['y'] + slider_box['height'] / 2
        end_x = cont_box['x'] + cont_box['width'] - slider_box['width'] / 2

        print(f"  尝试拖拽 (第{attempt+1}次)...")
        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        # 小步移动，模拟真人
        steps = 40
        for i in range(1, steps + 1):
            x = start_x + (end_x - start_x) * i / steps
            await page.mouse.move(x, start_y)
            await asyncio.sleep(0.02)  # 20ms 每步
        await page.mouse.up()
        await asyncio.sleep(1)

        text = await page.text_content("#sliderText")
        print(f"  滑块状态: {text}")
        if "验证通过" in text:
            break
    else:
        # 如果三次都失败，尝试用 JS 强制触发
        print("  常规拖拽失败，尝试 JS 强制验证...")
        await page.evaluate('''() => {
            const slider = document.getElementById('slider');
            const container = slider.parentElement;
            slider.style.left = (container.offsetWidth - slider.offsetWidth) + 'px';
            const evt = new Event('input', { bubbles: true });
            slider.dispatchEvent(evt);
            // 模拟 touchend / mouseup
            ['touchend', 'mouseup'].forEach(type => {
                const e = new Event(type, { bubbles: true });
                slider.dispatchEvent(e);
            });
            // 更新文本
            const text = document.getElementById('sliderText');
            if (text) text.innerText = '验证通过';
        }''')
        await asyncio.sleep(1)
        text = await page.text_content("#sliderText")
        print(f"  JS 后状态: {text}")

    if "验证通过" not in await page.text_content("#sliderText"):
        raise RuntimeError("滑块验证失败，无法继续登录")

    # 验证通过后，页面通常会自动提交，等待跳转
    print("  等待登录跳转...")
    try:
        await page.wait_for_url("**/user/index/index.html", timeout=30000)
    except:
        print("  未自动跳转，尝试手动点击登录按钮...")
        btn = page.locator("#loginButton")
        if await btn.is_enabled():
            await btn.click()
            await page.wait_for_url("**/user/index/index.html", timeout=15000)
        else:
            raise RuntimeError("登录失败，按钮仍不可用")
    print("✅ 登录成功")

    cookies = await context.cookies()
    await browser.close()
    return {c['name']: c['value'] for c in cookies}

# ===== 获取目录 =====
async def fetch_chapters(playwright, cookies_dict):
    req_ctx = await playwright.request.new_context(user_agent=USER_AGENT)
    await req_ctx.add_cookies([{"name": k, "value": v, "domain": ".ting13.cc", "path": "/"} for k, v in cookies_dict.items()])

    chapters = []
    max_page = 1
    resp = await req_ctx.get(f"{BASE_DIR_URL}?page=1&sort=asc")
    soup = BeautifulSoup(await resp.text(), 'html.parser')
    for a in soup.select(".chapter-list-block li a"):
        if m := re.search(r"page=(\d+)", a.get("href", "")):
            max_page = max(max_page, int(m.group(1)))
    print(f"📖 共 {max_page} 页")

    def parse_page(html):
        s = BeautifulSoup(html, 'html.parser')
        div = s.find("div", id="playlist")
        if not div: return []
        return [{"title": a.get("title","").strip(), "url": BASE_URL + a["href"]} for li in div.find_all("li") if (a := li.find("a")) and a.get("href")]

    chapters += parse_page(await resp.text())
    print(f"  第1页: {len(chapters)} 集")

    for pg in range(2, max_page+1):
        resp = await req_ctx.get(f"{BASE_DIR_URL}?page={pg}&sort=asc")
        if resp.status != 200:
            print(f"  第{pg}页 失败 {resp.status}")
            continue
        chs = parse_page(await resp.text())
        chapters += chs
        print(f"  第{pg}页: {len(chs)} 集")
        await asyncio.sleep(1)

    await req_ctx.dispose()
    return chapters

# ===== 获取音频地址 =====
async def fetch_audio_url(playwright, play_url, cookies_dict):
    browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-http2", "--disable-gpu"])
    context = await browser.new_context(user_agent=USER_AGENT)
    await context.add_cookies([{"name": k, "value": v, "domain": ".ting13.cc", "path": "/"} for k, v in cookies_dict.items()])
    page = await context.new_page()
    captured = {}

    async def on_response(resp):
        if "/api/mapi/play" in resp.url and resp.status == 200 and not captured:
            try:
                data = await resp.json()
                if data.get("status") == 200:
                    captured["name"] = data.get("name")
                    captured["url"] = data.get("url")
            except: pass

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
        await browser.close()
    return captured.get("name", ""), captured.get("url", "")

# ===== 下载音频 =====
def download_audio(url, filepath):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=120)
    resp.raise_for_status()
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(8192): f.write(chunk)
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

# ===== 主流程 =====
async def main():
    progress = load_progress()
    bp = progress.get(BOOK_KEY, {"last_index": 0, "total_chapters": 0})
    start = bp["last_index"] + 1
    print(f"📖 {BOOK_KEY} 已爬 {bp['last_index']} 集，从第 {start} 集开始")

    async with async_playwright() as p:
        cookies = await login(p)
        chapters = await fetch_chapters(p, cookies)
        total = len(chapters)
        print(f"📚 共获取 {total} 章节")
        if not total: return
        bp["total_chapters"] = total
        end = min(start + MAX_PER_RUN - 1, total)
        if start > total:
            print("✅ 已全部爬完")
            return
        print(f"⚡ 本次: 第{start}-{end}集")

        repo = clone_private_repo()
        entries = []
        for i in range(start-1, end):
            ch = chapters[i]
            ep = i+1
            print(f"\n🎯 第{ep}集: {ch['title']}")
            name, url = await fetch_audio_url(p, ch["url"], cookies)
            if not url:
                print("   ⚠️ 未获取到音频链接")
                continue
            fname = sanitize_filename(name or ch['title']) + ".m4a"
            dest = os.path.join(repo, TARGET_DIR, fname)
            try:
                download_audio(url, dest)
                print(f"   ✅ 下载成功: {fname}")
            except Exception as e:
                print(f"   ❌ 下载失败: {e}")
                continue
            entries.append({"name": fname[:-4], "title": name or ch['title'], "episode": ep, "url": f"{BOOK_KEY}/{fname}"})
            await asyncio.sleep(1)

        if entries:
            update_index_json(repo, entries)
            commit_and_push(repo, f"抓取 {BOOK_KEY} 第{start}-{end}集")
            bp["last_index"] = end
            progress[BOOK_KEY] = bp
            save_progress(progress)
            print(f"📈 进度已更新: last_index={end}")

if __name__ == "__main__":
    asyncio.run(main())

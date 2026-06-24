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

# ===== 登录 =====
async def login(playwright):
    raw = os.environ.get("TING13", "")
    if "-----" not in raw:
        raise RuntimeError("TING13 格式错误")
    username, password = raw.split("-----", 1)

    browser = await playwright.chromium.launch(
        headless=True, args=["--no-sandbox", "--disable-http2", "--disable-gpu"]
    )
    context = await browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 720})
    page = await context.new_page()

    print("🔐 正在打开登录页面...")
    await page.goto(f"{BASE_URL}/user/public/login.html", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_selector("#slider", state="visible", timeout=15000)
    await asyncio.sleep(2)

    await page.fill('input[name="username"]', username)
    await page.fill('input[name="password"]', password)
    await asyncio.sleep(0.5)

    print("  使用 JS 完整模拟登录流程...")
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

    await asyncio.sleep(0.5)
    token = await page.evaluate("() => window._loginToken")
    print(f"  生成 token: {token}")

    print("  POST store_token.html...")
    store_resp = await page.evaluate('''async (token) => {
        try {
            const resp = await fetch('/user/public/store_token.html', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({token: token})
            });
            return {status: resp.status, ok: resp.ok};
        } catch(e) { return {error: e.toString()}; }
    }''', token)
    print(f"  store_token 响应: {store_resp}")
    await asyncio.sleep(0.5)

    print("  提交登录表单，等待跳转...")
    try:
        async with page.expect_navigation(
            url="**/user/index/index.html",
            wait_until="domcontentloaded",
            timeout=30000
        ):
            await page.evaluate("() => document.getElementById('frmpassedit').submit()")
        print("✅ 登录成功（form submit）")
    except Exception as e:
        print(f"  form submit 超时: {e}")
        if "user/index" not in page.url:
            try:
                async with page.expect_navigation(
                    url="**/user/index/index.html",
                    wait_until="domcontentloaded",
                    timeout=15000
                ):
                    await page.click("#loginButton")
            except Exception as e2:
                print(f"  点击按钮也失败: {e2}")
                cookies_check = await context.cookies()
                if "PTCMS_userid" not in [c['name'] for c in cookies_check]:
                    await browser.close()
                    raise RuntimeError(f"登录失败，当前页面: {page.url}")

    cookies = await context.cookies()
    cookie_dict = {c['name']: c['value'] for c in cookies}
    if "PTCMS_userid" not in cookie_dict:
        await browser.close()
        raise RuntimeError("登录失败：未获取到 PTCMS_userid cookie")

    print(f"✅ 登录验证通过，用户ID: {cookie_dict.get('PTCMS_userid')}")
    await browser.close()
    return cookie_dict


# ===== 目录抓取：用 requests，修正选择器 =====
def fetch_chapters_sync(cookies_dict):
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    for name, value in cookies_dict.items():
        session.cookies.set(name, value, domain="www.ting13.cc")

    def parse_chapters(html):
        """解析 #playlist ul li a，获取章节链接"""
        s = BeautifulSoup(html, 'html.parser')
        playlist_div = s.find("div", id="playlist")
        if not playlist_div:
            print("  ⚠️ 未找到 #playlist div")
            return []
        results = []
        for li in playlist_div.find_all("li"):
            a = li.find("a")
            if not a or not a.get("href"):
                continue
            href = a["href"].strip()
            if not href.startswith("/play/"):
                continue
            title = a.get("title", "").strip() or a.get_text(strip=True)
            results.append({"title": title, "url": BASE_URL + href})
        return results

    def get_max_page(html):
        """从 .js_chapter_ul 里解析最大页码"""
        s = BeautifulSoup(html, 'html.parser')
        max_page = 1
        # 找分页导航：.chapter-list-block 或 .js_chapter_ul
        ul = s.find("ul", class_="js_chapter_ul")
        if ul:
            for a in ul.find_all("a", href=True):
                m = re.search(r"page=(\d+)", a["href"])
                if m:
                    max_page = max(max_page, int(m.group(1)))
        return max_page

    # 第一页
    url1 = f"{BASE_DIR_URL}?page=1&sort=asc"
    print(f"  请求: {url1}")
    resp = session.get(url1, timeout=30)
    resp.raise_for_status()
    resp.encoding = 'utf-8'

    max_page = get_max_page(resp.text)
    print(f"📖 共 {max_page} 页")

    chapters = parse_chapters(resp.text)
    print(f"  第1页: {len(chapters)} 集")

    for pg in range(2, max_page + 1):
        try:
            url_pg = f"{BASE_DIR_URL}?page={pg}&sort=asc"
            resp = session.get(url_pg, timeout=30)
            resp.raise_for_status()
            resp.encoding = 'utf-8'
            chs = parse_chapters(resp.text)
            chapters += chs
            print(f"  第{pg}页: {len(chs)} 集")
            time.sleep(1)
        except Exception as e:
            print(f"  第{pg}页 失败: {e}")

    return chapters


# ===== 音频地址：监听 /api/mapi/play 响应 =====
async def fetch_audio_url(playwright, play_url, cookies_dict):
    browser = await playwright.chromium.launch(
        headless=True, args=["--no-sandbox", "--disable-http2", "--disable-gpu"]
    )
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
                    captured["name"] = data.get("name", "")
                    captured["url"] = data.get("url", "")
                    print(f"    🎵 捕获: {captured['name']}")
            except:
                pass

    page.on("response", on_response)
    try:
        await page.goto(play_url, wait_until="domcontentloaded", timeout=30000)
        # 等待 API 响应，最多等 10 秒
        for _ in range(20):
            if captured:
                break
            await asyncio.sleep(0.5)
    except Exception as e:
        print(f"    播放页异常: {e}")
    finally:
        page.remove_listener("response", on_response)
        await page.close()
        await browser.close()

    return captured.get("name", ""), captured.get("url", "")


def download_audio(url, filepath):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    resp = requests.get(
        url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=120
    )
    resp.raise_for_status()
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(8192):
            f.write(chunk)
    return True


def clone_private_repo():
    subprocess.run(["rm", "-rf", "/tmp/private_repo"], check=False)
    subprocess.run([
        "git", "clone", "--depth", "1",
        f"https://{ACCESS_TOKEN}@github.com/{PRIVATE_REPO}.git",
        "/tmp/private_repo"
    ], check=True)
    return "/tmp/private_repo"


def commit_and_push(repo_path, msg):
    subprocess.run(["git", "-C", repo_path, "config", "user.email", "actions@github.com"], check=True)
    subprocess.run(["git", "-C", repo_path, "config", "user.name", "GitHub Actions"], check=True)
    subprocess.run(["git", "-C", repo_path, "add", "."], check=True)
    status = subprocess.run(
        ["git", "-C", repo_path, "status", "--porcelain"], capture_output=True, text=True
    ).stdout
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
        cookies = await login(p)

        loop = asyncio.get_event_loop()
        chapters = await loop.run_in_executor(None, fetch_chapters_sync, cookies)

        total = len(chapters)
        print(f"📚 共获取 {total} 章节")
        if not total:
            return

        bp["total_chapters"] = total
        end = min(start + MAX_PER_RUN - 1, total)
        if start > total:
            print("✅ 已全部爬完")
            return
        print(f"⚡ 本次: 第{start}-{end}集")

        repo = clone_private_repo()
        entries = []

        for i in range(start - 1, end):
            ch = chapters[i]
            ep = i + 1
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


if __name__ == "__main__":
    asyncio.run(main())

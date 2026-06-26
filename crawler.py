import os
import re
import json
import asyncio
import time
import random
import subprocess
import requests
from playwright.async_api import async_playwright

# ── 基础配置 ──────────────────────────────────────────────
BASE_URL     = "https://www.ting13.cc"
BOOK_KEY     = os.environ.get("BOOK_KEY", "赘婿")
NOVEL_PAGE   = os.environ.get("NOVEL_PAGE", f"{BASE_URL}/youshengxiaoshuo/19353/")
MAX_PER_RUN  = 60
PROGRESS_FILE = "progress.json"
PRIVATE_REPO = os.environ["PRIVATE_REPO"]
ACCESS_TOKEN = os.environ["ACCESS_TOKEN"]
TARGET_DIR   = f"public/{BOOK_KEY}"
USER_AGENT   = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)


# ── 进度管理 ──────────────────────────────────────────────
def load_progress() -> dict:
    if not os.path.exists(PROGRESS_FILE):
        return {}
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_progress(prog: dict) -> None:
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False, indent=2)


# ── 工具函数 ──────────────────────────────────────────────
def sanitize_filename(title: str) -> str:
    """移除文件名中的非法字符，并截断至 80 字符。"""
    name = re.sub(r'[\\/*?:"<>|]', "", title)
    return name[:80].strip()


# ── 登录 ──────────────────────────────────────────────────
async def login(playwright):
    """使用账号密码登录网站，返回 (browser, cookies_dict)。"""
    raw = os.environ.get("TING13", "")
    if "-----" not in raw:
        raise RuntimeError("TING13 格式错误，应为「账号-----密码」")
    username, password = raw.split("-----", 1)

    browser = await playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-http2", "--disable-gpu"],
    )
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1280, "height": 720},
    )
    page = await context.new_page()

    print("🔐 正在打开登录页面...")
    await page.goto(
        f"{BASE_URL}/user/public/login.html",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    await page.wait_for_selector("#slider", state="visible", timeout=15000)
    await asyncio.sleep(2)

    await page.fill('input[name="username"]', username)
    await page.fill('input[name="password"]', password)

    print("  使用 JS 完成滑块验证...")
    await page.evaluate("""() => {
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
    }""")

    token = await page.evaluate("() => window._loginToken")
    await page.evaluate("""async (token) => {
        await fetch('/user/public/store_token.html', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({token})
        });
    }""", token)

    print("  提交登录表单...")
    async with page.expect_navigation(
        url="**/user/index/index.html",
        wait_until="domcontentloaded",
        timeout=30000,
    ):
        await page.evaluate("document.getElementById('frmpassedit').submit()")
    print("✅ 登录成功")

    cookies = await context.cookies()
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    await page.close()
    await context.close()
    return browser, cookie_dict


# ── 目录页 URL ────────────────────────────────────────────
async def get_dir_base_url(browser, cookies_dict: dict) -> str:
    """从小说主页解析目录链接，返回不含查询参数的目录页基础 URL。"""
    context = await browser.new_context(user_agent=USER_AGENT)
    await context.add_cookies([
        {"name": k, "value": v, "domain": ".ting13.cc", "path": "/"}
        for k, v in cookies_dict.items()
    ])
    page = await context.new_page()

    print("📌 正在从小说主页获取目录链接...")
    for attempt in range(3):
        try:
            await asyncio.sleep(random.uniform(2, 4))
            await page.goto(NOVEL_PAGE, wait_until="domcontentloaded", timeout=30000)

            if "请求过于频繁" in await page.title():
                print("  ⚠️ 被限流，等待 60 秒...")
                await asyncio.sleep(60)
                continue

            dir_link = await page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="/tingdirs/"]');
                for (const a of links) {
                    const href = a.getAttribute('href');
                    if (href && href.includes('tingdirs') && href.endsWith('.html')) {
                        return href;
                    }
                }
                const a = document.querySelector('a[href*="page=1&sort=asc"]');
                return a ? a.getAttribute('href').split('?')[0] : null;
            }""")

            if not dir_link:
                raise RuntimeError("未找到目录链接")

            full_url = BASE_URL + dir_link.split("?")[0]
            print(f"✅ 目录页: {full_url}")
            await page.close()
            await context.close()
            return full_url

        except Exception as e:
            print(f"  尝试 {attempt + 1}/3 失败: {e}")
            await asyncio.sleep(5)

    await page.close()
    await context.close()
    raise RuntimeError("无法获取目录页 URL")


# ── 章节列表 ──────────────────────────────────────────────
async def fetch_page_chapters_with_numbers(
    browser, cookies_dict: dict, base_url: str, page_num: int
) -> list[dict]:
    """
    获取目录第 page_num 页的章节列表。

    返回格式：[{"title": ..., "url": ..., "episode": int}, ...]
    """
    context = await browser.new_context(user_agent=USER_AGENT)
    await context.add_cookies([
        {"name": k, "value": v, "domain": ".ting13.cc", "path": "/"}
        for k, v in cookies_dict.items()
    ])
    page = await context.new_page()

    url = f"{base_url}?page={page_num}&sort=asc"
    print(f"  请求目录页: {url}")

    for attempt in range(3):
        try:
            await asyncio.sleep(random.uniform(2, 4))
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            if "请求过于频繁" in await page.title():
                print("  ⚠️ 被限流，等待 60 秒...")
                await asyncio.sleep(60)
                continue

            await page.wait_for_selector("#playlist", state="visible", timeout=15000)

            chapter_data = await page.evaluate("""() => {
                const items = document.querySelectorAll('#playlist ul li a');
                return Array.from(items).map(a => {
                    const title = a.getAttribute('title') || a.innerText.trim();
                    const match = title.match(/第(\\d+)集/);
                    return {
                        title,
                        url: a.href,
                        episode: match ? parseInt(match[1], 10) : null
                    };
                });
            }""")

            # 集数提取失败时，从快速选集区域推算
            if chapter_data and chapter_data[0]["episode"] is None:
                print("  ⚠️ 标题中未找到集数，尝试从选集区域推算...")
                page_links = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('.chapter-list-block a'))
                        .map(a => {
                            const m = a.innerText.trim().match(/(\\d+)\\s*~\\s*(\\d+)/);
                            return m ? {start: parseInt(m[1]), end: parseInt(m[2])} : null;
                        })
                        .filter(Boolean);
                }""")
                if page_links and page_num - 1 < len(page_links):
                    start_ep = page_links[page_num - 1]["start"]
                    for i, ch in enumerate(chapter_data):
                        ch["episode"] = start_ep + i

            chapter_data = [c for c in chapter_data if c["episode"] is not None]

            # 处理倒序列表
            if chapter_data:
                first_ep, last_ep = chapter_data[0]["episode"], chapter_data[-1]["episode"]
                if first_ep > 100 and first_ep > last_ep:
                    print("  🔄 检测到倒序排列，自动反转")
                    chapter_data.reverse()

            await page.close()
            await context.close()
            return chapter_data

        except Exception as e:
            print(f"  第 {attempt + 1}/3 次失败: {e}")
            await asyncio.sleep(5)

    await page.close()
    await context.close()
    raise RuntimeError(f"无法获取第 {page_num} 页章节")


# ── 音频 URL ──────────────────────────────────────────────
async def fetch_audio_url(
    browser, play_url: str, cookies_dict: dict
) -> tuple[str, str]:
    """
    打开播放页并拦截 /api/mapi/play 接口响应，提取音频名称和下载地址。

    返回 (name, url)，失败时返回 ("", "")。
    """
    context = await browser.new_context(user_agent=USER_AGENT)
    await context.add_cookies([
        {"name": k, "value": v, "domain": ".ting13.cc", "path": "/"}
        for k, v in cookies_dict.items()
    ])
    page = await context.new_page()
    captured: dict = {}

    async def on_response(resp):
        if "/api/mapi/play" in resp.url and resp.status == 200 and not captured:
            try:
                data = await resp.json()
                if data.get("status") == 200:
                    captured["name"] = data.get("name", "")
                    captured["url"]  = data.get("url", "")
                    print(f"    ✅ 捕获音频: {captured['name']}")
            except Exception:
                pass

    page.on("response", on_response)

    for retry in range(3):
        try:
            print(f"    加载播放页（第 {retry + 1}/3 次）...")
            await page.goto(play_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector(
                "audio, video, .play-btn, #playButton, .audio-play",
                state="attached",
                timeout=10000,
            )
            await asyncio.sleep(3)
            await page.evaluate("""() => {
                document.querySelectorAll('.play-btn, #playButton, .audio-play')
                    .forEach(btn => btn.click());
                const audio = document.querySelector('audio');
                if (audio) audio.play();
            }""")
            await asyncio.sleep(5)
            if captured:
                break
            print("    （未捕获，继续等待...）")
            await asyncio.sleep(4)
        except Exception as e:
            print(f"    播放页异常: {e}")
        if retry < 2:
            await asyncio.sleep(3)
    else:
        print(f"    ❌ 未能获取音频地址，播放页: {play_url}")

    page.remove_listener("response", on_response)
    await page.close()
    await context.close()
    return captured.get("name", ""), captured.get("url", "")


# ── 文件下载 ──────────────────────────────────────────────
def download_audio(url: str, filepath: str) -> None:
    """下载音频文件到指定路径，失败时抛出异常。"""
    if os.path.exists(filepath):
        os.remove(filepath)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        stream=True,
        timeout=120,
    )
    resp.raise_for_status()
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(8192):
            f.write(chunk)


# ── Git 操作 ──────────────────────────────────────────────
def clone_private_repo() -> str:
    """克隆私有仓库到 /tmp/private_repo，返回本地路径。"""
    local = "/tmp/private_repo"
    subprocess.run(["rm", "-rf", local], check=False)
    subprocess.run(
        ["git", "clone", "--depth", "1",
         f"https://{ACCESS_TOKEN}@github.com/{PRIVATE_REPO}.git", local],
        check=True,
    )
    return local


def commit_and_push(repo_path: str, msg: str) -> None:
    """提交并推送变更到私有仓库 main 分支。"""
    run = lambda *args: subprocess.run(list(args), cwd=repo_path, check=True)
    run("git", "config", "user.email", "actions@github.com")
    run("git", "config", "user.name", "GitHub Actions")
    run("git", "add", ".")
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_path, capture_output=True, text=True,
    ).stdout
    if status.strip():
        run("git", "commit", "-m", msg)
        run("git", "push", "origin", "main")
        print("✅ 私有仓库已更新")
    else:
        print("ℹ️ 无变更，跳过提交")


# ── 索引文件 ──────────────────────────────────────────────
def update_index_json(repo_path: str, entries: list[dict]) -> None:
    """
    将新章节信息合并写入 {TARGET_DIR}/index.json，按集数升序排列。
    """
    idx_dir  = os.path.join(repo_path, TARGET_DIR)
    os.makedirs(idx_dir, exist_ok=True)
    idx_path = os.path.join(idx_dir, "index.json")

    existing: list[dict] = []
    if os.path.exists(idx_path):
        with open(idx_path, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError:
                pass

    # 兼容旧格式（移除多余字段）
    for item in existing:
        item.pop("title", None)

    existing_eps = {e["episode"] for e in existing}
    for entry in entries:
        if entry["episode"] not in existing_eps:
            existing.append(entry)
            existing_eps.add(entry["episode"])

    existing.sort(key=lambda x: x["episode"])
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f"📄 index.json 已更新，共 {len(existing)} 集")


def update_novels_json(repo_path: str, book_key: str) -> bool:
    """
    将小说名写入 novels.json（如已存在则跳过）。

    返回 True 表示有新增，False 表示已存在。
    """
    novels_path = os.path.join(repo_path, "novels.json")
    existing: list = []
    if os.path.exists(novels_path):
        with open(novels_path, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError:
                pass

    if book_key in existing:
        print(f"ℹ️ {book_key} 已在 novels.json 中，跳过")
        return False

    existing.append(book_key)
    with open(novels_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f"✅ 已将 {book_key} 写入 novels.json")
    return True


# ── 主流程 ────────────────────────────────────────────────
async def main():
    if not os.path.exists(PROGRESS_FILE):
        save_progress({})

    progress   = load_progress()
    bp         = progress.get(BOOK_KEY, {"last_index": 0, "page": 0})
    start_after = bp["last_index"]
    saved_page  = bp.get("page", 0)

    print(f"📖 {BOOK_KEY}：已爬 {start_after} 集，从第 {start_after + 1} 集继续")
    if saved_page:
        print(f"📑 使用保存的页码: {saved_page}")

    async with async_playwright() as p:
        browser, cookies = await login(p)

        # 1. 获取目录页基础 URL
        try:
            base_url = await get_dir_base_url(browser, cookies)
        except Exception as e:
            print(f"❌ 获取目录页失败: {e}")
            await browser.close()
            return

        target_ep = start_after + 1

        # 2. 定位目标页码
        if saved_page:
            page_num = saved_page
        else:
            page_num = 1
            while True:
                chapters = await fetch_page_chapters_with_numbers(
                    browser, cookies, base_url, page_num
                )
                if not chapters:
                    print("❌ 无法获取章节信息")
                    await browser.close()
                    return
                first_ep, last_ep = chapters[0]["episode"], chapters[-1]["episode"]
                print(f"  第 {page_num} 页范围: {first_ep} ~ {last_ep}")
                if first_ep <= target_ep <= last_ep:
                    break
                elif target_ep > last_ep:
                    page_num += 1
                else:
                    page_num = max(1, page_num - 1)
                    break

        # 3. 获取当前页章节
        chapters = await fetch_page_chapters_with_numbers(
            browser, cookies, base_url, page_num
        )
        if not chapters:
            print("❌ 无法获取章节信息")
            await browser.close()
            return

        first_ep, last_ep = chapters[0]["episode"], chapters[-1]["episode"]
        print(f"⚡ 本次抓取第 {page_num} 页（集数范围 {first_ep}~{last_ep}）")

        chapters_to_download = [c for c in chapters if c["episode"] > start_after]

        # 4. 当前页已全部下载时，尝试翻页
        if not chapters_to_download:
            print("✅ 本页所有章节已下载，尝试翻到下一页...")
            try:
                next_chapters = await fetch_page_chapters_with_numbers(
                    browser, cookies, base_url, page_num + 1
                )
                if next_chapters:
                    page_num += 1
                    chapters = next_chapters
                    first_ep, last_ep = chapters[0]["episode"], chapters[-1]["episode"]
                    chapters_to_download = [c for c in chapters if c["episode"] > start_after]
                    print(f"  已翻到第 {page_num} 页（集数 {first_ep}~{last_ep}）")
            except Exception:
                pass

        if not chapters_to_download:
            print("✅ 全部章节已爬取完毕")
            await browser.close()
            return

        # 5. 下载音频
        repo          = clone_private_repo()
        entries       = []
        max_success_ep = start_after

        for ch in chapters_to_download:
            ep = ch["episode"]
            print(f"\n🎯 第 {ep} 集: {ch['title']}")
            name, url = await fetch_audio_url(browser, ch["url"], cookies)

            if not url:
                print("   ⚠️ 未获取到音频链接，跳过")
                continue

            base_name = sanitize_filename(name or ch["title"])
            fname     = base_name + ".m4a"
            dest      = os.path.join(repo, TARGET_DIR, fname)

            try:
                download_audio(url, dest)
                print(f"   ✅ 下载成功: {fname}")
                entries.append({
                    "name":    base_name,
                    "episode": ep,
                    "url":     f"{BOOK_KEY}/{fname}",
                })
                max_success_ep = max(max_success_ep, ep)
            except Exception as e:
                print(f"   ❌ 下载失败: {e}")
                if os.path.exists(dest):
                    os.remove(dest)

            await asyncio.sleep(random.uniform(1, 2))

        # 6. 提交并更新进度
        if entries:
            update_index_json(repo, entries)
            update_novels_json(repo, BOOK_KEY)
            commit_and_push(
                repo,
                f"feat: 抓取 {BOOK_KEY} 第{entries[0]['episode']}~{entries[-1]['episode']}集",
            )
            bp["last_index"] = max_success_ep
            bp["page"]       = page_num
            progress[BOOK_KEY] = bp
            save_progress(progress)
            print(f"📈 进度已更新: last_index={max_success_ep}, page={page_num}")
        else:
            print("ℹ️ 无新文件，进度未更新")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())

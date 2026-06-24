import os
import re
import json
import random
import string
import asyncio
import time
import requests
import subprocess
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# ===== 配置 =====
BASE_URL = "https://www.ting13.cc"
BOOK_KEY = os.environ.get("BOOK_KEY", "赘婿")
BOOK_URLS = {
    "赘婿": f"{BASE_URL}/tingdirs/uiPlHh/cbbhASacUDuaQoFc.html",
}
BASE_DIR_URL = BOOK_URLS.get(BOOK_KEY)
if not BASE_DIR_URL:
    raise ValueError(f"未配置小说 {BOOK_KEY} 的目录页")

MAX_PER_RUN = 50
PROGRESS_FILE = "progress.json"
PRIVATE_REPO = os.environ["PRIVATE_REPO"]
ACCESS_TOKEN = os.environ["ACCESS_TOKEN"]
TARGET_DIR = f"public/{BOOK_KEY}"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 QQBrowser/21.1.8663.400"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ===== 登录函数 =====
def login():
    """使用账号密码登录，返回带 Cookie 的 requests.Session"""
    raw = os.environ.get("TING13", "")
    if not raw:
        raise RuntimeError("未设置 TING13 环境变量（格式：账号-----密码）")
    if "-----" not in raw:
        raise RuntimeError("TING13 格式错误，应为 账号-----密码")
    username, password = raw.split("-----", 1)

    session = requests.Session()
    session.headers.update(HEADERS)

    # 1. 访问登录页面，获取初始 Cookie
    print("🔐 正在登录...")
    resp = session.get(f"{BASE_URL}/user/public/login.html", timeout=15)
    resp.encoding = 'utf-8'

    # 2. 生成随机 token（模拟滑块验证）
    token = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
    print(f"  生成验证 token: {token}")

    # 3. 提交 token
    store_resp = session.post(
        f"{BASE_URL}/user/public/store_token.html",
        json={"token": token},
        headers={
            "Content-Type": "application/json",
            "Referer": f"{BASE_URL}/user/public/login.html",
            "Origin": BASE_URL,
        },
        timeout=15
    )
    print(f"  store_token 状态: {store_resp.status_code}")

    # 4. 提交登录表单
    login_data = {
        "username": username,
        "password": password,
        "verificationToken": token,
        "name": "1",
    }
    login_resp = session.post(
        f"{BASE_URL}/user/public/login.html",
        data=login_data,
        headers={
            "Referer": f"{BASE_URL}/user/public/login.html",
            "Origin": BASE_URL,
        },
        timeout=15
    )
    login_resp.encoding = 'utf-8'
    print(f"  登录 POST 状态: {login_resp.status_code}")

    # 5. 验证登录是否成功（检查 Cookie 中是否有 PTCMS_userid）
    if "PTCMS_userid" not in session.cookies:
        # 可能登录失败，打印部分响应文本
        print("❌ 登录失败，响应片段：", login_resp.text[:300])
        raise RuntimeError("登录失败，请检查账号密码是否正确")
    print(f"✅ 登录成功，用户 ID: {session.cookies.get('PTCMS_userid')}")
    return session

# ===== 工具函数 =====
def load_progress():
    if not os.path.exists(PROGRESS_FILE):
        return {}
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_progress(progress):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

def sanitize_filename(title):
    name = re.sub(r'[\\/*?:"<>|]', "", title)
    if len(name) > 80:
        name = name[:80]
    return name.strip()

def fetch_chapters(session):
    """使用已登录的 session 抓取所有分页的章节链接"""
    all_chapters = []
    max_page = 1

    print(f"正在获取首页: {BASE_DIR_URL}?page=1&sort=asc")
    resp = session.get(f"{BASE_DIR_URL}?page=1&sort=asc", timeout=15)
    resp.encoding = 'utf-8'
    if resp.status_code != 200:
        raise RuntimeError(f"首页状态码 {resp.status_code}")

    soup = BeautifulSoup(resp.text, 'html.parser')

    # 提取总页数
    page_links = soup.select(".chapter-list-block li a")
    for a in page_links:
        href = a.get("href", "")
        match = re.search(r"page=(\d+)", href)
        if match:
            p = int(match.group(1))
            if p > max_page:
                max_page = p
    print(f"📖 共检测到 {max_page} 页")

    # 解析第一页
    playlist = soup.find("div", id="playlist")
    if not playlist:
        print("❌ 未找到播放列表，可能是未登录或页面结构变化")
        return []
    chapter_count = 0
    for li in playlist.find_all("li"):
        a = li.find("a")
        if a and a.get("href"):
            all_chapters.append({
                "title": a.get("title", "").strip(),
                "url": BASE_URL + a["href"]
            })
            chapter_count += 1
    print(f"  第1页获取 {chapter_count} 集")

    # 剩余页面
    for pg in range(2, max_page + 1):
        print(f"  抓取第 {pg}/{max_page} 页...", end=" ")
        try:
            resp = session.get(f"{BASE_DIR_URL}?page={pg}&sort=asc", timeout=15)
            resp.encoding = 'utf-8'
            if resp.status_code != 200:
                print(f"状态码 {resp.status_code}，跳过")
                continue
            soup = BeautifulSoup(resp.text, 'html.parser')
            playlist = soup.find("div", id="playlist")
            if not playlist:
                print("未找到播放列表，跳过")
                continue
            count = 0
            for li in playlist.find_all("li"):
                a = li.find("a")
                if a and a.get("href"):
                    all_chapters.append({
                        "title": a.get("title", "").strip(),
                        "url": BASE_URL + a["href"]
                    })
                    count += 1
            print(f"获取 {count} 集")
        except Exception as e:
            print(f"失败 ({e})，跳过")
        time.sleep(1)

    return all_chapters

async def fetch_audio_url(play_url, cookies):
    """Playwright 打开播放页，拦截音频 API"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-http2", "--disable-gpu"])
        context = await browser.new_context(user_agent=USER_AGENT)
        await context.add_cookies([
            {"name": k, "value": v, "domain": ".ting13.cc", "path": "/"}
            for k, v in cookies.items()
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
        await browser.close()
        return captured.get("name", ""), captured.get("url", "")

def download_audio(url, filepath):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=120)
    resp.raise_for_status()
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    return True

def clone_private_repo():
    repo_url = f"https://{ACCESS_TOKEN}@github.com/{PRIVATE_REPO}.git"
    tmp_dir = "/tmp/private_repo"
    subprocess.run(["rm", "-rf", tmp_dir], check=False)
    subprocess.run(["git", "clone", "--depth", "1", repo_url, tmp_dir], check=True)
    return tmp_dir

def commit_and_push(repo_path, message):
    subprocess.run(["git", "-C", repo_path, "config", "user.email", "actions@github.com"], check=True)
    subprocess.run(["git", "-C", repo_path, "config", "user.name", "GitHub Actions"], check=True)
    subprocess.run(["git", "-C", repo_path, "add", "."], check=True)
    status = subprocess.run(["git", "-C", repo_path, "status", "--porcelain"], capture_output=True, text=True)
    if status.stdout.strip():
        subprocess.run(["git", "-C", repo_path, "commit", "-m", message], check=True)
        subprocess.run(["git", "-C", repo_path, "push", "origin", "main"], check=True)
        print("✅ 私有仓库更新已推送")
    else:
        print("ℹ️ 没有文件变更，无需推送")

def update_index_json(repo_path, entries):
    index_dir = os.path.join(repo_path, TARGET_DIR)
    os.makedirs(index_dir, exist_ok=True)
    index_path = os.path.join(index_dir, "index.json")

    existing = []
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
            except:
                existing = []

    episode_set = {e["episode"] for e in existing}
    for entry in entries:
        if entry["episode"] not in episode_set:
            existing.append(entry)
            episode_set.add(entry["episode"])

    existing.sort(key=lambda x: x["episode"])
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f"📄 更新 index.json，共 {len(existing)} 集")

# ===== 主流程 =====
async def main():
    # 1. 登录
    session = login()

    # 2. 读取进度
    progress = load_progress()
    book_progress = progress.get(BOOK_KEY, {"last_index": 0, "total_chapters": 0})
    start_index = book_progress["last_index"] + 1
    print(f"📖 当前进度：{BOOK_KEY} 已爬取 {book_progress['last_index']} 集，从第 {start_index} 集开始")

    # 3. 获取全部章节列表
    chapters = fetch_chapters(session)
    total_chapters = len(chapters)
    print(f"📚 共获取到 {total_chapters} 个章节")
    if total_chapters == 0:
        print("❌ 章节列表为空，退出")
        return

    book_progress["total_chapters"] = total_chapters

    # 4. 确定本次处理范围
    end_index = min(start_index + MAX_PER_RUN - 1, total_chapters)
    if start_index > total_chapters:
        print("✅ 所有章节已爬取完毕")
        return
    print(f"⚡ 本次处理第 {start_index} ~ {end_index} 集")

    # 5. 克隆私有仓库
    repo_path = clone_private_repo()

    # 6. 提取 cookies 字典用于 Playwright
    cookies = session.cookies.get_dict()

    # 7. 逐章下载
    new_entries = []
    for idx in range(start_index - 1, end_index):
        ch = chapters[idx]
        episode_num = idx + 1
        title = ch["title"]
        play_url = ch["url"]
        print(f"\n🎯 第 {episode_num} 集: {title}")

        audio_name, audio_url = await fetch_audio_url(play_url, cookies)
        if not audio_url:
            print("   ⚠️ 未获取到音频地址，跳过")
            continue

        base_name = sanitize_filename(audio_name if audio_name else title)
        actual_filename = base_name + ".m4a"
        dest_path = os.path.join(repo_path, TARGET_DIR, actual_filename)

        try:
            download_audio(audio_url, dest_path)
            print(f"   ✅ 下载成功: {actual_filename}")
        except Exception as e:
            print(f"   ❌ 下载失败: {e}")
            continue

        new_entries.append({
            "name": base_name,
            "title": audio_name if audio_name else title,
            "episode": episode_num,
            "url": f"{BOOK_KEY}/{actual_filename}"
        })
        time.sleep(1)

    # 8. 更新索引并推送
    if new_entries:
        update_index_json(repo_path, new_entries)
        commit_and_push(repo_path, f"抓取 {BOOK_KEY} 第{start_index}-{end_index}集")
    else:
        print("ℹ️ 本次未下载任何新音频")

    # 9. 更新进度
    if new_entries:
        book_progress["last_index"] = end_index
        progress[BOOK_KEY] = book_progress
        save_progress(progress)
        print(f"📈 进度更新：last_index = {end_index}")

if __name__ == "__main__":
    asyncio.run(main())

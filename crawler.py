import os
import re
import json
import asyncio
import time
import requests
import subprocess
from pathlib import Path
from playwright.async_api import async_playwright

# ===== 配置 =====
BASE_URL = "https://www.ting13.cc"
BOOK_KEY = os.environ.get("BOOK_KEY", "赘婿")
# 可扩展多本小说的目录页 URL
BOOK_URLS = {
    "赘婿": f"{BASE_URL}/tingdirs/uiPlHh/cbbhASacUDuaQoFc.html?page=1&sort=asc",
}
DIR_URL = BOOK_URLS.get(BOOK_KEY)
if not DIR_URL:
    raise ValueError(f"未配置小说 {BOOK_KEY} 的目录页")

MAX_PER_RUN = 50                     # 每次最多处理章节数
PROGRESS_FILE = "progress.json"      # 公开仓库根目录
PRIVATE_REPO = os.environ["PRIVATE_REPO"]
ACCESS_TOKEN = os.environ["ACCESS_TOKEN"]
TARGET_DIR = f"public/{BOOK_KEY}"    # 私有仓库内目标文件夹

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 QQBrowser/21.1.8663.400"

# ===== 工具函数 =====
def get_cookies():
    raw = os.environ.get("TING13_COOKIES", "")
    if not raw:
        raise RuntimeError("未设置 TING13_COOKIES")
    cookies = {}
    for item in raw.split("; "):
        if "=" in item:
            k, v = item.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies

def load_progress():
    """从公开仓库根目录读取 progress.json"""
    if not os.path.exists(PROGRESS_FILE):
        return {}
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_progress(progress):
    """更新公开仓库的 progress.json"""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

def sanitize_filename(title):
    """清理文件名，返回不含扩展名的安全字符串"""
    name = re.sub(r'[\\/*?:"<>|]', "", title)
    if len(name) > 80:
        name = name[:80]
    return name.strip()

async def fetch_all_chapters(cookies):
    """用 Playwright 获取目录页完整章节列表"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-http2"])
        context = await browser.new_context(user_agent=USER_AGENT,
                                            viewport={"width": 1280, "height": 720},
                                            locale="zh-CN")
        await context.add_cookies([
            {"name": k, "value": v, "domain": ".ting13.cc", "path": "/"}
            for k, v in cookies.items()
        ])
        page = await context.new_page()
        await page.goto(DIR_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)

        chapters = await page.evaluate('''() => {
            const ul = document.querySelector("#playlist ul");
            if (!ul) return [];
            const lis = ul.querySelectorAll("li a");
            return Array.from(lis).map(a => ({
                title: a.getAttribute("title") || a.innerText.trim(),
                url: a.href
            }));
        }''')
        await browser.close()
        return chapters

async def fetch_audio_url(play_url, cookies):
    """打开播放页，拦截 /api/mapi/play 获取音频地址"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-http2"])
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
            # 尝试点击播放按钮
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
    """下载音频文件"""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=120)
    resp.raise_for_status()
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    return True

def clone_private_repo():
    """克隆私有仓库到临时目录"""
    repo_url = f"https://{ACCESS_TOKEN}@github.com/{PRIVATE_REPO}.git"
    tmp_dir = "/tmp/private_repo"
    subprocess.run(["rm", "-rf", tmp_dir], check=False)
    subprocess.run(["git", "clone", "--depth", "1", repo_url, tmp_dir], check=True)
    return tmp_dir

def commit_and_push(repo_path, message):
    """在给定路径下提交并推送所有更改"""
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

def update_index_json(repo_path, book_key, entries):
    """更新 public/<book>/index.json，按 episode 去重排序"""
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

    # 用 episode 去重
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
    cookies = get_cookies()

    # 1. 读取进度
    progress = load_progress()
    book_progress = progress.get(BOOK_KEY, {"last_index": 0, "total_chapters": 0})
    start_index = book_progress["last_index"] + 1
    print(f"📖 当前进度：{BOOK_KEY} 已爬取 {book_progress['last_index']} 集，从第 {start_index} 集开始")

    # 2. 获取完整章节列表
    chapters = await fetch_all_chapters(cookies)
    total_chapters = len(chapters)
    print(f"📚 目录共 {total_chapters} 个章节")
    if total_chapters == 0:
        print("❌ 无法获取章节列表，退出")
        return

    book_progress["total_chapters"] = total_chapters

    # 3. 确定本次处理范围
    end_index = min(start_index + MAX_PER_RUN - 1, total_chapters)
    if start_index > total_chapters:
        print("✅ 所有章节已爬取完毕")
        return
    print(f"⚡ 本次将处理第 {start_index} 到第 {end_index} 集")

    # 4. 克隆私有仓库
    repo_path = clone_private_repo()

    # 5. 逐章处理
    new_entries = []
    for idx in range(start_index - 1, end_index):  # 0-based index
        ch = chapters[idx]
        episode_num = idx + 1
        title = ch["title"]
        play_url = ch["url"]
        print(f"\n🎯 处理第 {episode_num} 集: {title}")

        audio_name, audio_url = await fetch_audio_url(play_url, cookies)
        if not audio_url:
            print("   ⚠️ 未获取到音频地址，跳过")
            continue

        # 生成文件名（无后缀）
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
            "url": f"{BOOK_KEY}/{actual_filename}"   # 例如 "赘婿/xxx.m4a"
        })
        time.sleep(1)

    # 6. 更新私有仓库索引并推送
    if new_entries:
        update_index_json(repo_path, BOOK_KEY, new_entries)
        commit_and_push(repo_path, f"自动抓取 {BOOK_KEY} 第{start_index}-{end_index}集")
    else:
        print("ℹ️ 本次未下载任何新音频")

    # 7. 更新进度并写回公开仓库
    if new_entries:
        book_progress["last_index"] = end_index
        progress[BOOK_KEY] = book_progress
        save_progress(progress)
        print(f"📈 进度已更新：last_index = {end_index}")
    else:
        print("ℹ️ 未更新进度")

if __name__ == "__main__":
    asyncio.run(main())

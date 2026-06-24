import os
import sys
import json
import asyncio
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BASE_URL = "https://www.ting13.cc"
DIR_URL = f"{BASE_URL}/tingdirs/uiPlHh/cbbhASacUDuaQoFc.html?page=1&sort=asc"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

def get_cookies_from_env():
    cookie_str = os.getenv("TING13_COOKIES", "")
    if not cookie_str:
        raise RuntimeError("未设置 TING13_COOKIES 环境变量")
    cookies = {}
    for item in cookie_str.split("; "):
        if "=" in item:
            key, val = item.split("=", 1)
            cookies[key.strip()] = val.strip()
    return cookies

def fetch_directory(cookies):
    """用 requests 快速提取所有章节链接（前 N 章）"""
    print(f"正在获取目录: {DIR_URL}")
    resp = requests.get(DIR_URL, headers=HEADERS, cookies=cookies, timeout=15)
    resp.encoding = 'utf-8'
    if resp.status_code != 200:
        raise RuntimeError(f"目录页状态码 {resp.status_code}")
    soup = BeautifulSoup(resp.text, 'html.parser')
    playlist_div = soup.find("div", id="playlist")
    if not playlist_div:
        raise RuntimeError("未找到播放列表容器，检查 Cookie")
    chapters = []
    for li in playlist_div.find_all("li"):
        a = li.find("a")
        if a and a.get("href"):
            chapters.append({
                "title": a.get("title", "").strip(),
                "url": BASE_URL + a["href"]
            })
    print(f"提取到 {len(chapters)} 个章节")
    return chapters

async def get_audio_urls(chapters, cookies, max_chapters=3):
    """使用 Playwright 依次打开播放页，拦截 /api/mapi/play 响应"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            # 将 cookie 字典转换为 Playwright 需要的格式
            storage_state=None
        )
        # 手动添加 cookies
        cookie_list = []
        for name, value in cookies.items():
            cookie_list.append({
                "name": name,
                "value": value,
                "domain": ".ting13.cc",
                "path": "/"
            })
        await context.add_cookies(cookie_list)

        page = await context.new_page()
        audio_results = []

        for idx, ch in enumerate(chapters[:max_chapters], 1):
            print(f"\n[{idx}/{max_chapters}] 正在处理: {ch['title']}")
            # 设置请求拦截，捕获 /api/mapi/play 的响应
            async def handle_response(response):
                if "/api/mapi/play" in response.url and response.status == 200:
                    try:
                        data = await response.json()
                        if data.get("status") == 200:
                            audio_url = data.get("url")
                            audio_name = data.get("name")
                            print(f"  -> 音频地址: {audio_url}")
                            print(f"  -> 名称: {audio_name}")
                            audio_results.append({
                                "title": ch["title"],
                                "audio_name": audio_name,
                                "audio_url": audio_url,
                                "play_page": ch["url"]
                            })
                    except:
                        pass

            page.on("response", handle_response)
            try:
                await page.goto(ch["url"], wait_until="domcontentloaded", timeout=30000)
                # 等待页面触发播放请求 (有些页面需要点击播放按钮)
                # 可以等待某个元素出现，比如播放按钮，或直接等待几秒
                # 这里简单等待 3 秒，确保 JS 执行完毕并发起请求
                await asyncio.sleep(3)
                # 尝试点击播放按钮（如果页面有）
                play_btn = await page.query_selector(".play-btn, #playButton, .audio-play")
                if play_btn:
                    await play_btn.click()
                    await asyncio.sleep(2)
            except Exception as e:
                print(f"  ⚠️ 页面加载异常: {e}")
            finally:
                page.remove_listener("response", handle_response)

        await browser.close()
        return audio_results

def main():
    cookies = get_cookies_from_env()
    chapters = fetch_directory(cookies)
    if not chapters:
        print("未找到任何章节")
        return

    # 只爬前 3 章作为测试，避免过大压力
    max_chapters = 3
    print(f"\n开始提取前 {max_chapters} 章的音频地址...")
    audio_data = asyncio.run(get_audio_urls(chapters, cookies, max_chapters))

    print("\n" + "=" * 60)
    print("抓取结果：")
    for item in audio_data:
        print(f"标题: {item['title']}")
        print(f"音频名称: {item['audio_name']}")
        print(f"音频URL: {item['audio_url']}")
        print("-" * 40)

    # 可选：将结果保存为 JSON
    with open("audio_results.json", "w", encoding="utf-8") as f:
        json.dump(audio_data, f, ensure_ascii=False, indent=2)
    print("结果已保存到 audio_results.json")

if __name__ == "__main__":
    main()

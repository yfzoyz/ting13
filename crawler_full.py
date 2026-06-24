import os, sys, json, asyncio
from playwright.async_api import async_playwright

BASE_URL = "https://www.ting13.cc"
DIR_URL = f"{BASE_URL}/tingdirs/uiPlHh/cbbhASacUDuaQoFc.html?page=1&sort=asc"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 QQBrowser/21.1.8663.400"
MAX_CHAPTERS = 3

def get_cookies():
    raw = os.getenv("TING13_COOKIES", "")
    if not raw:
        raise RuntimeError("请设置 TING13_COOKIES 环境变量")
    cookies = {}
    for item in raw.split("; "):
        if "=" in item:
            k, v = item.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies

async def fetch_directory_with_request(context):
    """备用方法：直接使用 Playwright 的 APIRequest 获取目录页 HTML"""
    page = await context.new_page()
    try:
        # 用 page.goto 仍是首选，这里做备用
        resp = await page.goto(DIR_URL, wait_until="commit", timeout=30000)
        if resp and resp.status == 200:
            content = await page.content()
            return content
        else:
            raise Exception(f"状态码 {resp.status}")
    except Exception as e:
        print(f"目录页请求失败，尝试用 APIRequest: {e}")
        # 使用 context.request 直接发 GET
        req_context = await context.new_request()
        try:
            api_resp = await req_context.get(DIR_URL, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": "https://www.ting13.cc/tingdirs/uiPlHh/cbbhASacUDuaQoFc.html?page=9&sort=asc",
                "Upgrade-Insecure-Requests": "1"
            })
            if api_resp.status == 200:
                return await api_resp.text()
            else:
                raise Exception(f"APIRequest 失败，状态码 {api_resp.status}")
        finally:
            await req_context.dispose()
    finally:
        await page.close()

async def extract_chapters(page):
    """从 DOM 提取章节"""
    return await page.evaluate('''() => {
        const container = document.querySelector("#playlist ul");
        if (!container) return [];
        return Array.from(container.querySelectorAll("li a")).map(a => ({
            title: a.getAttribute("title") || a.innerText.trim(),
            url: a.href
        }));
    }''')

async def main():
    cookies = get_cookies()
    async with async_playwright() as p:
        # 启动参数隐藏自动化特征，并尝试避免 HTTP2 问题（如果可行）
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=VizDisplayCompositor",
                "--ignore-certificate-errors",
                "--disable-http2"   # 尝试禁用 HTTP/2
            ]
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
        )

        # 添加更完整的请求头（部分会自动包含）
        await context.set_extra_http_headers({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Ch-Ua": '"Chromium";v="123", "Not:A-Brand";v="8"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
        })

        # 注入 Cookie
        await context.add_cookies([
            {"name": k, "value": v, "domain": ".ting13.cc", "path": "/"}
            for k, v in cookies.items()
        ])

        # ---------- 1. 获取目录页 ----------
        print("正在加载目录页...")
        page = await context.new_page()
        chapters = []
        try:
            await page.goto(DIR_URL, wait_until="load", timeout=40000)
            print("页面加载完成，解析章节...")
            chapters = await extract_chapters(page)
        except Exception as e:
            print(f"首次加载失败: {e}，尝试备用方案...")
            await page.close()
            # 备用：用 APIRequest 获取 HTML，再设置到新页面
            page = await context.new_page()
            html = await fetch_directory_with_request(context)
            await page.set_content(html, wait_until="load")
            chapters = await extract_chapters(page)

        if not chapters:
            print("❌ 仍然无法获取章节，退出。页面标题:", await page.title())
            sys.exit(1)

        print(f"✅ 成功获取 {len(chapters)} 个章节")
        for i, ch in enumerate(chapters[:3]):
            print(f"   {i+1}. {ch['title']}")
        await page.close()

        # ---------- 2. 逐个获取音频地址 ----------
        audio_data = []
        for idx, ch in enumerate(chapters[:MAX_CHAPTERS], 1):
            print(f"\n[{idx}/{MAX_CHAPTERS}] 播放页: {ch['title']}")
            play_page = await context.new_page()
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

            play_page.on("response", on_response)
            try:
                await play_page.goto(ch["url"], wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)
                # 尝试触发播放
                await play_page.evaluate("() => { const btn = document.querySelector('.play-btn,#playButton,.audio-play'); if(btn) btn.click(); }")
                await asyncio.sleep(3)
            except Exception as e:
                print(f"⚠️ 播放页异常: {e}")

            play_page.remove_listener("response", on_response)

            if captured.get("url"):
                print(f"🎵 音频: {captured['name']}")
                print(f"🔗 地址: {captured['url']}")
                audio_data.append({
                    "title": ch["title"],
                    "audio_name": captured["name"],
                    "audio_url": captured["url"]
                })
            else:
                print("⚠️ 未捕获到音频地址")
            await play_page.close()

        await browser.close()

        # ---------- 3. 输出结果 ----------
        print("\n" + "=" * 60)
        for item in audio_data:
            print(f"✔ {item['title']} -> {item['audio_url']}")
        with open("audio_results.json", "w", encoding="utf-8") as f:
            json.dump(audio_data, f, ensure_ascii=False, indent=2)
        print("\n结果已保存至 audio_results.json")

if __name__ == "__main__":
    asyncio.run(main())

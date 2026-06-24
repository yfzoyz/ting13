import os, sys, json, asyncio
from playwright.async_api import async_playwright

BASE_URL = "https://www.ting13.cc"
DIR_URL = f"{BASE_URL}/tingdirs/uiPlHh/cbbhASacUDuaQoFc.html?page=1&sort=asc"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
MAX_CHAPTERS = 3   # 只爬前3集，可根据需要修改

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

async def main():
    cookies = get_cookies()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)

        # 注入 Cookie
        await context.add_cookies([
            {"name": k, "value": v, "domain": ".ting13.cc", "path": "/"}
            for k, v in cookies.items()
        ])

        # ---------- 1. 获取目录页（直接用浏览器） ----------
        page = await context.new_page()
        print(f"正在打开目录页: {DIR_URL}")
        try:
            await page.goto(DIR_URL, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"页面加载超时/错误: {e}，继续解析已有内容")

        # 从 DOM 中提取章节链接
        chapters = await page.evaluate('''() => {
            const ul = document.querySelector("#playlist ul");
            if (!ul) return [];
            const lis = ul.querySelectorAll("li");
            return Array.from(lis).map(li => {
                const a = li.querySelector("a");
                return a ? { title: a.getAttribute("title") || a.innerText.trim(), url: a.href } : null;
            }).filter(Boolean);
        }''')

        if not chapters:
            print("❌ 未找到章节列表，页面标题:", await page.title())
            sys.exit(1)

        print(f"✅ 成功提取 {len(chapters)} 个章节")
        for i, ch in enumerate(chapters[:3]):
            print(f"   {i+1}. {ch['title']}")

        await page.close()  # 关闭目录页

        # ---------- 2. 逐个打开播放页，拦截音频地址 ----------
        audio_data = []
        for idx, ch in enumerate(chapters[:MAX_CHAPTERS], 1):
            print(f"\n[{idx}/{MAX_CHAPTERS}] 打开: {ch['title']}")
            play_page = await context.new_page()
            captured = {}

            async def on_response(resp):
                if "/api/mapi/play" in resp.url and resp.status == 200:
                    try:
                        data = await resp.json()
                        if data.get("status") == 200 and not captured:
                            captured["name"] = data.get("name")
                            captured["url"] = data.get("url")
                    except:
                        pass

            play_page.on("response", on_response)

            try:
                await play_page.goto(ch["url"], wait_until="domcontentloaded", timeout=30000)
                # 等待 JS 执行并触发 API 请求
                await asyncio.sleep(3)
                # 如果页面有显式播放按钮，尝试点击
                btn = await play_page.query_selector(".play-btn, #playButton, .audio-play")
                if btn:
                    await btn.click()
                    await asyncio.sleep(2)
                await asyncio.sleep(2)  # 额外等待确保请求完成
            except Exception as e:
                print(f"⚠️ 播放页异常: {e}")

            play_page.remove_listener("response", on_response)

            if captured.get("url"):
                print(f"🎵 音频名称: {captured['name']}")
                print(f"🔗 音频地址: {captured['url']}")
                audio_data.append({
                    "title": ch["title"],
                    "audio_name": captured["name"],
                    "audio_url": captured["url"]
                })
            else:
                print("⚠️ 未捕获到音频地址")

            await play_page.close()

        await browser.close()

        # ---------- 3. 输出与保存 ----------
        print("\n" + "=" * 60)
        for item in audio_data:
            print(f"✔ {item['title']}")
            print(f"  {item['audio_url']}")
        with open("audio_results.json", "w", encoding="utf-8") as f:
            json.dump(audio_data, f, ensure_ascii=False, indent=2)
        print("\n结果已保存至 audio_results.json")

if __name__ == "__main__":
    asyncio.run(main())

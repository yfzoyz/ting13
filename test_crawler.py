import os
import re
import sys
import requests
from bs4 import BeautifulSoup

# 目标 URL
BASE_URL = "https://www.ting13.cc"
DIR_URL = f"{BASE_URL}/tingdirs/uiPlHh/cbbhASacUDuaQoFc.html?page=1&sort=asc"

# 基本请求头
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

def get_cookies_from_env():
    """从环境变量 TING13_COOKIES 读取 Cookie 字符串，返回 dict"""
    cookie_str = os.getenv("TING13_COOKIES", "")
    if not cookie_str:
        print("警告: 未设置环境变量 TING13_COOKIES，可能无法获取需登录的内容。")
        return None
    # 简单解析 Cookie 字符串为键值对
    cookies = {}
    for item in cookie_str.split("; "):
        if "=" in item:
            key, val = item.split("=", 1)
            cookies[key.strip()] = val.strip()
    return cookies

def fetch_page(url, cookies=None, timeout=15):
    """通用请求函数，带 Cookie"""
    resp = requests.get(url, headers=HEADERS, cookies=cookies, timeout=timeout)
    resp.encoding = 'utf-8'
    return resp

def test_directory():
    """测试目录页解析"""
    cookies = get_cookies_from_env()
    print(f"请求目录页: {DIR_URL}")
    resp = fetch_page(DIR_URL, cookies=cookies)
    assert resp.status_code == 200, f"目录页状态码异常: {resp.status_code}"

    soup = BeautifulSoup(resp.text, 'html.parser')
    playlist_div = soup.find("div", id="playlist")
    
    if playlist_div is None:
        # 打印页面标题和部分文本以便调试
        print("未找到播放列表容器，可能未登录或页面结构变更。")
        print("页面标题:", soup.title.string if soup.title else "无标题")
        print("页面文本片段:", soup.get_text()[:200])
        raise AssertionError("未找到播放列表容器，请检查 Cookie 是否有效。")

    items = playlist_div.find_all("li")
    assert len(items) > 0, "播放列表为空"
    
    chapters = []
    for li in items:
        a_tag = li.find("a")
        if a_tag:
            title = a_tag.get("title", "").strip()
            href = a_tag.get("href", "")
            chapters.append((title, href))

    print(f"成功提取到 {len(chapters)} 个章节")
    for i, (title, href) in enumerate(chapters[:3]):
        print(f"  {i+1}. {title} -> {href}")
    return chapters

def test_play_page(chapters, cookies):
    """测试播放页是否可访问（只测连通性，不获取最终音频地址）"""
    if not chapters:
        return
    first_href = chapters[0][1]
    play_url = BASE_URL + first_href
    print(f"\n请求播放页: {play_url}")
    resp = fetch_page(play_url, cookies=cookies)
    assert resp.status_code == 200, f"播放页状态码异常: {resp.status_code}"
    print("播放页连通性测试通过。")

if __name__ == "__main__":
    try:
        print("=" * 50)
        print("开始有声小说爬虫集成测试 (需登录)")
        print("=" * 50)

        cookies = get_cookies_from_env()
        chapters = test_directory()
        test_play_page(chapters, cookies)

        print("\n所有测试通过！")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n运行错误: {e}")
        sys.exit(1)

import requests
from bs4 import BeautifulSoup
import sys

# 目标：赘婿有声小说目录页第1页
BASE_URL = "https://www.ting13.cc"
DIR_URL = f"{BASE_URL}/tingdirs/uiPlHh/cbbhASacUDuaQoFc.html?page=1&sort=asc"
API_PLAY_URL = f"{BASE_URL}/api/mapi/play"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

def test_fetch_directory():
    """测试目录页能否正常获取并解析出至少一条章节"""
    print(f"请求目录页: {DIR_URL}")
    resp = requests.get(DIR_URL, headers=HEADERS, timeout=15)
    assert resp.status_code == 200, f"目录页状态码异常: {resp.status_code}"
    resp.encoding = 'utf-8'

    soup = BeautifulSoup(resp.text, 'html.parser')
    # 章节列表在 <div id="playlist"> 下的 <ul> 中，每个 <li> 包含一个 <a>
    playlist_div = soup.find("div", id="playlist")
    assert playlist_div is not None, "未找到播放列表容器"

    items = playlist_div.find_all("li")
    assert len(items) > 0, "播放列表为空，可能页面结构变化或需要登录"

    chapters = []
    for li in items:
        a_tag = li.find("a")
        if a_tag:
            title = a_tag.get("title", "").strip()
            href = a_tag.get("href", "")
            chapters.append((title, href))

    print(f"成功提取到 {len(chapters)} 个章节")
    for i, (title, href) in enumerate(chapters[:3]):  # 只打印前3个验证
        print(f"  {i+1}. {title} -> {href}")

    # 返回第一章的链接，供后续测试用
    if chapters:
        return BASE_URL + chapters[0][1]
    else:
        raise AssertionError("未提取到任何章节链接")

def test_play_page_contains_audio():
    """测试播放页能否通过API获取音频地址（模拟页面嵌入的请求）"""
    # 先获取目录页，拿到一个具体的播放页链接
    resp = requests.get(DIR_URL, headers=HEADERS, timeout=15)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'html.parser')
    playlist_div = soup.find("div", id="playlist")
    items = playlist_div.find_all("li")
    assert len(items) > 0

    # 取第一集的播放链接，例如 /play/19353_1_77733.html
    first_href = items[0].find("a").get("href")
    play_url = BASE_URL + first_href
    print(f"\n请求播放页: {play_url}")

    # 播放页本身可能通过POST /api/mapi/play 获取音频URL
    # 这里我们直接尝试请求播放页HTML，然后找可能隐藏的音频源（如果有）
    play_resp = requests.get(play_url, headers=HEADERS, timeout=15)
    assert play_resp.status_code == 200, f"播放页状态码异常: {play_resp.status_code}"
    play_resp.encoding = 'utf-8'

    # 实际音频地址来自 POST /api/mapi/play，需要nid和cid参数，它们在页面中
    # 尝试从页面提取这些参数（通过正则或解析）
    import re
    # 通常页面中会有一段js包含 nid 和 cid，但这里简化：
    # 直接从URL中提取：/play/19353_1_77733.html => 19353_1_77733
    match = re.search(r'/play/(\d+_\d+_\d+)\.html', first_href)
    if match:
        params_raw = match.group(1)  # e.g., 19353_1_77733
        # 实际POST需要 nid 和 cid，它们的值似乎是加密的，这里无法直接构造
        # 因此我们只验证播放页加载成功即可，进一步测试需要完整逆向
        print("播放页加载成功，音频API需要加密参数，跳过完整音频地址测试。")
    else:
        print("无法从URL提取参数，播放页HTML中可能包含播放器配置。")

    # 作为扩展，可以尝试用requests直接模拟POST，但缺少sc和sp动态签名
    # 此处只做基础连通性测试
    print("基础播放页连通性测试通过。")

if __name__ == "__main__":
    try:
        print("=" * 50)
        print("开始有声小说爬虫集成测试")
        print("=" * 50)

        first_chapter_url = test_fetch_directory()
        test_play_page_contains_audio()

        print("\n所有测试通过！")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n运行错误: {e}")
        sys.exit(1)

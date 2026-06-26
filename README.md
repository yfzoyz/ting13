## 环境变量说明

下表列出了所有需要在 GitHub Actions Secrets 或 workflow 中配置的环境变量。

| 变量名 | 是否必需 | 说明 |
|--------|:--------:|------|
| `TING13` | ✅ | 网站登录凭据，格式：`账号-----密码` |
| `PRIVATE_REPO` | ✅ | 存储音频文件的私有仓库全名，格式：`owner/repo`（如 `myuser/audiobooks-private`） |
| `ACCESS_TOKEN` | ✅ | 具有私有仓库 `repo` 权限的 GitHub Personal Access Token，用于克隆和推送文件 |
| `GH_TOKEN` | ✅ | 用于将 `progress.json` 推回当前公开仓库的 GitHub Token（通常与 `ACCESS_TOKEN` 相同） |
| `BOOK_KEY` | ✅ | 当前抓取的小说标识，默认为 `赘婿`，可在 workflow 中修改 |
| `NOVEL_PAGE` | ✅ | 小说详情页地址，默认为 `https://www.ting13.cc/youshengxiaoshuo/19353/`，可在 workflow 中修改 |

> 💡 **简单记忆**
> - `TING13` → 网站账号密码
> - `PRIVATE_REPO` + `ACCESS_TOKEN` → 私有仓库的"门牌号"和"钥匙"
> - `GH_TOKEN` → 公开仓库的提交凭据
> - `BOOK_KEY` + `NOVEL_PAGE` → 要下载的小说及其页面地址

---

## Secrets 配置步骤

> 路径：GitHub 仓库 → **Settings → Secrets and variables → Actions → New repository secret**

| 步骤 | 名称 | 值 |
|:----:|------|-----|
| 1 | `TING13` | 账号-----密码 |
| 2 | `PRIVATE_REPO` | 私有仓库全名（如 `myuser/my-private-storage`） |
| 3 | `ACCESS_TOKEN` | 生成的 Personal Access Token |
| 4 | `GH_TOKEN` | 可与 `ACCESS_TOKEN` 使用同一个 Token |

---

## 代码关键配置变量

| 变量 | 说明 |
|------|------|
| `BASE_URL` | 网站根地址，固定为 `https://www.ting13.cc` |
| `BOOK_URLS` | 字典，存储各小说目录页 URL，可在此添加更多小说 |
| `DIR_URL` | 由 `BOOK_KEY` 从 `BOOK_URLS` 中取出的当前小说目录页地址 |
| `MAX_PER_RUN` | 每次运行最多处理的章节数，默认 `60`，请勿设置过大以避免对网站造成压力 |
| `PROGRESS_FILE` | 公开仓库中的进度记录文件，固定为 `progress.json` |
| `TARGET_DIR` | 私有仓库中的音频存放目录，格式：`public/{BOOK_KEY}`（如 `public/赘婿`） |
| `USER_AGENT` | 模拟浏览器的请求头，尽量贴近真实浏览器以降低被反爬概率 |

---

## 两个仓库的分工

| 仓库 | 职责 | 存放内容 |
|------|------|----------|
| **公开仓库**（触发 Actions） | 运行爬虫、记录进度 | `crawler.py`、`progress.json`、workflow 文件 |
| **私有仓库**（`PRIVATE_REPO`） | 安全存放音频文件 | `public/赘婿/*.m4a`、`index.json`、`novels.json` |

进度文件 `progress.json` 在每次运行后自动更新并推送回公开仓库，下次运行将从断点处继续。

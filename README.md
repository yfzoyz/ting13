## 环境变量说明

下表列出了代码中使用的所有环境变量，它们需要在 GitHub Actions 的 Secrets 或 workflow 的 `env` 中设置。

| 变量名 | 是否必需 | 说明 |
|--------|----------|------|
| `TING13` | ✅ 必需 | 登录 `格式：账号-----密码` |
| `PRIVATE_REPO` | ✅ 必需 | 存储音频文件的私有仓库全名，格式为 `owner/repo`（例如 `myuser/audiobooks-private`）。 |
| `ACCESS_TOKEN` | ✅ 必需 | 具有访问上述私有仓库权限的 GitHub Personal Access Token（需勾选 `repo` 作用域），用于克隆和推送文件。 |
| `GH_TOKEN` | ✅ 必需 | 用于将 `progress.json` 推回当前公开仓库的 GitHub Token（通常与 `ACCESS_TOKEN` 相同即可）。 |
| `BOOK_KEY` | 可选 | 当前要抓取的小说标识，默认为 `"赘婿"`。若要抓取其他小说，可在 workflow 中修改或新增 `BOOK_URLS` 字典条目。 |

## 代码中的关键配置变量

| 变量 | 作用 |
|------|------|
| `BASE_URL` | 网站根地址，固定为 `https://www.ting13.cc` |
| `BOOK_URLS` | 字典，存储每本小说的目录页 URL。可在此处添加更多小说。 |
| `DIR_URL` | 由 `BOOK_KEY` 从 `BOOK_URLS` 中取出，即当前爬取小说的目录页地址。 |
| `MAX_PER_RUN` | 每次工作流运行最多处理的章节数，默认为 `50`。可根据需要调整（注意避免对网站造成过大压力）。 |
| `PROGRESS_FILE` | 公开仓库中的进度记录文件名，固定为 `progress.json`。 |
| `TARGET_DIR` | 私有仓库中的目标文件夹，格式为 `public/{BOOK_KEY}`（例如 `public/赘婿`）。音频文件及 `index.json` 均存放于此。 |
| `USER_AGENT` | 模拟浏览器请求时使用的 User-Agent 字符串，尽量贴近真实浏览器以降低被反爬概率。 |

## Secrets 配置示例（GitHub 仓库 Settings → Secrets and variables → Actions）

1. 新建 `TING13_COOKIES`，粘贴完整的 Cookie 字符串。  
2. 新建 `PRIVATE_REPO`，填入私有仓库全名（如 `myuser/my-private-storage`）。  
3. 新建 `ACCESS_TOKEN`，填入生成的 Personal Access Token。  
4. 新建 `GH_TOKEN`，可使用同一个 Token。  

> 提示：Cookie 有时效性，若爬虫突然失败，请重新登录网站并更新 Secret 中的 Cookie。

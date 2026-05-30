# 直拍播放量/点赞量监控系统 · 操作手册 (SOP)

这套系统做的事：每 5 分钟自动抓取你在 Google 表格里登记的 YouTube 直拍视频的**播放量**和**点赞量**，
按场次画**客观趋势图**（韩文/中文成员名、韩国时间 KST），在 **0.5/1/3/6/24/48/72 小时**这些时间点把
趋势图+数据表推送到你 iPhone 的 **Bark**；并实时检测"疑似刷量的异常增长"，发现就单独推送预警图。

全程零成本（GitHub Actions 免费）。日常你**只需在 Google 表格里加/停视频**，不用碰代码。

---

## 一、准备清单（你已完成的打勾）

- [x] YouTube Data API key（AIzaSy... 那串）
- [x] Google 服务账号的 .json 密钥文件
- [x] Google 表格已分享给服务账号邮箱（查看者权限）
- [x] 已启用 YouTube Data API v3 + Google Sheets API + Google Drive API
- [ ] GitHub 账号（有）
- [ ] iPhone 装好 Bark App（下面第 4 步做）

---

## 二、第一次搭建（只做一次，约 20 分钟）

### 步骤 1：建 GitHub 仓库

1. 登录 GitHub，点右上角「+」→「New repository」
2. 仓库名随便，例如 `ytb-monitor`
3. 选 **Public（公开）**　← 必须公开，这样免费且图片链接能打开
4. 点「Create repository」

### 步骤 2：上传代码

把我给你的 `ytb-monitor` 文件夹里**所有文件**传上去（保持目录结构）：
- 网页操作：仓库页点「uploading an existing file」，把所有文件拖进去（注意 `.github/workflows/monitor.yml` 这个子目录要一起传）
- 或用 git 命令（会的话）：clone 后把文件放进去 `git add . && git commit && git push`

> 文件清单：main.py、config.py、sheet_reader.py、youtube_fetch.py、storage.py、
> detector.py、plotting.py、notify.py、requirements.txt、.github/workflows/monitor.yml、data/.gitkeep

### 步骤 3：填入 4 个密钥（GitHub Secrets）

仓库页 →「Settings」→ 左栏「Secrets and variables」→「Actions」→「New repository secret」，
逐个新建以下 **5 个**（名字必须完全一致）：

| Secret 名称 | 值 |
|---|---|
| `YOUTUBE_API_KEY` | 你的 YouTube API key（AIzaSy...） |
| `GOOGLE_CREDENTIALS` | 打开那个 .json 密钥文件，**把全部内容复制粘贴进来** |
| `SHEET_ID` | 你的 Google 表格 ID（见下方说明） |
| `BARK_KEY` | 你的 Bark key（第 4 步获得） |
| `BARK_SERVER` | 填 `https://api.day.app`（用官方服务器；自建才改） |

> **表格 ID 怎么找**：打开你的 Google 表格，地址栏
> `https://docs.google.com/spreadsheets/d/【这一段就是ID】/edit`
> 复制中间那段。

### 步骤 4：配置 Bark（iPhone）

1. App Store 搜 **「Bark - Custom Notifications」**（开发者：丰 黄），安装
2. 打开 App，首页会显示一串你的专属 **key**（也可点示例推送测试里看到 URL，key 是 day.app 后面那段）
3. 把这个 key 填进步骤 3 的 `BARK_KEY`

### 步骤 5：准备 Google 表格

1. 用我给的 `Google表格模板.csv`：在 Google 表格里「文件」→「导入」→ 上传这个 CSV →「替换当前工作表」
2. 确认工作表（左下角 sheet 标签）名为 **监控列表**（脚本默认读这个名；不改也行，脚本会兜底读第一个表）
3. 表格列固定为这 4 列（表头别改名）：

   | 场次标题 | 成员名 | YouTube链接 | 状态 |
   |---|---|---|---|
   | 0530 KBS 뮤직뱅크 | 이상원 | https://youtu.be/xxxx | 运行 |
   | 0530 KBS 뮤직뱅크 | 주안신 | https://youtu.be/yyyy | 运行 |
   | 0529 Mnet 엠카운트다운 | 이상원 | https://youtu.be/zzzz | 停止 |

### 步骤 6：开跑

1. 仓库页 →「Actions」标签 → 若提示启用 workflow，点启用
2. 选左侧「YouTube Monitor」→ 点「Run workflow」手动跑一次，验证不报错
3. 之后它每 5 分钟自动跑，无需你管

✅ 搭建完成。

---

## 三、日常使用（每次有新直拍）

**只在 Google 表格操作，不碰 GitHub：**

- **加新直拍**：在表格加几行 —— 填「场次标题」（自己起，如 `0601 SBS 인기가요`）、「成员名」（韩文/中文都行）、「YouTube链接」（直接粘 YouTube 分享链接即可，各种格式都能识别）、「状态」填 **运行**。
- **一行 = 一个视频**，自动同时抓它的播放量和点赞量。
- **停止某场监控**：把那几行的「状态」改成 **停止**。
- **重新开始**：把「状态」改回 **运行** —— 会作为**新一轮**，计时从重新开始那刻算起（满 24/48/72h 各推一次图），异常检测也重新跑。
- 改完即生效，下一个 5 分钟周期脚本就会读到。

---

## 四、你会收到什么推送

**按场推送**（一场里所有成员画在同一张图对比）：

- 到达 **0.5h / 1h / 3h / 6h / 24h** 时：各推一次「点赞趋势图 + 播放趋势图 + 数据表格图」
- **48h**：只画**第 2 天**（24~48h）的数据
- **72h**：只画**第 3 天**（48~72h）的数据
- 满 72h 自动停止该场

**异常预警**（独立于上面，实时）：
- 检测到某成员疑似刷量的异常增长时，立即推一条，附**异常前后各 2 小时**的局部趋势图
- 预警图同样是**客观图**（不标红圈不高亮，只用颜色区分成员），你自己看图判断

所有图都是**点开通知 → 浏览器打开高清大图**，可放大看细节。

---

## 五、想调整时（可选，需改 GitHub 上的 config.py）

- 报警太灵敏/太迟钝：改 `config.py` 里的 `K_DEVIATION`（调大=更不灵敏）
- 改推送时间点：改 `PUSH_MILESTONES_H`
- 改"缺失"判定：改 `GAP_THRESHOLD_MIN`
- 改自动停止时长：改 `AUTO_STOP_HOURS`

改完 commit 即生效。

---

## 六、已知限制（提前知道，不影响核心功能）

1. **5 分钟间隔不精确**：GitHub Actions 高峰可能延迟到十几分钟。已用"按分钟速率"算法消除对趋势和异常判断的影响；定时推图可能晚几分钟，不影响。
2. **点赞被隐藏的视频**：若创作者关闭了点赞数显示，该视频点赞列会是空，只监控播放量。
3. **图片公开**：趋势图存在公开仓库，有链接的人能看到（不含隐私信息，一般无妨）。
4. **偶发状态小问题**：极少数情况下若某次运行失败 + commit 冲突，可能漏推或重复推一次某个里程碑图，不影响数据采集和异常检测本身。

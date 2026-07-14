# ytb-mr — YouTube 爱豆直拍数据监控

监控 Google 表格里配置的 YouTube 直拍视频的播放量/点赞数，定时画趋势图/排名表，通过 Bark 推送到 iPhone。

## 运行方式（重要）

- 触发：GitHub Actions（`.github/workflows/monitor.yml`），有两种触发：仓库自带 schedule cron（每 5 分钟）+ 外部定时服务调用 `workflow_dispatch`（每 5 分钟，传 `do_push=no`）。
- 总开关：仓库变量 `MONITOR_ENABLED`。在 GitHub 仓库 Settings → Secrets and variables → Actions → Variables 里把 `MONITOR_ENABLED` 设为 `true` 即开启；删除该变量或设为其他值即关闭（所有触发都会跳过，不消耗 Actions 时长）。单次强制运行：手动 Run workflow 时把 `force` 填 `yes`。
- 数据持久化：每轮运行把 `data/` 下的 CSV 和图表 commit 回仓库本身（`main.py` 的 `commit_and_push`，带 rebase 冲突重试）。

## 一次运行的流程（main.py）

1. 读取 Google 表格配置，只保留状态为“运行”的视频。
2. 用 YouTube Data API v3 批量抓取播放量和点赞数。
3. 将本轮数据追加到 `data/timeseries.csv`。
4. 维护 `data/state.json` 的运行状态：记录每场每成员的本轮开始时间和已推送里程碑；满 72 小时后继续记录数据，但不再推送图表。
5. 到达里程碑（30min、1h、1.5h、2h、3h、4h、5h、6h、24h、48h、72h）时，生成播放量和点赞数趋势图；指定里程碑另生成排名表。
6. 先将数据和图表 commit + push 回仓库，再统一发送 Bark，确保通知中的图片链接已在线。
7. 手动触发时，若 `do_push` 不为 `no`，还支持由 `manual_push` 按场次、时间范围和指标灵活推送。

## 文件说明

| 文件/目录 | 说明 |
| --- | --- |
| `main.py` | 主流程编排。 |
| `config.py` | 里程碑、时区、自动停止等配置。 |
| `sheet_reader.py` | 读取 Google 表格监控列表，只取“运行”状态行。 |
| `youtube_fetch.py` | 使用 YouTube Data API v3 批量抓取 views/likes；likes=0 视作接口缺失。 |
| `storage.py` | `data/` 读写，容忍 `timeseries.csv` 中的 git 冲突标记。 |
| `plotting.py` | 用 matplotlib 绘制趋势图、数据表和排名表，并配置韩文字体。 |
| `notify.py` | Bark 推送，支持多个 key。 |
| `Google表格模板.csv` | 表格模板：场次标题、成员名、YouTube 链接、状态。 |
| `data/` | `timeseries.csv`、`state.json` 和 `charts/` 生成的图；自动 commit。 |
| `restore-codex-auth.sh` | 恢复 codex CLI 登录态。 |
| `codex-workflow.md` | Claude × Codex 协作流程。 |
| `AGENTS.md` | 给 codex 的实现规则。 |

## 所需 Secrets

`YOUTUBE_API_KEY`、`GOOGLE_CREDENTIALS`（服务账号 json）、`SHEET_ID`、`BARK_KEY`（可用逗号分隔多个）、`BARK_SERVER`（可选，默认 `https://api.day.app`）。

## 日常操作

- 开/关监控：改仓库变量 `MONITOR_ENABLED`（见上）。
- 加/停某个视频：在 Google 表格把该行状态改为“运行”/“停止”。
- 注意：外部定时服务即使在开关关闭时仍会触发 workflow（job 会被跳过、几乎不耗时长）；长期停用建议把外部定时服务也暂停。

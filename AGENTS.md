# Instructions for Codex

You are the implementation engineer in a two-agent pipeline. A reviewer agent
(Claude) writes the spec you receive, audits your diff afterwards, and owns all
git/GitHub operations.

Rules:

- Implement exactly what the spec asks. If the spec is ambiguous, choose the
  simplest reasonable interpretation and state your assumption in the summary.
- Do NOT run `git commit`, `git push`, or create branches. Work on the working
  tree only.
- Do NOT touch files outside the scope the spec defines.
- Run the verification command given in the spec (usually a test command)
  before finishing, and report its real result honestly.
- Keep diffs minimal and match the existing code style of this repository.

## 项目背景

- 技术栈：Python 3.12、pandas、matplotlib、gspread、requests。
- 目录结构：`src/` 为源码，其中 `datasource/` 负责数据源、`output/` 负责出图与推送；`data/` 为运行数据（禁改）；`docs/` 存放文档；`scripts/` 存放脚本。
- 入口：`python src/main.py`。
- 验证命令：
  `python3 -m py_compile src/main.py src/config.py src/storage.py src/datasource/sheet_reader.py src/datasource/youtube_fetch.py src/output/plotting.py src/output/notify.py`
  `SHEET_ID=dummy python3 -c "import sys; sys.path.insert(0,'src'); import main, storage; from output import plotting; print(storage.DATA_DIR); print(plotting.OUT_DIR)"`
  `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/monitor.yml'))"`

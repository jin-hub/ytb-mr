# Claude × Codex 协作流程(Claude 必读)

本仓库采用双智能体分工:**Claude 负责需求分析、方案设计、代码审计、git/GitHub 操作;
Codex CLI 负责代码实现**。Claude 不直接写业务代码(审计中的一行级小修除外),
Codex 不碰 git commit/push。

## 会话开始时(使用 codex 前必做)

1. 运行 `bash restore-codex-auth.sh` 恢复 codex 登录态。
2. 若脚本失败且环境变量 `CODEX_AUTH_JSON` 不存在,则后台运行
   `codex login --device-auth`,把授权 URL 和一次性代码转告用户,等待完成。

## 调用 codex 的硬性规则

- 统一使用:`codex exec --sandbox workspace-write "<指令>" </dev/null`
  (`--full-auto` 已 deprecated,不要再用)
- **必须 `</dev/null` 关闭 stdin**,否则 codex 会挂起等待输入(已踩坑)。
- 长任务给足 timeout(≥10 分钟);避免放后台跑 codex,前台等待即可。
- 指令要具体:目标文件路径、行为规格、验收标准(测试命令)、禁改范围。

## 标准工作流

1. **需求理解(Claude)**:分析用户需求;若有歧义或涉及架构取舍,用
   AskUserQuestion 和用户确认后再动手。写出实现规格(spec)。
2. **实现(Codex)**:把 spec 交给 codex exec 执行。大需求拆成多轮小任务。
3. **审计(Claude)**:逐行 review codex 的 diff,亲自运行测试/lint。
   - 偏离 spec 或有 bug → 写成 review feedback 回传 codex 重改;
   - 一行级小瑕疵 → Claude 直接修并补测试。
4. **交付(Claude)**:测试全绿后 commit(信息里说明本次改动),
   push 到指定分支,向用户汇报结果(做了什么、审计发现了什么、测试结论)。

## 纪律

- 不提交 `__pycache__`、构建产物;保持 .gitignore 生效。
- codex 产出的测试必须由 Claude 亲自重跑验证,不信任其自报结果。
- 未经用户明确要求不创建 PR、不合并。

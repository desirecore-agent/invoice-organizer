# 发票整理助手

把散落在邮箱和本地的发票收拢成一本可对账、可复用的台账，并对每一条记录的来源负责。

安装前的前置条件、审批模式取舍与能力边界，见 [USAGE.zh-CN.md](./USAGE.zh-CN.md)
（[English](./USAGE.en-US.md)）。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `agent.json` | Agent 配置（AgentFS `agent-config` schema） |
| `persona.md` | 人格描述 |
| `principles.md` | 执行纪律：取证、对账与降级规则 |
| `USAGE.md` | 使用说明兜底版（内容同 `USAGE.zh-CN.md`） |
| `USAGE.zh-CN.md` / `USAGE.en-US.md` | 使用说明双语版 |
| `CHANGELOG.md` | 变更历史 |
| `skills/invoice-extract/` | 私有技能：票面文本形态识别与字段抽取 |
| `skills/invoice-ledger/` | 私有技能：台账与月度报告 |
| `skills/invoice-workflow/` | 私有技能：端到端整理流程 |
| `skills/invoice-automation/` | 私有技能：定时与无人值守 |

四个技能随 Agent 一起安装到它的私有技能目录，不进全局技能目录。

## 关于 USAGE 的三个版本

市场详情页按约定文件名取使用说明，locale 变体（`USAGE.<locale>.md`）的探测列表来自
`agent.json` 的 `i18n.locales`。而 pointer 形态的 `agent.json` 由 `agentConfigSchema`
直接校验，顶层没有 `i18n` 且根级 `additionalProperties: false`——声明不了 locales。

因此当前只有无后缀 `USAGE.md` 会被远程抓取到，两份 locale 版本作为事实源保留，
待平台侧补上「pointer 路径也传 locale hints」后自动生效。

## 市场条目

本仓库是内容事实源，市场条目 [`desirecore/market`](https://github.com/desirecore/market)
的 `agents/invoice-organizer/` 只保留 pointer，按不可变 commit ref 指向这里。

改动流程：本仓库合并 → market 条目重新 pin 到新 ref。

# 发票整理助手 · 开发与维护指引

> 本文件给**接手维护这个 Agent 的智能体或开发者**看，与 `AGENTS.md` 内容相同（两份必须保持同步）。
> DesireCore 运行时**不读取**本仓库里的这两个文件，所以这里写的是维护指引，不是 Agent 的行为规则——行为规则在 `persona.md` 和 `principles.md`。

---

## 1. 这个仓库是什么

DesireCore 官方市场里「发票整理助手」Agent 的**内容仓库**。它把散落在邮箱和本地的发票收拢成一本可对账、可复用的台账，并对每一条记录的来源负责。

与本组织下其它 Agent（`feishu-orchestrator` / `dingtalk-workspace`）的关键差异：**它不编排任何第三方 CLI**。能力全部来自 DesireCore 平台自带的工具（Mail Service、`Read` 的 PDF/OFD 解析、文件工具）、系统自带的解压工具与本仓库自带的 4 个私有技能。因此：

- 没有「用户要先装一个第三方二进制」这道前置
- `NOTICE` 文件不需要——没有第三方内容需要署名（`LICENSE` 仍然必需，见 §4）
- 前置条件是**授权类**的：邮箱账号、工作目录、审批模式

## 2. 两个仓库的关系（改动前必须懂）

```
desirecore-agent/invoice-organizer   ← 本仓库：Agent 的全部内容
        ↑ 被指向（pin 到具体 commit）
desirecore/market
  └── agents/invoice-organizer/
        ├── entry.json                 ← 卡片：写着「内容在本仓库，版本 <SHA>」
        ├── catalog-metadata.v1.json   ← 审核信息（证据六项）
        └── assets/avatar.webp         ← 头像
```

**市场侧只有这三个文件，不能多。** 校验器用 `inline_path.is_file() == pointer_path.is_file()` 判定——`agent.json` 与 `entry.json` **必须恰好存在一个**，都有或都没有都会让条目从市场消失。

**改内容 → 改本仓库；发新版 → 改市场卡片的 pin。** 新安装装到的是卡片上 pin 的那个 commit，不是本仓库最新 `main`。已安装用户的更新取决于客户端版本：

- **v10.0.177 之后的客户端**（包含 desirecore/desirecore#3728）：只会被更新到卡片当前的 pin。`main` 上领先 pin 的提交，在市场 repin PR 合并、客户端同步到新目录之前不会送达；条目被拦、下架或与本仓库对不上时，更新暂停。
- **v10.0.177 及更早的客户端**：仍跟随本仓库 `main` 的最新提交。`main` 上的 `agent.json#version` 一旦递增，这些用户最迟约 10 分钟内会被无人值守更新到 `main` 头，中间所有提交一并带上；版本号不变的提交不会单独推送，但会随下一次版本递增一起送达。

所以在旧客户端退出使用之前，**合进 `main` 仍等于对一部分已安装用户发布**：版本递增的提交只在市场 repin PR 已准备好时合入，并紧接着合并 repin。

### 迁移前的历史形态（读旧记录时会遇到）

本 Agent 原先是 **inline** 形态：全部内容直接放在 market 的 `agents/invoice-organizer/` 里，而 `provenance.content` 是指向 market 自身的 **self-pointer**（`url=market.git`, `path=agents/invoice-organizer`）——治理字段按 pointer 写、内容却在 market。2026-09-06 迁出本仓库后两者才一致。旧 PR（#117 / #120 / #138 / #144）都是在 inline 形态下改的，路径前缀是 `agents/invoice-organizer/`。

## 3. 目录结构与各文件职责

```
agent.json          AgentFS 运行时配置（纯运行时字段，不含市场展示字段）
persona.md          人格（L0/L1/L2 三层）
principles.md       行为规则：L1 Must Do / Must Not / Priority，L2 确认动作 / 隐私边界 / Plan 判据
USAGE.md            使用说明兜底版——市场详情页渲染的就是这个文件（见 §5.3）
USAGE.zh-CN.md      使用说明中文版（事实源，内容与 USAGE.md 相同）
USAGE.en-US.md      使用说明英文版（事实源，当前抓不到，见 §5.3）
README.md           仓库说明（给人看）
CHANGELOG.md        变更历史，与 market 卡片的 changelog 对应
LICENSE             MIT，市场 sidecar 的 compliance.licenseEvidencePath 指向它
skills/
├── invoice-extract/     票面字段抽取；references/ 有「票面文本形态」1 篇
├── invoice-ledger/      台账与月度报告；references/ 有「月度报告模板」1 篇；scripts/check-ledger.py 出台账前的自检（只读）
├── invoice-workflow/    端到端整理流程（无 references）；scripts/unpack-zip.sh 解 zip 附件
└── invoice-automation/  定时与无人值守（无 references）
```

四个技能随 Agent 一起安装到它的私有技能目录，**不进全局技能目录**。

**`references/` 里的文档必须在 `SKILL.md` 正文里有显式索引。** `Skill` 工具走 `loadSkillContent` 直接返回 SKILL.md 正文，**不输出 `<skill-resources>` 清单**——光把文件丢进 `references/` 目录 Agent 一篇都看不见。现有两篇都已在对应 SKILL.md 里索引过，新增文档时照做。

## 4. 发新版：pin 要同时改三处

市场卡片 pin 一个 40 位 SHA。**改 ref 必须同时改三处，漏任何一处校验必红**：

| 文件 | 字段 |
| --- | --- |
| `entry.json` | `source.ref` |
| `catalog-metadata.v1.json` | `provenance.content.ref` |
| `catalog-metadata.v1.json` | `governance.compliance.reviewedRef` |

自检别按路径逐个查，**全文扫 40 位 hex**：

```bash
grep -oE '[0-9a-f]{40}' agents/invoice-organizer/*.json | sed 's|.*:||' | sort | uniq -c
# 期望：3 <同一个 SHA>
```

同时更新 `timestamps.reviewedAt.value` 与 `governance.compliance.reviewedAt`（两者必须相等），以及 `entry.json#latestVersion`（必须等于本仓库 `agent.json#version`）。

**本 Agent 是 `installable`，证据六项必须齐**（比 `listing-only` 的条目严格）：

| 项 | 当前值 |
| --- | --- |
| `provenance.content.ref` | 与 `entry.source.ref` 相等 |
| `governance.compliance.reviewedRef` | 与 `content.ref` 相等 |
| `timestamps.reviewedAt` | 与 `compliance.reviewedAt` 相等 |
| `governance.availability` | `installable` |
| `governance.license` | `known` + `evidencePath: LICENSE` |
| `entry.json#license` | `MIT`（必填字段，与上一行对齐） |

**`licenseEvidencePath` 相对的是本内容仓库，不是市场条目目录。** 市场侧没有 `LICENSE` 文件是正常的，`feishu-orchestrator` 同样如此。

**`entry.json#redistribution` 必须等于 `catalog.governance.redistribution`**，当前两边都是 `source-pointer-only`。迁 pointer 时 catalog 侧旧值是 `allowed`，不改会报 `legacy-consistency`。

**顺序**：先推本仓库并用 `git ls-remote` 确认 SHA 可达，**再**改市场卡片。

市场 PR 前跑（`uv` 在 `~/.local/bin`，直接 `python3` 会缺 `yaml`）：

```bash
uv run --quiet scripts/catalog/validate_catalog_metadata.py --require-complete   # 要 0 error
```

**ERROR 淹在上百条 WARN 里**，用 `grep -E "^\[ERROR"` 抓；末尾 `agents=N` 计数掉了说明条目被判非法。

## 5. 硬性规则

### 5.1 `agent.json` 是 pointer 形态，schema 直接校验

inline 条目有 `projectMarketAgentConfigToAgentFs` 投影层兜底，**pointer 没有**——`agent.json` 由 `agentConfigSchema` 直接校验，根级 `additionalProperties: false`。

因此**禁止**出现这些市场展示字段：`category` / `tags` / `updatedAt` / `maintainer` / `installPolicy` / `updatePolicy` / `i18n` / `persona` / `changelog` / `contentSource`。迁移时从 `agent.json` 剥掉的就是这 10 个，它们的归属是 `entry.json` 与 `catalog-metadata.v1.json`。

另外两条 schema 硬要求：

- `id` 必须是 **UUID v4**（本仓库是 `e4023f8f-f905-412f-b14d-6dc8a5ac837c`）。`invoice-organizer` 这类 slug 是市场条目标识，住在 `entry.json#id`
- `avatar` 只能是 `{char, color}`（可选 `image`）。`{t, bg}` 是市场卡片字段，会被拒。当前是 `{char: "票", color: "green"}`——原 `bg` 是 `#34C759 → #007AFF` 的绿蓝渐变，取的起点色

**改完 `agent.json` 必须实跑校验，不能靠肉眼**：

```ts
// 在 DesireCore 主仓建一个临时 *.test.ts（放 /tmp 解析不到 @desirecore/schemas）
import { validateAgentConfig } from '@desirecore/schemas'
const r = validateAgentConfig(JSON.parse(readFileSync('<path>/agent.json', 'utf8')))
expect((r as { success?: boolean }).success).toBe(true)
```

### 5.2 版本号要三处对齐

发版时 `agent.json#version`、`CHANGELOG.md` 的最新条目、`entry.json#latestVersion` 必须一致。迁移时实测撞过：market 侧已经发到 1.0.3，本仓库还停在 1.0.2，pin 过去会让卡片显示的版本与内容对不上。

### 5.3 `USAGE.md` 的三个版本与当前的实际行为

本仓库有三份使用说明，**当前只有无后缀 `USAGE.md` 会被市场抓到**：

1. `fetchAgentRepoData` 固定抓 `agent.json` / `persona.md` / `CHANGELOG.md` / `USAGE.md` 四个文件
2. `USAGE.<locale>.md` 的探测列表由 `extractDeclaredLocales` 从**上游 agent.json 的 `i18n.locales`** 得出——而 §5.1 说了 pointer 的 agent.json 不能有 `i18n`
3. `readAgentDetailFromPointer` 调 `resolveUsageDocFromFiles(repoData.usage, locale)` 时**不传 localeHints**，回退链只剩 `locale → 无后缀`

**代价**：en-US 用户看到的是中文版。这是迁 pointer 的已知损失，根因在平台侧——补齐 `readAgentDetailFromPointer` 的 locale hints 后两份 locale 版本会自动生效，本仓库不用动。

**因此改使用说明要改两处**：`USAGE.zh-CN.md`（事实源）与 `USAGE.md`（兜底，内容相同）。`USAGE.en-US.md` 作为事实源保留，同步更新。三份都有 **16000 字符上限**。

### 5.4 隐私与公开信息边界（本仓库是公开的，且这个 Agent 处理真实票据）

这是本仓库**最需要小心的一条**——它的日常工作对象就是含公司名、税号、金额的真实发票。

**禁止任何真实业务数据进入 tracked 文件**：公司名、统一社会信用代码、发票号码、金额、邮箱、姓名、个人 HOME 路径。

实测教训：`skills/invoice-extract/SKILL.md` 里描述标签污染坑时，初稿写的是「两张携程用车发票」，合并到 market 时被改成「某网约车平台开具的」，同时去掉了具体时间。**举真实案例时一律脱敏到品类级别**，不要写具体品牌、日期、金额。

推之前扫一遍：

```bash
grep -rInE "[0-9]{15,20}|@[a-z0-9.-]+\.[a-z]{2,}|/Users/[a-z]+|¥[0-9]" . --exclude-dir=.git
```

（第一段正则是统一社会信用代码 / 发票号码的长度特征。）

`principles.md` 的 L2「隐私边界」是 Agent 运行时的规则，与这条是两回事——那条管 Agent 怎么处理用户数据，这条管仓库里能写什么。

### 5.5 不要把本机环境固化进公开分发物

`agent.json` 的 `llm` 保持 `smart` + `flagship` 默认，**不要**钉死到某个具体 Provider 的模型。`env` 块保持当前的上下文注入开关，`mcp_servers` 保持空对象、`webhooks.enabled` 保持 `false`——这三处都不是本 Agent 的能力面，改动前先确认真有需要。

### 5.6 重要纪律要放进 `Must Do` 编号列表

同一条规则写在 L2 说明段里往往无效，提升为 L1 `Must Do` 的编号祈使规则才生效。改 `principles.md` 时重要纪律一律进编号列表，别写成段落。

## 6. 能力边界（写文档时别越界）

`USAGE.md` 与 `catalog-metadata.compatibility.requirements` 已经明确的四条前置，改文档时保持口径一致：

| 前置 | 说明 |
| --- | --- |
| 邮箱授权 | Gmail / Outlook / IMAP，用户一次性自行授权；没有就停在 preflight 并说明缺什么 |
| 工作目录 | 需要 `ManageWorkDirs` 登记一个工作目录放归档、台账与报告 |
| 审批模式 | 默认模式下每次文件写入与邮件调用都弹审批卡片；**无人值守需要用户自己把本 Agent 切到 `allow-all`** |
| Python（可选） | 解析 PDF / OFD / XML / 扫描件不需要装任何东西；`.xlsx` 台账需要 `openpyxl` + `pandas`，不可用时降级为 UTF-8 BOM 的 CSV 并如实报告降级；出台账前的自检脚本 `check-ledger.py` 只用标准库，Python 不可用时改为按形式校验清单人工核对 |
| 解压工具（系统自带） | `.zip` 附件由 `skills/invoice-workflow/scripts/unpack-zip.sh` 解，依次尝试 `unzip`、`python3`、bsdtar（Windows 上显式找 `System32\tar.exe`），前一种解不开就换下一种；macOS / Windows 自带其中至少一种，精简 Linux 可能都没有，那时 zip 进 `_quarantine/` 交给用户 |

**不做发票真伪验证**——没有官方验真通道，Agent 只做格式与算术校验，并引导用户自行到国家税务总局平台查验。这条口径不能松，改文档时别写成「可验真」。

## 7. 踩过的坑

1. **改文件前先搜在途 PR。** 本仓库与 market 条目由多个会话并行维护。迁移期间实测撞过两次：#138（改 `invoice-extract/SKILL.md`）和 #144（版本 1.0.3）在迁移 PR 推出后才合并，差一点把它们丢在原地。开工前跑：
   ```bash
   gh pr list --repo desirecore/market --state all --search "invoice" --limit 10
   ```
   迁移这类会删文件的改动尤其危险——两个 PR 无论谁先合，另一个的改动都会丢。

2. **搬内容时不要机械替换。** 从同类仓库复制 `NOTICE` / `README` 这类文件时，`sed` 批量改名会造出事实错误（实测把一份 NOTICE 里的第三方 CLI 名称改错、还留下了错误的技能来源描述）。事实性文件重写，不要替换。

3. **安装是整棵 git 树检出。** 客户端把本仓库 fork 进用户的 `agents/<id>/`，所有被 git 跟踪的文件（`README.md` / `LICENSE` / `CHANGELOG.md` / `CLAUDE.md` / `AGENTS.md`）都会进用户的 AgentFS，之后每次更新也一并合入；安装回执不再记录 `contentDigest`。加文件前想一下它是否该出现在用户机器上。

4. **`validate` 的 ERROR 淹在上百条 WARN 里。** 末尾那行 `N error(s), M warning(s)` 才是判据。

## 8. 相关记录

| 类型 | 位置 |
| --- | --- |
| 市场卡片 | `desirecore/market` → `agents/invoice-organizer/` |
| `USAGE.md` 约定与市场详情区块 | DesireCore PR #2600 |
| 内联市场 Agent 的 Schema 冲突修复 | DesireCore PR #2515 |
| `Read` 工具的 OFD 解析 | DesireCore PR #2623 / #2626，ADR-144 |
| 首次上架 | market PR #117 |
| 报告文件名约定统一 | market PR #120 |
| PDF 文本层标签污染防护 | market PR #138 |
| 迁出为独立仓库并转 pointer | market PR #140 |

## 9. 改动前的自检清单

- [ ] 改的是本仓库还是市场卡片？（内容 → 本仓库；发版 → 卡片）
- [ ] 先搜过在途 PR，确认没人在改同一处
- [ ] `agent.json` 没混入市场展示字段，`id` 仍是 UUID，`avatar` 是 `{char, color}`
- [ ] 改了 `agent.json` 就实跑 `validateAgentConfig`，拿到 `success: true`
- [ ] 改使用说明时 `USAGE.md` 与 `USAGE.zh-CN.md` 同步，`USAGE.en-US.md` 也更新
- [ ] 新增 `references/` 文档已加进对应 `SKILL.md` 的显式索引
- [ ] 隐私扫描零命中，举例已脱敏到品类级别
- [ ] 发版时 `agent.json#version` / `CHANGELOG.md` / `entry.json#latestVersion` 三处一致
- [ ] 若要发版：先推本仓库确认 SHA 可达，再改市场三处 ref，`grep -oE '[0-9a-f]{40}'` 自检为 3 处同值
- [ ] 市场 PR 跑过 validate，`grep -E "^\[ERROR"` 零命中，`agents=` 计数没掉
- [ ] `AGENTS.md` 与 `CLAUDE.md` 内容一致

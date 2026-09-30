---
name: invoice-extract
description: >-
  把单份票据文件解析成结构化字段：PDF（数电票 / 旧版增值税票 / 铁路电子客票 / 航空行程单）、
  OFD（优先读内嵌国标结构化 XML）、数电票 XML、扫描件图片（视觉识别），含字段抽取规则、
  置信度评分、红字发票识别、勾稽校验与失败处理。Use when 需要读出某个发票文件里的发票号码 / 开票日期 / 销售方 / 价税合计等字段，
  或解析报错、抽出来的字段不对、遇到加密 PDF / 扫描件 / OFD 时。
  Also covers invoice field extraction, OCR fallback and confidence scoring.
version: 1.0.0
type: procedural
risk_level: low
status: enabled
tags:
  - invoice
  - extraction
  - pdf
  - ofd
metadata:
  category: extraction
  i18n:
    default_locale: en-US
    source_locale: zh-CN
    locales:
      - zh-CN
      - en-US
    zh-CN:
      name: 发票解析
      short_desc: PDF / OFD / 扫描件的字段抽取规则与置信度评分
    en-US:
      name: Invoice Extraction
      short_desc: Field extraction rules and confidence scoring for PDF, OFD and scans
  requires:
    tools:
      - Read
      - FileDigest
---

# 发票解析

## L0

三条路径，按可信度从高到低：**结构化数据（数电票 XML、OFD 内嵌数据）→ PDF/OFD 文本层启发式抽取 → 视觉识别**。

走哪条由文件本身决定，不由你选。OFD 的结构化数据有三个来源、可信度并不一样，取值前先认出处。
抽不齐必填字段就隔离，绝不用「常见格式」补全（票面本来不载销售方的票种按 `invoice-workflow`「发票记录字段」豁免）。

## L1

### 路径选择

| 文件 | 怎么做 | `extractedBy` | 置信度基线 |
| --- | --- | --- | --- |
| `.xml`，根元素是 `EInvoice` | `Read` 它，按下面「数电票 XML」取值 | `xml` | **1.0** |
| `.ofd` 有「内嵌附件」来源 | `Read` 它，取标注「来源: 内嵌附件」的那一段 | `ofd-xml` | **1.0** |
| `.ofd` 只有「发票标引」来源 | 同上，取标注「来源: …CustomTag.xml」的那一段，**缺的字段回页文本补** | `ofd-xml` | 0.9 |
| `.ofd` 只有 `DocInfo/CustomDatas` | 当线索用，**必须**与页文本交叉核对（尤其价税合计） | `text-layer` | 0.85 |
| `.ofd` 三个来源都没有 | 用 `Read` 给出的逐页文本走文本层规则 | `text-layer` | 0.85 起 |
| `.pdf` 有可用文字层 | `Read` 直接抽文本 | `text-layer` | 0.9 起 |
| `.pdf` 无文字层，或文字层抽出的内容不足以判定 | 无文字层时 `Read` 自动渲染成图；有垃圾文字层时显式 `pdf_mode:"render"` + `pages` | `vision` | 0.7 起 |
| `.jpg` / `.png` / `.webp` | 直接 `Read` 图片路径，你自己看 | `vision` | 0.7 起 |
| `.xml`，空文件 | 丢弃：不归档、不隔离，`files.json` 记「空文件，丢弃」（见 `invoice-workflow` 第 3 步） | — | — |
| `.xml`，根元素不是 `EInvoice` | 不作为解析来源；同封邮件有别的载体时当它们的伴随载体（见 `invoice-workflow` 第 3 步） | — | — |

不需要 Python，不需要安装任何东西，`Read` 一个工具全包。

**图片千万不要用 `UnderstandImage`**——那个工具只吃 HTTP URL，本地文件路径喂不进去。本地图片就是 `Read{file_path}`。

### 数电票 XML：与 OFD 标引同一套键，但值是完整的

开票平台常把数电票的 `.xml` 与 `.ofd`、`.pdf` 一起发。根元素是 `EInvoice` 的就是这种 XML，`Read` 返回的是 XML 原文，几 KB 大小。元素路径去掉根元素 `EInvoice` 后，与下面 OFD 的 **B 族**键一一对应（`<TaxSupervisionInfo><InvoiceNumber>` 就是 `TaxSupervisionInfo.InvoiceNumber`），按 B 族那张表取值，区别在下面几处：

- **价税合计是完整数字。** `EInvoiceData.BasicInformation.TotalTax-includedAmount` 直接写着 `1285.00`，不会像 OFD 标引那样只解引用出一个 `¥`，不需要走硬规则 1 的求和兜底
- **日期是标准格式**：`TaxSupervisionInfo.IssueTime` 写成 `2026-03-18`，也见过带时刻的 `2026-03-18 16:05:42`，取日期部分
- **多两个 OFD 标引里没有的元素**：红字发票的 `EInvoiceData.SpecificInformation.RedEInvoice.OriginalInvoiceCode`（被冲销的蓝字发票号码，记进 `redLetterOf`，见「特殊票据」的红字发票）；多行明细是多个 `IssuItemInformation` 兄弟元素，可以直接逐行写进 `items[]`。同名项目后面紧跟一行**只有负数金额、没有数量单价**的，是折扣行，照样写进 `items[]`，不是红字发票——红字票看的是整张票的合计为负，不是某一行
- 明细里的 `TaxRate` 多数是小数（`0.06`），也见过已经带 `%` 的；一律规范成 `6%` 这样的百分数

`Header.EIid` 与发票号码相同，不另记。`EInvoiceData.SpecificInformation.PassengerTransportation` 下的出行人、证件号是个人信息，不入记录。取完值同样做下面的四族自检。

### OFD：三个结构化数据来源，先认出处再取值

OFD 是中国电子发票的两大法定载体之一。`Read` 一个 `.ofd` 返回三段：**文档概览**（页数、电子签章、内嵌附件清单、内嵌标引清单、文档自带元数据）、**内嵌结构化数据**、**逐页文本**。

结构化数据有**三个互不等价的来源**，`Read` 的输出里每一段都写明了出处。**先看出处，再决定怎么用**——三者的字段完整度和可信度差得很远，混为一谈会算错税额。

| 来源 | 在输出里长什么样 | 年代 | 有什么 | 缺什么 |
| --- | --- | --- | --- | --- |
| **① 内嵌附件** | `## 内嵌结构化数据: … — 来源: 内嵌附件 Doc_0/Attachs/…` | 2020 年式样 | 字段最全：发票代码、号码、买卖双方名称/税号/地址电话/开户行、金额税额、逐行商品明细 | — |
| **② 发票标引** | `## 内嵌结构化数据: 发票标引 (TypeID=…) — 来源: …CustomTag.xml，值由 ObjectRef 指向的页面文字对象解引用得到` | **2024 数电票 / 全电发票**（当前主流） | 发票号码、开票日期、买卖双方名称与税号、不含税金额、税额、价税合计、开票人、货物名称、税率、单价、数量、备注 | **发票代码、地址电话、开户行、多行明细的逐行结构**——只能回逐页文本找 |
| **③ 文档自带元数据** | 只出现在 `## 文档概览` 的 `- 文档自带元数据 (DocInfo/CustomDatas):` 这一条 bullet 里，**从来不是** `## 内嵌结构化数据` 段 | 数电票常见 | 因开票系统而异：一类实测只有 6 个字段（发票号码、买卖方税号、合计金额、合计税额、开票日期）；B 族那类多一个 `价税合计` | **没有买卖方名称**；前一类连价税合计也没有 |

三者可能同时出现、可能只有一两个、也可能一个都没有（那就只剩逐页文本）。

**键名是点号路径。** 展平器剥掉命名空间前缀、丢掉根元素、用 `.` 连接层级、同名兄弟加 `[序号]`。真实的键长这样：

```
InvoiceNo = 24112000000000010001
Buyer.BuyerName = 示例数字科技（北京）有限公司
Seller.SellerTaxID = 91110105MA00P8L2RU
TaxInclusiveTotalAmount = ¥4449.00
GoodsInfos.GoodsInfo[1].Item = 餐费
```

`Buyer/BuyerName`、`GoodsInfos/GoodsInfo[]` 这类斜杠写法**在输出里一个都不存在**，别按它匹配。

**键名不止一族，取决于开票系统，不取决于来源编号。** 同一批真实邮件里实测见过四族，下面四张表各对应一族——先看键名前缀认出是哪一族，再查对应的表。部分键名末尾带 `.ObjectData`，匹配时忽略这个后缀。四张表都对不上时按字段语义匹配（键名本身就是英文语义词，`…SellerName` 就是销售方名称），**不要直接退回页文本**：结构化字段就在输出里，退回页文本是白白降级。

**A 族**：`InvoiceNo` / `Buyer.*` / `Seller.*`（2020 年式样的内嵌附件，以及部分数电票的标引）

| 键 | 记录字段 | 哪些来源有 |
| --- | --- | --- |
| `InvoiceNo` | `invoiceNumber` | ①② |
| `InvoiceCode` | `invoiceCode` | **只有 ①**（数电票本就没有发票代码，为空是正常的） |
| `IssueDate` | `invoiceDate`（原文 `2024年05月28日` 中文格式，转成 `YYYY-MM-DD`） | ①② |
| `InvoiceCheckCode` | `checkCode` | 只有 ① |
| `Buyer.BuyerName` · `Buyer.BuyerTaxID` | `buyerName` · `buyerTaxId` | ①② |
| `Seller.SellerName` · `Seller.SellerTaxID` | `sellerName` · `sellerTaxId` | ①② |
| `TaxInclusiveTotalAmount` | `totalAmount`（价税合计） | ①② |
| `TaxExclusiveTotalAmount` | `amountExcludingTax` | ①② |
| `TaxTotalAmount` | `taxAmount` | ①② |
| `GoodsInfos.GoodsInfo[n].{Item,Specification,MeasurementDimension,Price,Quantity,Amount,TaxScheme,TaxAmount}` | `items[]` | **只有 ①**；② 只有零散的 `Item` / `Price` / `Quantity`，凑不出逐行结构 |

**B 族**：`TaxSupervisionInfo.*` + `EInvoiceData.*`（数电票的标引，来源 ②；实测多个省份税务局开具的票都是这一族）

| 键 | 记录字段 | 备注 |
| --- | --- | --- |
| `TaxSupervisionInfo.InvoiceNumber` | `invoiceNumber` | |
| `TaxSupervisionInfo.IssueTime` | `invoiceDate` | 中文日期格式，转成 `YYYY-MM-DD` |
| `EInvoiceData.SellerInformation.SellerName` · `…SellerIdNum` | `sellerName` · `sellerTaxId` | |
| `EInvoiceData.BuyerInformation.BuyerName` · `…BuyerIdNum` | `buyerName` · `buyerTaxId` | |
| `EInvoiceData.BasicInformation.TotalAmWithoutTax` | `amountExcludingTax` | |
| `EInvoiceData.BasicInformation.TotalTaxAm` | `taxAmount` | |
| `EInvoiceData.BasicInformation.TotalTax-includedAmount` | `totalAmount` | **实测多数只有一个 `¥`**，按下面硬规则 1 处理 |
| `EInvoiceData.BasicInformation.TotalTax-includedAmountInChinese` | 不入记录 | 大写价税合计，用来校验小写 |
| `Header.InherentLabel.GeneralOrSpecialVAT.LabelName` | `invoiceType` | `普通发票` → `数电普票`，`增值税专用发票` → `数电专票`（`EInvoiceType.LabelName` 实测都只是「电子发票」，分不出普专） |
| `EInvoiceData.IssuItemInformation.{ItemName,Amount,TaxRate,ComTaxAm}` | `items[]` | 单行明细可直接用；多行时与页文本对过再写 |
| `EInvoiceData.SpecificInformation.PassengerTransportation.*` | 不入记录 | 出行类票的乘车人、证件号、出行日期，属个人信息 |
| `EInvoiceData.AdditionalInformation.Remark` | 备注 | 红字发票的备注以「被红冲蓝字数电发票号码：<20 位号码>」开头，号码记进 `redLetterOf`，见「特殊票据」的红字发票 |

**C 族**：航空运输电子客票行程单的内嵌附件（来源 ①，置信度 1.0）

| 键 | 记录字段 | 备注 |
| --- | --- | --- |
| `ElectronicInvoiceAirTransportReceiptNumber` | `invoiceNumber` | 20 位发票号码 |
| `IssueDate` | `invoiceDate` | 已经是 `YYYY-MM-DD` |
| `NameOfSeller`（与 `IssueParty` 相同） | `sellerName` | 没有销售方税号字段，票面也不印，`sellerTaxId` 为空是正常的 |
| `NameOfPurchaser` · `UnifiedSocialCreditCodeOfPurchaser` | `buyerName` · `buyerTaxId` | |
| `TotalAmount` | `totalAmount` | |
| `VatTaxAmount` · `VatRate` | `taxAmount` · `taxRate` | 税率是小数（`0.09`），写成 `9%` |
| `Fare` + `FuelSurcharge` | `amountExcludingTax` | 两项相加才是计税依据，见「特殊票据」 |
| `CivilAviationDevelopmentFund` + `OtherTaxes` | `otherCharges` | **不属于**计税依据，两项相加记进 `otherCharges`，勾稽时要加上 |
| `Insurance` | 不入记录 | 保险费另列；实测样本都是 0，是否计入合计未经验证——不为 0 时在收尾里提示人工核对 |
| `VerificationCode` | `checkCode` | |
| `ETicketNumber` | 不入记录 | 电子客票号码，**不是**发票号码 |
| `PassengerName` · `ValidIdNumber` · `QrCode` | 不入记录 | 乘机人个人信息与二维码原文，记录与台账里都不写 |
| （无） | `invoiceType` | 记 `航空行程单（电子发票）` |

**D 族**：电子发票（铁路电子客票）的内嵌附件（来源 ①，置信度 1.0）

| 键 | 记录字段 | 备注 |
| --- | --- | --- |
| `ElectronicInvoiceRailwayETicketNumber` | `invoiceNumber` | 20 位发票号码 |
| `DateOfIssue` | `invoiceDate` | 开票日期，已经是 `YYYY-MM-DD`；**不是**乘车日期 `TravelDate` |
| `TypeOfVoucher` | 不入记录 | 固定是「电子发票（铁路电子客票）」，用来认族；`invoiceType` 记 `铁路电子客票` |
| `NameOfPurchaser` · `UnifiedSocialCreditCodeOfPurchaser` | `buyerName` · `buyerTaxId` | |
| `Fare` | `totalAmount` | 票价就是价税合计 |
| `TotalAmountExcludingTax` · `TaxAmount` · `TaxRate` | `amountExcludingTax` · `taxAmount` · `taxRate` | **票面不印这三项**，只有结构化数据里有；税率是小数（`0.09`），写成 `9%` |
| `IssueParty` · `IssuePartyCode` | `sellerName` · `sellerTaxId` | 实测都是空元素（输出里只有 `IssueParty@nil = true`），票面也不印销售方——两项留 `null`，不算缺失（见 `invoice-workflow`「发票记录字段」）；哪天有值了照录 |
| `TypeOfBusiness` | 不入记录 | 实测都是 `售`。出现别的值（退票、改签之类）时标「待复核」，备注写「业务类型为 <值>」——这类票的金额口径未经实测 |
| `NumberOfOriginalInvoice` · `AmountRefunded` · `FareOfOriginalRailwayTicket` · `Remarks` | 不入记录（`Remarks` 记备注） | 退票、改签相关字段，实测都为空。有值时照录进备注，与 `TypeOfBusiness` 一起标「待复核」；票价为负时按红字发票处理，`NumberOfOriginalInvoice` 有值就用它当 `redLetterOf`——这层关系未经实测，所以同时标「待复核」 |
| `ETicketNumber` | 不入记录 | 电子客票号，**不是**发票号码 |
| `Name` · `IdNumber` | 不入记录 | 乘车人姓名与证件号，个人信息 |
| `DepartureStation` · `DestinationStation` · `TrainNumber` · `TravelDate` · `DepartureTime` · `SeatLevel` · `Carriage` · `Seat` 等 | 不入记录 | 行程信息与 C 族一样不入记录，需要时看归档的原件 |

**四族取完值都做同一个自检**：`amountExcludingTax + taxAmount + otherCharges（没有就按 0）= totalAmount`，票面有大写金额的（A、B 族）还要与大写一致；C、D 族票面没有大写。对不上说明认错了族或取错了键，回头查，不要带着矛盾的数入账。

#### 三条硬规则，每条都对应一种会把钱算错的写法

1. **`totalAmount` 只认价税合计键（A 族 `TaxInclusiveTotalAmount`、B 族 `TotalTax-includedAmount`、C 族 `TotalAmount`、D 族 `Fare`），而且剥掉符号后必须是数字。** 票面上「¥」和「4449.00」是两个页面文字对象：有的开票系统两个都标引，解引用后拼成 `¥4449.00`；**也有的只标引了 `¥` 那一个**，取到的值就只剩一个货币符号（B 族实测多数如此，数字在页面上，只是没被标引指到）。写进记录前剥掉货币符号与千分位逗号；剥完为空或不是数字 → 当这个键缺失处理：用 `不含税金额 + 税额` 求和，**与大写金额逐字折算的值一致才采用**（大写以「（负数）」开头的是红字发票，折算值取负：`（负数）壹佰伍拾圆整` 对 `-150.00`）（这里要精确到分，不适用形式校验清单里「大写只做数量级校验」的放宽；CustomDatas 里有 `价税合计` 时也可以作为印证），对不上就回页文本取 `（小写）¥xxxx`。绝不把 `¥`、`0` 或空串写进 `totalAmount`。

2. **绝不把 `DocInfo/CustomDatas` 的「合计金额」当 `totalAmount`。** 那是**不含税金额**。实测一张 2024 数电票：CustomDatas 写 `合计金额: 4197.17`、`合计税额: 251.83`，而同一张票的**价税合计是 ¥4449.00**——照抄就是每张票少记一个税额，还带着高置信度混进合计。那一类票的 CustomDatas 压根没有价税合计这一项（B 族那类有，名字就叫 `价税合计`，可以拿来印证）。正确对应：`合计金额` → `amountExcludingTax`，`合计税额` → `taxAmount`；`totalAmount` 按硬规则 1 取价税合计键，或回页文本取 `价税合计（大写）… （小写）¥xxxx`。两个都拿不到就按必填字段缺失隔离，不要拿合计金额顶替。

3. **只有来源 ① 配得上「别再从版面文字里猜字段」。** 来源 ② 拿得到四个必填字段，所以票不会被隔离——它的代价是**静默残缺**：缺的发票代码与逐行明细都是选填字段，于是 `invoiceCode` 永远为空、`items[]` 永远是空数组，而台账上看不出任何异常，用户也不会收到任何提示。来源 ③ 更彻底，连价税合计和买卖方名称都没有；标引链路断掉（`Read` 会在「解析诊断」里报出来）而只剩它时，必填字段是真的抽不齐。**② 与 ③ 都必须继续读逐页文本把缺的字段补上**，别在结构化段落上就收工。

#### 置信度按来源分档

| 来源 | `extractedBy` | 基线 | 理由 |
| --- | --- | --- | --- |
| 数电票 XML（独立的 `.xml` 附件） | `xml` | **1.0** | 开票系统写出的完整数据，价税合计是完整数字 |
| ① 内嵌附件 | `ofd-xml` | **1.0** | 值内联在开票系统写出的国标 XML 里，不是 OCR，也不是版面猜测 |
| ② 发票标引 | `ofd-xml` | **0.9** | 字段归属可信，但**值是页面文字对象解引用来的**，与版面文本同源 |
| ③ CustomDatas | `text-layer` | **0.85**，且必须与页文本交叉核对 | 字段少（6～7 个），且「合计金额」的语义极易读错（见上面第 2 条） |
| 三个来源都没有 | `text-layer` | 0.85 起 | 走下面的版面文本规则 |

来源 ② 的明细尤其不可盲信：实测 `单位 / 数量 / 单价` 三列会被挤成一格（`1 4197.16981132075`），且长词会断到下一行。窄列的值必须与页文本对过才写进 `items[]`；对不上就留空并降置信度，不要把挤在一起的串硬拆。合计三项（不含税金额 / 税额 / 价税合计）在实测里是可信的。

#### 三个模式参数

- 某个值在展平时被截断（回执提示「字段数超过上限」）→ `ofd_mode:"attachments"` 拿原文补那一个字段，不要凭截断值入账。该模式对来源 ② 同样给出解引用后的取值与标引原文
- 结构化数据太大把页文本挤没了 → `ofd_mode:"text"` 只看版面文字（注意该模式**不解析标引**，来源 ② 会一起消失）
- 只想读某几页 → `pages`，语法与 PDF 相同

`Signature`（base64 签章）和 `TaxControlCode`（密码区乱码）没有语义，**不要**为了拿它们去调 `attachments` 模式，也不要往上下文里倒——记一句「含签名，N 字节」就够了。

OFD **不会**被渲染成图片，也不做签章有效性校验（只报告是否含签章）。所以纯图形的 OFD 没有视觉兜底：三个结构化来源全缺、页文本又为空时，**先看同一封邮件里有没有同一张票的 PDF**——有就改读 PDF，这份 OFD 作为伴随载体归档，不隔离（判定办法见 `invoice-workflow` 第 3 步）；没有才隔离，写清「OFD 无结构化数据且无可读页文本」。实测一批里有三封邮件的 OFD 都是这种纯图形文件，同封邮件的 PDF 都能正常读。

**回落到版面文本时，OFD 的页文本比 PDF 好用**：模板层的固定标签已经与页内的值合并到同一行，`标签：值` 成对出现，表格列用 Tab 分隔——不会出现 PDF 那种竖排字段名被逐字拆行的形态。下面那套「忽略单字行」的规则是给 PDF 的，读 OFD 页文本时不必套用。

### PDF / 版面文本：抽取规则

具体到每种票的文本形态，读 `${SKILL_DIR}/references/票面文本形态.md`。那份文件是按真实票面版式构造的夹具在 `Read` 下的实测输出，不是示意图（票据本身是合成的，字段值为虚构值）。开工前**先读它**，尤其在遇到不认识的票种时。

跨票种通用的六条：

1. **先归一化空白再匹配。** 票面字段名里有全角排版留下的空格（`名 称：`、`校 验 码：`），把连续空白折叠成一个空格再匹配，别把空格写死进模式。
2. **忽略单字行。** 竖排字段（`购/买/方/信/息`、`销/售/方/信/息`、`备/注`）会被逐字拆成单行。这是正常的，整段跳过。
3. **买卖方靠顺序，不靠标签——但顺序必须自校验。** 数电票里两方的字段名一模一样，**先出现的是购买方，后出现的是销售方**（旧版票是购买方在上、销售方在下，中间隔着密码区）。这条规则依赖开票系统的版面布局，不是国标保证的：一旦某家系统的排版相反，每张票的买卖方都会静默对调，而勾稽校验一条都不会报错。所以**取到两方之后必须回头验一次**：preflight 已经问过用户的报销主体抬头，如果「后出现的一方」等于报销主体、而「先出现的一方」不是，说明这份票的顺序是反的——按抬头把两方归位，并在收尾里单独报告「检测到该开票系统买卖方顺序与常规相反」。两方都不等于报销主体时（代开、个人抬头、集团内其他主体）不动顺序，按现有的「抬头不符」流程标出来交用户判断。
4. **丢掉密码区。** 旧版票 `密码区` 之后的 4 行是乱码，进上下文只会干扰。
5. **金额取小写。** `价税合计（大写） 壹仟玖佰伍拾玖元玖角捌分 （小写）¥1959.98` —— 取 `（小写）` 后面的数字。大写金额用来做校验，不用来当值。
6. **税率不一定是百分比。** `免税` / `不征税` / `***` 都会出现，原样记录，不要强行转成 0。

### 名称字段的标签污染防护（PDF 文本层）

PDF 的文本层顺序由生成它的开票系统决定，不保证等于版面阅读顺序。部分模板会把「两栏的全部标签」连续输出完，再输出「两栏的全部取值」（`名称：` `统一社会信用代码/纳税人识别号：` 各连续出现两次，之后才是两个公司名和两个税号）——这是一次真实批量整理里实测踩到的坑：某网约车平台开具的两张「旅客运输服务」数电票，`sellerName` 被按"标签后紧跟的下一段文字"这个假设错误抽成了字面的「统一社会信用代码/纳税人识别号：」。**这条只针对 PDF 文本层**——OFD 的页面文本按坐标重建阅读顺序，模板标签与取值已经合并在同一行（见上一节），不会出现这种错位；结构化来源①②③同样不受影响，遇到 PDF 判断不可靠时优先切到 OFD 的结构化字段或视觉识别，而不是死磕文本层位置匹配。

对 PDF 文本层抽出的 `sellerName` / `buyerName` 做以下硬校验：

- 若名称等于或主要由 `统一社会信用代码`、`纳税人识别号`、`名称`、`名称：`、`销售方信息`、`购买方信息` 等字段标签组成，必须视为**提取失败**，不得以低置信度直接采信。
- 先尝试从同页其他文本块重新定位（常见于双栏模板整段错位，两个值仍在文本里，只是顺序对不上）；仍不能可靠确定时，改用整页视觉阅读（`pdf_mode:"render"`）。
- 视觉阅读仍不能确定时，按「抽不齐必填字段就隔离」处理——名称是必填字段，抽不出真实值就不是「低置信度」，是缺失：整份文件移入 `_quarantine/`，`.reason.txt` 写清「PDF 文本层名称字段疑似标签污染，视觉复核仍无法判定」；不得从邮件主题、文件名或相邻税号猜测公司名，也不要拿标签本身或空值凑一条低置信度记录混进台账。

### ⚠️ 文本里会有 NUL 字节

PDF 文本抽取的结果里常出现 `\x00`（未映射字形），位置多在 `价税合计（大写）` 与中文大写之间。

后果：这样的文件 `file` 会判成 `data`，**裸 `grep` 会当二进制处理并静默返回空结果**——你会以为「没找到关键词」，其实是根本没搜。

对策：

- 检索发票文本一律用 `grep -a`，或者干脆用 `Read` 读回来自己判断
- 不要依赖 shell 的 locale（很多环境里 `LANG` / `LC_ALL` 是空的，`grep 中文` 同样会静默失效）
- 写进记录之前把 `\x00` 与其它控制字符剥掉

### 扫描件与图片

`Read` 遇到**没有文字层**的 PDF 页会自动渲染成图交给你看，不需要额外参数。

- **有文字层但抽出来是垃圾的扫描件**（劣质 OCR 层，现实中很常见：乱码、字序错乱、只有零星几个字）不会触发自动渲染——`Read` 认为这页有文字，就只给你那堆垃圾。判据不是「有没有文字层」，而是**「抽出来的内容够不够判定」**：文字层存在但拼不出发票号码或价税合计时，显式 `pdf_mode:"render"` 加 `pages` 重读一次，走视觉路径，`extractedBy` 记 `vision`。
- **想看有文字层那一页上的印章 / 配图 / 版式**：同样要显式 `pdf_mode:"render"` 加明确的 `pages`（如 `"1"`、`"1,3-5"`），否则默认只抽文字，看不到图形。
- **多页扫描件**：不带 `pages` 连续调用即可，`Read` 会自动接着上次的位置读；每读完一批用一两句话记下关键数字，图会被自动退场只留占位。
- 视觉识别出的字段一律 `confidence ≤ 0.85`；金额、发票号码这两个字段视觉识别的错误代价最高，逐字复核一遍再写。
- 图片模糊、倾斜、只有半张票 → 不要硬猜，隔离并在 `.reason.txt` 里写清「图片质量不足以识别 <哪几个字段>」。

### 置信度评分

从路径基线出发，逐项扣分：

| 情况 | 调整 |
| --- | --- |
| 数电票 XML、OFD 内嵌附件（来源 ①） | 1.0，不再扣分 |
| OFD 发票标引（来源 ②） | 0.9 起；标引没覆盖到、靠页文本补的字段按页文本口径扣分 |
| OFD `DocInfo/CustomDatas`（来源 ③） | 0.85 起；与页文本核对不上的字段 −0.3 并标 `checkFailed` |
| `金额 + 税额 + otherCharges == 价税合计`（差 ≤ 0.01；`otherCharges` 没有按 0） | +0.05（上限 1.0） |
| 勾稽对不上 | −0.3，并在记录里标 `checkFailed` |
| 大写金额与小写金额不一致 | −0.3 |
| 必填字段靠视觉识别得到 | 每项 −0.05 |
| 销售方名称被截断或含明显乱码 | −0.2 |
| 开票日期不在用户给定的时间范围内 | 不扣分，但要在收尾里单独列出来 |

**低于 0.8 的字段在台账里标「待复核」**，并进收尾清单。整条记录的 `confidence` 取所有必填字段里的最小值（按票种豁免、留 `null` 的销售方不算）。

### 特殊票据

| 票种 | 关键差异 |
| --- | --- |
| 电子发票（铁路电子客票） | **不是数电票版式**：票面只印发票号码、开票日期、行程、票价、乘车人和购买方，**没有销售方，也不印不含税金额、税额与大写金额**。实测 PDF 的文字层要么只剩一个 `￥`，要么只剩一串没有中文标签的数字和拼音（发票号码、票价、两个日期都在，但分不清哪个日期是开票日期）——后一种超过自动渲染的门槛，`Read` 不会自己转成图，**两种都显式 `pdf_mode:"render"` 走视觉识别**，不要从这层文字按位置猜字段；视觉读得到的金额也只有票价；OFD 带 D 族内嵌附件，不含税金额与税额只在那里有——12306 把 OFD 和 PDF 打在一个 `.zip` 里发，解开后按 `invoice-workflow` 第 3 步先读 OFD。`totalAmount` 取票价，`invoiceDate` 取开票日期不是乘车日期，`invoiceType` 记 `铁路电子客票`，`sellerName` / `sellerTaxId` 留 `null` |
| 铁路电子客票报销凭证（旧版） | 没有 `发票号码：` 标签；抬头下的 21 位电子客票号当 `invoiceNumber`，`confidence` 上限 0.9 |
| 电子发票（航空运输电子客票行程单） | 票面标题就是这几个字，右上角有 **20 位发票号码**——它才是 `invoiceNumber`，`电子客票号码` 不是。`invoiceDate` 取**填开日期**不是航班日期；`验证码` 进 `checkCode`。**`amountExcludingTax` = 票价 + 燃油附加费**（计税依据），`taxAmount` = 增值税税额；**民航发展基金与其他税费不属于计税依据**，不能并进 `amountExcludingTax`，两项之和记进 `otherCharges`。勾稽式是 `票价 + 燃油附加费 + 增值税税额 + 民航发展基金 + 其他税费 = 合计`，即 `amountExcludingTax + taxAmount + otherCharges = totalAmount`，只看「金额 + 税额 = 价税合计」一定对不上——用「合计 − 税额」倒推不含税金额会把基金一并算进去（实测每张多出整整一笔基金），而且倒推出来的数永远能过勾稽，错了也没人发现。OFD 版带 C 族内嵌附件，有它就用它。`invoiceType` 记 `航空行程单（电子发票）` |
| 航空运输电子客票行程单（旧版，无发票号码） | 票面**没有** 20 位发票号码，用 `电子客票号码`；`invoiceType` 记 `航空行程单`；`invoiceDate` 取**填开日期**不是航班日期；`印刷序号` 进 `checkCode` |
| 出租车 / 网约车 | 网约车通常是标准数电票，正常处理；车牌与里程在备注里。**卷式与机打的出租车票、通行费票、部分定额票票面上只写「金额」「合计」，从不出现「价税合计」四个字**——按 `invoice-workflow` 的判定阶梯第 1 条，合计项认「价税合计 / 合计金额 /（金额+税额）」中的任意一种，别因为找不到「价税合计」就把它判成非发票 |
| 定额发票 | 只有代码 + 号码 + 面额，没有明细也没有税额；走兜底主键 |
| 作废票 | 文本里出现 `作废` / `已作废` → `isVoid: true`。**文本里没有不代表没作废**，作废戳可能是纯图形 |
| 红字发票 | 数电票开出后不能作废，开错了由销售方开一张**红字发票**冲销原来那张（蓝字发票）。认法，四条满足任一：XML 或 OFD 标引的 `Header.InherentLabel.InIssuType.LabelCode` 为 `N`（这个标签叫「是否蓝字发票标志」，蓝字票是 `Y`，最稳）；备注以「被红冲蓝字数电发票号码：<20 位号码>」开头；XML 有 `RedEInvoice.OriginalInvoiceCode`；价税合计为负、大写以「（负数）」开头（铁路电子客票没有大写金额，只看票价是否为负）。记 `isRedLetter: true`；金额、税额、价税合计**照票面记负数**，不要取绝对值；被冲销的号码记进 `redLetterOf`：数电蓝字票记 20 位号码；冲的是旧版税控票（备注写的是发票代码加号码）时写成 `<代码>-<号码>`，与归档文件名里旧版票的写法相同，`invoice-ledger` 按同样的形式去匹配蓝字票；备注和 XML 都拿不到号码时留空并标「待复核」——**是不是红字票看 `isRedLetter`，不看 `redLetterOf` 有没有值**。它不是作废票，`isVoid` 保持 `false`。只有某一明细行是负数的是折扣行，不是红字发票 |
| 免税 / 不征税 | `taxRate` 原样记 `免税`；`taxAmount == 0` 且 `amountExcludingTax == totalAmount` 是正常的 |
| 领票二维码页（开票通知截图） | 画面是「扫一扫，领取发票」一类的二维码，旁边列着销售方、购买方、发票号码、开票日期、金额——这是领票入口，**不是票面**，原票要用户扫码才能拿到。**不入账，也不算非发票**：移进 `_quarantine/`，`.reason.txt` 写「待领取：邮件只附了领票二维码页」，在 `ledger.json#quarantined` 这一条里加 `kind: "claim"` 与 `claim: {invoiceNumber, sellerName, invoiceDate, totalAmount}`（照图抄，抄不清的留 `null`，号码同样要过形式校验）。实测一轮把这类图片当发票入账（没有原件、没有税额），另一轮当成非发票隔离、报告里只说「3 份非完整截图」，用户看不出有 3 张票等着领 |
| 外币结算 | 票面仍是人民币，原币与汇率写在备注里。照抄备注，**不做换算**，`currency` 保持票面币种（也就是 `CNY`）——所以台账里不会出现非 CNY 的记录，外币信息只存在于备注 |

### 解析失败的处理

失败不是异常，是常态的一部分。四种失败态各自的处置：

| 失败态 | 现象 | 处置 |
| --- | --- | --- |
| 加密带口令 | `Read` 返回 `PDF 文件无法解析: <底层报错>` + 换行 + `可能原因: 文件损坏、加密或非标准格式`（半角冒号，中间夹着底层报错原文）。**加密没有被单独识别，这是一条通用兜底**——同一句话也会用于损坏和非标准格式，别只凭它就断定是加密 | 隔离。若底层报错里出现 `password` 之类字样，在收尾里告诉用户这张票带口令，口令通常写在**邮件正文**里（常见为发票号码后 6 位或手机号后 6 位），请他自己解密后重新放进 `_inbox/`。**不要**尝试猜口令 |
| 文件损坏 / 空文件（空 XML 除外，它直接丢弃） | 同一句兜底报错 | 隔离，`.reason.txt` 写「文件损坏或为空，字节数 N」，并把底层报错原文一并抄进去（那是唯一能区分这三种失败的线索） |
| 能读但内容为空 | 抽出来只有几个字符 | 隔离，写「文本层为空，可能是纯图形 PDF 但渲染也未产出可读内容」 |
| 判定为非发票 | 命中负向关键词或缺关键标签 | 隔离，写清是哪一条判据命中的 |

隔离 = 把文件移到 `_quarantine/`，写一个同名 `.reason.txt`，然后**继续处理下一份**。一份失败不能中断整批。

## L2

### 形式校验清单

抽完字段后逐条走一遍，任何一条不过都要在记录里留痕：

- `金额 + 税额 + otherCharges = 价税合计`（浮点比较留 0.01 容差；`otherCharges` 没有就按 0，目前只有航空电子行程单用得上，见「特殊票据」）
- **票面必印金额与税额的票种**（数电票、增值税发票、电子发票（航空运输电子客票行程单）），这两个字段为空**不算通过**——那是抽取失败，不是票面没有：标 `checkFailed`、按勾稽对不上扣分，再按下面「用脚本批量处理时」补抽。例外：免税 / 不征税票的 `taxAmount` 记 `0`（票面印的是「免税」「***」，不是空）；定额票、卷式出租车票、旧版航空行程单票面本来就不印税额，这两个字段空着才正常。铁路电子客票票面也不印金额与税额，但 OFD / XML 里有：从结构化数据解析的必须有值，只有 PDF 视觉识别的空着才正常
- 大写金额与小写金额一致（大写解析可以只做数量级校验，不必逐字）
- 明细行 `数量 × 单价 = 金额`（多行时求和；只有负数金额、没有数量单价的折扣行不做这一项）
- 数电票发票号码 20 位纯数字；旧版发票代码 12 位、号码 8 位
- **数电票发票号码的前两位是开票年份的后两位**（2026 年开的票以 `26` 开头）；**从 PDF 文本层抽的号码必须在文本层里作为一整段连续数字原样出现**。两栏版式里标签和值分开输出，号码前后常紧挨着别的字段的数字（「下载次数：1」、订单号），拼上一位、丢掉一位都能凑成 20 位——实测把「下载次数：1」的 `1` 拼到号码前面、又丢了号码最后一位，置信度还标了 0.98。任一条不满足就是**抽取失败**，按「名称字段的标签污染」同样处理：同页重新定位，再退到整页视觉阅读，仍不能确定就按必填字段缺失隔离。号码是去重主键，读错一位就会多记一张票、漏掉真的那张
- **价税合计为负数，`isRedLetter` 就必须是 `true`**（折扣行只让合计变小，不会让它变负）；两者不一致是抽取失败，回源数据重取红字标记与蓝字号码（见下面「用脚本批量处理时」第 4 条）
- **`otherCharges` 是一个数**（民航发展基金 + 其他税费之和），不是对象；航空电子行程单的 `amountExcludingTax` 只含票价与燃油附加费——实测一轮把基金算进了不含税金额、把 `otherCharges` 写成明细对象，勾稽照样能平，台账的金额列却全错了
- 开票日期是合法日期且不在未来
- 税号 18 位（老式 15 位纳税人识别号也合法，不要判错）

校验只降置信度、只留标记，**不修改抽出来的值**。票面本身写错的情况真实存在，改数据比留标记危险得多。

**也不许为了让校验通过去倒推一个没抽到的字段**——比如拿「价税合计 − 税额」填 `amountExcludingTax`。倒推出来的数永远能过勾稽，于是勾稽再也发现不了它错了。唯一的例外是硬规则 1 那种：两个独立的票面来源（标引里的不含税金额与税额、大写金额）互相印证之后才采用。

### 批量解析的顺序

一批文件的处理顺序：先 `FileDigest` 一次性算完所有哈希（一次最多 100 个路径），去掉命中缓存的，再逐个解析。

先算哈希的理由：重复下载在发票场景里非常常见（同一封邮件转发多次、用户手工又存了一份），先去重能省掉大部分解析开销。

#### 用脚本批量处理时

票多的时候写脚本批量抽 PDF 文本层是合理的，但有四条纪律：

1. **OFD 一律用 `Read` 读。** `Read` 走平台的 OFD 解析内核，直接给出三个来源的结构化字段，输出很短；PDF 库读不了 OFD，自己拆包重写解析既慢又会漏掉上面没见过的键名族。
2. **多轮只补全，不清空。** 后一轮某字段为空而前一轮有值 → 保留前一轮的值；两轮都有值且不同 → 保留置信度高的一方，另一方写进 `.index/raw/<sha256>.json` 备查。实测：第二轮脚本用更弱的正则重新解析 PDF，把第一轮已经抽到的税额覆盖成了空；勾稽又因为字段为空被跳过，一整批里大部分票的税额就这样丢了，没有一条报错。
3. **写台账前算一次完整率。** `amountExcludingTax`、`taxAmount`、`sellerTaxId`、`buyerTaxId` 各有几条为空，写进收尾报告（票面本来不印的单列，不算缺失：航空行程单没有销售方税号，铁路电子客票没有销售方与销售方税号、只有 PDF 时也不拆金额与税额，定额票、卷式出租车票、旧版航空行程单不拆金额与税额）。必印字段（见形式校验清单）有空缺时，先对这些票逐张用 `Read` 补抽，补不上的逐条标「待复核」，不要带着一列空值出台账。
4. **红字发票的号码别漏取。** 脚本从 XML / OFD 取值时，把 `Header.InherentLabel.InIssuType.LabelCode`、XML 的 `EInvoiceData.SpecificInformation.RedEInvoice.OriginalInvoiceCode` 与备注里「被红冲蓝字数电发票号码」一并取出。`isRedLetter` 为真、`redLetterOf` 却空着，而源数据里明明有号码，是脚本漏取——实测一轮就这样，台账因此配不上被冲销的蓝字票，看起来两张都能报销。

### 什么时候值得回看原图

台账做完后用户质疑某个数字时，找到这张票归档里的 PDF 或图片（`archivedPath` 本身是 `.pdf` 就用它；主件是 XML 或 OFD 时，用同目录下同名的 `.pdf` 伴随载体），加 `pdf_mode:"render"` 和该页页码重读一次，肉眼核对再答——`pdf_mode` 只接受 PDF，对 `.xml` / `.ofd` 会被拒绝。只有 XML / OFD、没有 PDF 的票，`Read` 那份 OFD 的逐页文本核对。不要凭 `.index/raw/<sha256>.json` 里的缓存回答「我当时读到的是这个」——用户问的是票面写的是什么。

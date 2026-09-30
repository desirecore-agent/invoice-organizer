#!/usr/bin/env python3
"""出台账前的台账自检：按规则逐条检查 .index/ledger.json，并用归档里同名的数电票 XML 复核关键字段。

用法：python3 check-ledger.py <发票目录>
  发票目录：<工作目录>/发票，里面要有 .index/ledger.json

输出（逐行）：
  ERROR <发票主键>  <问题>     必须改：改完重跑，直到没有 ERROR
  WARN  <发票主键>  <问题>     逐条看一眼，确认是票面如此就不用改
  SUMMARY records=<n> errors=<n> warnings=<n> xml_checked=<n> pending_claims=<n>
                               pending_claims 是还没领到的待领取张数（按整本台账派生）
退出码：没有 ERROR 为 0，有 ERROR 为 1，用法或目录不对为 2。

只读：不修改任何文件。检查的是 ledger.json 里已经写下的值——模型抽取时漏看、错拼、
写错类型的，这里用确定性的规则再过一遍。XML 来自邮件，属于不可信输入：超过 2MB、
带 DOCTYPE / ENTITY 声明的一律不解析，只报 WARN。
"""
import datetime
import json
import re
import sys
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from pathlib import Path

MAX_XML_BYTES = 2 * 1024 * 1024
TOLERANCE = Decimal("0.01")
# 这些票种不是 20 位数电票号码，前缀规则不适用
LEGACY_TYPES = ("铁路电子客票报销凭证", "航空运输电子客票行程单（旧版", "定额", "卷式", "出租")

errors = 0
warnings = 0
# 同一类轻微问题（空对象当 null 用）逐条打会淹没真正要改的，最后汇总成一行
empty_values = {}
string_values = {}
moved_paths = []


def report(level, key, message):
    global errors, warnings
    if level == "ERROR":
        errors += 1
    else:
        warnings += 1
    # 控制字符去掉，一条问题只占一行
    print(f"{level}\t{clean(key, 80)}\t{clean(message, 400)}")


def clean(v, limit):
    s = re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]", " ", str(v))
    return s if len(s) <= limit else s[:limit] + "…(截断)"


def dec(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        raise TypeError
    try:
        d = Decimal(str(value).replace("¥", "").replace("￥", "").replace(",", "").strip())
    except InvalidOperation:
        raise TypeError
    if not d.is_finite() or d.adjusted() > 12:
        raise TypeError
    return d


def load_records(ledger):
    if isinstance(ledger.get("invoices"), dict):
        return list(ledger["invoices"].items())
    if isinstance(ledger.get("records"), list):  # 历史形态，原样读
        return [((r.get("invoiceNumber") if isinstance(r, dict) else None) or f"#{i}", r)
                for i, r in enumerate(ledger["records"])]
    return []


def local(tag):
    return tag.rsplit("}", 1)[-1]


def find_text(root, *path):
    node = root
    for name in path:
        nxt = None
        for child in list(node):
            if local(child.tag) == name:
                nxt = child
                break
        if nxt is None:
            return None
        node = nxt
    text = (node.text or "").strip()
    return text or None


def parse_einvoice_xml(path):
    """返回 (fields, problem)。problem 非空时 fields 为 None。"""
    try:
        if not path.is_file():
            return None, "不是普通文件，未读取"
        if path.stat().st_size > MAX_XML_BYTES:
            return None, "XML 超过 2MB，未解析"
        with path.open("rb") as fh:
            raw = fh.read(MAX_XML_BYTES + 1)
    except OSError as exc:
        return None, f"读不了 XML：{exc.strerror}"
    if not raw.strip():
        return None, "EMPTY"
    if len(raw) > MAX_XML_BYTES:
        return None, "XML 超过 2MB，未解析"
    try:
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "XML 不是 UTF-8 / UTF-16，未解析"
    if not text.strip():
        return None, "EMPTY"
    upper = text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        return None, "XML 带 DOCTYPE / ENTITY 声明，未解析"
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None, "XML 格式错误"
    if local(root.tag) != "EInvoice":
        return None, None  # 不是数电票 XML，不参与复核
    basic = ("EInvoiceData", "BasicInformation")
    red_code = find_text(root, "EInvoiceData", "SpecificInformation", "RedEInvoice", "OriginalInvoiceCode")
    in_issu = find_text(root, "Header", "InherentLabel", "InIssuType", "LabelCode")
    remark = find_text(root, "EInvoiceData", "AdditionalInformation", "Remark") or ""
    m = re.search(r"被红冲蓝字数电发票号码[:：]\s*(\d{20})", remark)
    total = find_text(root, *basic, "TotalTax-includedAmount")
    issue = find_text(root, "TaxSupervisionInfo", "IssueTime") or ""
    fields = {
        "invoiceNumber": find_text(root, "TaxSupervisionInfo", "InvoiceNumber") or find_text(root, "Header", "EIid"),
        "invoiceDate": issue[:10] if re.match(r"\d{4}-\d{2}-\d{2}", issue) else None,
        "sellerTaxId": find_text(root, "EInvoiceData", "SellerInformation", "SellerIdNum"),
        "buyerTaxId": find_text(root, "EInvoiceData", "BuyerInformation", "BuyerIdNum"),
        "amountExcludingTax": find_text(root, *basic, "TotalAmWithoutTax"),
        "taxAmount": find_text(root, *basic, "TotalTaxAm"),
        "totalAmount": total,
        "redLetterOf": red_code or (m.group(1) if m else None),
    }
    negative = False
    try:
        negative = total is not None and dec(total) < 0
    except TypeError:
        pass
    fields["isRedLetter"] = bool(in_issu == "N" or red_code or m or negative)
    shapes = {"invoiceNumber": r"\d{8,21}", "redLetterOf": r"\d{20}", "invoiceDate": r"\d{4}-\d{2}-\d{2}",
              "sellerTaxId": r"[0-9A-Za-z]{15,20}", "buyerTaxId": r"[0-9A-Za-z]{15,20}"}
    for name, pattern in shapes.items():
        v = fields.get(name)
        if v is not None and not re.fullmatch(pattern, v):
            fields[name] = None
    return fields, None


def is_legacy(record):
    kind = str(record.get("invoiceType") or "")
    if kind.strip() == "航空行程单" or "旧版" in kind:
        return True
    return any(t in kind for t in LEGACY_TYPES)


def inside(p, root):
    try:
        p.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def check_record(key, r, base, all_numbers):
    kind = str(r.get("invoiceType") or "")
    railway = "铁路电子客票" in kind and "报销凭证" not in kind

    # 必填
    date = r.get("invoiceDate")
    if not (isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)):
        report("ERROR", key, f"invoiceDate 不是 YYYY-MM-DD：{date!r}")
        date = None
    else:
        try:
            if datetime.date.fromisoformat(date) > datetime.date.today():
                report("WARN", key, f"开票日期 {date} 在未来：多半是日期抽错了，重核票面")
        except ValueError:
            report("ERROR", key, f"开票日期 {date} 不是合法日期")
            date = None
    number = r.get("invoiceNumber")
    if number is not None and not isinstance(number, str):
        report("ERROR", key, f"invoiceNumber 必须写成字符串（现在是 {type(number).__name__}），数字会丢掉前导零、长号码会变形")
        number = str(number)
    must_print = not is_legacy(r) and any(t in kind for t in ("数电", "专票", "普票", "增值税", "电子发票", "铁路电子客票", "航空"))
    if not number:
        if must_print:
            report("ERROR", key, "invoiceNumber 为空：这类票面一定印着号码，按 invoice-extract 形式校验清单重取")
        else:
            report("WARN", key, "invoiceNumber 为空：按兜底键入账的票要标待复核")
    if not r.get("sellerName") and not railway:
        report("ERROR", key, "sellerName 为空（只有铁路电子客票可以不载销售方）")
    for tax_field in ("sellerTaxId", "buyerTaxId"):
        tid = r.get(tax_field)
        if tid and not re.fullmatch(r"[0-9A-Za-z]{15}|[0-9A-Za-z]{18}|[0-9A-Za-z]{20}", str(tid).strip()):
            report("WARN", key, f"{tax_field} 不是 15 / 18 / 20 位：{str(tid)[:40]}")
    if r.get("currency") not in (None, "", "CNY"):
        report("WARN", key, f"currency 是 {r.get('currency')!r}：票面都以人民币计价，外币只照抄进备注")

    # 金额类型
    values = {}
    for name in ("amountExcludingTax", "taxAmount", "otherCharges", "totalAmount"):
        raw = r.get(name)
        if isinstance(raw, (dict, list)) and not raw:
            values[name] = None
            empty_values.setdefault(name, []).append(key)
            continue
        try:
            values[name] = dec(raw)
        except TypeError:
            values[name] = None
            report("ERROR", key, f"{name} 必须是数字或 null，现在是 {type(raw).__name__}：{json.dumps(raw, ensure_ascii=False)[:120]}")
            continue
        if isinstance(raw, str) and raw.strip():
            string_values.setdefault(name, []).append(key)
    total = values["totalAmount"]
    if total is None:
        report("ERROR", key, "totalAmount 为空")

    # 勾稽
    a, t, o = values["amountExcludingTax"], values["taxAmount"], values["otherCharges"]
    if must_print and "铁路" not in kind and (a is None or t is None):
        report("ERROR", key, "票面必印的金额或税额为空：抽取失败，补抽；补不上标待复核（免税票税额记 0）")
    if a is not None and t is not None and total is not None:
        if abs(a + t + (o or 0) - total) > TOLERANCE:
            report("ERROR", key, f"勾稽不平：{a} + {t} + {o or 0} ≠ {total}")

    # 号码格式
    if isinstance(number, str) and number and not is_legacy(r):
        if re.fullmatch(r"\d{20}", number):
            if date and number[:2] != date[2:4]:
                report("ERROR", key, f"数电票号码前两位 {number[:2]} 与开票年份 {date[:4]} 对不上，多半是读错位了（相邻数字被拼进号码）")
        elif re.fullmatch(r"\d+", number) and len(number) not in (8, 20, 21):
            report("WARN", key, f"发票号码 {len(number)} 位，既不是数电票的 20 位也不是旧版的 8 位")

    # 红字
    red = r.get("isRedLetter")
    if total is not None and total < 0 and red is not True:
        report("ERROR", key, "价税合计为负数，isRedLetter 却不是 true")
    if red is True and total is not None and total > 0:
        report("ERROR", key, "isRedLetter 为 true，价税合计却是正数——照票面记负数，不要取绝对值")
    if red is True:
        of = r.get("redLetterOf")
        if not of:
            report("WARN", key, "isRedLetter 为 true，redLetterOf 为空：回源（备注、XML）再找一次被冲销的蓝字号码，确实没有就标待复核")
        elif of not in all_numbers:
            report("WARN", key, f"被冲销的蓝字票 {of} 不在本台账里，报告里要提醒核对以前是否报销过")

    # 航空行程单：基金不进不含税金额
    if "航空" in kind and "行程单" in kind and not is_legacy(r) and r.get("otherCharges") in (None, "", {}, []):
        report("WARN", key, "航空电子行程单没有 otherCharges：民航发展基金与其他税费应单独记进 otherCharges，不能算进不含税金额")

    # 归档与 XML 复核
    archived = r.get("archivedPath")
    if not archived:
        report("ERROR", key, "archivedPath 为空")
        return 0
    path = Path(archived)
    if not path.is_absolute():
        path = base / archived
    archive_root = (base / "归档").resolve()
    try:
        path.resolve().relative_to(archive_root)
    except ValueError:
        # 工作目录搬过时，旧的绝对路径还指着原来的位置；本目录里有同一相对路径的文件就接着查
        tail = str(archived).replace("\\", "/").rsplit("/归档/", 1)
        moved = archive_root / tail[1] if len(tail) == 2 and ".." not in tail[1].split("/") else None
        if moved is not None and inside(moved, archive_root) and moved.is_file():
            moved_paths.append(key)
            path = moved
        else:
            report("ERROR", key, "archivedPath 不在本目录的 归档/ 下")
            return 0
    if not path.exists():
        report("ERROR", key, f"归档文件不存在：{path.name}")
    xml_path = path.with_suffix(".xml")
    if not xml_path.exists():
        return 0
    if not inside(xml_path, archive_root):
        report("WARN", key, f"{xml_path.name} 解析后不在本目录 归档/ 下（符号链接？），未读取")
        return 0
    fields, problem = parse_einvoice_xml(xml_path)
    if problem == "EMPTY":
        report("ERROR", key, f"{xml_path.name} 是空文件：空 XML 不归档，从归档目录删掉它并在 files.json 里记「空文件，丢弃」")
        return 0
    if problem:
        report("WARN", key, f"{xml_path.name}：{problem}")
        return 0
    if fields is None:
        return 0
    for name in ("invoiceNumber", "invoiceDate", "sellerTaxId", "buyerTaxId", "redLetterOf"):
        want, got = fields.get(name), r.get(name)
        if isinstance(got, int) and not isinstance(got, bool):
            got = str(got)  # 号码写成数字的问题上面已经报过，这里按字符串比，免得重复报
        if name.endswith("TaxId") and want and isinstance(got, str) and want.strip().upper() == got.strip().upper():
            continue
        if want and want != got:
            report("ERROR", key, f"{name} 与 XML 不一致：台账 {got!r}，XML {want!r}")
    for name in ("amountExcludingTax", "taxAmount", "totalAmount"):
        try:
            want = dec(fields.get(name))
        except TypeError:
            continue
        if want is not None and values.get(name) is not None and abs(want - values[name]) > TOLERANCE:
            report("ERROR", key, f"{name} 与 XML 不一致：台账 {values[name]}，XML {want}")
    if fields["isRedLetter"] and red is not True:
        report("ERROR", key, "XML 标明这是红字发票（是否蓝字发票标志为 N / 带被冲销的蓝字号码），台账里 isRedLetter 不是 true")
    return 1


def check_claims(ledger, all_numbers, triples):
    """待领取按整本台账派生：号码已经入账就算领到了，这里只统计还没领到的，并查重复条目。"""
    seen = set()
    pending = 0
    for item in ledger.get("quarantined") or []:
        if not isinstance(item, dict) or item.get("kind") != "claim":
            continue
        claim = item.get("claim") if isinstance(item.get("claim"), dict) else {}
        number = claim.get("invoiceNumber")
        key = number or item.get("id") or "claim"
        if number and number in seen:
            report("WARN", key, "同一个号码有两条待领取，只留一条")
            continue
        if number:
            seen.add(number)
        if item.get("dismissed") is True or (number and str(number) in all_numbers):
            continue
        try:
            triple = (claim.get("sellerName"), dec(claim.get("totalAmount")), claim.get("invoiceDate"))
        except TypeError:
            triple = None
        if triple is not None and triple[1] is not None and triple in triples:
            if number:
                report("WARN", key, "领票页上的号码与入账的票不一致（销售方、价税合计、开票日期都相同，按已领取算）")
            continue
        pending += 1
        if not number:
            report("WARN", key, "待领取条目没有抄下发票号码，只能按销售方、金额、开票日期去认领到的票")
    return pending


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    if len(sys.argv) != 2:
        print("用法：python3 check-ledger.py <发票目录>", file=sys.stderr)
        return 2
    base = Path(sys.argv[1]).expanduser()
    ledger_path = base / ".index" / "ledger.json"
    if not ledger_path.is_file():
        print(f"找不到 {ledger_path}", file=sys.stderr)
        return 2
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(f"ledger.json 读不了或不是合法 JSON：{exc}", file=sys.stderr)
        return 2
    if not isinstance(ledger, dict) or not (isinstance(ledger.get("invoices"), dict) or isinstance(ledger.get("records"), list)):
        print("ledger.json 顶层既不是 invoices 字典也不是旧形态 records 数组", file=sys.stderr)
        return 2
    records = load_records(ledger)
    all_numbers, triples = set(), set()
    for k, r in records:
        if not isinstance(r, dict):
            continue
        all_numbers.add(str(k))
        if r.get("invoiceNumber"):
            all_numbers.add(str(r["invoiceNumber"]))
            if r.get("invoiceCode"):
                all_numbers.add(f"{r['invoiceCode']}-{r['invoiceNumber']}")
        try:
            triples.add((r.get("sellerName"), dec(r.get("totalAmount")), r.get("invoiceDate")))
        except TypeError:
            pass
    xml_checked = 0
    for key, r in records:
        if not isinstance(r, dict):
            report("ERROR", str(key), "记录不是对象")
            continue
        if r.get("isVoid") is True:
            continue
        xml_checked += check_record(str(key), r, base, all_numbers)
    pending_claims = check_claims(ledger, all_numbers, triples)
    if moved_paths:
        sample = "、".join(moved_paths[:3]) + ("等" if len(moved_paths) > 3 else "")
        report("WARN", f"{len(moved_paths)} 条", f"archivedPath 指向别的目录（工作目录搬过？如 {sample}），本目录 归档/ 里有同名文件，已按它检查；方便时把这些路径改成本目录下的")
    for name, keys in string_values.items():
        sample = "、".join(keys[:3]) + ("等" if len(keys) > 3 else "")
        report("WARN", f"{len(keys)} 条", f"{name} 写成了字符串（如 {sample}），应写成数字")
    for name, keys in empty_values.items():
        sample = "、".join(keys[:3]) + ("等" if len(keys) > 3 else "")
        report("WARN", f"{len(keys)} 条", f"{name} 写成了空对象或空数组（如 {sample}），没有就写 null")
    print(f"SUMMARY\trecords={len(records)}\terrors={errors}\twarnings={warnings}\txml_checked={xml_checked}\tpending_claims={pending_claims}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Labeling guidelines for QC: written in the UI or imported from a file, then applied to the QC of a job.

A guideline is plain JSON:
    {
      "format": 1,
      "classes": [{"name": "Car", "name_vi": "Ô tô con", "description": "...",
                   "size_min": [l, w, h], "size_max": [l, w, h],
                   "size_mean": [...] (optional), "size_std": [...] (optional),
                   "points_at_10m": 300 (optional), "max_speed": 40 (optional)}],
      "thresholds": {"overlap_iou": 0.3, ...},   # overrides of qc.rules.THRESHOLDS
      "rules": {"R08": false},                    # rules switched off for this guideline
      "autofix": {"exclude_classes": ["Pedestrian"],   # classes auto-fix never changes
                  "fix": {"ground": true, "fit": true, "class": true, "orientation": true}},
      "notes": "free text of the guide shown to reviewers"
    }

Imports: .json, .yaml/.yml, .csv, .md/.txt (tables, "key: value" lines and plain sentences, see
guideline_text.py), .docx (tables + text).
Pure python (no Django).
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
import zipfile

from .rules import RULES, THRESHOLDS, build_config
from .taxonomy import CLASSES, ClassSpec, as_dict

GUIDELINE_FORMAT = 1
MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_NOTES_CHARS = 50_000

RULE_TITLES = {
    "R01": "Nhãn không hợp lệ",
    "R02": "Trùng lặp / chồng lấn",
    "R03": "Class ngoài schema",
    "R04": "Góc quay bất thường",
    "R05": "Ít điểm LiDAR / box lệch",
    "R06": "Kích thước sai guideline",
    "R07": "Track nhảy vị trí",
    "R08": "Vận tốc vượt ngưỡng",
    "R09": "Lơ lửng / chìm",
    "R10": "Box không ôm sát vật",
    "R11": "Nghi sai class",
    "R12": "Khác với model (thiếu nhãn / sai class)",
    "R13": "Box ngắn hơn vật thật (theo các frame của cùng vật)",
    "R14": "Người duyệt đánh dấu lỗi",
}

# thresholds a guideline may change: key -> (Vietnamese label, min, max)
THRESHOLD_LIMITS = {
    "overlap_iou": ("R02: IoU 3D tối đa giữa 2 box", 0.01, 1.0),
    "duplicate_center_eps": ("R02: khoảng cách tâm coi là trùng (m)", 0.0, 1.0),
    "max_roll_pitch_deg": ("R04: roll/pitch tối đa (°)", 0.0, 90.0),
    "min_points": ("R05: số điểm tối thiểu", 0, 10_000),
    "far_distance": ("R05: xa hơn thì chỉ cảnh báo nhẹ (m)", 1.0, 500.0),
    "track_size_tolerance": ("R06: lệch kích thước so với trung vị track", 0.01, 1.0),
    "jump_min": ("R07: track nhảy tối thiểu (m)", 0.05, 20.0),
    "ground_tolerance": ("R09: cho phép lệch mặt đường (m)", 0.01, 3.0),
    "ground_major": ("R09: lệch mặt đường mức major (m)", 0.01, 5.0),
    "fit_max_distance": ("R10/R11: chỉ xét box trong bán kính (m)", 1.0, 200.0),
    "face_gap": ("R10: khe hở mặt nhìn thấy tối đa (m)", 0.01, 3.0),
    "height_gap": ("R10: đỉnh box cao hơn vật tối đa (m)", 0.01, 3.0),
    "class_margin": ("R11: độ chênh log-likelihood để nghi sai class", 0.5, 50.0),
    "model_max_distance": ("R12: chỉ so với model trong bán kính (m)", 5.0, 60.0),
    "model_missing_score": ("R12: độ tin cậy model để báo thiếu nhãn", 0.3, 0.99),
    "model_class_score": ("R12: độ tin cậy model để báo sai class", 0.3, 0.99),
    "model_missing_per_frame": ("R12: quá số vật thiếu nhãn này thì gộp thành 1 cảnh báo cho frame", 1, 500),
    "autofix_max_shift": ("Autofix: dời tâm box tối đa (m)", 0.05, 10.0),
    "autofix_max_scale_change": ("Autofix: đổi mỗi chiều kích thước tối đa (tỉ lệ)", 0.01, 3.0),
    "autofix_min_coverage": ("Autofix: tỉ lệ điểm của vật phải nằm trong box", 0.5, 1.0),
    "autofix_class_margin": ("Autofix: độ chắc chắn tối thiểu khi đổi class (log-likelihood)", 0.0, 50.0),
}
assert set(THRESHOLD_LIMITS) <= set(THRESHOLDS)

# kinds of change auto-fix may write; a guideline can switch each off (the proposal then goes to review)
AUTOFIX_KINDS = {
    "ground": "Chiều đứng: đáy chạm mặt đường, đỉnh sát vật",
    "fit": "Chiều ngang: dời, co giãn box ôm sát vật",
    "class": "Sửa class",
    "orientation": "Sửa góc quay",
    "create": "Thêm box cho vật model thấy mà chưa có nhãn",
}

DEFAULT_POINTS_AT_10M = 100
DEFAULT_MAX_SPEED = 40.0


class GuidelineError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def builtin_content() -> dict:
    """The VN taxonomy (qc/taxonomy.py) as a guideline: the starting point for a new guideline."""
    taxonomy = as_dict()
    return {
        "format": GUIDELINE_FORMAT,
        "classes": [{k: list(v) if isinstance(v, tuple) else v for k, v in c.items()} for c in taxonomy["classes"]],
        "thresholds": {},
        "rules": {},
        "autofix": default_autofix(),
        "notes": "",
    }


def default_autofix() -> dict:
    return {"exclude_classes": [], "fix": {kind: True for kind in AUTOFIX_KINDS}}


def _normalize_autofix(raw, class_names: set[str], errors: list[str], warnings: list[str]) -> dict:
    autofix = default_autofix()
    if raw is None:
        return autofix
    if not isinstance(raw, dict):
        errors.append("autofix phải là object JSON")
        return autofix
    excluded = raw.get("exclude_classes") or []
    if not isinstance(excluded, list) or not all(isinstance(x, str) for x in excluded):
        errors.append("autofix.exclude_classes cần danh sách tên class")
        excluded = []
    for name in excluded:
        if name not in class_names:
            warnings.append(f"autofix: class '{name}' không có trong guideline")
    autofix["exclude_classes"] = sorted(set(excluded))
    fix = raw.get("fix") or {}
    if not isinstance(fix, dict):
        errors.append("autofix.fix phải là object JSON")
        fix = {}
    for kind, enabled in fix.items():
        if kind not in AUTOFIX_KINDS:
            warnings.append(f"autofix: bỏ qua loại sửa không hỗ trợ '{kind}'")
        elif not isinstance(enabled, bool):
            errors.append(f"autofix.fix.{kind}: cần true hoặc false")
        else:
            autofix["fix"][kind] = enabled
    return autofix


def _number(value, where: str, errors: list[str]) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        errors.append(f"{where}: '{value}' không phải số")
        return None
    if number != number or number in (float("inf"), float("-inf")):
        errors.append(f"{where}: phải là số hữu hạn")
        return None
    return number


def _triple(value, where: str, errors: list[str]) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        errors.append(f"{where}: cần 3 giá trị [dài, rộng, cao]")
        return None
    numbers = [_number(v, f"{where}[{i}]", errors) for i, v in enumerate(value)]
    return None if None in numbers else numbers


def normalize(content: dict) -> tuple[dict, list[str]]:
    """Validate a guideline and fill optional values. Raises GuidelineError; returns (content, warnings)."""
    if not isinstance(content, dict):
        raise GuidelineError(["Guideline phải là một object JSON"])
    errors: list[str] = []
    warnings: list[str] = []

    classes = []
    names = set()
    raw_classes = content.get("classes")
    if not isinstance(raw_classes, list) or not raw_classes:
        errors.append("Guideline cần ít nhất 1 class")
        raw_classes = []
    for index, raw in enumerate(raw_classes):
        if not isinstance(raw, dict):
            errors.append(f"Class #{index + 1}: phải là object")
            continue
        name = str(raw.get("name") or "").strip()
        where = f"Class '{name or index + 1}'"
        if not name:
            errors.append(f"Class #{index + 1}: thiếu tên class (name)")
            continue
        if name in names:
            errors.append(f"{where}: trùng tên")
            continue
        names.add(name)
        size_min = _triple(raw.get("size_min"), f"{where} size_min", errors)
        size_max = _triple(raw.get("size_max"), f"{where} size_max", errors)
        if size_min is None or size_max is None:
            continue
        if any(lo <= 0 or lo > hi for lo, hi in zip(size_min, size_max)):
            errors.append(f"{where}: cần 0 < min ≤ max cho dài, rộng, cao")
            continue

        builtin = CLASSES.get(name)
        mean = raw.get("size_mean")
        mean = _triple(mean, f"{where} size_mean", errors) if mean is not None else None
        if mean is None:
            mean = list(builtin.size_mean) if builtin else [(lo + hi) / 2 for lo, hi in zip(size_min, size_max)]
        mean = [min(max(m, lo), hi) for m, lo, hi in zip(mean, size_min, size_max)]
        std = raw.get("size_std")
        std = _triple(std, f"{where} size_std", errors) if std is not None else None
        if std is None:
            # a guideline range is about +-2 sigma of the class sizes
            std = [max((hi - lo) / 4, 0.05) for lo, hi in zip(size_min, size_max)]
        elif min(std) <= 0:
            errors.append(f"{where}: size_std phải > 0")
            continue

        points = raw.get("points_at_10m")
        points = _number(points, f"{where} points_at_10m", errors) if points not in (None, "") else None
        if points is None:
            points = builtin.points_at_10m if builtin else DEFAULT_POINTS_AT_10M
            if not builtin:
                warnings.append(f"{where}: chưa có số điểm ở 10 m, dùng {DEFAULT_POINTS_AT_10M}")
        elif points < 0 or not points.is_integer():
            errors.append(f"{where}: points_at_10m phải là số nguyên không âm")
        speed = raw.get("max_speed")
        speed = _number(speed, f"{where} max_speed", errors) if speed not in (None, "") else None
        if speed is None:
            speed = builtin.max_speed if builtin else DEFAULT_MAX_SPEED
        elif speed < 0:
            errors.append(f"{where}: max_speed phải không âm")

        classes.append({
            "name": name,
            "name_vi": str(raw.get("name_vi") or (builtin.name_vi if builtin else "")).strip(),
            "description": str(raw.get("description") or (builtin.description if builtin else "")).strip(),
            "size_min": size_min,
            "size_max": size_max,
            "size_mean": [round(v, 3) for v in mean],
            "size_std": std,
            "points_at_10m": int(points),
            "max_speed": float(speed),
        })

    thresholds = {}
    raw_thresholds = content.get("thresholds")
    if raw_thresholds is None:
        raw_thresholds = {}
    elif not isinstance(raw_thresholds, dict):
        errors.append("thresholds phải là object JSON")
        raw_thresholds = {}
    for key, value in raw_thresholds.items():
        if key not in THRESHOLD_LIMITS:
            warnings.append(f"Bỏ qua ngưỡng không hỗ trợ: {key}")
            continue
        label, lo, hi = THRESHOLD_LIMITS[key]
        number = _number(value, f"Ngưỡng {key}", errors)
        if number is None:
            continue
        if not lo <= number <= hi:
            errors.append(f"Ngưỡng {key} ({label}) phải trong [{lo}, {hi}]")
            continue
        thresholds[key] = int(number) if isinstance(THRESHOLDS[key], int) else number

    rules = {}
    raw_rules = content.get("rules")
    if raw_rules is None:
        raw_rules = {}
    elif not isinstance(raw_rules, dict):
        errors.append("rules phải là object JSON")
        raw_rules = {}
    for code, enabled in raw_rules.items():
        if code not in RULES:
            warnings.append(f"Bỏ qua rule không tồn tại: {code}")
            continue
        if not isinstance(enabled, bool):
            errors.append(f"Rule {code}: cần true hoặc false")
            continue
        rules[code] = enabled
    if rules.get("R01") is False:
        errors.append("Không được tắt R01 (nhãn không hợp lệ)")
    autofix = _normalize_autofix(content.get("autofix"), names, errors, warnings)

    if errors:
        raise GuidelineError(errors)
    notes = str(content.get("notes") or "")
    if len(notes) > MAX_NOTES_CHARS:
        warnings.append(f"Ghi chú dài hơn {MAX_NOTES_CHARS} ký tự, đã cắt bớt")
        notes = notes[:MAX_NOTES_CHARS]
    return {
        "format": GUIDELINE_FORMAT, "classes": classes, "thresholds": thresholds, "rules": rules,
        "autofix": autofix, "notes": notes,
    }, warnings


def class_specs(content: dict) -> dict[str, ClassSpec]:
    """Class specs of a normalized guideline."""
    return {
        c["name"]: ClassSpec(
            c["name"], c["name_vi"], c["description"],
            tuple(c["size_min"]), tuple(c["size_max"]), tuple(c["size_mean"]), tuple(c["size_std"]),
            c["points_at_10m"], c["max_speed"],
        )
        for c in content["classes"]
    }


def qc_config(content: dict | None) -> dict:
    """QC configuration of a normalized guideline (None: the built-in VN taxonomy)."""
    if content is None:
        return build_config()
    return build_config(
        class_specs(content),
        content.get("thresholds"),
        [code for code, enabled in content.get("rules", {}).items() if not enabled],
    )


# ---------------------------------------------------------------------------------------------
# import


def _plain(text: str) -> str:
    """Lower case, no Vietnamese diacritics, no markdown emphasis: for matching headers and keys."""
    text = unicodedata.normalize("NFKD", str(text).replace("đ", "d").replace("Đ", "D"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[*_`]", "", text)).strip().lower()


def _clean_cell(text: str) -> str:
    return re.sub(r"[*`]", "", str(text)).strip()


_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(text: str) -> list[float]:
    return [float(n.replace(",", ".")) for n in _NUMBER.findall(str(text))]


def _column(header: str) -> str | None:
    h = _plain(header)
    if "toc do" in h or "speed" in h:
        return "max_speed"
    if "diem" in h or "points" in h:
        return "points_at_10m"
    axis = None
    if re.search(r"\b(dai|length)\b", h):
        axis = "length"
    elif re.search(r"\b(rong|width)\b", h):
        axis = "width"
    elif re.search(r"\b(cao|height)\b", h):
        axis = "height"
    if axis:
        if re.search(r"\b(min|toi thieu|tu)\b", h):
            return f"{axis}_min"
        if re.search(r"\b(max|toi da|den)\b", h):
            return f"{axis}_max"
        return axis
    if h in {"class", "label", "nhan", "lop", "ma class", "ma", "name", "ten class", "class name"}:
        return "name"
    if h.startswith("ten") or h == "name_vi":
        return "name_vi"
    if "bao gom" in h or "mo ta" in h or "description" in h or "dinh nghia" in h:
        return "description"
    return None


def _classes_from_table(rows: list[list[str]], warnings: list[str]) -> list[dict]:
    """A table whose header has a class column and length/width/height columns -> class dicts."""
    if len(rows) < 2:
        return []
    columns = [_column(h) for h in rows[0]]
    if "name" not in columns and "name_vi" in columns:
        columns[columns.index("name_vi")] = "name"  # only a "Tên" column: it is the class name
    has_size = all(
        axis in columns or {f"{axis}_min", f"{axis}_max"} <= set(columns) for axis in ("length", "width", "height")
    )
    if "name" not in columns or not has_size:
        return []

    classes = []
    for row in rows[1:]:
        cells = dict(zip(columns, row))
        name = _clean_cell(cells.get("name", ""))
        if not name:
            continue
        item = {"name": name}
        for key in ("name_vi", "description"):
            if cells.get(key):
                item[key] = _clean_cell(cells[key])
        size_min, size_max = [], []
        for axis in ("length", "width", "height"):
            if axis in cells:
                values = _numbers(cells[axis])
            else:
                values = _numbers(cells.get(f"{axis}_min", "")) + _numbers(cells.get(f"{axis}_max", ""))
            if len(values) < 2:
                warnings.append(f"Class '{name}': không đọc được khoảng '{axis}' (cần dạng 2,5–6,2)")
                break
            size_min.append(min(values[:2]))
            size_max.append(max(values[:2]))
        else:
            item["size_min"], item["size_max"] = size_min, size_max
        for key in ("points_at_10m", "max_speed"):
            values = _numbers(cells.get(key, ""))
            if values:
                item[key] = values[0]
        classes.append(item)
    return classes


_KEY_VALUE = re.compile(r"^\s*[-*]?\s*`?([A-Za-z0-9_]+)`?\s*[:=]\s*(.+?)\s*$")
_OFF = {"off", "false", "tat", "khong", "no", "0", "disabled"}
_ON = {"on", "true", "bat", "co", "yes", "1", "enabled"}


def _settings_from_lines(lines, content: dict) -> None:
    """'overlap_iou: 0.25' and 'R08: tắt' lines of a text guide."""
    for line in lines:
        match = _KEY_VALUE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        if key in THRESHOLD_LIMITS:
            numbers = _numbers(value)
            if numbers:
                content["thresholds"][key] = numbers[0]
        elif key.upper() in RULES:
            word = _plain(value).split(" ")[0]
            if word in _OFF:
                content["rules"][key.upper()] = False
            elif word in _ON:
                content["rules"][key.upper()] = True


def _markdown_tables(text: str) -> list[list[list[str]]]:
    tables, current = [], []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue  # header separator
            current.append(cells)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _docx_tables_and_text(data: bytes) -> tuple[list[list[list[str]]], list[str]]:
    from xml.etree.ElementTree import ParseError

    try:
        from defusedxml import ElementTree
        from defusedxml.common import DefusedXmlException
    except ImportError:  # pragma: no cover - CVAT ships defusedxml
        from xml.etree import ElementTree

        DefusedXmlException = ValueError

    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 50 * 1024 * 1024:
                raise GuidelineError(["File Word quá lớn"])
            root = ElementTree.fromstring(archive.read(info))
    except GuidelineError:
        raise
    except (zipfile.BadZipFile, KeyError, ParseError, DefusedXmlException) as e:
        raise GuidelineError(["Không đọc được file .docx"]) from e

    def text_of(element) -> str:
        return "".join(t.text or "" for t in element.iter(f"{w}t"))

    tables, paragraphs = [], []
    body = root.find(f"{w}body")
    for child in (body if body is not None else []):
        if child.tag == f"{w}tbl":
            tables.append([[text_of(cell) for cell in row.findall(f"{w}tc")] for row in child.findall(f"{w}tr")])
        elif child.tag == f"{w}p":
            text = text_of(child).strip()
            if text:
                paragraphs.append(text)
    return tables, paragraphs


def parse_file(filename: str, data: bytes, compiled: list[str] | None = None) -> tuple[dict, list[str]]:
    """
    Imported file -> normalized guideline + warnings. Raises GuidelineError.
    `compiled` (optional) receives what was understood from plain sentences.
    """
    if len(data) > MAX_IMPORT_BYTES:
        raise GuidelineError([f"File lớn hơn {MAX_IMPORT_BYTES // 1024 // 1024} MB"])
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    warnings: list[str] = []

    if extension in {"json", "yaml", "yml"}:
        try:
            if extension == "json":
                raw = json.loads(data.decode("utf-8-sig"))
            else:
                import yaml

                raw = yaml.safe_load(data.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as e:
            raise GuidelineError([f"File {extension.upper()} lỗi cú pháp: {e}"]) from e
        except Exception as e:  # yaml.YAMLError
            raise GuidelineError([f"File YAML lỗi cú pháp: {e}"]) from e
        if isinstance(raw, list):
            raw = {"classes": raw}
        content, more = normalize(raw)
        return content, warnings + more

    content = {"classes": [], "thresholds": {}, "rules": {}, "notes": ""}
    if extension == "docx":
        tables, paragraphs = _docx_tables_and_text(data)
        lines = paragraphs
        content["notes"] = "\n".join(paragraphs)
    elif extension in {"csv", "md", "markdown", "txt"}:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise GuidelineError(["File văn bản cần mã hoá UTF-8"]) from e
        lines = text.splitlines()
        if extension == "csv":
            try:
                dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t") if text.strip() else csv.excel
                tables = [[row for row in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in row)]]
            except csv.Error as e:
                raise GuidelineError([f"Không đọc được bảng CSV: {e}"]) from e
            lines = []
        else:
            tables = _markdown_tables(text)
            content["notes"] = text
    else:
        raise GuidelineError(["Định dạng chưa hỗ trợ; dùng .json, .yaml, .csv, .md, .txt hoặc .docx"])

    for table in tables:
        content["classes"].extend(_classes_from_table(table, warnings))
    _settings_from_lines(lines, content)
    has_table = bool(content["classes"])
    if not has_table:
        # sentences only: they refine the built-in VN classes
        content["classes"] = builtin_content()["classes"]
    understood = compile_into(content, "\n".join(lines), warnings, compiled)
    if not has_table and not understood and not content["thresholds"] and not content["rules"]:
        raise GuidelineError([
            "Không tìm thấy bảng class hay câu quy định nào. Cần bảng có cột Class và Dài / Rộng / Cao "
            "(ví dụ 2,5–6,2) hoặc câu như “Ô tô con dài từ 3 đến 5,5 m”"
        ])
    if not has_table:
        warnings.append("Không có bảng class: dùng bộ class giao thông VN mặc định, chỉnh theo các câu quy định")
    normalized, more = normalize(content)
    return normalized, warnings + more


def compile_into(content: dict, text: str, warnings: list[str], compiled: list[str] | None = None) -> int:
    """Apply plain sentences of `text` to `content`; returns how many settings they changed."""
    from .guideline_text import compile_text

    content["classes"] = [c for c in content["classes"] if isinstance(c, dict)]
    understood, unknown = compile_text(text, content)
    if compiled is not None:
        compiled.extend(understood)
    warnings.extend(f"Chưa hiểu câu: “{s[:160]}”" for s in unknown[:20])
    return len(understood)

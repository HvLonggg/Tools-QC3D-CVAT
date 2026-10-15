# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Natural-language guideline sentences (Vietnamese or English) -> guideline settings.

Deterministic and auditable: every sentence that changes a setting is reported back with what it set,
and sentences with numbers that were not understood are reported as warnings, so QA can check the
compiled guideline before saving it. Examples of understood sentences:

    Ô tô con dài từ 3 đến 5,5 m, rộng 1,5-2,1 m.
    Xe tải cao tối đa 4 m.
    Đáy box lệch mặt đường không quá 10 cm.
    Khe hở giữa box và vật không quá 20 cm.
    Đỉnh box cao hơn vật không quá 30 cm.
    Hai box chồng lấn tối đa 20%.
    Chỉ kiểm tra trong bán kính 50 m.
    Không kiểm tra vận tốc.
    Không tự sửa người đi bộ.            -> autofix never touches Pedestrian
    Autofix không đổi class.             -> autofix keeps classes (class proposals go to review)
"""

from __future__ import annotations

import re

from .guideline import AUTOFIX_KINDS, RULE_TITLES, THRESHOLD_LIMITS, _numbers, _plain

# aliases (no diacritics, lower case) of the built-in classes; a guideline's own class names and
# Vietnamese names are added at compile time
SYNONYMS = {
    "Car": ["o to con", "xe con", "xe hoi", "o to", "oto", "sedan", "suv", "car"],
    "Truck": ["xe tai", "xe ben", "dau keo", "xe container", "truck"],
    "Bus": ["xe buyt", "xe bus", "xe khach", "bus"],
    "Rider": ["nguoi di xe may", "nguoi di xe dap", "nguoi lai xe may", "nguoi lai xe", "xe may co nguoi",
              "xe dap co nguoi", "rider"],
    "Motorbike": ["xe may do", "xe dap do", "xe hai banh do", "xe 2 banh do", "xe may", "xe dap", "motorbike"],
    "Pedestrian": ["nguoi di bo", "nguoi dung", "pedestrian"],
}

_NUM = r"(\d+(?:[.,]\d+)?)"
_UNIT = r"\s*(cm|mm|m\b|met|%|do\b|°)?"
_AXES = {"length": r"(?:chieu )?dai|length", "width": r"(?:chieu )?rong|width", "height": r"(?:chieu )?cao|height"}
_RANGE = re.compile(rf"(?:tu|khoang|trong khoang|from)?\s*{_NUM}{_UNIT}\s*(?:den|toi|-|–|~|to)\s*{_NUM}{_UNIT}")
_MAX_WORDS = r"(?:toi da|khong qua|khong vuot qua|nho hon|duoi|<=|<|max|at most|up to)"
_MIN_WORDS = r"(?:toi thieu|it nhat|khong duoi|lon hon|tren|>=|>|min|at least)"
_NEGATION = re.compile(r"\b(khong|cam|bo qua|loai tru|ngoai tru|tat(?! ca)|never|do not|don't|no)\b")
_AUTOFIX = re.compile(r"\b(tu sua|tu dong sua|tu fix|auto ?fix|tu dong fix|sua tu dong)\b")

# rule switches: words of a sentence that names the check
RULE_WORDS = {
    "R02": ["chong lan", "trung lap"],
    "R03": ["ngoai schema"],
    "R04": ["goc quay", "nghieng"],
    "R05": ["it diem", "so diem lidar"],
    "R06": ["kich thuoc"],
    "R07": ["track nhay", "nhay vi tri"],
    "R08": ["van toc", "toc do"],
    "R09": ["lo lung", "mat duong", "cham dat"],
    "R10": ["om sat"],
    "R11": ["sai class", "sai nhan"],
}
AUTOFIX_WORDS = {
    "class": ["class", "nhan", "loai"],
    "ground": ["mat duong", "cham dat", "lo lung", "chim", "do cao"],
    "fit": ["om sat", "kich thuoc", "vi tri", "doi box", "co gian", "can chinh"],
    "orientation": ["goc", "huong"],
}


def _has(plain: str, words: list[str]) -> bool:
    return any(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", plain) for w in words)


def _sentences(text: str) -> list[str]:
    parts = []
    for line in str(text).splitlines():
        line = line.strip().lstrip("-*#>• ").strip()
        if not line or line.startswith("|"):
            continue  # tables are read by the table parser
        parts.extend(s.strip() for s in re.split(r"(?<!\d)[.;!?](?!\d)|\.\s", line) if s.strip())
    return parts


def _metres(value: str, unit: str | None) -> float:
    number = float(value.replace(",", "."))
    if unit == "cm":
        return number / 100
    if unit == "mm":
        return number / 1000
    return number


def _class_aliases(content: dict) -> list[tuple[str, str]]:
    names = {c["name"] for c in content.get("classes", [])}
    aliases = []
    for cls in content.get("classes", []):
        for alias in [cls["name"], cls.get("name_vi", "")] + SYNONYMS.get(cls["name"], []):
            alias = _plain(alias)
            if alias:
                aliases.append((alias, cls["name"]))
    # longest first: "nguoi di xe may" is Rider, not Motorbike
    return sorted({a for a in aliases if a[1] in names}, key=lambda a: -len(a[0]))


def _find_classes(plain: str, aliases: list[tuple[str, str]]) -> list[str]:
    found, taken = [], []
    for alias, name in aliases:
        for match in re.finditer(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", plain):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in taken):
                continue
            taken.append(span)
            if name not in found:
                found.append(name)
    return found


def _limit(plain: str, keywords: str) -> tuple[float, str | None] | None:
    """'<keywords> ... toi da 10 cm' -> (10, 'cm')."""
    match = re.search(rf"(?:{keywords}).*?{_MAX_WORDS}\s*{_NUM}{_UNIT}", plain) or re.search(
        rf"{_MAX_WORDS}\s*{_NUM}{_UNIT}.*?(?:{keywords})", plain
    )
    return (match.group(1), match.group(2)) if match else None


def _set_threshold(content: dict, key: str, value: float, compiled: list[str], sentence: str) -> None:
    label, lo, hi = THRESHOLD_LIMITS[key]
    value = round(min(max(value, lo), hi), 4)
    content.setdefault("thresholds", {})[key] = value
    compiled.append(f"“{sentence}” → {label} = {value:g}")


def _sizes(plain: str, sentence: str, cls: dict, compiled: list[str]) -> bool:
    if not (isinstance(cls.get("size_min"), list) and isinstance(cls.get("size_max"), list)):
        return False  # the class row itself could not be read: reported by the table parser
    changed = []
    for axis_index, (axis, words) in enumerate(_AXES.items()):
        match = re.search(rf"\b(?:{words})\b\s*(?:la|:)?\s*(.*?)(?=\b(?:{'|'.join(_AXES.values())})\b|$)", plain)
        if not match:
            continue
        part = match.group(1)
        size_min, size_max = list(cls["size_min"]), list(cls["size_max"])
        rng = _RANGE.search(part)
        hi = re.search(rf"{_MAX_WORDS}\s*{_NUM}{_UNIT}", part)
        lo = re.search(rf"{_MIN_WORDS}\s*{_NUM}{_UNIT}", part)
        if rng:
            a = _metres(rng.group(1), rng.group(2) or rng.group(4))
            b = _metres(rng.group(3), rng.group(4))
            size_min[axis_index], size_max[axis_index] = min(a, b), max(a, b)
        elif hi or lo:
            if hi:
                size_max[axis_index] = _metres(hi.group(1), hi.group(2))
            if lo:
                size_min[axis_index] = _metres(lo.group(1), lo.group(2))
        else:
            continue
        cls["size_min"], cls["size_max"] = size_min, size_max
        changed.append(f"{('dài', 'rộng', 'cao')[axis_index]} {size_min[axis_index]:g}–{size_max[axis_index]:g} m")
    if changed:
        # the guideline range is now the authority: size priors are re-derived from it
        cls.pop("size_std", None)
        compiled.append(f"“{sentence}” → {cls['name']}: " + ", ".join(changed))
    return bool(changed)


def compile_text(text: str, content: dict) -> tuple[list[str], list[str]]:
    """
    Apply the sentences of `text` to `content` (a guideline dict, changed in place).
    Returns (what was understood, sentences with numbers that were not understood).
    """
    content.setdefault("thresholds", {})
    content.setdefault("rules", {})
    autofix = content.setdefault("autofix", {})
    autofix.setdefault("exclude_classes", [])
    autofix.setdefault("fix", {})
    aliases = _class_aliases(content)
    by_name = {c["name"]: c for c in content.get("classes", [])}
    compiled: list[str] = []
    unknown: list[str] = []

    for sentence in _sentences(text):
        plain = _plain(sentence)
        if len(sentence) > 300:
            continue
        classes = _find_classes(plain, aliases)
        negated = bool(_NEGATION.search(plain))
        understood = False

        # 1) auto-fix scope: "khong tu sua nguoi di bo", "autofix khong doi class"
        if _AUTOFIX.search(plain) and negated:
            if classes:
                for name in classes:
                    if name not in autofix["exclude_classes"]:
                        autofix["exclude_classes"].append(name)
                compiled.append(f"“{sentence}” → autofix không sửa: {', '.join(classes)}")
            else:
                kinds = [kind for kind, words in AUTOFIX_WORDS.items() if _has(plain, words)]
                for kind in kinds:
                    autofix["fix"][kind] = False
                if kinds:
                    compiled.append(
                        f"“{sentence}” → autofix không: " + ", ".join(AUTOFIX_KINDS[k].lower() for k in kinds)
                    )
            understood = True

        # 2) class sizes: "o to con dai tu 3 den 5,5 m"
        elif classes and any(re.search(rf"\b(?:{w})\b", plain) for w in _AXES.values()) and _numbers(plain):
            understood = _sizes(plain, sentence, by_name[classes[0]], compiled)

        # 3) thresholds
        if not understood:
            checks = [
                ("height_gap", r"dinh.*cao hon|cao hon vat|top gap"),
                ("ground_tolerance", r"mat duong|mat dat|cham dat|lo lung|ground"),
                ("face_gap", r"khe ho|\bho\b|om sat|sat vat|face gap"),
                ("overlap_iou", r"chong lan|de len|\biou\b|overlap"),
                ("max_roll_pitch_deg", r"nghieng|roll|pitch"),
            ]
            for key, words in checks:
                if not re.search(words, plain):
                    continue
                found = _limit(plain, words)
                if found:
                    value, unit = found
                    number = float(value.replace(",", "."))
                    if key == "overlap_iou":
                        number = number / 100 if unit == "%" or number > 1 else number
                    elif key != "max_roll_pitch_deg":
                        number = _metres(value, unit)
                    _set_threshold(content, key, number, compiled, sentence)
                    understood = True
                    break
            if not understood:
                distance = re.search(rf"(?:ban kinh|pham vi|trong vong|trong)\s*{_NUM}\s*(?:m\b|met)", plain)
                if distance and re.search(r"kiem tra|xet|sua|check", plain):
                    _set_threshold(content, "fit_max_distance", float(distance.group(1).replace(",", ".")),
                                   compiled, sentence)
                    understood = True

        # 4) checks switched off: "khong kiem tra van toc"
        if not understood and negated and re.search(r"kiem tra|xet|check|rule|quy tac|bat loi", plain):
            codes = [code for code, words in RULE_WORDS.items() if _has(plain, words)]
            codes += [c.upper() for c in re.findall(r"\b(r\d\d)\b", plain) if c.upper() in RULE_TITLES]
            for code in dict.fromkeys(codes):
                if code == "R01":
                    continue
                content["rules"][code] = False
                compiled.append(f"“{sentence}” → tắt {code} ({RULE_TITLES[code]})")
                understood = True

        if not understood and _numbers(plain) and (classes or re.search(r"\b(m|cm|%)\b", plain)):
            unknown.append(sentence)
    return compiled, unknown

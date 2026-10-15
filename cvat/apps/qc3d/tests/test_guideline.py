# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""Pure unit tests for guidelines (no Django): python -m pytest cvat/apps/qc3d/tests/test_guideline.py"""

import io
import zipfile

import pytest

from cvat.apps.qc3d.qc.geometry import Cuboid
from cvat.apps.qc3d.qc.guideline import GuidelineError, builtin_content, normalize, parse_file, qc_config
from cvat.apps.qc3d.qc.rules import QcContext, run_rules

MARKDOWN_GUIDE = """
# Guideline dự án ABC

Ghi chú cho annotator: box ôm sát vật, đáy chạm mặt đường.

| Class | Tên | Bao gồm | Dài (m) | Rộng (m) | Cao (m) |
| --- | --- | --- | --- | --- | --- |
| **Car** | Ô tô con | Sedan, SUV | 3–5,5 | 1,5–2,1 | 1,3–2 |
| XeBaGac | Xe ba gác | Xe ba bánh chở hàng | 2,2–3,2 | 1–1,6 | 1–2,2 |

Ngưỡng:
- overlap_iou: 0,2
- ground_tolerance = 0.25
- R08: tắt
"""


def box(label="Car", scale=(4.5, 1.9, 1.6), uid="s:1", x=10.0) -> Cuboid:
    return Cuboid(uid=uid, frame=0, label=label, position=(x, 0.0, scale[2] / 2), rotation=(0, 0, 0), scale=scale)


def test_markdown_guide_import():
    content, warnings = parse_file("guide.md", MARKDOWN_GUIDE.encode("utf-8"))
    classes = {c["name"]: c for c in content["classes"]}
    assert set(classes) == {"Car", "XeBaGac"}
    assert classes["Car"]["size_min"] == [3.0, 1.5, 1.3] and classes["Car"]["size_max"] == [5.5, 2.1, 2.0]
    assert classes["XeBaGac"]["name_vi"] == "Xe ba gác"
    assert content["thresholds"] == {"overlap_iou": 0.2, "ground_tolerance": 0.25}
    assert content["rules"] == {"R08": False}
    assert "box ôm sát vật" in content["notes"]
    assert any("XeBaGac" in w for w in warnings)  # no LiDAR point count given for a new class


def test_csv_with_min_max_columns():
    text = "class;length min;length max;width min;width max;height min;height max;points\nVan;4;5.5;1.7;2;1.8;2.4;320\n"
    content, _ = parse_file("classes.csv", text.encode())
    van = content["classes"][0]
    assert van["size_min"] == [4.0, 1.7, 1.8] and van["size_max"] == [5.5, 2.0, 2.4]
    assert van["points_at_10m"] == 320


def test_docx_table_import():
    cell = lambda text: f"<w:tc><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>"  # noqa: E731
    row = lambda *cells: "<w:tr>" + "".join(cell(c) for c in cells) + "</w:tr>"  # noqa: E731
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        "<w:p><w:r><w:t>R10: off</w:t></w:r></w:p>"
        "<w:tbl>" + row("Class", "Dài", "Rộng", "Cao") + row("Truck", "4,5 - 12", "1,8 - 2,6", "2 - 4") + "</w:tbl>"
        "</w:body></w:document>"
    )
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("word/document.xml", xml)
    content, _ = parse_file("guide.docx", data.getvalue())
    assert content["classes"][0]["size_max"] == [12.0, 2.6, 4.0]
    assert content["rules"] == {"R10": False}


def test_invalid_guidelines_are_rejected():
    with pytest.raises(GuidelineError):
        normalize({"classes": [{"name": "Car", "size_min": [5, 1, 1], "size_max": [4, 2, 2]}]})
    with pytest.raises(GuidelineError):
        normalize({**builtin_content(), "rules": {"R01": False}})
    with pytest.raises(GuidelineError):
        normalize({**builtin_content(), "thresholds": {"overlap_iou": 5}})
    with pytest.raises(GuidelineError):
        parse_file("notes.md", "Không có bảng class".encode())


def test_builtin_content_round_trip_keeps_qc_config():
    content, _ = normalize(builtin_content())
    assert qc_config(content)["size_ranges"] == qc_config(None)["size_ranges"]


def test_guideline_changes_qc_result():
    content, _ = parse_file("guide.md", MARKDOWN_GUIDE.encode("utf-8"))
    config = qc_config(content)
    long_car = box(scale=(5.8, 1.9, 1.6))  # fine for the built-in taxonomy (Car up to 6.2 m)

    def r06(cfg):
        return next(r for r in run_rules(QcContext([long_car], config=cfg)) if r.rule == "R06")

    assert not r06(qc_config(None)).issues
    assert [i.uid for i in r06(config).issues] == ["s:1"]  # this guide allows Car up to 5.5 m

    results = {r.rule: r for r in run_rules(QcContext([long_car], config=config))}
    assert results["R08"].status == "not_checked" and results["R08"].reason == "tắt theo guideline"
    assert config["overlap_iou"] == 0.2


def test_guideline_class_is_used_for_class_suggestion():
    content, _ = parse_file("guide.md", MARKDOWN_GUIDE.encode("utf-8"))
    cargo_tricycle_as_car = box(scale=(2.7, 1.3, 1.6))
    r11 = next(r for r in run_rules(QcContext([cargo_tricycle_as_car], config=qc_config(content))) if r.rule == "R11")
    assert r11.issues and r11.issues[0].evidence["suggested"] == "XeBaGac"


@pytest.mark.parametrize("field", ["thresholds", "rules"])
@pytest.mark.parametrize("value", [[], ["R08"], "false", False])
def test_settings_must_be_objects(field, value):
    with pytest.raises(GuidelineError, match=field):
        normalize({**builtin_content(), field: value})


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_rule_switch_must_be_boolean(value):
    with pytest.raises(GuidelineError, match="R08"):
        normalize({**builtin_content(), "rules": {"R08": value}})


@pytest.mark.parametrize("field,value", [("points_at_10m", -1), ("points_at_10m", 1.5), ("max_speed", -1)])
def test_invalid_class_point_counts_and_speeds_are_rejected(field, value):
    content = builtin_content()
    content["classes"][0][field] = value
    with pytest.raises(GuidelineError, match=field):
        normalize(content)


def test_small_positive_standard_deviation_stays_usable():
    content = builtin_content()
    content["classes"][0]["size_std"] = [0.0001, 0.0001, 0.0001]
    normalized, _ = normalize(content)
    results = run_rules(QcContext([box()], config=qc_config(normalized)))
    assert any(result.rule == "R11" for result in results)


def test_malformed_imports_return_guideline_errors():
    with pytest.raises(GuidelineError, match="CSV"):
        parse_file("classes.csv", b"not a class table")

    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("word/document.xml", "<broken>")
    with pytest.raises(GuidelineError, match="docx"):
        parse_file("guide.docx", data.getvalue())

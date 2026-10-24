from __future__ import annotations

from types import SimpleNamespace


from b2c_ingest.docling_pdf import (
    _logical_arabic_lines_for_item,
    _restore_logical_arabic_order,
)


def test_restore_logical_arabic_order_on_rapidocr_visual_order() -> None:
    visual_order = "نيعمثدحعوقوةصرفلسايقموهلامتحالا"
    assert _restore_logical_arabic_order(visual_order) == "الاحتمالهومقياسلفرصةوقوعحدثمعين"


def test_restore_logical_arabic_order_keeps_line_boundaries() -> None:
    visual_lines = "نيعمثدحعوقوةصرفلسايقموهلامتحالا\nدحاوورفصنلامتحالاةميقنوكت"
    logical = _restore_logical_arabic_order(visual_lines)
    assert logical.splitlines() == [
        "الاحتمالهومقياسلفرصةوقوعحدثمعين",
        "تكونقيمةالاحتمالنصفروواحد",
    ]


def _line_box(top: float, bottom: float, *, left: float = 10, right: float = 90):
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def test_arabic_ocr_reorders_per_line_and_excludes_text_outside_region() -> None:
    item_bbox = SimpleNamespace(
        l=0, t=80, r=100, b=20,
        coord_origin=SimpleNamespace(value="BOTTOMLEFT"),
    )
    predictions = [
        (_line_box(60, 70), "أدحاو يواسي هلامتحا نإف أدكؤم ثدحلا ناك اذإ"),
        (_line_box(25, 35), "نيعم ثدح عوقو ةصرفل سايقم وه لامتحالا"),
        (_line_box(40, 50), "دحاوو رفص نيب لامتحالالا ةميق نوكت"),
        (_line_box(5, 15), "outside text must be discarded"),
    ]

    assert _logical_arabic_lines_for_item(predictions, item_bbox, page_height=100, scale=1) == [
        "الاحتمال هو مقياس لفرصة وقوع حدث معين",
        "تكون قيمة الالاحتمال بين صفر وواحد",
        "إذا كان الحدث مؤكدأ فإن احتماله يساوي واحدأ",
    ]


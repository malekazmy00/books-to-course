import json

import pytest

from b2c_ingest import surya_ocr


def test_parse_surya_html_keeps_order_and_text():
    raw = (
        '<div data-label="Section-Header" data-bbox="1 2 3 4"><h2>Heading</h2></div>'
        '<div data-label="List-Group" data-bbox="5 6 7 8"><ul><li>First</li><li>Second</li></ul></div>'
        '<div data-label="Table" data-bbox="9 10 11 12"><table><tr><td>A</td><td>B</td></tr></table></div>'
    )
    blocks = surya_ocr.parse_surya_html(raw)
    assert [block["label"] for block in blocks] == ["Section-Header", "List-Group", "Table"]
    assert blocks[0]["text"] == "Heading"
    assert blocks[0]["level"] == 2
    assert blocks[1]["text"] == "First\nSecond"
    assert blocks[2]["text"] == "A\tB"


def test_normalized_boxes_are_mapped_to_pdf_points():
    assert surya_ocr.normalized_bbox_to_points("100 200 900 800", 600, 800) == (
        60.0, 160.0, 540.0, 640.0,
    )
    assert surya_ocr.normalized_bbox_to_points("900 200 100 800", 600, 800) is None


def test_request_surya_page_uses_trained_prompt_and_large_budget(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self):
            return json.dumps({
                "model": "surya-test",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": '<div data-label="Text" data-bbox="1 2 3 4"><p>OCR text</p></div>'},
                }],
                "usage": {"completion_tokens": 120},
            }).encode()

    def fake_urlopen(request, timeout):
        captured["request"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(surya_ocr, "urlopen", fake_urlopen)
    blocks, stats = surya_ocr.request_surya_page(
        b"image", base_url="http://127.0.0.1:18082/v1",
        timeout_seconds=1800, max_tokens=16384,
    )
    content = captured["request"]["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[1]["text"] == surya_ocr.SURYA_PROMPT
    assert captured["request"]["max_tokens"] == 8192
    assert captured["timeout"] == 1800
    assert stats["max_tokens_used"] == 8192
    assert blocks[0]["text"] == "OCR text"
    assert stats["usage"]["completion_tokens"] == 120


def test_request_retries_with_more_tokens_when_server_truncates(monkeypatch):
    budgets = []

    class Response:
        def __init__(self, finish_reason):
            self.finish_reason = finish_reason
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self):
            return json.dumps({"choices": [{
                "finish_reason": self.finish_reason,
                "message": {"content": '<div data-label="Text" data-bbox="1 2 3 4"><p>Complete</p></div>'},
            }]}).encode()

    def fake_urlopen(request, timeout):
        budgets.append(json.loads(request.data)["max_tokens"])
        return Response("length" if len(budgets) == 1 else "stop")

    monkeypatch.setattr(surya_ocr, "urlopen", fake_urlopen)
    blocks, stats = surya_ocr.request_surya_page(
        b"image", base_url="http://surya-ocr:8080/v1", max_tokens=16384,
    )
    assert budgets == [8192, 16384]
    assert blocks[0]["text"] == "Complete"
    assert stats["max_tokens_used"] == 16384


def test_request_rejects_short_timeout_and_small_generation_budget():
    with pytest.raises(ValueError, match="30-minute"):
        surya_ocr.request_surya_page(
            b"image", base_url="http://127.0.0.1:18082/v1", timeout_seconds=10,
        )
    with pytest.raises(ValueError, match="8192"):
        surya_ocr.request_surya_page(
            b"image", base_url="http://127.0.0.1:18082/v1", max_tokens=4096,
        )


def test_request_rejects_truncated_result_at_maximum(monkeypatch):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self):
            return b'{"choices":[{"finish_reason":"length","message":{"content":"partial"}}]}'

    monkeypatch.setattr(surya_ocr, "urlopen", lambda request, timeout: Response())
    with pytest.raises(surya_ocr.SuryaUnavailable, match="truncated"):
        surya_ocr.request_surya_page(
            b"image", base_url="http://localhost:18082/v1", max_tokens=8192,
        )


def test_surya_endpoint_must_be_local():
    with pytest.raises(ValueError, match="Public OCR"):
        surya_ocr.validate_local_url("http://8.8.8.8:8080/v1")
    surya_ocr.validate_local_url("http://surya-ocr:8080/v1")

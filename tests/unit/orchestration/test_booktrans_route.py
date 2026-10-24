import json
from pathlib import Path

from b2c_orchestration.booktrans_route import run_booktrans_pro
from b2c_translation import SourceSegment, TranslatedSegment


def test_booktrans_route_keeps_ids_persists_and_never_puts_secret_in_argv(monkeypatch, tmp_path):
    observed = {}

    def fake_run(command, *, env, **kwargs):
        observed["command"] = command
        observed["env_key"] = env["OPENAI_API_KEY"]
        observed["work"] = Path(command[command.index("--work") + 1])
        from booktrans import pipeline
        blocks = json.loads((observed["work"] / "book.json").read_text())["blocks"]
        chunks = pipeline.make_chunks(blocks, target=1800)
        tr_dir = Path(pipeline.lpath(str(observed["work"]), "tr", "ar"))
        tr_dir.mkdir(parents=True, exist_ok=True)
        for chunk in chunks:
            path = tr_dir / f"{chunk['index']:04d}.json"
            if path.exists():
                continue
            source = {block["id"]: block["text"] for block in chunk["blocks"]}
            path.write_text(json.dumps({
                "index": chunk["index"],
                "model": "small-model",
                "cost_usd": 0.01,
                "tokens": {"in": 100, "cached": 20, "out": 50},
                "tr": {key: "ترجمة صحيحة" for key in source},
                "src": {key: pipeline.fingerprint(value) for key, value in source.items()},
            }))
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr("b2c_orchestration.booktrans_route.subprocess.run", fake_run)
    segments = [SourceSegment(segment_id="book:p:1", text="A source paragraph."),
                SourceSegment(segment_id="book:p:2", text="A second source paragraph.")]
    result = run_booktrans_pro(
        segments, work_root=str(tmp_path), source_identity="a" * 64,
        model_id="small-model", base_url="https://api.example.test/v1",
          api_key="secret-value", source_language="en", target_language="ar",
        glossary={"intelligence": "استخبارات"}, domain_id="intelligence-analysis",
    )
    assert [item.segment_id for item in result] == ["book:p:1", "book:p:2"]
    assert all(item.text == "ترجمة صحيحة" for item in result)
    assert observed["env_key"] == "secret-value"
    assert "secret-value" not in observed["command"]
    assert observed["command"][observed["command"].index("--self-edit") + 1] == "allow"
    assert "--force-translate" not in observed["command"]
    assert (observed["work"] / "book.json").exists()
    assert (observed["work"] / "b2c-instructions.md").exists()
    assert "استخبارات" in (observed["work"] / "b2c-instructions.md").read_text()
    assert oct(observed["work"].stat().st_mode & 0o777) == "0o700"
    from b2c_orchestration.booktrans_route import booktrans_usage_summary
    usage = booktrans_usage_summary(
        str(tmp_path), source_identity="a" * 64, model_id="small-model",
        base_url="https://api.example.test/v1", source_language="en", target_language="ar",
        glossary={"intelligence": "استخبارات"}, domain_id="intelligence-analysis")
    assert usage["api_calls"] == 1
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 50
    assert usage["reported_cost_usd"] == 0.01


def test_booktrans_route_seeds_saved_translation_before_any_new_request(monkeypatch, tmp_path):
    seen = {}

    def fake_run(command, *, env, **kwargs):
        work = Path(command[command.index("--work") + 1])
        seen["seed_files"] = list((work / "ar" / "tr").glob("*.json"))
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr("b2c_orchestration.booktrans_route.subprocess.run", fake_run)
    result = run_booktrans_pro(
        [SourceSegment(segment_id="book:p:1", text="A source paragraph.")],
        work_root=str(tmp_path), source_identity="b" * 64,
        model_id="small-model", base_url="https://api.example.test/v1",
          api_key="not-logged", source_language="en", target_language="ar",
        glossary={}, seed_translations=[
            TranslatedSegment(segment_id="book:p:1", text="ترجمة محفوظة")
        ],
    )
    assert seen["seed_files"]
    assert result[0].text == "ترجمة محفوظة"

import importlib.util
import json
import sys
import types
import wave
import io
from pathlib import Path
import pytest

@pytest.fixture
def renderer(monkeypatch,tmp_path):
    for name in ("tts", "tts.text", "tts.text.normalize", "tts.text.numbers"):
        monkeypatch.setitem(sys.modules,name,types.ModuleType(name))
    sys.modules["tts.text.normalize"].normalize=lambda text:text
    sys.modules["tts.text.numbers"].expand_numbers=lambda text:text.replace("2001","ألفان وواحد")
    path=Path(__file__).resolve().parents[3]/"docker/supertonic/audio_renderer.py"
    spec=importlib.util.spec_from_file_location("renderer_catt_test",path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    monkeypatch.setenv("B2C_RENDERER_CACHE_DIR",str(tmp_path/"cache"))
    return mod


def _spaces(text):
    return " ".join(text.split())


def test_arabic_is_bounded_by_provider_limit_and_left_unexpanded_for_fasih(renderer):
    # Number expansion and CATT-sized chunking now happen inside Fasih; the
    # renderer only bounds requests to the 1200-char provider limit.
    text=("لجان التحقيق التي تلت الهجمات الإرهابية في 2001 "*80)+"أ"*1100
    parts=renderer.tts_segments(text,"ar")
    assert renderer._PROVIDER_TEXT_LIMIT==1200
    assert renderer.RENDER_POLICY_VERSION.startswith("sentence-aware-boundaries-v4")
    assert all(0<len(p)<=1200 for p in parts)
    assert _spaces(" ".join(parts))==_spaces(text)
    assert "2001" in parts[0]
    assert len(parts)>2


def test_long_arabic_render_orders_audio_and_reuses_completed_wav(renderer,monkeypatch):
    text="النص العربي الطويل في 2001. "*90
    expected=renderer.tts_segments(text,"ar");requests=[]
    wav=io.BytesIO()
    with wave.open(wav,"wb") as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(24000);output.writeframes(b"\0"*480)
    data=wav.getvalue()
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return data
    def request(req,timeout):
        body=json.loads(req.data);assert len(body["text"])<=1200;requests.append(body["text"]);return Response()
    monkeypatch.setattr(renderer.urllib.request,"urlopen",request)
    def assemble(command,**kwargs):
        listing=Path(command[command.index("-i")+1]).read_text().splitlines()
        assert len(listing)==len(expected)
        return types.SimpleNamespace(stdout=b"ID3"+b"0"*300)
    monkeypatch.setattr(renderer.subprocess,"run",assemble)
    assert len(expected)>1
    req=renderer.RenderRequest(texts=[text],language="ar")
    assert renderer.render(req).status_code==200
    assert requests==list(dict.fromkeys(expected))
    before=len(requests);assert renderer.render(req).status_code==200;assert len(requests)==before


def test_english_limit_is_preserved(renderer):
    with pytest.raises(renderer.HTTPException) as error:renderer.tts_segments("a"*4801,"en")
    assert error.value.status_code==422



def test_arabic_prefers_sentence_boundaries_and_keeps_exact_order(renderer):
    first = " ".join(["أ" * 9] * 90) + "."   # 899 chars, sentence end inside the 0.65-1.0 window
    second = " ".join(["ب" * 9] * 30) + "،"
    third = " ".join(["ت" * 9] * 40)
    text = first + " " + second + " " + third
    parts = renderer.tts_segments(text, "ar")
    assert parts[0] == first
    assert " ".join(parts) == text
    assert max(map(len, parts)) <= 1200


def test_space_outside_preferred_window_is_used_before_rejecting(renderer):
    text = "ب" * 100 + " " + "أ" * 1150
    parts = renderer.tts_segments(text, "ar")
    assert parts == ["ب" * 100, "أ" * 1150]


def test_truly_unbroken_arabic_token_is_rejected(renderer):
    with pytest.raises(renderer.HTTPException) as error:
        renderer.tts_segments("أ" * 1201, "ar")
    assert error.value.status_code == 422

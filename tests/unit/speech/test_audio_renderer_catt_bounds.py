import importlib.util, json, sys, types, wave, io
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


def test_arabic_is_bounded_after_number_expansion_without_losing_text(renderer):
    text=("لجان التحقيق التي تلت الهجمات الإرهابية في 2001 "*80)+"أ"*1100
    parts=renderer.tts_segments(text,"ar")
    assert all(0<len(p)<=160 for p in parts)
    assert "".join(parts)==text.replace("2001","ألفان وواحد")
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
        body=json.loads(req.data);assert len(body["text"])<=160;requests.append(body["text"]);return Response()
    monkeypatch.setattr(renderer.urllib.request,"urlopen",request)
    def assemble(command,**kwargs):
        listing=Path(command[command.index("-i")+1]).read_text().splitlines()
        assert len(listing)==len(expected)
        return types.SimpleNamespace(stdout=b"ID3"+b"0"*300)
    monkeypatch.setattr(renderer.subprocess,"run",assemble)
    req=renderer.RenderRequest(texts=[text],language="ar")
    assert renderer.render(req).status_code==200
    assert requests==list(dict.fromkeys(expected))
    before=len(requests);assert renderer.render(req).status_code==200;assert len(requests)==before


def test_english_limit_is_preserved(renderer):
    with pytest.raises(renderer.HTTPException) as error:renderer.tts_segments("a"*4801,"en")
    assert error.value.status_code==422



def test_arabic_prefers_sentence_boundaries_and_keeps_exact_order(renderer):
    text = "أ" * 70 + ". " + "ب" * 70 + ". " + "ت" * 90
    parts = renderer.tts_segments(text, "ar")
    assert parts[0].endswith(". ")
    assert "".join(parts) == text
    assert max(map(len, parts)) <= 160

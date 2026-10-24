import pytest

from b2c_speech.tts.fasih_runtime import (
    FasihCpuThreadConfig,
    configure_fasih_cpu_threads,
)


class FakeTorch:
    def __init__(self):
        self.intra_op = None
        self.inter_op = None

    def set_num_threads(self, value):
        self.intra_op = value

    def set_num_interop_threads(self, value):
        self.inter_op = value


def test_fasih_thread_config_defaults_and_applies_only_requested_values(monkeypatch):
    monkeypatch.delenv("B2C_FASIH_INTRA_OP_THREADS", raising=False)
    monkeypatch.delenv("B2C_FASIH_INTER_OP_THREADS", raising=False)
    config = FasihCpuThreadConfig.from_environment()
    torch = FakeTorch()

    assert config == FasihCpuThreadConfig(4, 4)
    assert configure_fasih_cpu_threads(config, torch_module=torch) == config
    assert torch.intra_op == 4
    assert torch.inter_op == 4


def test_fasih_thread_config_supports_fasih_scoped_overrides(monkeypatch):
    monkeypatch.setenv("B2C_FASIH_INTRA_OP_THREADS", "6")
    monkeypatch.setenv("B2C_FASIH_INTER_OP_THREADS", "2")
    config = FasihCpuThreadConfig.from_environment()
    torch = FakeTorch()

    configure_fasih_cpu_threads(config, torch_module=torch)
    assert (torch.intra_op, torch.inter_op) == (6, 2)


@pytest.mark.parametrize("value", [0, -1])
def test_fasih_thread_config_rejects_nonpositive_counts(value):
    with pytest.raises(ValueError, match="positive"):
        FasihCpuThreadConfig(intra_op_threads=value)


def test_fasih_thread_config_rejects_invalid_environment_value(monkeypatch):
    monkeypatch.setenv("B2C_FASIH_INTRA_OP_THREADS", "many")
    with pytest.raises(ValueError, match="integers"):
        FasihCpuThreadConfig.from_environment()

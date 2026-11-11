import pytest


@pytest.fixture(autouse=True)
def no_real_langfuse(monkeypatch):
    # 测试数据不能混进线上追踪：本机即使配置了 LANGFUSE_*，测试也不向真实 Langfuse 发送。
    monkeypatch.delenv('LANGFUSE_PUBLIC_KEY', raising=False)
    monkeypatch.delenv('LANGFUSE_SECRET_KEY', raising=False)

import hashlib
import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EXTENSIONS = {'.py', '.md', '.txt', '.rst', '.js', '.jsx', '.ts', '.tsx', '.json', '.toml', '.yaml', '.yml', '.sql', '.css', '.sh'}
BLOCKED = {'.git', 'node_modules', 'data', 'output', 'outputs', 'dist', 'build', 'vendor', '.venv', 'venv', '.ssh', '.codex', '.claude', 'secrets'}
SECRET = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{20,}|(?i:(?:api[_-]?key|password|secret|access[_-]?token)\s*[:=]\s*[\"\'][A-Za-z0-9_+/=-]{24,}[\"\'])')


def allowed_path(path):
    parts = PurePosixPath(path).parts
    return bool(parts and len(path) <= 400 and not path.startswith('/') and '\\' not in path and ':' not in path
                and all(part not in {'..', '.'} and part.lower() not in BLOCKED for part in parts)
                and not any(part.lower().startswith('.env') for part in parts)
                and (PurePosixPath(path).suffix.lower() in EXTENSIONS or PurePosixPath(path).name == 'Dockerfile')
                and not any(value in path.lower() for value in ['package-lock', 'yarn.lock', 'pnpm-lock', 'credentials']))


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)


class SourceFile(Strict):
    path: str
    content: str = Field(max_length=150000)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def verify(self):
        if not allowed_path(self.path):
            raise ValueError('不允许的文件路径或类型')
        if '\x00' in self.content or SECRET.search(self.content):
            raise ValueError('文件疑似含密钥或二进制内容，请先排除')
        if hashlib.sha256(self.content.encode('utf-8')).hexdigest() != self.sha256:
            raise ValueError('文件内容哈希不一致')
        return self


class Excluded(Strict):
    path: str = Field(max_length=400)
    reason: str = Field(max_length=200)


class ExportScope(Strict):
    selected_roots: list[str] = Field(default_factory=list, max_length=200)
    tracked_file_count: int = Field(ge=0)
    out_of_scope_count: int = Field(ge=0)


class Bundle(Strict):
    schema_version: Literal[1] = Field(alias='schema')
    project: str = Field(min_length=1, max_length=100)
    branch: str = Field(min_length=1, max_length=150)
    commit: str = Field(pattern=r'^[a-f0-9]{40,64}$')
    files: list[SourceFile] = Field(min_length=1, max_length=200)
    exported_at: str = Field(max_length=50)
    excluded: list[Excluded] = Field(default_factory=list, max_length=5000)
    scope: ExportScope | None = None

    @model_validator(mode='after')
    def bounded(self):
        if sum(len(file.content.encode('utf-8')) for file in self.files) > 2_000_000:
            raise ValueError('文本总量超过 2 MB，请缩小目录范围')
        if len({file.path for file in self.files}) != len(self.files):
            raise ValueError('快照包含重复路径')
        return self


class Question(Strict):
    project_id: str = Field(max_length=64)
    question: str = Field(min_length=8, max_length=2000)
    snapshot_id: str | None = Field(default=None, max_length=64)

    @field_validator('question')
    @classmethod
    def strip_question(cls, value):
        if len(value.strip()) < 8:
            raise ValueError('问题至少需要 8 个字符')
        return value.strip()


class Search(Strict):
    project_id: str = Field(max_length=64)
    question: str = Field(min_length=2, max_length=2000)
    snapshot_id: str | None = Field(default=None, max_length=64)
    top_k: int = Field(default=6, ge=1, le=10)


class Evidence(Strict):
    source_id: str
    quote: str = Field(min_length=4, max_length=800)


class Claim(Strict):
    text: str = Field(min_length=1, max_length=1800)
    evidence: list[Evidence] = Field(min_length=1, max_length=4)


class Answer(Strict):
    status: Literal['answered', 'insufficient', 'conflict']
    summary: str = Field(min_length=1, max_length=1800)
    claims: list[Claim] = Field(max_length=8)
    gaps: list[str] = Field(max_length=8)

    @model_validator(mode='after')
    def consistency(self):
        if self.status in {'answered', 'conflict'} and not self.claims:
            raise ValueError('回答必须有证据支持的结论')
        if self.status == 'insufficient' and not self.gaps:
            raise ValueError('证据不足需说明缺口')
        return self

from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, field_validator, model_validator

from ..models import Strict

LOG_LIMIT = 64_000


class ChatMessage(Strict):
    request_id: UUID
    message: str = Field(min_length=1, max_length=4000)
    project_id: str | None = Field(default=None, max_length=64)
    allow_web: StrictBool = False
    search_query: str = Field(default='', max_length=160)

    @field_validator('message', 'search_query')
    @classmethod
    def strip_message(cls, value, info):
        value = value.strip()
        if info.field_name == 'message' and not value:
            raise ValueError('请填写想咨询的问题')
        return value


class ChatReply(Strict):
    reply: str = Field(min_length=1, max_length=900)
    source_ids: list[str] = Field(max_length=6)
    needs_diagnosis: StrictBool

    @field_validator('reply')
    @classmethod
    def nonempty_reply(cls, value):
        if not value.strip():
            raise ValueError('回复不能为空')
        return value.strip()


class SessionStart(Strict):
    symptom: str = Field(min_length=8, max_length=4000)
    project_id: str | None = Field(default=None, max_length=64)

    @field_validator('symptom')
    @classmethod
    def strip_symptom(cls, value):
        text = value.strip()
        if len(text) < 8:
            raise ValueError('问题描述至少 8 个字符')
        return text


class SessionSupplement(Strict):
    product_version: str | None = Field(default=None, max_length=200)
    reproduction: str | None = Field(default=None, max_length=4000)
    log_text: str | None = Field(default=None, max_length=LOG_LIMIT)

    @field_validator('product_version', 'reproduction', 'log_text')
    @classmethod
    def strip_text(cls, value):
        return value.strip() if value is not None else None

    @field_validator('log_text')
    @classmethod
    def bounded_utf8(cls, value):
        if value is not None and len(value.encode('utf-8')) > LOG_LIMIT:
            raise ValueError('日志最多 64000 字节，请只粘贴故障前后的片段')
        return value

    @model_validator(mode='after')
    def has_content(self):
        if not any(value and value.strip() for value in (self.product_version, self.reproduction, self.log_text)):
            raise ValueError('请至少补充版本、复现步骤或日志中的一项')
        return self


class SessionConfirm(Strict):
    resolved: StrictBool
    resolution_note: str = Field(default='', max_length=2000)


class Hypothesis(Strict):
    text: str = Field(min_length=1, max_length=1200)
    basis: list[str] = Field(min_length=1, max_length=4)


class Analysis(Strict):
    status: Literal['analyzed', 'insufficient']
    cause_hypotheses: list[Hypothesis] = Field(max_length=5)
    suggestions: list[str] = Field(max_length=6)
    insufficient_reason: str | None = Field(default=None, max_length=1200)

    @model_validator(mode='after')
    def consistent(self):
        if self.status == 'analyzed' and not self.cause_hypotheses:
            raise ValueError('status=analyzed 需要至少一条原因假设')
        if self.status == 'insufficient' and not self.insufficient_reason:
            raise ValueError('status=insufficient 需要说明未确定原因')
        if self.status == 'insufficient' and self.cause_hypotheses:
            raise ValueError('证据不足时只说明缺口，不能同时给出原因结论')
        return self

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: int | None = None
    username: str
    role: str
    is_active: bool = True
    last_login_at: datetime | None = None
    must_change_password: bool = False
    operator_id: int | None = None


class PasswordChangeIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class AdminUserIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=10, max_length=128)
    role: str = "analyst"
    operator_id: int | None = None


class AdminUserPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    operator_id: int | None = None


class AdminPasswordIn(BaseModel):
    password: str = Field(min_length=10, max_length=128)


class TokenOut(BaseModel):
    token: str
    user: UserOut


class OperatorIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    external_id: str | None = Field(default=None, max_length=64)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Укажите имя оператора")
        return cleaned


class OperatorOut(BaseModel):
    id: int
    name: str
    external_id: str | None
    calls_count: int = 0
    avg_overall: float | None = None
    violation_pct: float | None = None
    avg_politeness: float | None = None
    avg_completeness: float | None = None
    avg_script: float | None = None
    avg_resolution: float | None = None


class CallListItem(BaseModel):
    id: int
    orig_filename: str
    status: str
    progress: int
    call_at: datetime
    uploaded_at: datetime
    duration_sec: float | None
    operator_id: int | None
    operator_name: str | None
    operator_external_id: str | None = None
    overall: float | None = None
    politeness: float | None = None
    completeness: float | None = None
    script_compliance: float | None = None
    resolution: float | None = None
    tonality: str | None = None
    violation_pct: float | None = None
    avg_response_sec: float | None = None
    is_demo: bool = False
    error_message: str | None = None
    flagged: bool = False


class SegmentOut(BaseModel):
    start: float
    end: float
    speaker: str
    role: str
    text: str


class CheckOut(BaseModel):
    id: str
    title: str
    category: str
    passed: bool
    score: float
    weight: float
    detail: str
    evidence: str | None = None


class QualityOut(BaseModel):
    politeness: float
    completeness: float
    script_compliance: float
    resolution: float
    overall: float
    tonality: str
    tonality_score: float
    avg_response_sec: float | None
    talk_ratio_operator: float
    violation_pct: float
    violations: list[dict]
    recommendations: list[str]
    checks: list[CheckOut]
    summary: str


class CallDetail(CallListItem):
    language: str | None = None
    processed_sec: float | None = None
    mime: str | None = None
    has_audio: bool = False
    full_text: str = ""
    segments: list[SegmentOut] = Field(default_factory=list)
    quality: QualityOut | None = None
    review_note: str = ""
    masked: bool = False


class DashboardKpis(BaseModel):
    calls_total: int
    calls_done: int
    overall: float
    violation_pct: float
    politeness: float
    avg_response_sec: float | None
    processed_sec: float | None


class TrendPoint(BaseModel):
    date: str
    overall: float
    violation_pct: float
    count: int


class QueueOut(BaseModel):
    queued: int
    processing: int
    done_today: int
    failed: int


class DashboardOut(BaseModel):
    offline: bool = False
    kpis: DashboardKpis
    trend: list[TrendPoint]
    tonality: dict[str, int]
    metrics_avg: dict[str, float]
    operators: list[OperatorOut]
    recent: list[CallListItem]
    attention: list[CallListItem] = Field(default_factory=list)
    queue: QueueOut


class SettingsOut(BaseModel):
    whisper_model: str
    whisper_device: str
    max_parallel_jobs: int
    max_upload_mb: int
    max_duration_sec: int
    local_offline: bool
    company_phrases: list[str]
    extra_script_phrases: list[str]
    whisper_beam_size: int = 5
    whisper_batch_size: int = 8


class SettingsIn(BaseModel):
    company_phrases: list[str] | None = None
    extra_script_phrases: list[str] | None = None


class CallPatch(BaseModel):
    review_note: str | None = Field(default=None, max_length=2000)
    flagged: bool | None = None


class AnalyzeTextIn(BaseModel):
    text: str | None = Field(default=None, max_length=80_000)
    segments: list[SegmentOut] | None = None
    operator_first: bool = True

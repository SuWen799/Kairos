"""Kairos 数据模型（需求文档第 12 章 / 设计文档第 2 章）。
20 张表，全表 user_id + created_at + updated_at（多租户地基，需求 14.8）。
主键一律 ULID 文本（storage/ulid.py）。大 payload 存文件，表里存路径+hash。"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from kairos.storage.ulid import LOCAL_USER_ID


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[str] = mapped_column(Text, default=utcnow)
    updated_at: Mapped[str] = mapped_column(Text, default=utcnow, onupdate=utcnow)


class UserMixin:
    user_id: Mapped[str] = mapped_column(Text, default=LOCAL_USER_ID, index=True)


# ── 账号域 ──────────────────────────────────────────────

class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # usr_...
    email: Mapped[str | None] = mapped_column(Text, unique=True)
    phone_hash: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text, default="local")
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    # LLM key 不落库：进系统 keyring（需求 9.1 凭据层）


# ── 对话与执行域 ────────────────────────────────────────

class Session(Base, TimestampMixin, UserMixin):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # sess_...
    project: Mapped[str] = mapped_column(Text, default="default")
    title: Mapped[str | None] = mapped_column(Text)
    title_auto: Mapped[bool] = mapped_column(Boolean, default=True)
    model: Mapped[str | None] = mapped_column(Text)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # v2.0 R3/R4/R5：模式 / 已批准计划 / 权限档 / goal 绑定（旧库 goals 表的 id 字符串）
    mode: Mapped[str] = mapped_column(Text, default="chat")       # chat|plan|goal
    plan_md: Mapped[str | None] = mapped_column(Text)             # 已批准的计划（chat 模式注入执行）
    autonomy: Mapped[str] = mapped_column(Text, default="standard")  # standard|semi|auto
    goal_ref: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("idx_sessions_user", "user_id", "updated_at"),)


class Message(Base, TimestampMixin, UserMixin):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # msg_...
    session_id: Mapped[str] = mapped_column(
        Text, ForeignKey("sessions.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    tool_calls_json: Mapped[str | None] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (Index("idx_messages_session", "session_id", "seq"),)


class Run(Base, TimestampMixin, UserMixin):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # run_...
    session_id: Mapped[str] = mapped_column(Text, ForeignKey("sessions.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, default="queued")  # queued|running|done|failed|cancelled
    trigger: Mapped[str] = mapped_column(Text, default="chat")
    model: Mapped[str | None] = mapped_column(Text)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_est: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[str | None] = mapped_column(Text)
    ended_at: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("idx_runs_session", "session_id", "started_at"),)


class RunEvent(Base):
    """append-only：禁 UPDATE/DELETE（SQLite 用触发器强制，见 0001 迁移）。"""
    __tablename__ = "run_events"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # evt_...
    run_id: Mapped[str] = mapped_column(Text, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    ctx_digest: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, default=utcnow)
    __table_args__ = (Index("idx_run_events_run", "run_id", "seq"),)


# ── 调度与恢复域 ────────────────────────────────────────

class Task(Base, TimestampMixin, UserMixin):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # task_...
    run_id: Mapped[str | None] = mapped_column(Text, ForeignKey("runs.id"))
    kind: Mapped[str] = mapped_column(Text, default="background")  # background|hitl|scheduled
    status: Mapped[str] = mapped_column(Text, default="queued")
    # queued|running|waiting_approval|paused|done|failed|cancelled|interrupted
    checkpoint_ref: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[float | None] = mapped_column(Float)
    heartbeat_at: Mapped[float | None] = mapped_column(Float)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    budget_limit: Mapped[int | None] = mapped_column(Integer)
    scheduled_for: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("idx_tasks_dispatch", "status", "lease_until"),
        Index("idx_tasks_user", "user_id", "status"),
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    result_hash: Mapped[str | None] = mapped_column(Text)
    result_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, default=utcnow)


# ── 工单域（v3.0 M-S1：参考 dsh-superagent 领域模型的 Kairos 落地）──

class Mandate(Base, TimestampMixin, UserMixin):
    """不可变授权：记录一项工单为什么被允许执行（政策版本 + 范围 + 强度）。
    模型只判断怎么干，不能自我扩权——授权只来自人的明确动作（计划批准/手动开单）。"""
    __tablename__ = "mandates"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # mdl_...
    session_id: Mapped[str] = mapped_column(Text, ForeignKey("sessions.id"))
    basis: Mapped[str] = mapped_column(Text, default="plan_approval")
    # plan_approval(计划批准) | explicit(人直接开单)
    scope: Mapped[str] = mapped_column(Text, default="")        # 授权范围描述
    strength: Mapped[str] = mapped_column(Text, default="explicit")  # explicit|inferred
    plan_ref: Mapped[str | None] = mapped_column(Text)          # 来源 confirmations.id
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, default=utcnow)
    __table_args__ = (Index("idx_mandates_session", "session_id", "created_at"),)


class Ticket(Base, TimestampMixin, UserMixin):
    """工单：授权工作单元。状态机独立于执行会话——
    queued → working → ready | failed | blocked | done；人的消息可复活 ended 工单（恢复走新 Assignment）。"""
    __tablename__ = "tickets"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # tkt_...
    session_id: Mapped[str] = mapped_column(Text, ForeignKey("sessions.id"))
    mandate_id: Mapped[str] = mapped_column(Text, ForeignKey("mandates.id"))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="queued")
    # queued|working|waiting_human|ready|blocked|failed|done|cancelled
    latest_run_id: Mapped[str | None] = mapped_column(Text, ForeignKey("runs.id"))
    instruction: Mapped[str | None] = mapped_column(Text)       # 给执行器的开工指令
    error: Mapped[str | None] = mapped_column(Text)
    # M-S3 租约执行：auto_start=1 才被后台 worker 认领（chat 驱动的工单保持 0，避免双执行）
    auto_start: Mapped[bool] = mapped_column(Boolean, default=False)
    lease_until: Mapped[float | None] = mapped_column(Float)    # 租约到期时间戳（worker 心跳续租）
    claimed_by: Mapped[str | None] = mapped_column(Text)        # 认领的 worker id
    created_at: Mapped[str] = mapped_column(Text, default=utcnow)
    updated_at: Mapped[str] = mapped_column(Text, default=utcnow)
    finished_at: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("idx_tickets_status", "status", "created_at"),
        Index("idx_tickets_session", "session_id", "created_at"),
    )


class Trigger(Base, TimestampMixin, UserMixin):
    """事件触发器（M-S2）：Source 产出的不可变事件，先观察（observe）再由人决定是否转工单。
    幂等靠 dedup_key（源引用+内容哈希），同一事件不会重复打扰。"""
    __tablename__ = "triggers"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # trg_...
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)  # web_watch|cron|file|manual
    source_ref: Mapped[str] = mapped_column(Text, default="")       # sources.id / automation.id / 路径
    title: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str] = mapped_column(Text, default="")          # ground truth 摘要（截断）
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="observed")   # observed|accepted|dismissed
    gate: Mapped[str] = mapped_column(Text, default="pass")         # gate 结论（pass|block:原因）
    assessment: Mapped[str | None] = mapped_column(Text)            # LLM 研判摘要
    ticket_id: Mapped[str | None] = mapped_column(Text, ForeignKey("tickets.id"))
    __table_args__ = (
        Index("idx_triggers_status", "status", "created_at"),
        UniqueConstraint("user_id", "dedup_key", name="uq_triggers_user_dedup"),
    )


class Checkpoint(Base):
    __tablename__ = "checkpoints"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # ckpt_...
    run_id: Mapped[str] = mapped_column(Text, ForeignKey("runs.id"), nullable=False)
    step: Mapped[int] = mapped_column(Integer, default=0)
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    artifact_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[str] = mapped_column(Text, default=utcnow)
    __table_args__ = (Index("idx_checkpoints_run", "run_id", "step"),)


# ── HITL / 审计 / 预算 ──────────────────────────────────

class Confirmation(Base, TimestampMixin, UserMixin):
    __tablename__ = "confirmations"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # cfm_...
    run_id: Mapped[str | None] = mapped_column(Text, ForeignKey("runs.id"))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    content_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str | None] = mapped_column(Text)     # 防 TOCTOU（需求 9.3）
    scope: Mapped[str] = mapped_column(Text, default="once")   # once|session|task|timed|permanent
    expires_at: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(Text, default="pending")  # pending|approved|rejected|expired
    approver: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("idx_confirmations_pending", "decision", "expires_at"),)


class AuditEvent(Base):
    """append-only + 简化哈希链（需求 9.5）：prev_hash=sha256(上一条 id+ts+event_type)。"""
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # aud_...
    user_id: Mapped[str] = mapped_column(Text, default=LOCAL_USER_ID, index=True)
    ts: Mapped[str] = mapped_column(Text, default=utcnow)
    actor: Mapped[str] = mapped_column(Text, default="loop")  # loop|subagent:<n>|scheduler|channel:*
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str | None] = mapped_column(Text)
    policy_decision: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(Text)
    params_hash: Mapped[str | None] = mapped_column(Text)
    result_hash: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    approver: Mapped[str | None] = mapped_column(Text)
    prev_hash: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("idx_audit_ts", "ts"),)


class Budget(Base, TimestampMixin):
    __tablename__ = "budgets"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # bud_...
    user_id: Mapped[str] = mapped_column(Text, default=LOCAL_USER_ID, index=True)
    period: Mapped[str] = mapped_column(Text, nullable=False)  # 2026-11 | 2026-11-01
    scope: Mapped[str] = mapped_column(Text, default="global")  # global|channel:wecom|session:<id>
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    tokens_limit: Mapped[int] = mapped_column(Integer, default=200_000)
    cost_limit: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (UniqueConstraint("scope", "period", name="uq_budgets_scope_period"),)


# ── Agent 能力域（v0.4 表升级）─────────────────────────

class Memory(Base, TimestampMixin, UserMixin):
    __tablename__ = "memories"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # mem_...
    content: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, default="fact")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    embedding_ref: Mapped[str | None] = mapped_column(Text)    # sqlite-vec 预留
    forgettable: Mapped[bool] = mapped_column(Boolean, default=True)


class Skill(Base, TimestampMixin, UserMixin):
    __tablename__ = "skills"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # skl_...
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str | None] = mapped_column(Text)             # SKILL.md 文件路径
    content: Mapped[str] = mapped_column(Text, default="")
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_skills_user_slug"),)


class Failure(Base, TimestampMixin, UserMixin):
    __tablename__ = "failures"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # fail_...
    task_desc: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    failure_class: Mapped[str | None] = mapped_column(Text)    # 五分类（需求 10.6）
    run_id: Mapped[str | None] = mapped_column(Text)


class KbDoc(Base, TimestampMixin, UserMixin):
    __tablename__ = "kb_docs"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # kbd_...
    doc_name: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str | None] = mapped_column(Text)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    __table_args__ = (UniqueConstraint("user_id", "doc_name", name="uq_kb_docs_user_name"),)


class KbChunk(Base, TimestampMixin, UserMixin):
    __tablename__ = "kb_chunks"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # kbc_...
    doc_id: Mapped[str] = mapped_column(Text, ForeignKey("kb_docs.id", ondelete="CASCADE"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_ref: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("idx_kb_chunks_doc", "doc_id", "seq"),)


class Source(Base, TimestampMixin, UserMixin):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # src_...
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(Text)
    file_ref: Mapped[str | None] = mapped_column(Text)         # 原文落盘路径
    last_check_at: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="active")
    __table_args__ = (UniqueConstraint("user_id", "url", name="uq_sources_user_url"),)


class Application(Base, TimestampMixin, UserMixin):
    __tablename__ = "applications"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # app_...
    company: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="投递")
    source_url: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (Index("idx_applications_user", "user_id", "status"),)


class WatchTarget(Base, TimestampMixin, UserMixin):
    __tablename__ = "watch_targets"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # wt_...
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    cadence_hours: Mapped[float] = mapped_column(Float, default=6.0)
    next_check: Mapped[str | None] = mapped_column(Text)
    last_digest: Mapped[str] = mapped_column(Text, default="")
    last_check_at: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="active")


class Automation(Base, TimestampMixin, UserMixin):
    __tablename__ = "automations"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # auto_...
    name: Mapped[str] = mapped_column(Text, nullable=False)
    cron: Mapped[str] = mapped_column(Text, nullable=False)    # v1 用简化 cron（分 时 日 月 周）
    session_id: Mapped[str | None] = mapped_column(Text, ForeignKey("sessions.id"))
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_fire: Mapped[str | None] = mapped_column(Text)
    last_run: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("idx_automations_fire", "enabled", "next_fire"),)


class ChannelBinding(Base, TimestampMixin, UserMixin):
    __tablename__ = "channel_bindings"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # chn_...
    channel: Mapped[str] = mapped_column(Text, nullable=False)  # wecom|feishu
    external_id: Mapped[str] = mapped_column(Text, nullable=False)  # userid/chatid
    display_name: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(Text)  # 绑定的 Kairos 会话
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    whitelisted: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("channel", "external_id", name="uq_channel_binding"),)

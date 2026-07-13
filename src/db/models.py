"""Database models for affiliate video tracking."""

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="user")  # admin | user
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | rejected
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    rejected_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    plan: Mapped[str] = mapped_column(String(32), default="trial")  # trial | monthly | yearly | lifetime
    payment_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    profiles: Mapped[List["Profile"]] = relationship("Profile", back_populates="owner")


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # tiktok | instagram
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    video_count: Mapped[int] = mapped_column(Integer, default=0)
    last_scanned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    owner: Mapped[Optional["User"]] = relationship("User", back_populates="profiles")
    videos: Mapped[List["Video"]] = relationship("Video", back_populates="profile")

    __table_args__ = (
        UniqueConstraint("user_id", "platform", "username", name="uq_user_platform_username"),
    )


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    platform_video_id: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    views: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    likes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comments: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    shares: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    gmv: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    commission: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    orders: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_downloaded: Mapped[bool] = mapped_column(Boolean, default=False)
    downloaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    youtube_video_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    youtube_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    youtube_uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    profile: Mapped["Profile"] = relationship("Profile", back_populates="videos")
    youtube_uploads: Mapped[List["VideoYouTubeUpload"]] = relationship(
        "VideoYouTubeUpload", back_populates="video"
    )
    facebook_uploads: Mapped[List["VideoFacebookUpload"]] = relationship(
        "VideoFacebookUpload", back_populates="video"
    )
    threads_uploads: Mapped[List["VideoThreadsPost"]] = relationship(
        "VideoThreadsPost", back_populates="video"
    )

    __table_args__ = (
        UniqueConstraint("profile_id", "platform_video_id", name="uq_profile_video"),
    )


class YouTubeAppConfig(Base):
    """Google OAuth app credentials — multiple rows for backup / failover."""

    __tablename__ = "youtube_app_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(128), default="Primary OAuth App")
    client_id: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    client_secret: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    redirect_uri: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    grants_today: Mapped[int] = mapped_column(Integer, default=0)
    refreshes_today: Mapped[int] = mapped_column(Integer, default=0)
    uploads_today: Mapped[int] = mapped_column(Integer, default=0)
    daily_grant_limit: Mapped[int] = mapped_column(Integer, default=100)
    daily_refresh_limit: Mapped[int] = mapped_column(Integer, default=5000)
    minute_grant_limit: Mapped[int] = mapped_column(Integer, default=18)
    token_calls_window: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    usage_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    rate_limited_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    channels: Mapped[List["YouTubeChannel"]] = relationship(
        "YouTubeChannel", back_populates="oauth_app"
    )


class YouTubeChannel(Base):
    """Connected YouTube channel account (multiple rows)."""

    __tablename__ = "youtube_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    oauth_app_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("youtube_app_config.id"), nullable=True
    )
    label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    refresh_token: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    access_token: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    channel_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    channel_title: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    channel_thumbnail: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    default_privacy: Mapped[str] = mapped_column(String(20), default="private")
    default_category: Mapped[str] = mapped_column(String(10), default="22")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_upload_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    oauth_app: Mapped[Optional["YouTubeAppConfig"]] = relationship(
        "YouTubeAppConfig", back_populates="channels"
    )
    uploads: Mapped[List["VideoYouTubeUpload"]] = relationship(
        "VideoYouTubeUpload", back_populates="youtube_channel"
    )


class VideoYouTubeUpload(Base):
    """Track which videos were uploaded to which YouTube channel."""

    __tablename__ = "video_youtube_uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), nullable=False)
    youtube_channel_id: Mapped[int] = mapped_column(ForeignKey("youtube_channels.id"), nullable=False)
    youtube_video_id: Mapped[str] = mapped_column(String(64), nullable=False)
    youtube_url: Mapped[str] = mapped_column(String(512), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    video: Mapped["Video"] = relationship("Video", back_populates="youtube_uploads")
    youtube_channel: Mapped["YouTubeChannel"] = relationship(
        "YouTubeChannel", back_populates="uploads"
    )

    __table_args__ = (
        UniqueConstraint("video_id", "youtube_channel_id", name="uq_video_yt_channel"),
    )


class FacebookAppConfig(Base):
    """Meta / Facebook app credentials (App ID + Secret)."""

    __tablename__ = "facebook_app_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(128), default="Facebook App")
    app_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    app_secret: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    redirect_uri: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    pages: Mapped[List["FacebookPage"]] = relationship(
        "FacebookPage", back_populates="app_config"
    )


class FacebookPage(Base):
    """Connected Facebook Page for video publishing."""

    __tablename__ = "facebook_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    app_config_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("facebook_app_config.id"), nullable=True
    )
    label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    page_id: Mapped[str] = mapped_column(String(64), nullable=False)
    page_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    page_thumbnail: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    page_access_token: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    user_access_token: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    default_published: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_upload_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    app_config: Mapped[Optional["FacebookAppConfig"]] = relationship(
        "FacebookAppConfig", back_populates="pages"
    )
    uploads: Mapped[List["VideoFacebookUpload"]] = relationship(
        "VideoFacebookUpload", back_populates="facebook_page"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "page_id", name="uq_user_facebook_page"),
    )


class VideoFacebookUpload(Base):
    """Track videos uploaded to Facebook Pages."""

    __tablename__ = "video_facebook_uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), nullable=False)
    facebook_page_id: Mapped[int] = mapped_column(ForeignKey("facebook_pages.id"), nullable=False)
    platform_post_id: Mapped[str] = mapped_column(String(64), nullable=False)
    post_url: Mapped[str] = mapped_column(String(512), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    video: Mapped["Video"] = relationship("Video", back_populates="facebook_uploads")
    facebook_page: Mapped["FacebookPage"] = relationship(
        "FacebookPage", back_populates="uploads"
    )

    __table_args__ = (
        UniqueConstraint("video_id", "facebook_page_id", name="uq_video_fb_page"),
    )


class ThreadsAccount(Base):
    """Connected Threads profile (multi-account via separate OAuth)."""

    __tablename__ = "threads_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    app_config_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("facebook_app_config.id"), nullable=True
    )
    label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    threads_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    profile_picture: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    access_token: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    voice_locale: Mapped[str] = mapped_column(String(8), default="id")  # id | us
    voice_style: Mapped[str] = mapped_column(String(32), default="genz")  # genz | millennial | us_slang
    niche: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_post_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    app_config: Mapped[Optional["FacebookAppConfig"]] = relationship("FacebookAppConfig")
    uploads: Mapped[List["VideoThreadsPost"]] = relationship(
        "VideoThreadsPost", back_populates="threads_account"
    )
    autopost: Mapped[Optional["ThreadsAutoPostConfig"]] = relationship(
        "ThreadsAutoPostConfig", back_populates="threads_account", uselist=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "threads_user_id", name="uq_user_threads_account"),
    )


class ThreadsAutoPostConfig(Base):
    """Auto-post schedule + AI voice per Threads account."""

    __tablename__ = "threads_autopost_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    threads_account_id: Mapped[int] = mapped_column(
        ForeignKey("threads_accounts.id"), nullable=False, unique=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_hours: Mapped[float] = mapped_column(Float, default=4.0)
    posts_per_day: Mapped[int] = mapped_column(Integer, default=6)
    posts_today: Mapped[int] = mapped_column(Integer, default=0)
    usage_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    post_video: Mapped[bool] = mapped_column(Boolean, default=True)
    profile_id: Mapped[Optional[int]] = mapped_column(ForeignKey("profiles.id"), nullable=True)
    topic_seed: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    threads_account: Mapped["ThreadsAccount"] = relationship(
        "ThreadsAccount", back_populates="autopost"
    )


class VideoThreadsPost(Base):
    """Track videos/text posts published to Threads."""

    __tablename__ = "video_threads_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[Optional[int]] = mapped_column(ForeignKey("videos.id"), nullable=True)
    threads_account_id: Mapped[int] = mapped_column(ForeignKey("threads_accounts.id"), nullable=False)
    platform_post_id: Mapped[str] = mapped_column(String(64), nullable=False)
    post_url: Mapped[str] = mapped_column(String(512), nullable=False)
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    topic_tag: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    media_type: Mapped[str] = mapped_column(String(16), default="TEXT")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    video: Mapped[Optional["Video"]] = relationship("Video", back_populates="threads_uploads")
    threads_account: Mapped["ThreadsAccount"] = relationship(
        "ThreadsAccount", back_populates="uploads"
    )

    __table_args__ = (
        UniqueConstraint("video_id", "threads_account_id", name="uq_video_threads_account"),
    )


class AIProviderConfig(Base):
    """AI API keys (OpenAI, Gemini) with usage tracking and backup failover."""

    __tablename__ = "ai_provider_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    label: Mapped[str] = mapped_column(String(128), default="Primary AI")
    provider: Mapped[str] = mapped_column(String(32), default="openai")  # openai | gemini
    api_key: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), default="gpt-4o-mini")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    tokens_today: Mapped[int] = mapped_column(Integer, default=0)
    requests_today: Mapped[int] = mapped_column(Integer, default=0)
    daily_token_limit: Mapped[int] = mapped_column(Integer, default=100_000)
    daily_request_limit: Mapped[int] = mapped_column(Integer, default=500)
    usage_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    rate_limited_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class MonitoringAccount(Base):
    """Connected social account for Social Monitoring (isolated from Multiupload)."""

    __tablename__ = "monitoring_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    handle: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    thumbnail: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    profile_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    access_token: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    refresh_token: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    oauth_app_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    followers: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    views: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    uploads_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    revenue: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    metrics_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("user_id", "platform", "external_id", name="uq_monitoring_user_platform_ext"),
    )


class MonitoringPlatformConfig(Base):
    """OAuth app credentials for monitoring-only platforms (e.g. X/Twitter)."""

    __tablename__ = "monitoring_platform_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    platform: Mapped[str] = mapped_column(String(20), default="twitter")
    client_id: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    client_secret: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    redirect_uri: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class TikTokShopConfig(Base):
    """TikTok Shop Partner API credentials (single row)."""

    __tablename__ = "tiktok_shop_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    app_key: Mapped[str] = mapped_column(String(128), nullable=False)
    app_secret: Mapped[str] = mapped_column(String(256), nullable=False)
    access_token: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    refresh_token: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    shop_cipher: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    shop_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    region: Mapped[str] = mapped_column(String(10), default="ID")
    base_url: Mapped[str] = mapped_column(
        String(256), default="https://open-api.tiktokglobalshop.com"
    )
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


def get_engine(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{db_path}", echo=False)


def _migrate_schema(engine) -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "videos" in tables:
        video_cols = {col["name"] for col in inspector.get_columns("videos")}
        additions = {
            "youtube_video_id": "VARCHAR(64)",
            "youtube_url": "VARCHAR(512)",
            "youtube_uploaded_at": "DATETIME",
        }
        with engine.begin() as conn:
            for name, col_type in additions.items():
                if name not in video_cols:
                    conn.execute(text(f"ALTER TABLE videos ADD COLUMN {name} {col_type}"))

    _migrate_oauth_app_columns(engine, tables)
    _migrate_legacy_youtube_config(engine, tables)
    _migrate_facebook_tables(engine, tables)
    _migrate_threads_tables(engine, tables)
    _migrate_ai_provider_tables(engine, tables)
    _migrate_users_tables(engine, tables)
    _migrate_monitoring_tables(engine, tables)


def _migrate_oauth_app_columns(engine, tables: set[str]) -> None:
    from sqlalchemy import inspect, text
    from sqlalchemy.orm import sessionmaker

    if "youtube_app_config" not in tables:
        return

    cols = {col["name"] for col in inspect(engine).get_columns("youtube_app_config")}
    additions = {
        "label": "VARCHAR(128) DEFAULT 'Primary OAuth App'",
        "priority": "INTEGER DEFAULT 100",
        "is_active": "BOOLEAN DEFAULT 1",
        "grants_today": "INTEGER DEFAULT 0",
        "refreshes_today": "INTEGER DEFAULT 0",
        "uploads_today": "INTEGER DEFAULT 0",
        "daily_grant_limit": "INTEGER DEFAULT 100",
        "daily_refresh_limit": "INTEGER DEFAULT 5000",
        "minute_grant_limit": "INTEGER DEFAULT 18",
        "token_calls_window": "TEXT",
        "usage_date": "VARCHAR(10)",
        "rate_limited_until": "DATETIME",
        "last_error": "TEXT",
    }
    with engine.begin() as conn:
        for name, col_type in additions.items():
            if name not in cols:
                conn.execute(text(f"ALTER TABLE youtube_app_config ADD COLUMN {name} {col_type}"))

    if "youtube_channels" in tables:
        ch_cols = {col["name"] for col in inspect(engine).get_columns("youtube_channels")}
        if "oauth_app_id" not in ch_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE youtube_channels ADD COLUMN oauth_app_id INTEGER"))

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        default_app = session.query(YouTubeAppConfig).order_by(YouTubeAppConfig.id.asc()).first()
        if default_app:
            orphans = session.query(YouTubeChannel).filter(YouTubeChannel.oauth_app_id.is_(None)).all()
            for ch in orphans:
                ch.oauth_app_id = default_app.id
            session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def _migrate_legacy_youtube_config(engine, tables: set[str]) -> None:
    """Migrate single-row youtube_config → youtube_app_config + youtube_channels."""
    from sqlalchemy.orm import sessionmaker

    if "youtube_config" not in tables:
        return

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        from sqlalchemy import text

        rows = session.execute(text("SELECT * FROM youtube_config WHERE id = 1")).mappings().all()
        if not rows:
            return
        old = rows[0]

        app_cfg = session.query(YouTubeAppConfig).filter_by(id=1).first()
        if not app_cfg and (old.get("client_id") or old.get("client_secret")):
            app_cfg = YouTubeAppConfig(
                id=1,
                client_id=old.get("client_id") or "",
                client_secret=old.get("client_secret") or "",
                redirect_uri=old.get("redirect_uri"),
            )
            session.add(app_cfg)

        if old.get("refresh_token") and old.get("channel_id"):
            existing = (
                session.query(YouTubeChannel)
                .filter_by(channel_id=old.get("channel_id"))
                .first()
            )
            if not existing:
                session.add(
                    YouTubeChannel(
                        label=old.get("channel_title") or "Channel 1",
                        refresh_token=old.get("refresh_token"),
                        access_token=old.get("access_token"),
                        token_expires_at=old.get("token_expires_at"),
                        channel_id=old.get("channel_id"),
                        channel_title=old.get("channel_title"),
                        channel_thumbnail=old.get("channel_thumbnail"),
                        default_privacy=old.get("default_privacy") or "private",
                        default_category=old.get("default_category") or "22",
                        is_active=bool(old.get("is_active", True)),
                        last_upload_at=old.get("last_upload_at"),
                    )
                )

        session.commit()

        videos = session.query(Video).filter(Video.youtube_video_id.isnot(None)).all()
        channel = session.query(YouTubeChannel).first()
        if channel and videos:
            for video in videos:
                exists = (
                    session.query(VideoYouTubeUpload)
                    .filter_by(video_id=video.id, youtube_channel_id=channel.id)
                    .first()
                )
                if not exists and video.youtube_video_id and video.youtube_url:
                    session.add(
                        VideoYouTubeUpload(
                            video_id=video.id,
                            youtube_channel_id=channel.id,
                            youtube_video_id=video.youtube_video_id,
                            youtube_url=video.youtube_url,
                            uploaded_at=video.youtube_uploaded_at or datetime.utcnow(),
                        )
                    )
            session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def _migrate_threads_tables(engine, tables: set[str]) -> None:
    needed = {"threads_accounts", "threads_autopost_config", "video_threads_posts"}
    if needed.issubset(tables):
        return
    Base.metadata.create_all(
        engine,
        tables=[
            ThreadsAccount.__table__,
            ThreadsAutoPostConfig.__table__,
            VideoThreadsPost.__table__,
        ],
    )


def _migrate_ai_provider_tables(engine, tables: set[str]) -> None:
    if "ai_provider_config" in tables:
        return
    Base.metadata.create_all(engine, tables=[AIProviderConfig.__table__])


def _migrate_facebook_tables(engine, tables: set[str]) -> None:
    """Ensure facebook tables exist on older databases."""
    if "facebook_app_config" in tables and "facebook_pages" in tables:
        return
    Base.metadata.create_all(
        engine,
        tables=[
            FacebookAppConfig.__table__,
            FacebookPage.__table__,
            VideoFacebookUpload.__table__,
        ],
    )


def _migrate_monitoring_tables(engine, tables: set[str]) -> None:
    needed = {"monitoring_accounts", "monitoring_platform_config"}
    if needed.issubset(tables):
        return
    Base.metadata.create_all(
        engine,
        tables=[
            MonitoringAccount.__table__,
            MonitoringPlatformConfig.__table__,
        ],
    )


def _migrate_users_tables(engine, tables: set[str]) -> None:
    from sqlalchemy import inspect, text
    from sqlalchemy.orm import sessionmaker

    if "users" not in tables:
        Base.metadata.create_all(engine, tables=[User.__table__])

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    def _add_col(table: str, col: str, col_type: str) -> None:
        if table not in tables:
            return
        cols = {c["name"] for c in inspector.get_columns(table)}
        if col not in cols:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))

    _add_col("profiles", "user_id", "INTEGER")
    _add_col("youtube_channels", "user_id", "INTEGER")
    _add_col("facebook_pages", "user_id", "INTEGER")
    _add_col("threads_accounts", "user_id", "INTEGER")
    _add_col("ai_provider_config", "user_id", "INTEGER")
    _add_col("users", "expires_at", "DATETIME")
    _add_col("users", "plan", "VARCHAR(32) DEFAULT 'trial'")
    _add_col("users", "payment_ref", "VARCHAR(128)")

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        from ..users import ensure_admin_user
        from ..auth import AuthStore
        from pathlib import Path as P

        base_dir = P(__file__).resolve().parent.parent.parent
        auth_store = AuthStore(base_dir / "data" / "auth.json")
        admin = ensure_admin_user(session, auth_store)

        session.query(Profile).filter(Profile.user_id.is_(None)).update(
            {Profile.user_id: admin.id}, synchronize_session=False
        )
        session.query(YouTubeChannel).filter(YouTubeChannel.user_id.is_(None)).update(
            {YouTubeChannel.user_id: admin.id}, synchronize_session=False
        )
        session.query(FacebookPage).filter(FacebookPage.user_id.is_(None)).update(
            {FacebookPage.user_id: admin.id}, synchronize_session=False
        )
        session.query(ThreadsAccount).filter(ThreadsAccount.user_id.is_(None)).update(
            {ThreadsAccount.user_id: admin.id}, synchronize_session=False
        )
        session.query(AIProviderConfig).filter(AIProviderConfig.user_id.is_(None)).update(
            {AIProviderConfig.user_id: admin.id}, synchronize_session=False
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    if "profiles" in tables:
        indexes = []
        with engine.connect() as conn:
            for row in conn.execute(text("PRAGMA index_list('profiles')")).mappings():
                indexes.append(row["name"])
        if "uq_user_platform_username" not in indexes:
            with engine.begin() as conn:
                conn.execute(text("PRAGMA foreign_keys=OFF"))
                conn.execute(
                    text(
                        """
                        CREATE TABLE profiles_new (
                            id INTEGER PRIMARY KEY,
                            user_id INTEGER,
                            platform VARCHAR(20) NOT NULL,
                            username VARCHAR(255) NOT NULL,
                            url VARCHAR(512) NOT NULL,
                            video_count INTEGER DEFAULT 0,
                            last_scanned_at DATETIME,
                            created_at DATETIME,
                            FOREIGN KEY(user_id) REFERENCES users(id),
                            UNIQUE(user_id, platform, username)
                        )
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO profiles_new
                            (id, user_id, platform, username, url, video_count, last_scanned_at, created_at)
                        SELECT id, user_id, platform, username, url, video_count, last_scanned_at, created_at
                        FROM profiles
                        """
                    )
                )
                conn.execute(text("DROP TABLE profiles"))
                conn.execute(text("ALTER TABLE profiles_new RENAME TO profiles"))
                conn.execute(text("PRAGMA foreign_keys=ON"))


def init_db(db_path: Path):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    _migrate_schema(engine)
    return sessionmaker(bind=engine)()
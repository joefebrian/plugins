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


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # tiktok | instagram
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    video_count: Mapped[int] = mapped_column(Integer, default=0)
    last_scanned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    videos: Mapped[List["Video"]] = relationship("Video", back_populates="profile")

    __table_args__ = (UniqueConstraint("platform", "username", name="uq_platform_username"),)


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
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    profile: Mapped["Profile"] = relationship("Profile", back_populates="videos")

    __table_args__ = (
        UniqueConstraint("profile_id", "platform_video_id", name="uq_profile_video"),
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
    region: Mapped[str] = mapped_column(String(10), default="ID")  # ID, US, SG, UK, etc.
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


def init_db(db_path: Path):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()
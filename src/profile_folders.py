"""User profile folders for sidebar organization."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from .db.models import Profile, ProfileFolder


def list_folders(session: Session, user_id: int) -> list[ProfileFolder]:
    return (
        session.query(ProfileFolder)
        .filter_by(user_id=user_id)
        .order_by(ProfileFolder.sort_order.asc(), ProfileFolder.name.asc(), ProfileFolder.id.asc())
        .all()
    )


def folder_to_dict(folder: ProfileFolder, profile_count: int | None = None) -> dict:
    return {
        "id": folder.id,
        "name": folder.name,
        "sort_order": folder.sort_order,
        "profile_count": profile_count,
        "created_at": folder.created_at.isoformat() if folder.created_at else None,
    }


def list_folders_with_counts(session: Session, user_id: int) -> list[dict]:
    folders = list_folders(session, user_id)
    counts = dict(
        session.query(Profile.folder_id, func.count(Profile.id))
        .filter_by(user_id=user_id)
        .filter(Profile.folder_id.isnot(None))
        .group_by(Profile.folder_id)
        .all()
    )
    return [folder_to_dict(f, counts.get(f.id, 0)) for f in folders]


def get_folder(session: Session, folder_id: int, user_id: int) -> ProfileFolder | None:
    return session.query(ProfileFolder).filter_by(id=folder_id, user_id=user_id).first()


def create_folder(session: Session, user_id: int, name: str) -> ProfileFolder:
    name = name.strip()
    if not name:
        raise ValueError("Nama folder wajib diisi")
    if len(name) > 64:
        raise ValueError("Nama folder maksimal 64 karakter")

    existing = session.query(ProfileFolder).filter_by(user_id=user_id, name=name).first()
    if existing:
        raise ValueError(f"Folder '{name}' sudah ada")

    max_order = (
        session.query(func.max(ProfileFolder.sort_order))
        .filter_by(user_id=user_id)
        .scalar()
    ) or 0

    folder = ProfileFolder(user_id=user_id, name=name, sort_order=max_order + 1)
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return folder


def rename_folder(session: Session, user_id: int, folder_id: int, name: str) -> ProfileFolder:
    folder = get_folder(session, folder_id, user_id)
    if not folder:
        raise ValueError("Folder tidak ditemukan")

    name = name.strip()
    if not name:
        raise ValueError("Nama folder wajib diisi")

    clash = (
        session.query(ProfileFolder)
        .filter_by(user_id=user_id, name=name)
        .filter(ProfileFolder.id != folder_id)
        .first()
    )
    if clash:
        raise ValueError(f"Folder '{name}' sudah ada")

    folder.name = name
    session.commit()
    session.refresh(folder)
    return folder


def delete_folder(session: Session, user_id: int, folder_id: int) -> dict:
    folder = get_folder(session, folder_id, user_id)
    if not folder:
        raise ValueError("Folder tidak ditemukan")

    moved = (
        session.query(Profile)
        .filter_by(user_id=user_id, folder_id=folder_id)
        .update({Profile.folder_id: None}, synchronize_session=False)
    )
    session.delete(folder)
    session.commit()
    return {"ok": True, "profiles_moved": moved}


def move_profile_to_folder(
    session: Session,
    user_id: int,
    profile_id: int,
    folder_id: int | None,
) -> Profile:
    profile = session.query(Profile).filter_by(id=profile_id, user_id=user_id).first()
    if not profile:
        raise ValueError("Profil tidak ditemukan")

    if folder_id is not None:
        folder = get_folder(session, folder_id, user_id)
        if not folder:
            raise ValueError("Folder tidak ditemukan")

    profile.folder_id = folder_id
    session.commit()
    session.refresh(profile)
    return profile
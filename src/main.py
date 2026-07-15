#!/usr/bin/env python3
"""Affiliate Video Tool - CLI for TikTok/Instagram video tracking & download."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from .db.models import init_db
from .gmv.importer import import_gmv_csv
from .db.models import User
from .services import download_videos, get_hero_videos, list_videos, sync_profile_videos


def _cli_user_id(session) -> int:
    admin = session.query(User).filter_by(role="admin").order_by(User.id.asc()).first()
    if not admin:
        raise click.ClickException("Admin user belum ada. Jalankan web server sekali untuk bootstrap DB.")
    return admin.id

console = Console()

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "affiliate.db"
DEFAULT_DOWNLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "downloads"


def _fmt_num(n) -> str:
    if n is None:
        return "-"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def _fmt_money(n) -> str:
    if n is None:
        return "-"
    return f"Rp {n:,.0f}"


@click.group()
@click.option("--db", default=str(DEFAULT_DB), help="Path ke database SQLite")
@click.pass_context
def cli(ctx, db):
    """Affiliate Video Tool - scan, track, download & rank hero videos."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = Path(db)
    ctx.obj["session"] = init_db(ctx.obj["db_path"])


@cli.command()
@click.argument("platform", type=click.Choice(["tiktok", "instagram", "kuaishou", "rednote"]))
@click.argument("username")
@click.option("--cookies", default=None, help="Path ke cookies.txt (wajib untuk Instagram private)")
@click.pass_context
def scan(ctx, platform, username, cookies):
    """Scan profil & hitung jumlah video (sync ke database)."""
    session = ctx.obj["session"]
    console.print(f"[bold]Scanning[/bold] {platform}/@{username}...")

    try:
        result = sync_profile_videos(session, platform, username, cookies, user_id=_cli_user_id(session))
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    console.print(f"\n[green]Selesai![/green]")
    console.print(f"  Total video  : [bold]{result['total']}[/bold]")
    console.print(f"  Baru         : {result['new']}")
    console.print(f"  Di-update    : {result['updated']}")
    console.print(f"  Sudah download: [cyan]{result['downloaded']}[/cyan]")
    console.print(f"  Belum download: [yellow]{result['pending']}[/yellow]")


@cli.command("list")
@click.argument("platform", type=click.Choice(["tiktok", "instagram", "kuaishou", "rednote"]))
@click.argument("username")
@click.option("--status", type=click.Choice(["all", "downloaded", "pending"]), default="all")
@click.option("--sort", "sort_by", type=click.Choice(["gmv", "views", "likes"]), default="gmv")
@click.pass_context
def list_cmd(ctx, platform, username, status, sort_by):
    """Tampilkan daftar video dengan status download."""
    session = ctx.obj["session"]
    filter_status = None if status == "all" else status
    uid = _cli_user_id(session)
    videos = list_videos(session, platform, username, filter_status, sort_by, user_id=uid)

    if not videos:
        console.print("[yellow]Belum ada data. Jalankan 'scan' dulu.[/yellow]")
        return

    table = Table(title=f"@{username} ({platform}) - {len(videos)} video")
    table.add_column("Status", style="bold")
    table.add_column("Video ID")
    table.add_column("Views")
    table.add_column("Likes")
    table.add_column("GMV")
    table.add_column("Komisi")
    table.add_column("Title", max_width=40)

    for v in videos:
        status_icon = "[green]✓ DL[/green]" if v.is_downloaded else "[yellow]○ Pending[/yellow]"
        table.add_row(
            status_icon,
            v.platform_video_id[:16],
            _fmt_num(v.views),
            _fmt_num(v.likes),
            _fmt_money(v.gmv),
            _fmt_money(v.commission),
            (v.title or "-")[:40],
        )

    console.print(table)

    downloaded = sum(1 for v in videos if v.is_downloaded)
    console.print(f"\nDownloaded: {downloaded} | Pending: {len(videos) - downloaded}")


@cli.command()
@click.argument("platform", type=click.Choice(["tiktok", "instagram", "kuaishou", "rednote"]))
@click.argument("username")
@click.option("--limit", default=10, type=int, help="Max video yang di-download (default: 10)")
@click.option("--all", "download_all", is_flag=True, help="Download ulang termasuk yang sudah ada")
@click.option("--video-id", multiple=True, help="Download video spesifik (bisa multiple)")
@click.option("--quality", type=click.Choice(["best", "1080", "720"]), default="best", help="Kualitas video")
@click.option("--cookies", default=None)
@click.option("--dir", "download_dir", default=str(DEFAULT_DOWNLOAD_DIR))
@click.pass_context
def download(ctx, platform, username, limit, download_all, video_id, cookies, download_dir, quality):
    """Download video yang belum di-download."""
    session = ctx.obj["session"]
    console.print(f"[bold]Downloading[/bold] {platform}/@{username}...")

    try:
        result = download_videos(
            session,
            platform,
            username,
            Path(download_dir),
            cookies_file=cookies,
            limit=limit,
            only_pending=not download_all,
            video_ids=list(video_id) if video_id else None,
            quality=quality,
            user_id=_cli_user_id(session),
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    console.print(f"[green]Berhasil:[/green] {result['success']}")
    console.print(f"[yellow]Skip (sudah ada):[/yellow] {result['skipped']}")
    if result["failed"]:
        console.print(f"[red]Gagal:[/red] {result['failed']}")
        for err in result.get("errors", []):
            console.print(f"  [red]{err}[/red]")


@cli.command()
@click.argument("csv_path", type=click.Path(exists=True))
@click.pass_context
def import_gmv(ctx, csv_path):
    """Import data GMV/komisi dari export TikTok Shop Affiliate (CSV)."""
    session = ctx.obj["session"]
    try:
        result = import_gmv_csv(session, Path(csv_path))
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    console.print(f"[green]Updated:[/green] {result['updated']} video")
    if result["unmatched"]:
        console.print(f"[yellow]Tidak cocok:[/yellow] {result['unmatched']} baris (belum di-scan?)")


@cli.command()
@click.argument("platform", type=click.Choice(["tiktok", "instagram", "kuaishou", "rednote"]))
@click.argument("username")
@click.option("--top", default=10, help="Jumlah hero video")
@click.pass_context
def heroes(ctx, platform, username, top):
    """Tampilkan hero video (ranking GMV tertinggi) untuk cross-platform test."""
    session = ctx.obj["session"]
    videos = get_hero_videos(session, platform, username, top)

    if not videos:
        console.print("[yellow]Belum ada data. Scan profil dulu.[/yellow]")
        return

    has_gmv = any(v.gmv for v in videos)

    table = Table(title=f"Hero Videos - @{username} ({'by GMV' if has_gmv else 'by engagement'})")
    table.add_column("#", style="bold")
    table.add_column("Video ID")
    table.add_column("GMV", style="green")
    table.add_column("Komisi")
    table.add_column("Views")
    table.add_column("Likes")
    table.add_column("Downloaded")
    table.add_column("URL", max_width=30)

    for i, v in enumerate(videos, 1):
        table.add_row(
            str(i),
            v.platform_video_id[:16],
            _fmt_money(v.gmv),
            _fmt_money(v.commission),
            _fmt_num(v.views),
            _fmt_num(v.likes),
            "✓" if v.is_downloaded else "○",
            v.url[:50],
        )

    console.print(table)
    if not has_gmv:
        console.print(
            "\n[yellow]Tip:[/yellow] Import CSV GMV dari TikTok Shop Affiliate "
            "untuk ranking yang lebih akurat: [bold]import-gmv data.csv[/bold]"
        )


if __name__ == "__main__":
    cli()
"""Safe yt-dlp option helpers (impersonation, error formatting)."""

from __future__ import annotations

from typing import Any


def format_ytdlp_error(err: BaseException | None, fallback: str = "unknown error") -> str:
    """Human-readable error; never return empty string (AssertionError often has none)."""
    if err is None:
        return fallback
    text = str(err).strip()
    if text:
        return text
    return f"{type(err).__name__}: {fallback}"


def apply_chrome_impersonate(opts: dict[str, Any]) -> dict[str, Any]:
    """
    Enable curl_cffi Chrome impersonation when yt-dlp + curl_cffi support it.

    yt-dlp 2025+ requires ImpersonateTarget, not the bare string \"chrome\".
    A plain string raises AssertionError('') and breaks all TikTok scans/downloads.
    """
    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        return opts

    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget

        opts["impersonate"] = ImpersonateTarget.from_str("chrome")
    except Exception:
        # Older yt-dlp: string may work; if not, omit impersonate entirely
        try:
            opts["impersonate"] = "chrome"
            # Validate immediately so we never leave a broken value
            import yt_dlp

            with yt_dlp.YoutubeDL({"quiet": True, "impersonate": opts["impersonate"]}):
                pass
        except Exception:
            opts.pop("impersonate", None)
    return opts

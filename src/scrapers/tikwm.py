"""Fetch TikTok video download URLs via tikwm.com API + resilient CDN fetch."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


TIKWM_API = "https://www.tikwm.com/api/"
TIKWM_API_MIRRORS = (
    "https://www.tikwm.com/api/",
    "https://tikwm.com/api/",
)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

CDN_HEADERS_BASE = {
    "User-Agent": BROWSER_UA,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
}


def _cdn_headers(referer: str = "https://www.tiktok.com/") -> dict:
    h = dict(CDN_HEADERS_BASE)
    h["Referer"] = referer
    # Origin helps some TikTok CDNs; omit for non-tiktok hosts
    if "tiktok" in (referer or "").lower() or "tikwm" in (referer or "").lower():
        h["Origin"] = "https://www.tiktok.com"
    return h


def get_tiktok_video_url(page_url: str, quality: str = "best") -> dict:
    """
    Return dict with download_url, candidates[], title, size, is_hd.
    quality: best | 1080 | 720
    """
    params = urllib.parse.urlencode({"url": page_url, "hd": "1"})
    last_err: Exception | None = None
    payload = None

    for base in TIKWM_API_MIRRORS:
        req = urllib.request.Request(
            f"{base}?{params}",
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.tikwm.com/",
                "Origin": "https://www.tikwm.com",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                payload = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            last_err = ValueError(f"TikWM API error HTTP {e.code}")
        except Exception as e:
            last_err = ValueError(f"TikWM API gagal: {e}")

    if payload is None:
        raise last_err or ValueError("TikWM API gagal")

    if payload.get("code") != 0:
        raise ValueError(payload.get("msg") or "Gagal ambil URL video dari TikTok (TikWM)")

    data = payload.get("data") or {}
    candidates: list[str] = []
    if quality == "720":
        ordered_keys = ("play", "hdplay", "wmplay")
    else:
        ordered_keys = ("hdplay", "play", "wmplay")

    for key in ordered_keys:
        u = data.get(key)
        if u and isinstance(u, str) and u not in candidates:
            candidates.append(u)

    # Some responses nest under data.play_addr / similar — ignore if not str

    if not candidates:
        raise ValueError("URL video tidak ditemukan di TikWM. Coba lagi nanti.")

    is_hd = bool(data.get("hdplay")) and quality != "720"
    size = data.get("hd_size") if is_hd else data.get("size")

    return {
        "download_url": candidates[0],
        "candidates": candidates,
        "title": data.get("title"),
        "size": size,
        "is_hd": is_hd,
        "duration": data.get("duration"),
    }


def _read_body(url: str, referer: str) -> bytes:
    """Fetch CDN bytes via curl_cffi (Chrome impersonation) or urllib."""
    headers = _cdn_headers(referer)

    # Prefer curl_cffi — far more reliable from cloud IPs against TikTok CDN
    try:
        from curl_cffi import requests as creq

        r = creq.get(
            url,
            headers=headers,
            impersonate="chrome131",
            timeout=180,
            allow_redirects=True,
        )
        if r.status_code in (401, 403):
            raise ValueError(f"HTTP Error {r.status_code}: Forbidden")
        if r.status_code >= 400:
            raise ValueError(f"HTTP Error {r.status_code}")
        return r.content
    except ImportError:
        pass
    except ValueError:
        raise
    except Exception as e:
        # fall through to urllib
        if "403" in str(e) or "Forbidden" in str(e):
            raise ValueError(f"HTTP Error 403: Forbidden") from e

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise ValueError(f"HTTP Error {e.code}: {e.reason or 'Forbidden'}") from e


def download_file(
    url: str,
    dest: str,
    referer: str = "https://www.tiktok.com/",
    *,
    candidates: Optional[list[str]] = None,
) -> None:
    """Download remote file. On 403, try alternate candidates / referers."""
    urls = list(candidates or [])
    if url and url not in urls:
        urls.insert(0, url)
    if not urls:
        raise ValueError("Tidak ada URL download")

    referers = [
        referer,
        "https://www.tiktok.com/",
        "https://www.tikwm.com/",
        "https://www.tiktok.com/foryou",
        "https://www.tiktok.com/explore",
    ]

    last_err: Exception | None = None
    for u in urls:
        for ref in referers:
            try:
                data = _read_body(u, ref)
                if len(data) < 50_000:
                    raise ValueError("File terlalu kecil — bukan video valid")
                if data[:4] == b"ID3\x03" or data[:3] == b"ID3":
                    raise ValueError("Yang terdownload audio MP3, bukan video")
                with open(dest, "wb") as f:
                    f.write(data)
                return
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                if "404" in msg or "410" in msg:
                    break
                continue

    err_text = str(last_err).strip() if last_err else ""
    if not err_text and last_err is not None:
        err_text = type(last_err).__name__
    raise ValueError(
        f"Gagal mengambil video: {err_text or 'HTTP Error 403: Forbidden'}. "
        "TikTok memblok IP server — pastikan cookies TikTok ter-upload di Settings, "
        "atau download dari local PC."
    )


def open_cdn_stream(url: str, referer: str = "https://www.tiktok.com/"):
    """
    Open a readable stream for StreamingResponse.
    Uses curl_cffi when available (better on Railway/cloud).
    """
    headers = _cdn_headers(referer)
    last_err: Exception | None = None

    try:
        from curl_cffi import requests as creq

        for ref in (referer, "https://www.tiktok.com/", "https://www.tikwm.com/"):
            try:
                h = _cdn_headers(ref)
                r = creq.get(
                    url,
                    headers=h,
                    impersonate="chrome131",
                    timeout=300,
                    stream=True,
                    allow_redirects=True,
                )
                if r.status_code in (401, 403):
                    last_err = ValueError(f"HTTP Error {r.status_code}: Forbidden")
                    continue
                if r.status_code >= 400:
                    last_err = ValueError(f"HTTP Error {r.status_code}")
                    continue
                return _CurlStreamAdapter(r)
            except ValueError as e:
                last_err = e
                continue
            except Exception as e:
                last_err = e
                continue
    except ImportError:
        pass

    for ref in (referer, "https://www.tiktok.com/", "https://www.tikwm.com/"):
        try:
            req = urllib.request.Request(url, headers=_cdn_headers(ref))
            return urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as e:
            last_err = ValueError(f"HTTP Error {e.code}: {e.reason or 'Forbidden'}")
            if e.code not in (401, 403):
                break
            continue
        except Exception as e:
            last_err = e
            continue

    raise ValueError(
        f"Gagal membuka stream video: {last_err or 'HTTP Error 403: Forbidden'}"
    )


class _CurlStreamAdapter:
    """Minimal file-like adapter over curl_cffi streaming response."""

    def __init__(self, response):
        self._resp = response
        self._iter = response.iter_content(chunk_size=65536)
        self._buf = b""

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            parts = [self._buf]
            self._buf = b""
            for chunk in self._iter:
                if chunk:
                    parts.append(chunk)
            return b"".join(parts)
        while len(self._buf) < n:
            try:
                chunk = next(self._iter)
            except StopIteration:
                break
            if not chunk:
                break
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def close(self) -> None:
        try:
            self._resp.close()
        except Exception:
            pass

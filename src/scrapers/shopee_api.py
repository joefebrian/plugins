"""Shopee shop API client — product videos from seller profiles."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import quote

from .base import VideoInfo, _safe_int

SHOPEE_ORIGIN = "https://shopee.co.id"
SHOP_BASE_URI = "/api/v4/shop/get_shop_base"
SHOP_SEO_URI = "/api/v4/shop/get_shop_seo"
SEARCH_ITEMS_URI = "/api/v4/shop/search_items"
RCMD_ITEMS_URI = "/api/v4/shop/rcmd_items"
ACCOUNT_INFO_URI = "/api/v4/account/basic/get_account_info"
ITEM_GET_URI = "/api/v4/item/get"

_SESSION_COOKIE_NAMES = ("SPC_EC", "SPC_ST")


class ShopeeAPIError(ValueError):
    pass


def cookies_dict_from_file(
    path: str | None,
    *,
    domains: tuple[str, ...] = ("shopee.co.id", "shopee.com"),
) -> dict[str, str]:
    if not path:
        return {}
    file_path = Path(path)
    if not file_path.exists():
        return {}
    pairs: dict[str, str] = {}
    for line in file_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain = parts[0].lstrip(".").lower()
        if not any(d in domain for d in domains):
            continue
        name, value = parts[5], parts[6].strip()
        if name and value and name not in pairs:
            pairs[name] = value
    return pairs


def cookies_header_from_file(path: str | None) -> str:
    pairs = cookies_dict_from_file(path)
    return "; ".join(f"{k}={v}" for k, v in pairs.items())


def _base_headers(
    cookie_header: str,
    *,
    referer: str = f"{SHOPEE_ORIGIN}/",
) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": SHOPEE_ORIGIN,
        "Referer": referer,
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "x-api-source": "pc",
        "x-shopee-language": "id",
        "x-requested-with": "XMLHttpRequest",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers


def _http_json(method: str, url: str, *, headers: dict[str, str]) -> dict:
    try:
        from curl_cffi import requests as curl_requests

        resp = curl_requests.request(
            method,
            url,
            headers=headers,
            impersonate="chrome131",
            timeout=30,
        )
        data = resp.json()
        if not isinstance(data, dict):
            raise ShopeeAPIError(f"Shopee API respons tidak valid (HTTP {resp.status_code})")
        return data
    except ImportError:
        import urllib.request

        req = urllib.request.Request(url, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as raw:
            data = json.loads(raw.read().decode())
        if not isinstance(data, dict):
            raise ShopeeAPIError("Shopee API respons tidak valid")
        return data
    except Exception as exc:
        if isinstance(exc, ShopeeAPIError):
            raise
        raise ShopeeAPIError(f"Request Shopee gagal: {exc}") from exc


def _ensure_ok(data: dict, *, action: str) -> dict:
    error = data.get("error")
    if error in (0, None):
        payload = data.get("data")
        return payload if isinstance(payload, dict) else {}
    if error == 90309999:
        raise ShopeeAPIError(
            f"Shopee {action}: akses diblokir anti-bot (90309999). "
            "Login di shopee.co.id, export cookies baru (harus ada SPC_EC), lalu upload di menu Cookies."
        )
    if error == 19:
        raise ShopeeAPIError(
            f"Shopee {action}: sesi belum login. "
            "Login di shopee.co.id, buka halaman toko, export cookies, upload di menu Cookies."
        )
    raise ShopeeAPIError(f"Shopee {action} gagal (error {error})")


def check_shopee_login(cookies_file: str | None) -> dict:
    cookies = cookies_dict_from_file(cookies_file)
    has_session = any(name in cookies for name in _SESSION_COOKIE_NAMES)
    if not cookies:
        return {
            "ok": False,
            "guest": True,
            "message": "Cookies Shopee belum di-upload",
            "has_session": False,
        }

    cookie_header = cookies_header_from_file(cookies_file)
    try:
        data = _http_json(
            "GET",
            f"{SHOPEE_ORIGIN}{ACCOUNT_INFO_URI}",
            headers=_base_headers(cookie_header),
        )
    except ShopeeAPIError as exc:
        return {
            "ok": has_session,
            "guest": not has_session,
            "message": str(exc) if has_session else "Cookies Shopee ada tapi sesi login belum terdeteksi (tanpa SPC_EC)",
            "has_session": has_session,
        }

    if data.get("error") == 19 or not has_session:
        return {
            "ok": False,
            "guest": True,
            "message": (
                "Cookies Shopee terdeteksi sebagai tamu (belum login). "
                "Login di shopee.co.id, buka satu halaman toko, lalu export cookies lagi."
            ),
            "has_session": has_session,
        }

    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    return {
        "ok": True,
        "guest": False,
        "message": "Sesi Shopee aktif",
        "has_session": True,
        "username": payload.get("username"),
    }


def resolve_shop(username: str, *, cookies_file: str | None = None) -> dict:
    cookie_header = cookies_header_from_file(cookies_file)
    referer = f"{SHOPEE_ORIGIN}/{quote(username)}"
    data = _http_json(
        "GET",
        f"{SHOPEE_ORIGIN}{SHOP_BASE_URI}?username={quote(username)}",
        headers=_base_headers(cookie_header, referer=referer),
    )
    payload = _ensure_ok(data, action="resolve toko")
    shopid = payload.get("shopid")
    if not shopid:
        raise ShopeeAPIError(f"Toko Shopee tidak ditemukan: {username}")
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    return {
        "shopid": int(shopid),
        "userid": int(payload.get("userid") or account.get("userid") or 0),
        "username": str(account.get("username") or username),
        "name": str(payload.get("name") or account.get("username") or username),
        "item_count": _safe_int(payload.get("item_count")) or 0,
    }


def build_product_url(username: str, shopid: int, itemid: int) -> str:
    return f"{SHOPEE_ORIGIN}/{quote(username)}-i.{shopid}.{itemid}"


def _video_url_from_info(video_info: dict) -> str:
    default = video_info.get("default_format") if isinstance(video_info.get("default_format"), dict) else {}
    url = default.get("url")
    if isinstance(url, str) and url.startswith("http"):
        return url
    formats = video_info.get("formats") or []
    if isinstance(formats, list):
        for fmt in formats:
            if isinstance(fmt, dict):
                candidate = fmt.get("url")
                if isinstance(candidate, str) and candidate.startswith("http"):
                    return candidate
    path = video_info.get("video_id") or video_info.get("path")
    if isinstance(path, str) and path:
        if path.startswith("http"):
            return path
        return f"https://mms.vod.susercontent.com/{path.lstrip('/')}"
    return ""


def _item_basic(item: dict) -> dict:
    if isinstance(item.get("item_basic"), dict):
        return item["item_basic"]
    return item if isinstance(item, dict) else {}


def _video_infos_from_item(item: dict) -> list[dict]:
    basic = _item_basic(item)
    infos = basic.get("video_info_list") or item.get("video_info_list") or []
    return [info for info in infos if isinstance(info, dict)]


def item_to_video_info(
    item: dict,
    *,
    username: str,
    shopid: int,
) -> Optional[VideoInfo]:
    basic = _item_basic(item)
    itemid = basic.get("itemid") or item.get("itemid")
    if not itemid:
        return None

    video_infos = _video_infos_from_item(item)
    if not video_infos:
        return None

    video_info = video_infos[0]
    video_url = _video_url_from_info(video_info)
    if not video_url:
        return None

    vid = str(video_info.get("vid") or video_info.get("video_id") or f"{itemid}")
    platform_video_id = f"{shopid}_{itemid}_{vid}"

    title = basic.get("name") or item.get("name") or f"Shopee {itemid}"
    posted_at = None
    ts = basic.get("ctime") or item.get("ctime")
    if ts:
        try:
            value = int(ts)
            if value > 1_000_000_000_000:
                value //= 1000
            posted_at = datetime.utcfromtimestamp(value)
        except (TypeError, ValueError, OSError):
            posted_at = None

    rating = basic.get("item_rating") if isinstance(basic.get("item_rating"), dict) else {}
    rating_counts = rating.get("rating_count") if isinstance(rating.get("rating_count"), list) else []
    comments = _safe_int(rating_counts[0]) if rating_counts else _safe_int(basic.get("cmt_count"))

    return VideoInfo(
        platform_video_id=platform_video_id,
        url=build_product_url(username, shopid, int(itemid)),
        title=str(title),
        description=str(title),
        views=_safe_int(basic.get("historical_sold") or basic.get("sold")),
        likes=_safe_int(basic.get("liked_count")),
        comments=comments,
        posted_at=posted_at,
        # Store direct MP4 in description suffix for downloader fallback — use private attr via url only
    )


def _collect_items_from_payload(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    for key in ("items", "item_cards", "sections"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return [row for row in value if isinstance(row, dict)]
    return []


def fetch_shop_seo_items(shopid: int, *, cookies_file: str | None = None) -> list[dict]:
    cookie_header = cookies_header_from_file(cookies_file)
    data = _http_json(
        "GET",
        f"{SHOPEE_ORIGIN}{SHOP_SEO_URI}?shopid={shopid}",
        headers=_base_headers(cookie_header),
    )
    payload = _ensure_ok(data, action="ambil preview toko")
    return _collect_items_from_payload(payload)


def _fetch_search_items_page(
    shopid: int,
    *,
    offset: int,
    limit: int,
    cookies_file: str | None,
    username: str,
) -> list[dict]:
    cookie_header = cookies_header_from_file(cookies_file)
    referer = f"{SHOPEE_ORIGIN}/{quote(username)}"
    query = (
        f"limit={limit}&offset={offset}&shopid={shopid}"
        "&sort_by=pop&order=desc&use_case=1"
    )
    data = _http_json(
        "GET",
        f"{SHOPEE_ORIGIN}{SEARCH_ITEMS_URI}?{query}",
        headers=_base_headers(cookie_header, referer=referer),
    )
    if data.get("error") == 90309999:
        return []
    payload = _ensure_ok(data, action="scan produk toko")
    return _collect_items_from_payload(payload)


def _netscape_playwright_cookies(path: str | None) -> list[dict]:
    cookies: list[dict] = []
    if not path:
        return cookies
    for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 7 or "shopee" not in parts[0].lower():
            continue
        domain = parts[0][1:] if parts[0].startswith(".") else parts[0]
        cookies.append(
            {
                "name": parts[5],
                "value": parts[6].strip(),
                "domain": domain,
                "path": parts[2] or "/",
                "expires": int(parts[4]) if parts[4].isdigit() else -1,
                "httpOnly": parts[3].upper() == "TRUE",
                "secure": parts[1].upper() == "TRUE",
                "sameSite": "Lax",
            }
        )
    return cookies


def _playwright_fetch_items(
    username: str,
    shopid: int,
    *,
    cookies_file: str | None,
    max_pages: int = 20,
    page_size: int = 30,
) -> list[dict]:
    from playwright.sync_api import sync_playwright

    profile_url = f"{SHOPEE_ORIGIN}/{quote(username)}#product_list"
    collected: list[dict] = []
    seen_item_ids: set[int] = set()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="id-ID",
        )
        cookies = _netscape_playwright_cookies(cookies_file)
        if cookies:
            context.add_cookies(cookies)
        page = context.new_page()

        intercepted: list[dict] = []

        def on_response(response) -> None:
            url = response.url
            if "/api/v4/" not in url:
                return
            if not any(token in url for token in ("search_items", "rcmd_items", "recommend", "get_shop_seo")):
                return
            try:
                if "json" not in (response.headers.get("content-type") or ""):
                    return
                body = response.json()
                if body.get("error") not in (0, None):
                    return
                items = _collect_items_from_payload(body.get("data") or {})
                if items:
                    intercepted.extend(items)
            except Exception:
                return

        page.on("response", on_response)
        page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        for offset in range(0, max_pages * page_size, page_size):
            result = page.evaluate(
                """async ({shopid, offset, limit}) => {
                    const headers = {
                        'x-api-source': 'pc',
                        'x-shopee-language': 'id',
                        'x-requested-with': 'XMLHttpRequest',
                    };
                    const query = `limit=${limit}&offset=${offset}&shopid=${shopid}&sort_by=pop&order=desc&use_case=1`;
                    const resp = await fetch(`/api/v4/shop/search_items?${query}`, {
                        credentials: 'include',
                        headers,
                    });
                    const json = await resp.json();
                    const items = (json.data && (json.data.items || json.data.item_cards)) || [];
                    return {error: json.error, items};
                }""",
                {"shopid": shopid, "offset": offset, "limit": page_size},
            )
            if not isinstance(result, dict):
                break
            if result.get("error") == 90309999:
                break
            batch = result.get("items") or []
            if not batch:
                break
            for item in batch:
                if not isinstance(item, dict):
                    continue
                basic = _item_basic(item)
                itemid = basic.get("itemid") or item.get("itemid")
                if itemid and int(itemid) not in seen_item_ids:
                    seen_item_ids.add(int(itemid))
                    collected.append(item)
            if len(batch) < page_size:
                break
            page.wait_for_timeout(400)

        for _ in range(6):
            page.mouse.wheel(0, 2400)
            page.wait_for_timeout(1200)

        for item in intercepted:
            basic = _item_basic(item)
            itemid = basic.get("itemid") or item.get("itemid")
            if itemid and int(itemid) not in seen_item_ids:
                seen_item_ids.add(int(itemid))
                collected.append(item)

        browser.close()

    return collected


def iter_shop_video_items(
    username: str,
    *,
    cookies_file: str | None = None,
    max_pages: int = 20,
) -> Iterable[dict]:
    shop = resolve_shop(username, cookies_file=cookies_file)
    shopid = shop["shopid"]
    username = shop["username"] or username

    seen: set[int] = set()
    items: list[dict] = []

    login = check_shopee_login(cookies_file)
    if login.get("ok"):
        try:
            items.extend(_playwright_fetch_items(username, shopid, cookies_file=cookies_file, max_pages=max_pages))
        except Exception:
            pass
        if not items:
            for offset in range(0, max_pages * 30, 30):
                batch = _fetch_search_items_page(
                    shopid,
                    offset=offset,
                    limit=30,
                    cookies_file=cookies_file,
                    username=username,
                )
                if not batch:
                    break
                items.extend(batch)
                if len(batch) < 30:
                    break
    else:
        try:
            items.extend(_playwright_fetch_items(username, shopid, cookies_file=cookies_file, max_pages=3))
        except Exception:
            pass

    if not items:
        items.extend(fetch_shop_seo_items(shopid, cookies_file=cookies_file))

    for item in items:
        basic = _item_basic(item)
        itemid = basic.get("itemid") or item.get("itemid")
        if not itemid:
            continue
        itemid_int = int(itemid)
        if itemid_int in seen:
            continue
        if not _video_infos_from_item(item):
            continue
        seen.add(itemid_int)
        yield item


def iter_profile_videos(
    username: str,
    *,
    cookies_file: str | None = None,
    max_pages: int = 20,
) -> Iterable[VideoInfo]:
    shop = resolve_shop(username, cookies_file=cookies_file)
    shopid = shop["shopid"]
    username = shop["username"] or username

    count = 0
    for item in iter_shop_video_items(username, cookies_file=cookies_file, max_pages=max_pages):
        info = item_to_video_info(item, username=username, shopid=shopid)
        if info:
            count += 1
            yield info

    if count == 0:
        login = check_shopee_login(cookies_file)
        if not login.get("ok"):
            raise ShopeeAPIError(
                "Tidak ada video produk yang bisa diambil. "
                "Login di shopee.co.id, export cookies (wajib ada SPC_EC), upload di menu Cookies, lalu scan lagi."
            )
        raise ShopeeAPIError(
            f"Tidak ada produk dengan video di toko {username}. "
            "Pastikan toko punya video produk di halaman Shopee."
        )


def fetch_item_detail(
    shopid: int,
    itemid: int,
    *,
    cookies_file: str | None = None,
    username: str = "",
) -> dict:
    cookie_header = cookies_header_from_file(cookies_file)
    referer = f"{SHOPEE_ORIGIN}/{quote(username)}" if username else f"{SHOPEE_ORIGIN}/"
    data = _http_json(
        "GET",
        f"{SHOPEE_ORIGIN}{ITEM_GET_URI}?itemid={itemid}&shopid={shopid}",
        headers=_base_headers(cookie_header, referer=referer),
    )
    payload = _ensure_ok(data, action="ambil detail produk")
    item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    if not isinstance(item, dict):
        raise ShopeeAPIError("Detail produk Shopee tidak ditemukan")
    return item


def resolve_shopee_download_url(
    product_url: str,
    *,
    shopid: int | None = None,
    itemid: int | None = None,
    cookies_file: str | None = None,
    username: str = "",
) -> str:
    parsed_shopid = shopid
    parsed_itemid = itemid
    if parsed_shopid is None or parsed_itemid is None:
        marker = "-i."
        if marker in product_url:
            tail = product_url.split(marker, 1)[1]
            parts = tail.split(".")
            if len(parts) >= 2:
                parsed_shopid = int(parts[0])
                parsed_itemid = int(parts[1].split("?")[0])

    if parsed_shopid is None or parsed_itemid is None:
        raise ValueError("URL produk Shopee tidak valid")

    try:
        item = fetch_item_detail(
            parsed_shopid,
            parsed_itemid,
            cookies_file=cookies_file,
            username=username,
        )
        video_infos = _video_infos_from_item({"item_basic": item})
        if video_infos:
            url = _video_url_from_info(video_infos[0])
            if url:
                return url
    except ShopeeAPIError:
        pass

    try:
        for raw in fetch_shop_seo_items(parsed_shopid, cookies_file=cookies_file):
            basic = _item_basic(raw)
            if int(basic.get("itemid") or 0) != int(parsed_itemid):
                continue
            video_infos = _video_infos_from_item(raw)
            if video_infos:
                url = _video_url_from_info(video_infos[0])
                if url:
                    return url
    except ShopeeAPIError:
        pass

    raise ShopeeAPIError(
        "Gagal mengambil URL video Shopee. Login ulang di shopee.co.id, upload cookies baru, lalu scan ulang profil."
    )
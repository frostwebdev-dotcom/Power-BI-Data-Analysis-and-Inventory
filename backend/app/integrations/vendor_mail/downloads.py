"""Approved direct HTTPS files. No portal login, cookies, browser or Graph token."""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

from app.integrations.vendor_mail.types import IntakeError


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.text: list[str] = []

    def handle_data(self, data: str) -> None:
        self.text.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self.links.extend(value for key, value in attrs if key.lower() == "href" and value)


def email_links(body: str) -> list[str]:
    parser = _Links()
    parser.feed(body[:512000])
    plain = re.findall(r"https://[^\s<>\"']+", " ".join(parser.text))
    # Query strings stay private and intact. HTML entities are decoded once.
    return sorted(
        {unescape(link) for link in parser.links}
        | {unescape(link).rstrip(".,);") for link in plain}
    )


def approved_initial_link(url: str, hosts: list[str], prefix: str) -> bool:
    try:
        parsed = checked_url(url, hosts)
        path = unquote(urlsplit(parsed).path)
        return path.startswith(prefix)
    except IntakeError:
        return False


def checked_url(url: str, hosts: list[str]) -> str:
    try:
        value = urlsplit(url)
        host = (value.hostname or "").lower().rstrip(".")
        path = unquote(value.path)
        if (
            len(url) > 8192
            or value.scheme != "https"
            or value.port not in (None, 443)
            or value.username is not None
            or value.password is not None
            or host not in hosts
            or value.fragment
            or any(ord(c) < 32 for c in url)
            or "\\" in url
            or any(part in (".", "..") for part in path.split("/"))
        ):
            raise ValueError("not approved")
        return url
    except ValueError:
        raise IntakeError("DOWNLOAD_LINK_NOT_APPROVED", review=True) from None


def public_address(host: str) -> str:
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = [ipaddress.ip_address(address[4][0]) for address in addresses]
    except (OSError, ValueError):
        raise IntakeError("DOWNLOAD_DNS_FAILED", retryable=True) from None
    if not ips or any(
        not ip.is_global
        or ip.is_multicast
        or ip.is_reserved
        or (
            isinstance(ip, ipaddress.IPv6Address)
            and (ip.ipv4_mapped is not None or ip.sixtofour is not None or ip.teredo is not None)
        )
        for ip in ips
    ):
        raise IntakeError("DOWNLOAD_ADDRESS_NOT_PUBLIC", review=True)
    return str(ips[0])


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str) -> None:
        self.tls_context = ssl.create_default_context()
        super().__init__(host, timeout=30, context=self.tls_context)
        self.address = address

    def connect(self) -> None:
        # No second hostname lookup after public-address validation. SNI and
        # certificate verification still use the approved hostname, not its IP.
        raw = socket.create_connection((self.address, 443), timeout=30)
        try:
            self.sock = self.tls_context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


@dataclass(frozen=True)
class DownloadResponse:
    status: int
    headers: dict[str, str]
    content: bytes


def fetch_public(url: str, address: str, max_bytes: int) -> DownloadResponse:
    parsed = urlsplit(url)
    connection = _PinnedHTTPSConnection(parsed.hostname or "", address)
    try:
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        connection.request("GET", target, headers={"User-Agent": "PRMS-Vendor-Inventory/1.0"})
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        if response.status in (301, 302, 303, 307, 308):
            return DownloadResponse(response.status, headers, b"")
        try:
            if int(headers.get("content-length", "0")) > max_bytes:
                raise IntakeError("FILE_OR_MESSAGE_TOO_LARGE", review=True)
        except ValueError:
            raise IntakeError("DOWNLOAD_RESPONSE_INVALID", review=True) from None
        data = bytearray()
        deadline = time.monotonic() + 120
        while True:
            if time.monotonic() > deadline:
                raise IntakeError("DOWNLOAD_TIMED_OUT", retryable=True)
            chunk = response.read1(min(65536, max_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > max_bytes:
                raise IntakeError("FILE_OR_MESSAGE_TOO_LARGE", review=True)
        return DownloadResponse(response.status, headers, bytes(data))
    except (OSError, http.client.HTTPException):
        raise IntakeError("DOWNLOAD_CONNECTION_FAILED", retryable=True) from None
    finally:
        connection.close()


def download_file(
    url: str,
    *,
    hosts: list[str],
    max_bytes: int,
    resolve: Callable[[str], str] = public_address,
    fetch: Callable[[str, str, int], DownloadResponse] = fetch_public,
) -> bytes:
    for _ in range(6):
        checked_url(url, hosts)
        address = resolve(urlsplit(url).hostname or "")
        result = fetch(url, address, max_bytes)
        if result.status in (301, 302, 303, 307, 308):
            location = result.headers.get("location")
            if not location:
                raise IntakeError("DOWNLOAD_REDIRECT_INVALID", review=True)
            url = urljoin(url, location)
            continue  # Every redirect is rechecked, resolved and pinned independently.
        if result.status in (401, 403):
            raise IntakeError("DOWNLOAD_LOGIN_REQUIRED", review=True)
        if result.status == 429 or result.status >= 500:
            raise IntakeError("DOWNLOAD_TEMPORARILY_UNAVAILABLE", retryable=True)
        if result.status != 200:
            raise IntakeError("DOWNLOAD_REFUSED", review=True)
        if len(result.content) > max_bytes:
            raise IntakeError("FILE_OR_MESSAGE_TOO_LARGE", review=True)
        head = result.content.lstrip()[:256].lower()
        if "text/html" in result.headers.get("content-type", "").lower() or head.startswith(
            (b"<!doctype html", b"<html", b"<head", b"<form")
        ):
            raise IntakeError("DOWNLOAD_PAGE_REQUIRES_REVIEW", review=True)
        if not result.content:
            raise IntakeError("DOWNLOAD_EMPTY", review=True)
        return result.content
    raise IntakeError("DOWNLOAD_TOO_MANY_REDIRECTS", review=True)

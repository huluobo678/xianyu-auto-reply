from __future__ import annotations

import time
from dataclasses import dataclass

import aiohttp

from common.utils.xianyu_utils import generate_sign, trans_cookies


TOKEN_API_URL = (
    "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.login.token/1.0/"
)


class XianyuTokenError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "token_error",
        verification_required: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.verification_required = verification_required


@dataclass(frozen=True)
class TokenFetchResult:
    token: str
    cookies: str


def _marshal_cookies(values: dict[str, str]) -> str:
    return "; ".join(f"{key}={value}" for key, value in values.items())


def _requires_verification(payload: dict) -> bool:
    text = (str(payload.get("ret") or "") + str(payload.get("data") or "")).lower()
    failure_markers = ("fail_sys_user_validate", "rgv587_error", "captcha_required")
    has_verification_url = "https://" in text and any(
        marker in text for marker in ("verify", "captcha", "punish")
    )
    return any(marker in text for marker in failure_markers) or has_verification_url


async def fetch_login_token(cookies_string: str, device_id: str) -> TokenFetchResult:
    cookies = trans_cookies(cookies_string)
    timestamp = str(int(time.time() * 1000))
    data_value = (
        '{"appKey":"444e9908a51d1cb236a27862abc769c9","deviceId":"' + device_id + '"}'
    )
    token_seed = cookies.get("_m_h5_tk", "").split("_")[0]
    params = {
        "jsv": "2.7.2",
        "appKey": "34839810",
        "t": timestamp,
        "sign": generate_sign(timestamp, token_seed, data_value),
        "v": "1.0",
        "type": "originaljson",
        "accountSite": "xianyu",
        "dataType": "json",
        "timeout": "20000",
        "api": "mtop.taobao.idlemessage.pc.login.token",
        "sessionOption": "AutoLoginOnly",
        "dangerouslySetWindvaneParams": "%5Bobject%20Object%5D",
        "smToken": "token",
        "queryToken": "sm",
        "sm": "sm",
        "spm_cnt": "a21ybx.im.0.0",
        "spm_pre": "a21ybx.home.sidebar.1.4c053da6vYwnmf",
        "log_id": "4c053da6vYwnmf",
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://www.goofish.com",
        "referer": "https://www.goofish.com/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139.0.0.0 Safari/537.36",
        "cookie": cookies_string.replace("\n", "").replace("\r", ""),
    }
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            TOKEN_API_URL, params=params, data={"data": data_value}, headers=headers
        ) as response:
            payload = await response.json(content_type=None)
            for raw_cookie in response.headers.getall("set-cookie", []):
                if "=" in raw_cookie:
                    name, value = raw_cookie.split(";", 1)[0].split("=", 1)
                    cookies[name.strip()] = value.strip()
    fresh_token = (
        (payload.get("data") or {}).get("accessToken")
        if isinstance(payload, dict)
        else None
    )
    if fresh_token:
        return TokenFetchResult(token=fresh_token, cookies=_marshal_cookies(cookies))
    if isinstance(payload, dict) and _requires_verification(payload):
        raise XianyuTokenError(
            "?????????????????????",
            code="xianyu_verification_required",
            verification_required=True,
        )
    summary = (
        (payload.get("ret") or ["unknown error"])[0]
        if isinstance(payload, dict)
        else "invalid response"
    )
    raise XianyuTokenError(f"?? Token ?????{summary}", code="xianyu_token_failed")

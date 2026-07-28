from __future__ import annotations

import asyncio
import base64
import inspect
import json
import time
from collections.abc import Awaitable, Callable

from common.utils.xianyu_utils import generate_device_id, generate_mid, generate_uuid, trans_cookies
from connector.core_loader import load_connection_manager_types, load_message_handler_class
from connector.xianyu_token import XianyuTokenError, fetch_login_token


StateCallback = Callable[[str, str | None], Awaitable[None] | None]
CredentialCallback = Callable[[dict], Awaitable[None] | None]
MessageCallback = Callable[[dict, object], Awaitable[None] | None]
MAX_RECONNECT_FAILURES = 5


class LocalXianyuRuntime:
    def __init__(
        self,
        cookies: str,
        *,
        initial_token: str | None = None,
        on_state: StateCallback | None = None,
        on_credentials: CredentialCallback | None = None,
        on_message: MessageCallback | None = None,
    ):
        parsed = trans_cookies(cookies)
        account_id = parsed.get("unb")
        if not account_id:
            raise ValueError("?? Cookie ?? unb?????????")
        self.cookies_str = cookies
        self.cookies = parsed
        self.cookie_id = str(account_id)
        self.device_id = generate_device_id(self.cookie_id)
        self.base_url = "wss://wss-goofish.dingtalk.com/"
        self.heartbeat_interval = 15
        self.heartbeat_timeout = 30
        self.proxy_config = {"proxy_type": "none", "proxy_host": "", "proxy_port": 0}
        self.current_token = initial_token or None
        self._stop_event = asyncio.Event()
        self._websocket = None
        self._on_state = on_state
        self._on_credentials = on_credentials
        self._on_message = on_message
        manager_type, self.connection_state_type = load_connection_manager_types()
        self.connection_manager = manager_type(self)
        self.message_handler = load_message_handler_class()(self.cookie_id, self.cookie_id)
        if self._on_message is not None:
            self.message_handler.set_chat_message_handler(self._handle_chat_message)

    def _get_proxy_url(self) -> None:
        return None

    async def _interruptible_sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return

    async def _emit(self, state: str, message: str | None = None) -> None:
        if self._on_state is None:
            return
        result = self._on_state(state, message)
        if inspect.isawaitable(result):
            await result

    async def _save_credentials(self) -> None:
        if self._on_credentials is None:
            return
        result = self._on_credentials(
            {
                "account_id": self.cookie_id,
                "cookies": self.cookies_str,
                "token": self.current_token or "",
                "xianyu_device_id": self.device_id,
            }
        )
        if inspect.isawaitable(result):
            await result

    async def _handle_chat_message(self, parsed_message: dict, websocket) -> None:
        if self._on_message is None:
            return
        result = self._on_message(parsed_message, websocket)
        if inspect.isawaitable(result):
            await result

    async def send_text(self, websocket, chat_id: str, send_user_id: str, content: str) -> None:
        encoded = base64.b64encode(
            json.dumps({"contentType": 1, "text": {"text": content}}, ensure_ascii=False).encode("utf-8")
        ).decode("utf-8")
        await websocket.send(json.dumps({
            "lwp": "/r/MessageSend/sendByReceiverScope",
            "headers": {"mid": generate_mid()},
            "body": [{
                "uuid": generate_uuid(), "cid": f"{chat_id}@goofish", "conversationType": 1,
                "content": {"contentType": 101, "custom": {"type": 1, "data": encoded}},
                "redPointPolicy": 0, "extension": {"extJson": "{}"},
                "ctx": {"appVersion": "1.0", "platform": "web"}, "mtags": {}, "msgReadStatusSetting": 1,
            }, {"actualReceivers": [f"{send_user_id}@goofish", f"{self.cookie_id}@goofish"]}],
        }, ensure_ascii=False))

    async def _refresh_token(self) -> None:
        result = await fetch_login_token(self.cookies_str, self.device_id)
        self.current_token = result.token
        self.cookies_str = result.cookies
        self.cookies = trans_cookies(result.cookies)
        await self._save_credentials()

    async def _register(self, websocket) -> None:
        if not self.current_token:
            await self._refresh_token()
        await websocket.send(
            json.dumps(
                {
                    "lwp": "/reg",
                    "headers": {
                        "cache-header": "app-key token ua wv",
                        "app-key": "444e9908a51d1cb236a27862abc769c9",
                        "token": self.current_token,
                        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "dt": "j",
                        "wv": "im:3,au:3,sy:6",
                        "sync": "0,0;0;0;",
                        "did": self.device_id,
                        "mid": generate_mid(),
                    },
                }
            )
        )
        await asyncio.sleep(1)
        current_time = int(time.time() * 1000)
        await websocket.send(
            json.dumps(
                {
                    "lwp": "/r/SyncStatus/ackDiff",
                    "headers": {"mid": generate_mid()},
                    "body": [
                        {
                            "pipeline": "sync",
                            "tooLong2Tag": "PNM,1",
                            "channel": "sync",
                            "topic": "sync",
                            "highPts": 0,
                            "pts": current_time * 1000,
                            "seq": 0,
                            "timestamp": current_time,
                        }
                    ],
                }
            )
        )

    async def run_forever(self) -> None:
        failures = 0
        await self._emit("connecting")
        while not self._stop_event.is_set():
            heartbeat_task = None
            try:
                if not self.current_token:
                    await self._refresh_token()
                self.connection_manager.set_connection_state(
                    self.connection_state_type.CONNECTING, "local connector"
                )
                async with await self.connection_manager.create_websocket_connection(
                    {}
                ) as websocket:
                    self._websocket = websocket
                    self.connection_manager.ws = websocket
                    await self._register(websocket)
                    failures = 0
                    self.connection_manager.set_connection_state(
                        self.connection_state_type.CONNECTED, "local connector ready"
                    )
                    await self._emit("connected")
                    heartbeat_task = asyncio.create_task(
                        self.connection_manager.heartbeat_loop(websocket)
                    )
                    async for raw_message in websocket:
                        if self._stop_event.is_set():
                            break
                        try:
                            message = json.loads(raw_message)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        self.connection_manager.handle_heartbeat_response(message)
                        await self.message_handler.handle_message(message, websocket)
            except XianyuTokenError as exc:
                await self._emit(
                    "verification_required" if exc.verification_required else "error",
                    str(exc),
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                await self._emit("reconnecting", str(exc))
                self.current_token = None
                if failures >= MAX_RECONNECT_FAILURES:
                    await self._emit("error", "?? WebSocket ????????????")
                    return
                await self._interruptible_sleep(
                    self.connection_manager.calculate_retry_delay(str(exc))
                )
            finally:
                self._websocket = None
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    await asyncio.gather(heartbeat_task, return_exceptions=True)
        await self._emit("stopped")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._websocket is not None:
            await self._websocket.close()

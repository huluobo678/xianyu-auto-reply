from __future__ import annotations

import argparse
import asyncio
import base64
import json
import hashlib
import platform
import tempfile
import threading
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import messagebox, ttk

from connector import __version__
from connector.cloud_client import ConnectorCloudClient, ConnectorCloudError
from connector.core_loader import load_local_runtime_class, load_qr_login_manager_class
from connector.local_state import LocalDeliveryState
from connector.recovery import runtime_is_active, should_auto_recover
from connector.secure_store import SecureCredentialStore
from common.utils.sensitive_logging import redact_sensitive_text
from connector.updater import (
    ConnectorUpdateError,
    download_installer,
    launch_installer,
    version_is_newer,
)


class AsyncRunner:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)


class ConnectorApp:
    def __init__(self, *, start_hidden: bool = False):
        self.store = SecureCredentialStore()
        self.delivery_state = LocalDeliveryState()
        self.credentials = self.store.load()
        self.credential_lock = threading.Lock()
        self.runner = AsyncRunner()
        self.qr_manager = load_qr_login_manager_class()()
        self.qr_session_id: str | None = None
        self.runtime = None
        self.runtime_future = None
        self.manually_stopped = False
        self.heartbeat_job = None
        self.tray_icon: pystray.Icon | None = None
        self.exiting = False
        self.root = tk.Tk()
        self.root.title("???????")
        self.root.geometry("720x650")
        self.server_url = tk.StringVar(value=self.credentials.get("server_url", ""))
        self.username = tk.StringVar(value=self.credentials.get("username", ""))
        self.password = tk.StringVar()
        self.status = tk.StringVar(value="???? SaaS ???????")
        self.account_status = tk.StringVar(value="????????")
        self.qr_image = None
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        if start_hidden:
            self.root.after(0, self.root.withdraw)
        if self._device_ready():
            self.status.set("??????????????????")
            self.root.after(1000, self._heartbeat)
            self.root.after(1500, self._auto_recover_saved_account)
            self.root.after(3000, lambda: self._check_for_update(silent=True))

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="???????", font=("Microsoft YaHei UI", 18, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            frame,
            text=f"?? {__version__} ? Cookie ? Token ??? Windows DPAPI ?????",
            foreground="#555555",
        ).pack(anchor="w", pady=(4, 18))

        login = ttk.LabelFrame(frame, text="1. ?? SaaS ?????", padding=12)
        login.pack(fill="x")
        ttk.Label(login, text="SaaS ??").grid(row=0, column=0, sticky="w")
        ttk.Entry(login, textvariable=self.server_url, width=62).grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=8
        )
        ttk.Label(login, text="???").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(login, textvariable=self.username).grid(
            row=1, column=1, sticky="ew", padx=8, pady=(8, 0)
        )
        ttk.Label(login, text="??").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(login, textvariable=self.password, show="*").grid(
            row=1, column=3, sticky="ew", padx=8, pady=(8, 0)
        )
        buttons = ttk.Frame(login)
        buttons.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="???????", command=self._login_and_register).pack(
            side="left"
        )
        ttk.Button(buttons, text="????", command=self._heartbeat).pack(
            side="left", padx=8
        )
        ttk.Button(buttons, text="????", command=self._check_for_update).pack(
            side="left"
        )
        ttk.Button(buttons, text="??????", command=self._clear).pack(side="right")
        login.columnconfigure(1, weight=1)
        login.columnconfigure(3, weight=1)
        ttk.Label(login, textvariable=self.status).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(10, 0)
        )

        xianyu = ttk.LabelFrame(frame, text="2. ????? WebSocket", padding=12)
        xianyu.pack(fill="both", expand=True, pady=(16, 0))
        actions = ttk.Frame(xianyu)
        actions.pack(fill="x")
        ttk.Button(actions, text="?????????", command=self._start_qr_login).pack(
            side="left"
        )
        ttk.Button(actions, text="?????????", command=self._start_saved_account).pack(
            side="left", padx=8
        )
        ttk.Button(actions, text="????", command=self._stop_runtime).pack(side="right")
        ttk.Label(
            xianyu, textvariable=self.account_status, font=("Microsoft YaHei UI", 11)
        ).pack(anchor="w", pady=(12, 8))
        self.qr_label = ttk.Label(xianyu, anchor="center")
        self.qr_label.pack(fill="both", expand=True)
        ttk.Label(
            xianyu,
            text="???????????? ID ??????Cookie?Token???????????",
            foreground="#666666",
        ).pack(anchor="w", pady=(8, 0))

    def _device_ready(self) -> bool:
        return bool(
            self.credentials.get("device_id") and self.credentials.get("device_token")
        )

    def _save_credentials(self) -> None:
        with self.credential_lock:
            self.store.save(self.credentials)

    def _run_thread(self, action, *, working: str) -> None:
        self.status.set(working)

        def worker():
            try:
                message = action()
            except (ConnectorCloudError, ConnectorUpdateError, OSError, ValueError) as exc:
                self.root.after(0, lambda value=str(exc): self.status.set(value))
            else:
                self.root.after(0, lambda value=message: self.status.set(value))

        threading.Thread(target=worker, daemon=True).start()

    def _login_and_register(self) -> None:
        server_url = self.server_url.get().strip()
        binding_code = self.password.get().strip()
        if not server_url or not binding_code:
            messagebox.showerror("绑定失败", "请输入 SaaS 地址和一次性绑定码")
            return

        def action() -> str:
            client = ConnectorCloudClient(server_url)
            device_uuid = self.credentials.get("device_uuid") or uuid.uuid4().hex
            device = client.register_device_by_code(
                binding_code,
                login["token"],
                {
                    "device_uuid": device_uuid,
                    "device_name": platform.node() or "Windows device",
                    "platform": "windows",
                    "app_version": __version__,
                },
            )
            self.credentials.update(
                {
                    "server_url": server_url,
                    "username": username,
                    "device_id": str(device["id"]),
                    "device_token": device["device_token"],
                }
            )
            self.root.after(0, lambda: self.password.set(""))
            self._save_credentials()
            self.root.after(0, self._schedule_heartbeat)
            return f"SaaS ?????????????{device['device_name']}"

        self._run_thread(action, working="???? SaaS ?????...")

    def _schedule_heartbeat(self) -> None:
        if self.heartbeat_job is not None:
            self.root.after_cancel(self.heartbeat_job)
        if self._device_ready():
            self.heartbeat_job = self.root.after(30000, self._heartbeat)

    def _heartbeat(self) -> None:
        if not self._device_ready():
            self.status.set("????????")
            return
        self.status.set("??????...")

        def worker():
            try:
                account_count = (
                    1 if (self.credentials.get("xianyu") or {}).get("account_id") else 0
                )
                ConnectorCloudClient(self.credentials["server_url"]).heartbeat(
                    int(self.credentials["device_id"]),
                    self.credentials["device_token"],
                    account_count=account_count,
                )
            except ConnectorCloudError as exc:
                message = str(exc)
                if "HTTP 401" in message:
                    self.root.after(0, self._handle_device_revoked)
                else:
                    self.root.after(0, lambda value=message: self.status.set(value))
            else:
                self.root.after(0, lambda: self.status.set("????????????"))
            finally:
                self.root.after(0, self._schedule_heartbeat)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_device_revoked(self) -> None:
        self.status.set("??????????????????")
        self.credentials.pop("device_id", None)
        self.credentials.pop("device_token", None)
        self._save_credentials()
        self._stop_runtime()

    def _start_qr_login(self) -> None:
        if not self._device_ready():
            messagebox.showinfo("????", "???? SaaS ???????")
            return
        self.account_status.set("?????????...")
        future = self.runner.submit(self.qr_manager.generate_qr_code())

        def completed(result_future):
            try:
                result = result_future.result()
            except Exception as exc:
                self.root.after(
                    0,
                    lambda value=str(exc): self.account_status.set(f"????????{value}"),
                )
                return
            self.root.after(0, lambda: self._show_qr_result(result))

        future.add_done_callback(completed)

    def _show_qr_result(self, result: dict) -> None:
        if not result.get("success"):
            self.account_status.set(result.get("message") or "???????")
            return
        self.qr_session_id = result["session_id"]
        self._show_data_url(result["qr_code_url"])
        self.account_status.set("????? App ???????")
        self.root.after(1000, self._poll_qr_status)

    def _show_data_url(self, data_url: str | None) -> None:
        if not data_url or "," not in data_url:
            return
        image_data = base64.b64decode(data_url.split(",", 1)[1])
        self.qr_image = tk.PhotoImage(data=base64.b64encode(image_data).decode("ascii"))
        self.qr_label.configure(image=self.qr_image)

    def _poll_qr_status(self) -> None:
        if not self.qr_session_id:
            return
        result = self.qr_manager.get_session_status(self.qr_session_id)
        state = result.get("status")
        if state in {"waiting", "scanned"}:
            self.account_status.set(
                "?????????????" if state == "scanned" else "????..."
            )
            self.root.after(1000, self._poll_qr_status)
            return
        if state == "verification_required":
            self._show_data_url(result.get("face_qr_url"))
            self.account_status.set(result.get("message") or "???????")
            self.root.after(1500, self._poll_qr_status)
            return
        if state != "success":
            self.account_status.set(
                "????????????" if state == "expired" else f"?????{state}"
            )
            return
        account = self.qr_manager.get_session_cookies(self.qr_session_id) or {}
        if not account.get("cookies") or not account.get("unb"):
            self.account_status.set("?????????? Cookie")
            return
        self.credentials["xianyu"] = {
            "account_id": str(account["unb"]),
            "cookies": account["cookies"],
            "token": "",
        }
        self._save_credentials()
        self.qr_label.configure(image="")
        self.account_status.set("??????????? WebSocket...")
        self._start_runtime(account["cookies"])

    def _start_saved_account(self) -> None:
        account = self.credentials.get("xianyu") or {}
        if not account.get("cookies"):
            messagebox.showinfo("?????", "????????????")
            return
        self._start_runtime(account["cookies"], initial_token=account.get("token"))

    def _auto_recover_saved_account(self) -> None:
        if not should_auto_recover(
            self.credentials, manually_stopped=self.manually_stopped
        ):
            return
        account = self.credentials["xianyu"]
        self.account_status.set("正在安全恢复本地闲鱼连接...")
        self._start_runtime(
            account["cookies"], initial_token=account["token"], automatic=True
        )

    def _start_runtime(
        self,
        cookies: str,
        *,
        initial_token: str | None = None,
        automatic: bool = False,
    ) -> None:
        if runtime_is_active(self.runtime_future):
            self.account_status.set("???????????")
            return
        if not automatic:
            self.manually_stopped = False

        async def on_state(state: str, message: str | None) -> None:
            safe_message = redact_sensitive_text(message) if message else None
            self.root.after(
                0, lambda: self.account_status.set(safe_message or f"???????{state}")
            )
            account_id = (self.credentials.get("xianyu") or {}).get("account_id")
            if account_id and self._device_ready():
                try:
                    await asyncio.to_thread(
                        ConnectorCloudClient(
                            self.credentials["server_url"]
                        ).sync_account_state,
                        int(self.credentials["device_id"]),
                        self.credentials["device_token"],
                        account_id,
                        state,
                        None
                        if state not in {"error", "verification_required"}
                        else state,
                        safe_message,
                    )
                except ConnectorCloudError as exc:
                    self.root.after(
                        0, lambda value=str(exc): self.status.set(f"???????{value}")
                    )

        async def on_credentials(values: dict) -> None:
            self.credentials["xianyu"] = values
            await asyncio.to_thread(self._save_credentials)

        async def on_message(parsed: dict, websocket) -> None:
            if not self._device_ready():
                self.root.after(0, lambda: self.account_status.set("云端设备未绑定，自动回复已暂停"))
                return
            raw_message = parsed.get("raw_message") or {}
            source_message_id = self.runtime.message_handler.extract_message_id(raw_message) if self.runtime else None
            identity = source_message_id or hashlib.sha256(
                f"{parsed.get('chat_id','')}|{parsed.get('send_user_id','')}|{parsed.get('msg_time','')}|{parsed.get('send_message','')}".encode("utf-8")
            ).hexdigest()
            account_id = (self.credentials.get("xianyu") or {}).get("account_id") or parsed.get("account_id")
            global_message_id = f"xy:{account_id}:{identity}"[:191]
            if self.delivery_state.is_sent(global_message_id):
                return
            client = ConnectorCloudClient(self.credentials["server_url"])
            try:
                decision = await asyncio.to_thread(
                    client.process_message,
                    int(self.credentials["device_id"]),
                    self.credentials["device_token"],
                    {
                        "global_message_id": global_message_id,
                        "account_id": account_id,
                        "source_message_id": source_message_id,
                        "chat_id": parsed.get("chat_id") or "",
                        "sender_user_id": parsed.get("send_user_id") or "",
                        "sender_user_name": parsed.get("send_user_name") or "",
                        "message_text": parsed.get("send_message") or "",
                        "item_id": parsed.get("item_id") or None,
                        "msg_time": str(parsed.get("msg_time") or ""),
                        "sender_is_self": (parsed.get("send_user_id") or "") == account_id,
                    },
                )
            except ConnectorCloudError as exc:
                self.root.after(0, lambda value=str(exc): self.account_status.set(f"云端不可用，自动回复已暂停：{value}"))
                return
            if not decision.get("should_reply"):
                return
            self.delivery_state.mark(global_message_id, "sending")
            send_result_id = uuid.uuid4().hex
            try:
                await self.runtime.send_text(
                    websocket,
                    parsed.get("chat_id") or "",
                    parsed.get("send_user_id") or "",
                    decision.get("reply_content") or "",
                )
            except Exception as exc:
                self.delivery_state.mark(global_message_id, "failed")
                await asyncio.to_thread(
                    client.report_send_result,
                    int(self.credentials["device_id"]), self.credentials["device_token"],
                    global_message_id,
                    {"send_result_id": send_result_id, "success": False, "error_code": "local_send_failed", "error_message": redact_sensitive_text(exc)[:255]},
                )
                return
            self.delivery_state.mark(global_message_id, "sent")
            await asyncio.to_thread(
                client.report_send_result,
                int(self.credentials["device_id"]), self.credentials["device_token"],
                global_message_id,
                {"send_result_id": send_result_id, "success": True},
            )
        self.runtime = load_local_runtime_class()(
            cookies,
            initial_token=initial_token,
            on_state=on_state,
            on_credentials=on_credentials,
            on_message=on_message,
        )
        self.runtime_future = self.runner.submit(self.runtime.run_forever())

    def _stop_runtime(self) -> None:
        self.manually_stopped = True
        if self.runtime is None:
            self.account_status.set("????????????")
            return
        self.runner.submit(self.runtime.stop())

    def _clear(self) -> None:
        if not messagebox.askyesno("????", "??????? SaaS?????????"):
            return
        self._stop_runtime()
        self.store.clear()
        self.credentials = {}
        if self.heartbeat_job is not None:
            self.root.after_cancel(self.heartbeat_job)
            self.heartbeat_job = None
        self.status.set("???????")
        self.account_status.set("????????")

    def _show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    @staticmethod
    def _tray_image():
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (64, 64), "#1677ff")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill="#ffffff")
        draw.arc((16, 17, 48, 49), 200, 340, fill="#1677ff", width=6)
        draw.ellipse((20, 20, 27, 27), fill="#1677ff")
        draw.ellipse((37, 20, 44, 27), fill="#1677ff")
        return image

    def _start_tray(self) -> None:
        import pystray

        if self.tray_icon is not None:
            return
        self.tray_icon = pystray.Icon(
            "XianyuConnector",
            self._tray_image(),
            "???????",
            pystray.Menu(
                pystray.MenuItem("??", lambda _icon, _item: self.root.after(0, self._show), default=True),
                pystray.MenuItem("????", lambda _icon, _item: self.root.after(0, self._check_for_update)),
                pystray.MenuItem("??", lambda _icon, _item: self.root.after(0, self._exit)),
            ),
        )
        self.tray_icon.run_detached()

    def _check_for_update(self, *, silent: bool = False) -> None:
        if not self._device_ready():
            if not silent:
                messagebox.showinfo("????", "???? SaaS ??")
            return

        def worker() -> None:
            try:
                client = ConnectorCloudClient(self.credentials["server_url"])
                release = client.latest_release(
                    int(self.credentials["device_id"]),
                    self.credentials["device_token"],
                )
            except (ConnectorCloudError, OSError, ValueError) as exc:
                if not silent:
                    self.root.after(0, lambda value=str(exc): messagebox.showerror("????", value))
                return
            if not release or not version_is_newer(__version__, str(release.get("version", ""))):
                if not silent:
                    self.root.after(0, lambda: messagebox.showinfo("????", "????????"))
                return
            self.root.after(0, lambda: self._offer_update(release))

        threading.Thread(target=worker, daemon=True).start()

    def _offer_update(self, release: dict) -> None:
        remote_version = str(release.get("version", ""))
        if not messagebox.askyesno(
            "?????",
            f"???? {remote_version}??????? SHA-256 ???????????",
        ):
            return
        self.status.set(f"?????? {remote_version}...")

        def worker() -> None:
            try:
                installer = download_installer(
                    str(release["download_url"]),
                    str(release["sha256"]),
                )
                launch_installer(installer)
            except (ConnectorUpdateError, KeyError, OSError) as exc:
                self.root.after(0, lambda value=str(exc): messagebox.showerror("????", value))
                return
            self.root.after(0, self._exit)

        threading.Thread(target=worker, daemon=True).start()

    def _close(self) -> None:
        self.root.withdraw()
        if self.tray_icon is not None:
            try:
                self.tray_icon.notify("??????????", "???????")
            except Exception:
                pass

    def _exit(self) -> None:
        if self.exiting:
            return
        self.exiting = True
        if self.heartbeat_job is not None:
            self.root.after_cancel(self.heartbeat_job)
        if self.runtime is not None:
            try:
                self.runner.submit(self.runtime.stop()).result(timeout=3)
            except Exception:
                pass
        self.runner.close()
        if self.tray_icon is not None:
            self.tray_icon.stop()
        self.root.destroy()

    def run(self) -> None:
        self._start_tray()
        self.root.mainloop()


def run_self_test(output_path: str | None = None) -> int:
    qr_manager = load_qr_login_manager_class()
    with tempfile.TemporaryDirectory() as directory:
        store = SecureCredentialStore(Path(directory) / "credentials.bin")
        sample = {
            "device_id": "1",
            "xianyu": {"account_id": "local-only", "cookies": "secret"},
        }
        store.save(sample)
        secure_store_ok = store.load() == sample
    result = json.dumps(
        {
            "version": __version__,
            "qr_core": qr_manager.__name__,
            "dpapi_roundtrip": secure_store_ok,
        },
        ensure_ascii=False,
    )
    if output_path:
        Path(output_path).write_text(result, encoding="utf-8")
    else:
        print(result)
    return 0 if secure_store_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Xianyu local connector")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run local dependency and DPAPI smoke test",
    )
    parser.add_argument(
        "--self-test-output",
        help="write self-test JSON to this path for windowed builds",
    )
    parser.add_argument(
        "--startup",
        action="store_true",
        help="start minimized to the Windows notification area",
    )
    args = parser.parse_args()
    if args.self_test:
        return run_self_test(args.self_test_output)
    ConnectorApp(start_hidden=args.startup).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

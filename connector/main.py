from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import hashlib
import platform
import tempfile
import sys
import threading
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import messagebox, ttk

from connector import __version__
from connector.cloud_client import ConnectorCloudClient, ConnectorCloudError
from connector.core_loader import load_local_runtime_class, load_qr_login_manager_class
from connector.local_state import LocalDeliveryState
from connector.pairing import (
    OFFICIAL_SAAS_URL,
    PairingCancelled,
    PairingTimeout,
    run_pairing,
)
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
        self.credentials["server_url"] = OFFICIAL_SAAS_URL
        self.credential_lock = threading.Lock()
        self.runner = AsyncRunner()
        self.qr_manager = load_qr_login_manager_class()()
        self.qr_session_id: str | None = None
        self.runtime = None
        self.runtime_future = None
        self.manually_stopped = False
        self.heartbeat_job = None
        self.tray_icon = None
        self.exiting = False
        self.pairing_cancel = threading.Event()
        self.pairing_active = False
        self.root = tk.Tk()
        self.root.title("闲鱼本地连接器")
        self.root.geometry("720x650")
        self.status = tk.StringVar(value="正在准备本机绑定…")
        self.account_status = tk.StringVar(value="尚未登录闲鱼")
        self.qr_image = None
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        if start_hidden and self._device_ready():
            self.root.after(0, self.root.withdraw)
        if self._device_ready():
            self.status.set("本机已绑定，正在连接 SaaS…")
            self.root.after(1000, self._heartbeat)
            self.root.after(1500, self._auto_recover_saved_account)
            self.root.after(3000, lambda: self._check_for_update(silent=True))
        else:
            self.root.after(500, self._start_pairing)

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text="闲鱼本地连接器", font=("Microsoft YaHei UI", 18, "bold")
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text=f"版本 {__version__}｜官方服务：{OFFICIAL_SAAS_URL}｜Cookie 和 Token 仅保存在本机 DPAPI",
            foreground="#555555",
        ).pack(anchor="w", pady=(4, 18))

        pairing = ttk.LabelFrame(frame, text="1. 绑定此电脑", padding=12)
        pairing.pack(fill="x")
        ttk.Label(
            pairing,
            text="连接器会自动打开系统浏览器。登录或注册后，只需点击一次“绑定此电脑”。",
        ).pack(anchor="w")
        buttons = ttk.Frame(pairing)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="打开浏览器并绑定", command=self._start_pairing).pack(
            side="left"
        )
        ttk.Button(buttons, text="取消等待", command=self._cancel_pairing).pack(
            side="left", padx=8
        )
        ttk.Button(buttons, text="检查更新", command=self._check_for_update).pack(
            side="left"
        )
        ttk.Button(buttons, text="清除本机绑定", command=self._clear).pack(side="right")
        ttk.Label(pairing, textvariable=self.status).pack(anchor="w", pady=(10, 0))

        xianyu = ttk.LabelFrame(frame, text="2. 扫码登录闲鱼", padding=12)
        xianyu.pack(fill="both", expand=True, pady=(16, 0))
        actions = ttk.Frame(xianyu)
        actions.pack(fill="x")
        ttk.Button(
            actions, text="生成闲鱼登录二维码", command=self._start_qr_login
        ).pack(side="left")
        ttk.Button(
            actions, text="重新连接已保存账号", command=self._start_saved_account
        ).pack(side="left", padx=8)
        ttk.Button(actions, text="手动停止", command=self._stop_runtime).pack(
            side="right"
        )
        ttk.Label(
            xianyu, textvariable=self.account_status, font=("Microsoft YaHei UI", 11)
        ).pack(anchor="w", pady=(12, 8))
        self.qr_label = ttk.Label(xianyu, anchor="center")
        self.qr_label.pack(fill="both", expand=True)
        ttk.Label(
            xianyu,
            text="扫码、人脸和滑块验证均在本机执行；Cookie、闲鱼 Token 和验证链接不会上传云端。",
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
            except (
                ConnectorCloudError,
                ConnectorUpdateError,
                OSError,
                ValueError,
            ) as exc:
                self.root.after(0, lambda value=str(exc): self.status.set(value))
            else:
                self.root.after(0, lambda value=message: self.status.set(value))

        threading.Thread(target=worker, daemon=True).start()

    def _start_pairing(self) -> None:
        if self._device_ready():
            self.status.set("本机已经绑定，无需重复配对")
            return
        if self.pairing_active:
            self.status.set("正在等待浏览器确认绑定…")
            return
        self.pairing_active = True
        self.pairing_cancel.clear()
        device_uuid = self.credentials.get("device_uuid") or uuid.uuid4().hex
        self.credentials.update(
            {"server_url": OFFICIAL_SAAS_URL, "device_uuid": device_uuid}
        )
        self._save_credentials()
        self.status.set("正在创建安全配对会话…")

        def update_status(message: str) -> None:
            self.root.after(0, lambda value=message: self.status.set(value))

        def worker() -> None:
            try:
                device = run_pairing(
                    ConnectorCloudClient(OFFICIAL_SAAS_URL),
                    {
                        "device_uuid": device_uuid,
                        "device_name": platform.node() or "Windows 电脑",
                        "platform": "windows",
                        "app_version": __version__,
                    },
                    self.pairing_cancel,
                    on_status=update_status,
                )
            except (
                ConnectorCloudError,
                PairingCancelled,
                PairingTimeout,
                OSError,
                ValueError,
            ) as exc:
                self.root.after(0, lambda value=str(exc): self.status.set(value))
            else:
                self.credentials.update(
                    {
                        "server_url": OFFICIAL_SAAS_URL,
                        "device_uuid": device_uuid,
                        "device_id": str(device["id"]),
                        "device_token": device["device_token"],
                    }
                )
                self._save_credentials()
                self.root.after(
                    0, lambda: self.status.set("本机绑定成功，可以扫码登录闲鱼")
                )
                self.root.after(0, self._schedule_heartbeat)
                self.root.after(500, self._heartbeat)
            finally:
                self.pairing_active = False

        threading.Thread(target=worker, daemon=True).start()

    def _cancel_pairing(self) -> None:
        self.pairing_cancel.set()
        if self.pairing_active:
            self.status.set("正在取消配对…")

    def _schedule_heartbeat(self) -> None:
        if self.heartbeat_job is not None:
            self.root.after_cancel(self.heartbeat_job)
        if self._device_ready():
            self.heartbeat_job = self.root.after(30000, self._heartbeat)

    def _heartbeat(self) -> None:
        if not self._device_ready():
            self.status.set("正在发送设备心跳…")
            return
        self.status.set("正在发送设备心跳…")

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
                self.root.after(0, lambda: self.status.set("设备在线"))
            finally:
                self.root.after(0, self._schedule_heartbeat)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_device_revoked(self) -> None:
        self.status.set("设备尚未绑定")
        self.credentials.pop("device_id", None)
        self.credentials.pop("device_token", None)
        self._save_credentials()
        self._stop_runtime()

    def _start_qr_login(self) -> None:
        if not self._device_ready():
            messagebox.showinfo("提示", "请先完成本机绑定")
            return
        self.account_status.set("正在生成闲鱼登录二维码…")
        future = self.runner.submit(self.qr_manager.generate_qr_code())

        def completed(result_future):
            try:
                result = result_future.result()
            except Exception as exc:
                self.root.after(
                    0,
                    lambda value=str(exc): self.account_status.set(
                        f"二维码生成失败：{value}"
                    ),
                )
                return
            self.root.after(0, lambda: self._show_qr_result(result))

        future.add_done_callback(completed)

    def _show_qr_result(self, result: dict) -> None:
        if not result.get("success"):
            self.account_status.set(result.get("message") or "二维码生成失败")
            return
        self.qr_session_id = result["session_id"]
        self._show_data_url(result["qr_code_url"])
        self.account_status.set("请使用闲鱼 App 扫码登录")
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
                "已扫码，请在手机上确认" if state == "scanned" else "等待扫码…"
            )
            self.root.after(1000, self._poll_qr_status)
            return
        if state == "verification_required":
            self._show_data_url(result.get("face_qr_url"))
            self.account_status.set(result.get("message") or "二维码生成失败")
            self.root.after(1500, self._poll_qr_status)
            return
        if state != "success":
            self.account_status.set(
                "二维码已过期，请重新生成"
                if state == "expired"
                else f"登录状态：{state}"
            )
            return
        account = self.qr_manager.get_session_cookies(self.qr_session_id) or {}
        if not account.get("cookies") or not account.get("unb"):
            self.account_status.set("登录成功但未取得本机 Cookie")
            return
        self.credentials["xianyu"] = {
            "account_id": str(account["unb"]),
            "cookies": account["cookies"],
            "token": "",
        }
        self._save_credentials()
        self.qr_label.configure(image="")
        self.account_status.set("闲鱼登录成功，正在连接本地 WebSocket…")
        self._start_runtime(account["cookies"])

    def _start_saved_account(self) -> None:
        account = self.credentials.get("xianyu") or {}
        if not account.get("cookies"):
            messagebox.showinfo("提示", "本机没有已保存的闲鱼账号")
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
            self.account_status.set("闲鱼连接已在运行")
            return
        if not automatic:
            self.manually_stopped = False

        async def on_state(state: str, message: str | None) -> None:
            safe_message = redact_sensitive_text(message) if message else None
            self.root.after(
                0, lambda: self.account_status.set(safe_message or f"连接状态：{state}")
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
                        0,
                        lambda value=str(exc): self.status.set(
                            f"状态同步失败：{value}"
                        ),
                    )

        async def on_credentials(values: dict) -> None:
            self.credentials["xianyu"] = values
            await asyncio.to_thread(self._save_credentials)

        async def on_message(parsed: dict, websocket) -> None:
            if not self._device_ready():
                self.root.after(
                    0, lambda: self.account_status.set("云端设备未绑定，自动回复已暂停")
                )
                return
            raw_message = parsed.get("raw_message") or {}
            source_message_id = (
                self.runtime.message_handler.extract_message_id(raw_message)
                if self.runtime
                else None
            )
            identity = (
                source_message_id
                or hashlib.sha256(
                    f"{parsed.get('chat_id', '')}|{parsed.get('send_user_id', '')}|{parsed.get('msg_time', '')}|{parsed.get('send_message', '')}".encode(
                        "utf-8"
                    )
                ).hexdigest()
            )
            account_id = (self.credentials.get("xianyu") or {}).get(
                "account_id"
            ) or parsed.get("account_id")
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
                        "sender_is_self": (parsed.get("send_user_id") or "")
                        == account_id,
                    },
                )
            except ConnectorCloudError as exc:
                self.root.after(
                    0,
                    lambda value=str(exc): self.account_status.set(
                        f"云端不可用，自动回复已暂停：{value}"
                    ),
                )
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
                    int(self.credentials["device_id"]),
                    self.credentials["device_token"],
                    global_message_id,
                    {
                        "send_result_id": send_result_id,
                        "success": False,
                        "error_code": "local_send_failed",
                        "error_message": redact_sensitive_text(exc)[:255],
                    },
                )
                return
            self.delivery_state.mark(global_message_id, "sent")
            await asyncio.to_thread(
                client.report_send_result,
                int(self.credentials["device_id"]),
                self.credentials["device_token"],
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
            self.account_status.set("闲鱼连接未运行")
            return
        self.runner.submit(self.runtime.stop())

    def _clear(self) -> None:
        if not messagebox.askyesno(
            "清除本机绑定", "确认清除 SaaS 设备凭据和本机闲鱼登录信息？"
        ):
            return
        self._cancel_pairing()
        self._stop_runtime()
        self.store.clear()
        self.credentials = {"server_url": OFFICIAL_SAAS_URL}
        if self.heartbeat_job is not None:
            self.root.after_cancel(self.heartbeat_job)
            self.heartbeat_job = None
        self.status.set("设备在线")
        self.account_status.set("尚未登录闲鱼")

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
            "闲鱼本地连接器",
            pystray.Menu(
                pystray.MenuItem(
                    "显示",
                    lambda _icon, _item: self.root.after(0, self._show),
                    default=True,
                ),
                pystray.MenuItem(
                    "检查更新",
                    lambda _icon, _item: self.root.after(0, self._check_for_update),
                ),
                pystray.MenuItem(
                    "退出", lambda _icon, _item: self.root.after(0, self._exit)
                ),
            ),
        )
        self.tray_icon.run_detached()

    def _check_for_update(self, *, silent: bool = False) -> None:
        if not self._device_ready():
            if not silent:
                messagebox.showinfo("提示", "请先绑定本机")
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
                    self.root.after(
                        0, lambda value=str(exc): messagebox.showerror("错误", value)
                    )
                return
            if not release or not version_is_newer(
                __version__, str(release.get("version", ""))
            ):
                if not silent:
                    self.root.after(
                        0, lambda: messagebox.showinfo("检查更新", "当前已经是最新版本")
                    )
                return
            self.root.after(0, lambda: self._offer_update(release))

        threading.Thread(target=worker, daemon=True).start()

    def _offer_update(self, release: dict) -> None:
        remote_version = str(release.get("version", ""))
        if not messagebox.askyesno(
            "发现新版本",
            f"发现版本 {remote_version}，是否下载并校验 SHA-256 后安装？",
        ):
            return
        self.status.set(f"正在下载版本 {remote_version}…")

        def worker() -> None:
            try:
                installer = download_installer(
                    str(release["download_url"]),
                    str(release["sha256"]),
                )
                launch_installer(installer)
            except (ConnectorUpdateError, KeyError, OSError) as exc:
                self.root.after(
                    0, lambda value=str(exc): messagebox.showerror("错误", value)
                )
                return
            self.root.after(0, self._exit)

        threading.Thread(target=worker, daemon=True).start()

    def _close(self) -> None:
        self._cancel_pairing()
        self.root.withdraw()
        if self.tray_icon is not None:
            try:
                self.tray_icon.notify("连接器仍在后台运行", "闲鱼本地连接器")
            except Exception:
                pass

    def _exit(self) -> None:
        if self.exiting:
            return
        self.exiting = True
        self._cancel_pairing()
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
            "official_saas_url": OFFICIAL_SAAS_URL,
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
    exit_code = main()
    if getattr(sys, "frozen", False):
        os._exit(exit_code)
    raise SystemExit(exit_code)

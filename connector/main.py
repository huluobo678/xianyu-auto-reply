from __future__ import annotations

import platform
import threading
import tkinter as tk
import uuid
from tkinter import messagebox, ttk

from connector import __version__
from connector.cloud_client import ConnectorCloudClient, ConnectorCloudError
from connector.secure_store import SecureCredentialStore


class ConnectorApp:
    def __init__(self):
        self.store = SecureCredentialStore()
        self.credentials = self.store.load()
        self.root = tk.Tk()
        self.root.title("Xianyu Local Connector")
        self.root.geometry("620x390")
        self.server_url = tk.StringVar(value=self.credentials.get("server_url", ""))
        self.access_token = tk.StringVar()
        self.status = tk.StringVar(value="Device not registered")
        self._build()
        if self.credentials.get("device_id") and self.credentials.get("device_token"):
            self.status.set("Device registered; cloud check is available")

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Xianyu Local Connector", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text=f"Version {__version__} - device registration only; existing connections remain unchanged").pack(anchor="w", pady=(4, 22))
        ttk.Label(frame, text="SaaS URL").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.server_url).pack(fill="x", pady=(4, 14))
        ttk.Label(frame, text="One-time access token").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.access_token, show="*").pack(fill="x", pady=(4, 8))
        ttk.Label(frame, text="The access token is used once and is not stored locally.", foreground="#666666").pack(anchor="w")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=20)
        ttk.Button(buttons, text="Register device", command=self._register).pack(side="left")
        ttk.Button(buttons, text="Check cloud", command=self._heartbeat).pack(side="left", padx=10)
        ttk.Button(buttons, text="Clear credentials", command=self._clear).pack(side="right")
        ttk.Separator(frame).pack(fill="x", pady=8)
        ttk.Label(frame, textvariable=self.status, font=("Microsoft YaHei UI", 11)).pack(anchor="w", pady=12)

    def _run(self, action) -> None:
        def worker():
            try:
                message = action()
            except (ConnectorCloudError, OSError, ValueError) as exc:
                error_message = str(exc)
                self.root.after(0, lambda value=error_message: self.status.set(value))
            else:
                self.root.after(0, lambda value=message: self.status.set(value))
        threading.Thread(target=worker, daemon=True).start()

    def _register(self) -> None:
        server_url = self.server_url.get().strip()
        access_token = self.access_token.get().strip()
        if not server_url.startswith("https://") or not access_token:
            messagebox.showerror("Registration failed", "Enter an HTTPS SaaS URL and a valid access token")
            return
        self.status.set("Registering device...")

        def action() -> str:
            device_uuid = self.credentials.get("device_uuid") or uuid.uuid4().hex
            data = ConnectorCloudClient(server_url).register_device(access_token, {"device_uuid": device_uuid, "device_name": platform.node() or "Windows device", "platform": "windows", "app_version": __version__})
            self.credentials = {"server_url": server_url, "device_uuid": device_uuid, "device_id": str(data["id"]), "device_token": data["device_token"]}
            self.store.save(self.credentials)
            self.access_token.set("")
            return f"Device registered: {data['device_name']}"
        self._run(action)

    def _heartbeat(self) -> None:
        if not self.credentials.get("device_id") or not self.credentials.get("device_token"):
            messagebox.showinfo("Not registered", "Register this device first")
            return
        self.status.set("Checking cloud connection...")

        def action() -> str:
            ConnectorCloudClient(self.credentials["server_url"]).heartbeat(int(self.credentials["device_id"]), self.credentials["device_token"])
            return "Cloud connection is healthy; heartbeat updated"
        self._run(action)

    def _clear(self) -> None:
        if messagebox.askyesno("Clear credentials", "Remove local device credentials?"):
            self.store.clear()
            self.credentials = {}
            self.status.set("Local device credentials cleared")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    ConnectorApp().run()

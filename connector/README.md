# Xianyu Local Connector 0.1.0

This is the first non-disruptive connector milestone.

## Included

- SaaS device registration API.
- Device heartbeat, account binding, unbinding and revocation APIs.
- Windows DPAPI credential storage.
- Desktop registration and heartbeat client.
- PyInstaller standalone executable.
- Inno Setup installer with desktop, Start Menu and optional startup shortcuts.

## Safety Boundary

- This version does not start local Xianyu WebSocket connections.
- This version does not stop or change existing cloud account tasks.
- Cookies and Xianyu tokens are not uploaded to the SaaS API.
- The SaaS access token entered during registration is not stored locally.

## Build

Run `build_connector.bat`. Artifacts are written to:

`D:\AI\Agent_Data\Generated_Files\xianyu-local-connector`

## Install and Uninstall

- Run `XianyuConnectorSetup-0.1.0.exe` to install.
- The default target is the current user's local Programs directory.
- Use Windows Apps settings or the Start Menu uninstall entry to remove it.
- The uninstaller asks whether local device credentials should also be deleted.

## Known Limitations

- The package is not Authenticode signed, so Windows SmartScreen may show a warning.
- Account QR login and local Xianyu messaging are not enabled in this milestone.
- Device registration currently requires a one-time SaaS access token.

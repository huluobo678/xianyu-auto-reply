from __future__ import annotations

import importlib.util
import sys
import types
from functools import lru_cache
from pathlib import Path


REPOSITORY_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def _load_module(name: str, path: Path):
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _ensure_package(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    package = types.ModuleType(name)
    package.__path__ = [str(path)]
    sys.modules[name] = package


@lru_cache(maxsize=1)
def prepare_common_utils() -> None:
    common_root = REPOSITORY_ROOT / "common"
    utils_root = common_root / "utils"
    _ensure_package("common", common_root)
    _ensure_package("common.utils", utils_root)
    _load_module("common.utils.xianyu_utils", utils_root / "xianyu_utils.py")
    _load_module(
        "common.utils.xianyu_message_parser",
        utils_root / "xianyu_message_parser.py",
    )


@lru_cache(maxsize=1)
def load_qr_login_manager_class():
    backend_root = REPOSITORY_ROOT / "backend-web" / "app"
    _ensure_package("app", backend_root)
    _ensure_package("app.services", backend_root / "services")
    _ensure_package("app.services.qr_login", backend_root / "services" / "qr_login")
    _load_module(
        "app.services.qr_login.face_verification",
        backend_root / "services" / "qr_login" / "face_verification.py",
    )
    module = _load_module(
        "app.services.qr_login.manager",
        backend_root / "services" / "qr_login" / "manager.py",
    )
    package = sys.modules["app.services.qr_login"]
    package.QRLoginManager = module.QRLoginManager
    package.qr_login_manager = module.qr_login_manager
    return module.QRLoginManager


@lru_cache(maxsize=1)
def load_connection_manager_types():
    prepare_common_utils()
    module = _load_module(
        "connector._existing_connection_manager",
        REPOSITORY_ROOT
        / "websocket"
        / "app"
        / "services"
        / "xianyu"
        / "connection_manager.py",
    )
    from common.utils.xianyu_utils import generate_mid

    module.generate_mid = generate_mid
    return module.ConnectionManager, module.ConnectionState

@lru_cache(maxsize=1)
def load_message_handler_class():
    prepare_common_utils()
    package_root = REPOSITORY_ROOT / "websocket" / "app" / "services" / "xianyu"
    _ensure_package("connector._existing_xianyu", package_root)
    _load_module("connector._existing_xianyu.utils", package_root / "utils.py")
    module = _load_module(
        "connector._existing_xianyu.message_handler",
        package_root / "message_handler.py",
    )
    return module.MessageHandler


@lru_cache(maxsize=1)
def load_local_runtime_class():
    prepare_common_utils()
    connector_root = REPOSITORY_ROOT / "connector"
    _load_module("connector.xianyu_token", connector_root / "xianyu_token.py")
    module = _load_module(
        "connector._local_xianyu_runtime",
        connector_root / "xianyu_runtime.py",
    )
    return module.LocalXianyuRuntime

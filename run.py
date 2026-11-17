from __future__ import annotations

import argparse
import socket
import subprocess
import time
import urllib.error
import urllib.request

from app.config import settings
from app.security import LOOPBACK_HOSTS, public_bind_allowed


def _pids_on_port(port: int) -> list[int]:
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue).OwningProcess",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    pids: list[int] = []
    for token in (completed.stdout or "").split():
        if token.isdigit():
            pid = int(token)
            if pid > 0 and pid not in pids:
                pids.append(pid)
    return pids


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def _health_ok(host: str, port: int) -> bool:
    url = f"http://{host}:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _stop(pids: list[int]) -> None:
    for pid in pids:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            capture_output=True,
            check=False,
        )


def _loopback_sockets(port: int) -> list[socket.socket]:
    """Слушаем и 127.0.0.1, и ::1: на Windows localhost часто открывается только как IPv6."""
    targets: list[tuple[int, str]] = [(socket.AF_INET, "127.0.0.1")]
    if socket.has_ipv6:
        targets.append((socket.AF_INET6, "::1"))
    sockets: list[socket.socket] = []
    first_error: OSError | None = None
    for family, addr in targets:
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            if family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((addr, port))
            sock.set_inheritable(True)
            sockets.append(sock)
        except OSError as exc:
            sock.close()
            if first_error is None:
                first_error = exc
    if not any(item.family == socket.AF_INET for item in sockets):
        for item in sockets:
            item.close()
        if first_error is not None:
            raise first_error
        raise OSError("не удалось открыть 127.0.0.1")
    return sockets


def _serve(host: str, port: int) -> None:
    try:
        import uvicorn
    except ModuleNotFoundError:
        print("Не найден пакет uvicorn.")
        print("Установите зависимости: python -m pip install -r requirements.txt")
        raise SystemExit(1)
    if host in LOOPBACK_HOSTS:
        sockets = _loopback_sockets(port)
        config = uvicorn.Config("app.main:app", log_level="info", proxy_headers=False)
        server = uvicorn.Server(config)
        try:
            server.run(sockets=sockets)
        finally:
            for item in sockets:
                try:
                    item.close()
                except OSError:
                    pass
        return
    uvicorn.run("app.main:app", host=host, port=port, reload=False, log_level="info", proxy_headers=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Запуск РечьКонтроль")
    parser.add_argument("--stop", action="store_true", help="остановить процесс на порту")
    parser.add_argument("--replace", action="store_true", help="остановить старый процесс и запустить заново")
    args = parser.parse_args()

    host = settings.app_host
    port = settings.app_port
    url = f"http://{host}:{port}"
    if not public_bind_allowed(host):
        print("APP_HOST указывает не на эту машину.")
        print("Для доступа из сети явно задайте ALLOW_REMOTE=true и поставьте TLS-прокси перед приложением.")
        return 1

    if args.stop:
        pids = _pids_on_port(port)
        if not pids:
            print(f"На порту {port} никто не слушает.")
            return 0
        _stop(pids)
        print(f"Остановлены процессы: {', '.join(map(str, pids))}")
        return 0

    if args.replace:
        pids = _pids_on_port(port)
        if pids:
            _stop(pids)
            time.sleep(0.8)
    elif _health_ok(host, port):
        print(f"РечьКонтроль уже запущена: {url}")
        print("Повторный запуск не нужен. Чтобы перезапустить: python run.py --replace")
        return 0
    elif _port_open(host, port):
        pids = _pids_on_port(port)
        extra = f" PID: {', '.join(map(str, pids))}." if pids else ""
        print(f"Порт {port} занят.{extra}")
        print("Если это старый сервер, выполните: python run.py --replace")
        return 1

    print(f"Запускаю РечьКонтроль: {url}")
    if host in LOOPBACK_HOSTS:
        print(f"Тот же кабинет открывается как http://localhost:{port}")
    last_error: OSError | None = None
    for attempt in range(1, 6):
        try:
            _serve(host, port)
            return 0
        except OSError as exc:
            last_error = exc
            if "10048" not in str(exc) and "10013" not in str(exc) and "Address already in use" not in str(exc):
                raise
            print(f"Порт {port} ещё освобождается, повтор {attempt}/5…")
            time.sleep(1.2)
    print(f"Не удалось занять порт {port}: {last_error}")
    print("Закройте другой python run.py или выполните: python run.py --replace")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Командная строка для администратора сервера.

    python -m app.cli apikey create --name CRM [--scopes calls:read,calls:write]
    python -m app.cli apikey list
    python -m app.cli apikey revoke 3
    python -m app.cli inbox scan          # один проход по INBOX_DIR
    python -m app.cli webhook send        # отправить накопленные вебхуки сейчас
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="РечьКонтроль: интеграции")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ak = sub.add_parser("apikey", help="API-ключи внешних систем").add_subparsers(dest="action", required=True)
    c = ak.add_parser("create", help="создать ключ (показывается один раз)")
    c.add_argument("--name", required=True)
    c.add_argument("--scopes", default="calls:read,calls:write")
    ak.add_parser("list", help="список ключей")
    r = ak.add_parser("revoke", help="отозвать ключ")
    r.add_argument("id", type=int)
    ib = sub.add_parser("inbox", help="папка входящих").add_subparsers(dest="action", required=True)
    ib.add_parser("scan", help="один проход по INBOX_DIR")
    wh = sub.add_parser("webhook", help="вебхуки").add_subparsers(dest="action", required=True)
    wh.add_parser("send", help="отправить ожидающие вебхуки")
    args = parser.parse_args(argv)

    from app.audit import write_audit
    from app.db import SessionLocal, init_db

    init_db()
    if args.cmd == "apikey":
        from app.apikeys import create_key, key_out, revoke_key
        from app.models import ApiKey

        with SessionLocal() as session:
            if args.action == "create":
                try:
                    row, key = create_key(session, args.name, args.scopes, created_by="cli")
                except ValueError as exc:
                    print(exc, file=sys.stderr)
                    return 2
                write_audit(session, "apikey.create", actor="cli", detail=f"ключ №{row.id} «{row.name}» ({row.scopes})")
                session.commit()
                print(f"Ключ №{row.id} «{row.name}», права: {row.scopes}")
                print("Сохраните его сейчас, повторно он не показывается:")
                print(key)
            elif args.action == "list":
                for row in session.query(ApiKey).order_by(ApiKey.id).all():
                    k = key_out(row)
                    state = "отозван" if k["revoked"] else "активен"
                    print(f"{k['id']:>4}  {k['prefix']:<16} {state:<8} {','.join(k['scopes']):<24} {k['name']}")
            else:
                row = revoke_key(session, args.id)
                if row is None:
                    print("Ключ не найден", file=sys.stderr)
                    return 1
                write_audit(session, "apikey.revoke", actor="cli", detail=f"ключ №{row.id} «{row.name}»")
                session.commit()
                print(f"Ключ №{row.id} отозван")
        return 0
    if args.cmd == "inbox":
        from app.inbox import inbox_root, recover_processing, scan_once

        if inbox_root() is None:
            print("INBOX_DIR не задан", file=sys.stderr)
            return 1
        recover_processing()
        # два прохода с паузой: файл берётся, только если его размер не меняется
        import time

        from app.config import settings

        scan_once()
        time.sleep(max(0.0, settings.inbox_settle_sec) + 0.2)
        for name, outcome, call_id in scan_once():
            print(f"{name}: {outcome}" + (f" (звонок №{call_id})" if call_id else ""))
        return 0
    from app.webhooks import dispatch_due

    print(f"Отправлено попыток: {dispatch_due(force=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

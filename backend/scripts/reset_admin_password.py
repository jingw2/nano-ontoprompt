"""重置 admin 用户密码。

用法:
    python scripts/reset_admin_password.py                       # 重置为 admin123
    python scripts/reset_admin_password.py --password <new_pwd>  # 重置为指定密码
    python scripts/reset_admin_password.py --user <username>     # 指定用户名 (默认 admin)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models.user import User
from app.services.auth_service import hash_password


def main(username: str, password: str):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            print(f"用户不存在: {username}")
            sys.exit(1)
        user.password_hash = hash_password(password)
        db.commit()
        print(f"已重置 {username} 的密码")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="admin123")
    args = parser.parse_args()
    main(args.user, args.password)

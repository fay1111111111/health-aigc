"""停用账号，令牌立即失效（令牌泄露或人员离开时使用）。

用法（在服务器项目目录下）：
    docker-compose exec -T api python -m scripts.deactivate_user <账号ID>
查看账号 ID：
    docker-compose exec -T api python -m scripts.deactivate_user --list
"""

import sys

from sqlalchemy import select

from app.auth import deactivate_user
from app.db import SessionLocal, init_db
from app.models import User


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    init_db()
    with SessionLocal() as session:
        if sys.argv[1] == "--list":
            for u in session.scalars(select(User).order_by(User.id)):
                print(f"{u.id}\t{u.name}\t{u.role}\t{'启用' if u.active else '已停用'}")
            return
        try:
            user = deactivate_user(session, int(sys.argv[1]))
        except ValueError as e:
            print(f"失败：{e}")
            sys.exit(1)
    print(f"已停用账号：{user.id} {user.name}（{user.role}）")


if __name__ == "__main__":
    main()

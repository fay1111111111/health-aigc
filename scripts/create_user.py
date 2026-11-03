"""创建账号并打印登录令牌（令牌只显示这一次）。

用法（在服务器项目目录下）：
    docker compose exec api python -m scripts.create_user 张三 admin
角色：operator（运营）/ reviewer（审核）/ admin（管理员）
"""

import sys

from app.auth import ROLES, create_user
from app.db import SessionLocal, init_db


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[2] not in ROLES:
        print(__doc__)
        sys.exit(1)
    init_db()
    with SessionLocal() as session:
        user, token = create_user(session, sys.argv[1], sys.argv[2])
    print(f"已创建账号：{user.name}（{user.role}）")
    print(f"登录令牌：{token}")
    print("请妥善保存，令牌只显示这一次。")


if __name__ == "__main__":
    main()

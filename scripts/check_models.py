"""检查服务器上配置的模型能不能调通：主模型、备用模型、向量模型各试一次。

用法（在服务器项目目录下）：
    docker compose exec api python -m scripts.check_models
"""

import sys
import time

from app.config import get_settings
from app.llm import RoutedLLM


def main() -> None:
    s = get_settings()
    llm = RoutedLLM(s)
    ok = True
    messages = [{"role": "user", "content": "用一句话回答：晚上睡不着可以先做什么？"}]
    for name in dict.fromkeys((s.llm_primary, s.llm_fallback)):
        p = llm.providers.get(name)
        if p is None:
            print(f"✗ {name}：没有配置（检查 .env 里的 key）")
            ok = False
            continue
        start = time.time()
        try:
            r = p.chat(messages, 0.7)
            print(f"✓ {r.model}  {time.time() - start:.1f} 秒  {r.text[:40]}")
        except Exception as e:  # noqa: BLE001
            print(f"✗ {name}:{p.model}  {e}")
            ok = False
    try:
        vec = llm.embed(["测试"])[0]
        print(f"✓ 向量模型 {s.embedding_model}  维度 {len(vec)}")
    except Exception as e:  # noqa: BLE001
        print(f"✗ 向量模型 {s.embedding_model}  {e}")
        ok = False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

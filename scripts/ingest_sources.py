"""按来源清单自动抓取公开资料，改写成知识卡片入库。

用法（在服务器项目目录下）：
    docker-compose -p health-aigc exec -T api python -m scripts.ingest_sources sources/batch_01.yaml
可选参数：
    --limit 5        只处理前 5 条（先试跑看效果）
    --retry-failed   重新处理之前失败的
    --force          已处理过的也重新处理

已处理过的资料会自动跳过，中途中断后重新运行即可接着做。
低风险卡片直接可用；中、高风险卡片进入「待审核」，在小程序知识库页审核。
"""

import argparse
import sys
import time
from pathlib import Path

import yaml
from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.ingest import SourceIn, ingest_source
from app.llm import get_llm
from app.models import Source


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    items = yaml.safe_load(Path(args.file).read_text(encoding="utf-8")) or []
    sources = [SourceIn(**{k: v for k, v in it.items() if k in SourceIn.__dataclass_fields__}) for it in items]
    if args.limit:
        sources = sources[:args.limit]

    init_db()
    llm = get_llm()
    totals = {"done": 0, "failed": 0, "skipped": 0, "added": 0, "pending": 0, "copy": 0, "dup": 0}
    failed = []
    with SessionLocal() as session:
        for i, src in enumerate(sources, 1):
            prev = session.scalar(select(Source).where(Source.url == src.url))
            force = args.force or (args.retry_failed and prev is not None and prev.status == "failed")
            t0 = time.time()
            r = ingest_source(session, llm, src, force=force)
            totals[r.status] = totals.get(r.status, 0) + 1
            totals["added"] += r.added
            totals["pending"] += r.pending
            totals["copy"] += r.dropped_copy
            totals["dup"] += r.dropped_dup
            label = f"[{i}/{len(sources)}] {src.publisher}《{src.title}》"
            if r.status == "done":
                print(f"{label}：{r.chunks} 段，提取 {r.extracted} 张，入库 {r.added} 张，待审 {r.pending} 张，"
                      f"去重 {r.dropped_dup}，照搬丢弃 {r.dropped_copy}（{time.time() - t0:.0f} 秒）", flush=True)
            elif r.status == "skipped":
                print(f"{label}：已处理过，跳过", flush=True)
            else:
                failed.append((src, r.error))
                print(f"{label}：失败 —— {r.error}", flush=True)

    print("\n===== 汇总 =====")
    print(f"成功 {totals['done']} 条，失败 {totals['failed']} 条，跳过 {totals['skipped']} 条")
    print(f"直接入库 {totals['added']} 张，待审核 {totals['pending']} 张，"
          f"去重丢弃 {totals['dup']} 张，照搬丢弃 {totals['copy']} 张")
    if failed:
        print("\n失败清单：")
        for src, err in failed:
            print(f"- {src.url}  {err}")
    sys.exit(0)


if __name__ == "__main__":
    main()

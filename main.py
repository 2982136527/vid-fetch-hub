#!/usr/bin/env python3
"""
Vid-Fetch-Hub — 统一视频爬虫 + Emby 代理
===========================================
菜单交互模式 + 全自动守护模式。
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from core import Config
from core.database import Database
from core.downloader import Downloader
from core.proxy import VideoProxy

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------
_config: Config = None
_db: Database = None
_dl: Downloader = None
_proxy: VideoProxy = None
_running = True


def setup():
    global _config, _db, _dl

    # Docker: auto-generate config.yaml if user mounted empty /config directory
    _ensure_docker_config()

    _config = Config()

    out = _config.output_dir
    out.mkdir(parents=True, exist_ok=True)
    db_dir = out / ".db"
    db_dir.mkdir(parents=True, exist_ok=True)
    _db = Database(db_dir / "crawl_state.sqlite")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )


def _ensure_docker_config():
    """If /config dir exists but config.yaml doesn't, copy the default."""
    docker_config = Path("/config/config.yaml")
    if docker_config.parent.exists() and not docker_config.exists():
        bundled = Path(__file__).parent / "config.yaml"
        if bundled.exists():
            import shutil
            shutil.copy(str(bundled), str(docker_config))
            print(f"  📝 已生成默认配置文件: {docker_config}")


# ---------------------------------------------------------------------------
# 菜单
# ---------------------------------------------------------------------------

def menu():
    print()
    print("=" * 50)
    print("   Vid-Fetch-Hub  v1.0")
    print("=" * 50)
    print("  1. 🚀  全量爬取（所有启用的源）")
    print("  2. 🔄  增量更新（只检查新的）")
    print("  3. 🌐  启动 / 停止 代理服务")
    print("  4. 📊  查看统计")
    print("  5. ⚙️  修改配置")
    print("  7. 🤖  全自动模式（代理 + 回填 + 定时增量）")
    print("  6. ❌  退出")
    print("-" * 50)


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def get_choice(prompt: str = "  请选择 [1-7]: ") -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return "6"


def wait_enter():
    input("\n  按回车键继续...")


# ---------------------------------------------------------------------------
# 爬虫
# ---------------------------------------------------------------------------

def _init_crawlers(dl):
    from crawlers.xvideos import XVideosCrawler
    from crawlers.youporn import YouPornCrawler
    from crawlers.redtube import RedTubeCrawler
    from crawlers.thumbzilla import ThumbzillaCrawler
    from crawlers.acfan import AcfanCrawler
    from crawlers.pornhub import PornHubCrawler
    from crawlers.tube8 import Tube8Crawler
    from crawlers.madou import MadouCrawler
    from crawlers.eighteen_mh import EighteenMHCrawler

    mapping = {
        "xvideos": XVideosCrawler,
        "youporn": YouPornCrawler,
        "redtube": RedTubeCrawler,
        "thumbzilla": ThumbzillaCrawler,
        "acfan": AcfanCrawler,
        "pornhub": PornHubCrawler,
        "tube8": Tube8Crawler,
        "madou": MadouCrawler,
        "eighteen_mh": EighteenMHCrawler,
    }
    result = []
    for name, cls in mapping.items():
        if _config.is_site_enabled(name):
            result.append(cls(_config, _db, dl))
    return result


async def run_crawl(backfill: bool = False) -> int:
    """Run all enabled crawlers in parallel. Returns total new videos."""
    dl = Downloader(
        user_agent=_config.user_agent,
        timeout=_config.timeout,
        retry_max=_config.retry_max,
        retry_delay=_config.retry_delay,
        rate_limit=_config.rate_limit,
        http_proxy=_config.http_proxy,
        https_proxy=_config.https_proxy,
    )
    crawlers = _init_crawlers(dl)
    if not crawlers:
        logger = logging.getLogger("crawl")
        logger.warning("没有启用的爬虫")
        await dl.close()
        return 0

    label = "全量" if backfill else "增量"
    logger = logging.getLogger("crawl")
    logger.info(f"开始{label}爬取 ({len(crawlers)} 个源并行)...")

    # 并行运行所有爬虫
    results = await asyncio.gather(
        *(c.crawl(backfill=backfill) for c in crawlers),
        return_exceptions=True,
    )

    total = 0
    for c, result in zip(crawlers, results):
        if isinstance(result, Exception):
            logger.error(f"{c.name} 出错: {result}")
        else:
            total += result
            logger.info(f"{c.name}: 新增 {result}")

    await dl.close()
    return total


# ---------------------------------------------------------------------------
# 自动守护模式
# ---------------------------------------------------------------------------

async def auto_daemon():
    """全自动模式：启动代理 → 全量回填 → 定时增量循环。按 Q 退出。"""
    global _proxy

    logger = logging.getLogger("daemon")
    interval = _config.check_interval_minutes * 60

    # 1. 启动代理
    if not _proxy or not _proxy.running:
        dl = Downloader(
            user_agent=_config.user_agent,
            timeout=_config.timeout,
            retry_max=_config.retry_max,
            retry_delay=_config.retry_delay,
            rate_limit=_config.rate_limit,
        )
        _proxy = VideoProxy(_config, _db, dl)
        await _proxy.start()
        logger.info(f"代理已启动: http://{_config.proxy_host}:{_config.proxy_port}")

    # 2. 全量回填：持续跑直到0新增才算完成
    logger.info("开始全量回填（未完成的会一直跑直到覆盖全部历史）...")
    while True:
        total = await run_crawl(backfill=True)
        if total == 0:
            # 所有源都没有新视频了，全量回填确实完成
            stats = _db.get_stats()
            logger.info(f"全量回填完成！数据库总计 {stats['total']} 个视频")
            break
        else:
            stats = _db.get_stats()
            logger.info(f"回填进度: 本次新增 {total}, 累计 {stats['total']}。继续下一轮...")

    # 3. 定时增量循环
    logger.info(f"进入定时增量模式，间隔 {_config.check_interval_minutes} 分钟")
    while _running:
        logger.info(f"等待 {_config.check_interval_minutes} 分钟后下次检查...")
        for _ in range(interval):
            if not _running:
                break
            await asyncio.sleep(1)
        if not _running:
            break

        logger.info("开始增量更新...")
        try:
            cnt = await run_crawl(backfill=False)
            if cnt:
                logger.info(f"增量更新完成: 新增 {cnt}")
            else:
                logger.info("无新视频")
        except Exception as e:
            logger.error(f"增量更新出错: {e}")

    # 清理
    if _proxy and _proxy.running:
        await _proxy.stop()
    logger.info("自动模式已停止")


# ---------------------------------------------------------------------------
# 菜单动作
# ---------------------------------------------------------------------------

async def run_backfill():
    total = await run_crawl(backfill=True)
    s = _db.get_stats()
    print(f"\n  ✅ 全量爬取完成！本次新增: {total}")
    print(f"  数据库总计: {s['total']} 个视频")
    wait_enter()


async def run_incremental():
    total = await run_crawl(backfill=False)
    print(f"\n  ✅ 增量更新完成！本次新增: {total}")
    wait_enter()


async def toggle_proxy():
    global _proxy
    if _proxy and _proxy.running:
        print("\n  正在停止代理服务...")
        await _proxy.stop()
        _proxy = None
        print("  ✅ 代理已停止")
    else:
        dl = Downloader(
            user_agent=_config.user_agent,
            timeout=_config.timeout,
            retry_max=_config.retry_max,
            retry_delay=_config.retry_delay,
            rate_limit=_config.rate_limit,
            http_proxy=_config.http_proxy,
            https_proxy=_config.https_proxy,
        )
        _proxy = VideoProxy(_config, _db, dl)
        await _proxy.start()
        print(f"\n  🌐 代理已启动: http://{_config.proxy_host}:{_config.proxy_port}")
        print(f"  STRM 格式: http://{_config.proxy_host}:{_config.proxy_port}/proxy/{{source}}/{{video_id}}")
    wait_enter()


def show_stats():
    s = _db.get_stats()
    print(f"\n  📊  数据库统计")
    print(f"  {'=' * 40}")
    print(f"  视频总数: {s['total']}")
    print(f"  {'─' * 40}")
    if s["by_source"]:
        for src, cnt in sorted(s["by_source"].items()):
            print(f"    {src}: {cnt}")
    else:
        print("    (还没有数据，先爬取吧)")
    wait_enter()


def edit_config():
    cf = Path(__file__).parent / "config.yaml"
    print(f"\n  配置文件路径: {cf}")
    print(f"  请手动编辑此文件后重启程序。")
    print(f"\n  常用编辑命令:")
    print(f"    nano {cf}")
    print(f"    vim {cf}")
    wait_enter()


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

async def main():
    global _running, _dl

    setup()
    clear_screen()

    print("  Vid-Fetch-Hub 启动中...")
    print(f"  输出目录: {_config.output_dir}")
    print(f"  配置文件: config.yaml")
    print()

    # Command-line shortcut: --auto 直接进自动模式
    if "--auto" in sys.argv:
        await auto_daemon()
        return

    while _running:
        menu()
        choice = get_choice()

        if choice == "1":
            await run_backfill()
        elif choice == "2":
            await run_incremental()
        elif choice == "3":
            await toggle_proxy()
        elif choice == "4":
            show_stats()
        elif choice == "5":
            edit_config()
        elif choice == "7":
            print("\n  🤖 进入全自动模式...")
            print("  按 Ctrl+C 停止\n")
            await auto_daemon()
            print("\n  已退出自动模式\n")
        elif choice == "6":
            print("\n  正在退出...")
            if _proxy and _proxy.running:
                await _proxy.stop()
            _running = False
        else:
            print("  ❓ 无效选择，请输入 1-7")

    if _db:
        _db.close()
    print("  Bye!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  Bye!")

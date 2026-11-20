"""环境侦察脚本:一次性摸清各签到目标的路径、窗口与截图。

用法:
    .venv\\Scripts\\python.exe probe.py                     # 侦察所有已配置目标
    .venv\\Scripts\\python.exe probe.py --target trae       # 只看一个目标
    .venv\\Scripts\\python.exe probe.py --vision            # 额外调用视觉模型定位一次
    .venv\\Scripts\\python.exe probe.py --exe "D:\\x\\Trae CN.exe"   # 顺手把路径写回目标配置

产出:
    data/probe/report-<时间>.txt   侦察报告
    data/probe/shot-<时间>[-目标].png  截图(窗口优先,找不到窗口则整屏)
"""
from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from datetime import datetime
from pathlib import Path


def set_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="签到目标环境侦察")
    parser.add_argument("--target", default="", help="只侦察某个目标 id(默认全部)")
    parser.add_argument("--exe", default="", help="把该路径写入目标配置后再侦察")
    parser.add_argument("--vision", action="store_true", help="额外跑一次视觉定位测试(需已配置 API Key)")
    parser.add_argument("--title", default=None, help="窗口标题关键字(默认读目标配置)")
    args = parser.parse_args()

    set_dpi_awareness()

    from core.config import PROJECT_ROOT, ConfigStore
    from core.logging_setup import setup_logging

    setup_logging(None, keep_days=3)
    from loguru import logger

    import core.capture as capture
    import core.targets as tmod
    import core.zcode_ctrl as zcode
    from core.vision import VisionClient

    cfg = ConfigStore()
    probe_dir = PROJECT_ROOT / "data" / "probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    lines: list[str] = []

    def report(text: str = "") -> None:
        lines.append(text)
        print(text)

    items = tmod.all_targets(cfg.all())
    if args.target:
        items = [t for t in items if t["id"] == args.target]
        if not items:
            report(f"没有 id 为 {args.target} 的签到目标,现有:{[t['id'] for t in tmod.all_targets(cfg.all())]}")
            return 2

    report(f"=== 签到目标环境侦察 {datetime.now():%Y-%m-%d %H:%M:%S} ===")
    report(f"本程序自身:{zcode.own_exe()}")
    names = ", ".join("{}({})".format(t["id"], t["flow"]) for t in items)
    report(f"待侦察目标:{names or '(无)'}")

    shot_for_vision = None
    for target in items:
        if args.exe:
            if not Path(args.exe).is_file():
                report(f"\n⚠ 路径不存在,未写入配置:{args.exe}")
            elif zcode.is_own_exe(args.exe):
                report(f"\n⚠ 拒绝把助手自己({args.exe})设为签到目标")
            else:
                patched = tmod.all_targets(cfg.all())
                for item in patched:
                    if item["id"] == target["id"]:
                        item["exe"] = args.exe
                cfg.patch({"targets": patched})
                target["exe"] = args.exe
                report(f"\n已把路径写入目标 {target['id']}:{args.exe}")

        report(f"\n----- 目标 {target['id']}「{target['name']}」 -----")
        title = args.title or target["window_title_match"]
        report(f"  查找方式:{target['find_by']}  标题关键字:{title}")

        # 1) 进程与路径
        exe = target["exe"]
        report(f"  配置路径:{exe or '(空)'}"
               + ("" if not exe else ("  [文件存在]" if Path(exe).is_file() else "  [文件不存在!]")))
        if not exe:
            found = zcode.autodetect_exe(Path(title or "zcode").stem or "zcode")
            report(f"  自动探测:{found or '未发现'}(不会返回本程序自身)")
        pids = zcode.pids_of_exe(exe) if exe else []
        report(f"  运行中的进程:{pids or '无'}")

        # 2) 窗口
        windows = zcode.windows_of_pid(pids[0]) if pids else []
        if pids and not windows:
            report("  进程在运行,但没有找到属于它的合格主窗口(可能最小化或在别的桌面)")
        if not windows:
            windows = zcode.list_windows(title)
            for win in windows:
                ok, reason = zcode.verify_client_window(win, exe)
                report(f"  {'✓ 可用' if ok else '✗ 已排除'} hwnd={win['hwnd']} rect={win['rect']} "
                       f"title={win['title']!r}")
                report(f"       {reason}")
        else:
            for win in windows:
                report(f"  hwnd={win['hwnd']} pid={zcode.window_pid(win['hwnd'])} "
                       f"rect={win['rect']} title={win['title']!r}")
        main_window = zcode.find_target_window(target)
        report(f"  → 采用窗口:{(main_window or {}).get('rect') or '无'}")

        # 3) 每个录制点的实际屏幕坐标
        if target["steps"]:
            from core.coords import resolve

            report(f"  录制点击点 {len(target['steps'])} 个:")
            for index, step in enumerate(target["steps"], start=1):
                point = resolve((main_window or {}).get("rect") or [0, 0, 0, 0], step)
                report(f"    {index}. {step['label']} [{step['anchor']} +{step['dx']},-{step['dy']}] → {point}")

        # 4) 截图
        if main_window:
            shot = capture.grab_region(tuple(main_window["rect"]))
            source = "窗口"
        else:
            shot = capture.grab_fullscreen()
            source = "整屏"
        shot_path = probe_dir / f"shot-{stamp}-{target['id']}.png"
        shot_path.write_bytes(shot.png)
        report(f"  截图来源:{source} 尺寸:{shot.width}x{shot.height} 单色占比:{shot.uniform_ratio:.3f}")
        report(f"  已保存:{shot_path}")
        if shot.uniform_ratio > 0.95:
            report("  ⚠ 画面接近纯色:可能锁屏/黑屏,或窗口尚未渲染")
        if shot_for_vision is None:
            shot_for_vision = (target, shot)

    # 5) 视觉定位(可选)
    if args.vision:
        report("\n===== 视觉定位测试(DeepSeek) =====")
        if not (cfg.get("vision", "api_key") or "").strip():
            report("  未配置 API Key,跳过。请先在面板「识别」页填写。")
        elif shot_for_vision is None:
            report("  没有可用截图,跳过。")
        else:
            target, shot = shot_for_vision
            report(f"  使用目标:{target['id']}「{target['name']}」")
            start = time.time()
            result = VisionClient(cfg).locate(shot.png)
            report(f"  耗时:{time.time() - start:.1f}s")
            report("  返回:" + json.dumps(result, ensure_ascii=False, indent=2))
            if not result.get("error"):
                report("  → 请核对:图上是否有可点击的领取按钮?button_box 是否大致落在按钮上?")

    report("\n=== 侦察结束 ===")
    report_path = probe_dir / f"report-{stamp}.txt"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"报告已保存:{report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

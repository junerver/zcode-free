"""配置管理:data/config.yaml 与默认值深度合并,线程安全读写。

配置结构见技术方案 §8。UI 每次修改走 patch() 即时落盘。

路径分两种,打包后必须分开:
  资源(ui/)      从解包目录 sys._MEIPASS 读,只读
  数据(data/)    写到 exe 同级目录,保证重启后配置还在;不可写时回退 LOCALAPPDATA
源码运行时两者都在项目根目录下,行为与以前完全一致。
"""
from __future__ import annotations

import copy
import os
import sys
import threading
from pathlib import Path

import yaml
from loguru import logger

from core.targets import DEFAULT_TARGETS

FROZEN = getattr(sys, "frozen", False)


def _writable(path: Path) -> bool:
    """目录是否可写(不存在则看父目录能否创建)。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


if FROZEN:
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    _exe_dir = Path(sys.executable).parent
    DATA_DIR = _exe_dir / "data"
    if not _writable(DATA_DIR):
        # exe 放在 Program Files 等只读位置时,退到用户目录
        _fallback = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ZCode福利助手" / "data"
        DATA_DIR = _fallback if _writable(_fallback) else DATA_DIR
    # 打包后没有"项目根",保留同名常量指向 exe 所在目录(仅开发期工具引用)
    PROJECT_ROOT = _exe_dir
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    RESOURCE_DIR = PROJECT_ROOT
    DATA_DIR = PROJECT_ROOT / "data"

CONFIG_PATH = DATA_DIR / "config.yaml"
LOG_DIR = DATA_DIR / "logs"
SHOTS_DIR = DATA_DIR / "shots"
UI_DIR = RESOURCE_DIR / "ui"

DEFAULTS: dict = {
    # 全局运行时开关。每个签到目标的exe/窗口/流程/周期都在 targets 里,见 core/targets.py
    "app": {
        "dry_run": False,
    },
    "targets": copy.deepcopy(DEFAULT_TARGETS),
    "schedule": {
        "enabled": True,
        # 示例: {"time": "10:00", "enabled": True, "target": "zcode", "attempts": None, "retry_gap_s": None}
        # target 留空 = 跑所有启用的目标;指定 id 则只跑那一个
        "slots": [],
        "keep_awake": True,
    },
    "vision": {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model": "deepseek-v4-flash-vision-exp",
        "temperature": 0.2,
        "detail": "auto",
        "max_tokens": 800,
        "timeout_s": 20,
        "locate_confidence_min": 0.70,
        "humanize_mouse": False,
    },
    "retry": {
        "max_attempts": 5,
        "retry_gap_s": 5,
        "open_timeout_s": 30,
        "settle_s": 2.0,
        "focus_settle_s": 0.4,
        "app_ready_timeout_s": 20,     # 等待客户端界面渲染完成(加载期纯色不算锁屏)
        "verify_ready_timeout_s": 8,   # 单次校验截图等待界面就绪的上限
        "verify_wait_s": 30,           # 点击后等待加载动画的秒数,到点截图判定一次
        "no_card_wait_s": 5,           # 卡片不在画面上时,复查前等待的秒数
        "ready_poll_s": 1.0,           # 渲染等待的轮询间隔
        "uniform_ratio_max": 0.98,     # 单色占比高于此值视为"还没渲染出来"
        "user_idle_s": 1.0,            # 需连续多少秒无键鼠输入才动手(0=关闭该保护)
        "user_idle_wait_s": 20,        # 用户正在用电脑时,最多等这么久再放弃本场
        "watchdog_s": 480,       # 单次尝试的超时上限;按次重置,不再提前腰斩长场次
    },
    "success_keywords": ["领取成功", "开始体验", "奖励到账", "已到账", "领取奖励", "获得", "+"],
    "claimed_keywords": ["已领取", "今日已领", "今日已领取", "明天再来", "明日再来", "已参加", "次数已用完"],
    "failure_keywords": ["领取失败", "知道了"],
    # 领取请求已提交、界面还在加载的文案:命中则继续等结果,不当作失败
    "loading_keywords": ["领取中", "处理中", "加载中", "请稍候", "请稍等", "提交中", "发放中"],
    "notify": {
        "desktop": True,
        "webhook_url": "",
    },
    "data": {
        "keep_days": 7,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """override 覆盖 base 的深度合并,返回新 dict。"""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _with_migration(cfg: dict, path: Path, user_cfg: dict) -> dict:
    """把 v1.x 的单目标 `app:` 配置翻译成 `targets` 列表,一次性落盘。

    迁移只做保真(路径/标题/全屏/启动等待/每周一次),不改行为语义之外的东西;
    每个已有场次补上 target,保证老配置迁移后跑的还是同一个应用。
    """
    from core.targets import migrate_from_legacy

    # 判据必须是「用户文件里有没有自己的目标」:DEFAULTS 一直带一个出厂目标,
    # 拿合并后的 cfg 判断会永远以为"已有 targets",v1.x 的配置就再也不会被迁移。
    if not path.exists() or user_cfg.get("targets"):
        return cfg
    migrated = migrate_from_legacy({**cfg, "targets": user_cfg.get("targets")})
    if not migrated:
        return cfg
    cfg["targets"] = migrated
    # 旧键全部作废:留在文件里只会让人以为改它还有效
    for legacy_key in ("zcode_path", "window_title_match", "fullscreen", "startup_wait_s",
                       "restore_window_rect", "window_rect", "weekly_once"):
        cfg.get("app", {}).pop(legacy_key, None)
    slots = cfg.get("schedule", {}).get("slots") or []
    for slot in slots:
        if isinstance(slot, dict) and not slot.get("target"):
            slot["target"] = migrated[0]["id"]
    try:
        save_config(cfg, path)
    except OSError as exc:
        logger.warning(f"配置迁移结果写入失败(本次仍按迁移后的配置运行):{exc}")
    return cfg


def load_config(path: Path = CONFIG_PATH) -> dict:
    """读取配置并与默认值合并(缺项自动补全),必要时执行一次旧配置迁移。"""
    user_cfg: dict = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict):
            user_cfg = raw
    return _with_migration(_deep_merge(DEFAULTS, user_cfg), path, user_cfg)


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    """写盘(UTF-8,允许中文原文)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


class ConfigStore:
    """线程安全的配置容器,支持分节 patch 即时落盘。"""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._cfg = load_config(self.path)
        if not self.path.exists():
            save_config(self._cfg, self.path)

    def get(self, *keys, default=None):
        """cfg.get("vision") 或 cfg.get("retry", "max_attempts")。"""
        with self._lock:
            node = self._cfg
            for key in keys:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return node

    def all(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._cfg)

    def patch(self, patch_dict: dict) -> dict:
        """深度合并并落盘,返回合并后的完整配置。"""
        with self._lock:
            self._cfg = _deep_merge(self._cfg, patch_dict or {})
            save_config(self._cfg, self.path)
            return copy.deepcopy(self._cfg)

    def reset(self) -> dict:
        """恢复默认配置(保留签到目标与 API Key,其余全部重置)。"""
        with self._lock:
            keep = {
                "targets": self._cfg.get("targets") or copy.deepcopy(DEFAULT_TARGETS),
                "vision": {"api_key": self._cfg.get("vision", {}).get("api_key", "")},
            }
            self._cfg = _deep_merge(DEFAULTS, keep)
            save_config(self._cfg, self.path)
            return copy.deepcopy(self._cfg)

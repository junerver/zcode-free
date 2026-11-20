"""签到目标模型:每个应用一份「怎么打开、怎么点、怎么判定、多久跑一次」。

一个 target 描述:
  exe / find_by / window_title_match  如何找到自己的主窗口
  fullscreen / startup_wait_s         打开后的窗口处理
  flow = vision | steps               点击方式:视觉找按钮,或按录制坐标逐步点
  steps[]                             flow=steps 时的点击序列(锚点 + 偏移)
  post_steps[]                        领取成功之后再补点的位置(关掉结果浮窗)
  verify = vision | none              点击后是否用视觉复核结果
  once = none | daily | weekly        周期间隔:none 表示每个场次都照常执行(不跳过)

坐标一律由录制面板产生(见 api.capture_target),不在源码里硬编码像素 ——
换显示器/改分辨率时只有面板知道当前窗口矩形。
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

from core.coords import ANCHORS

FLOW_VISION = "vision"
FLOW_STEPS = "steps"
VERIFY_VISION = "vision"
VERIFY_NONE = "none"
ONCE_NONE = "none"
ONCE_DAILY = "daily"
ONCE_WEEKLY = "weekly"

FLOWS = (FLOW_VISION, FLOW_STEPS)
VERIFIES = (VERIFY_VISION, VERIFY_NONE)
ONCE_MODES = (ONCE_NONE, ONCE_DAILY, ONCE_WEEKLY)

TARGET_DEFAULTS: dict = {
    "enabled": True,
    "name": "",
    "exe": "",
    "find_by": "process",          # process:先找进程再认窗口 | title:老逻辑,按标题找
    "window_title_match": "",
    "fullscreen": True,
    "startup_wait_s": 5.0,
    "flow": FLOW_VISION,
    "steps": [],
    "post_steps": [],
    "verify": VERIFY_VISION,
    "once": ONCE_NONE,
    # 覆盖全局词表(可空):某个应用的文案和别家不一样时用
    "success_keywords": [],
    "claimed_keywords": [],
    "failure_keywords": [],
    "loading_keywords": [],
}

# 出厂只有一个目标:ZCode 的视觉流程。路径交给「探测」或用户填写。
DEFAULT_TARGETS: list[dict] = [
    {
        "id": "zcode",
        "name": "ZCode",
        "exe": "",
        "find_by": "process",
        "window_title_match": "ZCode",
        "flow": FLOW_VISION,
        "verify": VERIFY_VISION,
        "once": ONCE_NONE,
    }
]

_ID_RE = re.compile(r"[^a-z0-9_-]+")


def slugify(text: str, fallback: str = "target") -> str:
    out = _ID_RE.sub("", str(text or "").strip().lower())
    return out or fallback


def _clean_step(raw: dict, index: int) -> dict | None:
    if not isinstance(raw, dict):
        return None
    anchor = str(raw.get("anchor") or "window")
    if anchor not in ANCHORS:
        return None
    try:
        dx = float(raw.get("dx"))
        dy = float(raw.get("dy"))
    except (TypeError, ValueError):
        return None
    out: dict = {
        "id": str(raw.get("id") or f"step{index + 1}"),
        "label": str(raw.get("label") or f"第 {index + 1} 步"),
        "anchor": anchor,
        "dx": dx,
        "dy": dy,
        "wait_after_s": max(0.0, float(raw.get("wait_after_s") or 0)),
    }
    if raw.get("note"):
        out["note"] = str(raw["note"])[:120]
    return out


def normalize(raw: dict, target_id: str = "") -> dict | None:
    """补齐默认值并清洗成可执行的目标;入参不是 dict 时返回 None。

    id 由名称/exe 名转写;中文等无法转写的名称退回 target_id 或 "target",
    再由 normalize_all 保证同表内不重名。
    """
    if not isinstance(raw, dict):
        return None
    tid = slugify(raw.get("id") or target_id or raw.get("name") or "")
    out = copy.deepcopy(TARGET_DEFAULTS)
    out["id"] = tid
    # 只遍历默认字段:id 已经在上面清洗过,不能被原始值重新覆盖回去
    for key in TARGET_DEFAULTS:
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        if key in ("name", "exe", "window_title_match", "find_by", "flow", "verify", "once"):
            out[key] = str(value).strip()
        elif key in ("steps", "post_steps"):
            out[key] = [s for s in (
                _clean_step(item, i) for i, item in enumerate(value or [])
            ) if s]
        elif key in ("success_keywords", "claimed_keywords", "failure_keywords", "loading_keywords"):
            out[key] = [str(k).strip() for k in (value or []) if str(k).strip()]
        elif key == "enabled":
            out[key] = bool(value)
        else:
            out[key] = value

    out["find_by"] = "title" if out["find_by"] == "title" else "process"
    out["flow"] = out["flow"] if out["flow"] in FLOWS else FLOW_VISION
    out["verify"] = out["verify"] if out["verify"] in VERIFIES else VERIFY_VISION
    out["once"] = out["once"] if out["once"] in ONCE_MODES else ONCE_NONE
    try:
        out["startup_wait_s"] = max(0.0, float(out.get("startup_wait_s") or 0))
    except (TypeError, ValueError):
        out["startup_wait_s"] = 5.0
    if not out["name"]:
        out["name"] = Path(out["exe"]).stem if out["exe"] else out["id"]
    if not out["window_title_match"]:
        out["window_title_match"] = out["name"] or out["id"]
    return out


def normalize_all(raw_list) -> list[dict]:
    """清洗目标列表,顺带保证 id 唯一(重复时追加序号)。"""
    out: list[dict] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_list or []):
        target = normalize(raw, target_id=f"target{index + 1}")
        if not target:
            continue
        base = target["id"]
        suffix = 2
        while target["id"] in seen:
            target["id"] = f"{base}-{suffix}"
            suffix += 1
        seen.add(target["id"])
        out.append(target)
    return out


def all_targets(cfg) -> list[dict]:
    getter = getattr(cfg, "get", None)
    raw = getter("targets") if callable(getter) else (cfg or {}).get("targets")
    return normalize_all(raw or DEFAULT_TARGETS)


def find(cfg, target_id: str = "") -> dict | None:
    """按 id 取目标;id 为空时取第一个启用的目标。"""
    items = all_targets(cfg)
    if not items:
        return None
    if target_id:
        for target in items:
            if target["id"] == str(target_id):
                return target
    for target in items:
        if target["enabled"]:
            return target
    return items[0]


def enabled_targets(cfg) -> list[dict]:
    return [t for t in all_targets(cfg) if t["enabled"]]


def keywords_for(target: dict, cfg, key: str) -> list[str]:
    """目标自带的词表优先,没有则退回全局配置。"""
    own = (target or {}).get(key)
    if own:
        return list(own)
    getter = getattr(cfg, "get", None)
    return list(getter(key) if callable(getter) else (cfg or {}).get(key) or [])


def migrate_from_legacy(cfg: dict) -> list[dict] | None:
    """把 v1.x 的单目标 app 段翻译成一个 ZCode 目标。

    只负责保真:weekly_once=True 的语义是「本周期已领则跳过后续场次」,对应 once=weekly。
    想要「每天每场都跑」需要显式改成 once=none(部署时按用户要求处理)。
    返回 None 表示无需迁移。
    """
    if cfg.get("targets"):
        return None
    legacy = cfg.get("app")
    if not isinstance(legacy, dict):
        return None
    target = {
        "id": "zcode",
        "name": "ZCode",
        "exe": str(legacy.get("zcode_path") or "").strip(),
        "find_by": "process",
        "window_title_match": str(legacy.get("window_title_match") or "ZCode"),
        "fullscreen": bool(legacy.get("fullscreen", True)),
        "startup_wait_s": float(legacy.get("startup_wait_s") or 5.0),
        "flow": FLOW_VISION,
        "verify": VERIFY_VISION,
        "once": ONCE_WEEKLY if legacy.get("weekly_once", True) else ONCE_NONE,
    }
    return [target]


def label(target: dict) -> str:
    return f"{target.get('name') or target.get('id')}"

"""UI 后端 API:原生窗口(js_api)与浏览器回退(JSON-RPC)共用同一套方法。

前端调用约定:每个方法接收一个 payload dict(可为 None),返回 JSON 可序列化结果。
"""
from __future__ import annotations

import base64
import os
import threading
import time
from pathlib import Path

from loguru import logger

import core.capture as capture
import core.targets as tmod
import core.zcode_ctrl as zcode
from core.config import CONFIG_PATH, DATA_DIR, DEFAULTS, LOG_DIR, ConfigStore
from core.coords import ANCHORS, from_click, resolve
from core.events import EventBus
from core.logging_setup import get_logs
from core.runner import Runner, steps_for
from core.scheduler import SlotScheduler, parse_days, parse_hhmm
from core.storage import SessionStore
from core.validate import judge_verify, normalize_box
from core.vision import LOCATE_PROMPT, VERIFY_PROMPT, VisionClient, verify_prompt_for


def _data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


class Api:
    def __init__(self, cfg: ConfigStore, bus: EventBus, store: SessionStore,
                 vision: VisionClient, runner: Runner, scheduler: SlotScheduler):
        self.cfg = cfg
        self.bus = bus
        self.store = store
        self.vision = vision
        self.runner = runner
        self.scheduler = scheduler
        self.ui_ready_event = threading.Event()

    # 事件统一由 core/server.py 的 /events(SSE)推送给浏览器面板;此处不持有任何窗口对象。

    def ui_ready(self, payload=None) -> dict:
        """前端启动完成时调用一次:确认面板已加载(事件走 SSE,不依赖 JS 注入)。"""
        self.ui_ready_event.set()
        logger.info("面板前端已就绪")
        return {"ok": True, "mode": "web"}

    # ---------- 总状态 ----------

    def get_state(self, payload=None) -> dict:
        cfg = self.cfg.all()
        slots = cfg["schedule"].get("slots") or []
        items = tmod.all_targets(cfg)
        return {
            "master": bool(cfg["schedule"].get("enabled", True)),
            "running": self.runner.is_running(),
            "current": self.runner.current,
            "next_slot": self.scheduler.next_run(),
            "slots_total": len(slots),
            "slots_enabled": len([s for s in slots if isinstance(s, dict) and s.get("enabled", True)]),
            "slots": slots,          # 面板首页的 24 小时轨道按时间画出每个场次
            "today": self.store.today_stats(),
            "recent": self.store.list_sessions(limit=12),   # 概览底部的记录表要铺满面板
            "has_key": bool((cfg["vision"].get("api_key") or "").strip()),
            "targets": [self._target_view(t, cfg) for t in items],
            "dry_run": bool(cfg["app"].get("dry_run")),
            "config_path": str(CONFIG_PATH),
        }

    def _target_view(self, target: dict, cfg: dict) -> dict:
        """给面板用的目标概览:配置 + 是否已在运行 + 本周期领取状态。"""
        running = bool(zcode.pids_of_exe(target["exe"])) if target["exe"] else False
        state = self.runner.claims.state(target["id"], target.get("once") or tmod.ONCE_NONE)
        return {
            "id": target["id"],
            "name": target["name"],
            "enabled": target["enabled"],
            "exe": target["exe"],
            "find_by": target["find_by"],
            "window_title_match": target["window_title_match"],
            "fullscreen": target["fullscreen"],
            "startup_wait_s": target["startup_wait_s"],
            "flow": target["flow"],
            "verify": target["verify"],
            "once": target["once"],
            "steps": target["steps"],
            "steps_count": len(target["steps"] or []),
            "post_steps": target["post_steps"],
            "post_steps_count": len(target["post_steps"] or []),
            "running": running,
            "executable_missing": bool(target["exe"]) and not Path(target["exe"]).is_file(),
            "needs_setup": not target["exe"] or (
                target["flow"] == tmod.FLOW_STEPS and not target["steps"]),
            "phases": steps_for(target),
            "period": state,
        }

    def set_master(self, payload=None) -> dict:
        enabled = bool((payload or {}).get("enabled", True))
        self.cfg.patch({"schedule": {"enabled": enabled}})
        self.scheduler.reload()
        self.bus.publish("state_change", {})
        logger.info(f"总开关已{'开启' if enabled else '关闭'}")
        return {"ok": True, "master": enabled}

    def run_once(self, payload=None) -> dict:
        """手动跑一次:<payload.dry> 为演练(只截图识别,不点击、不关客户端);
        <payload.target> 指定只跑某个签到目标,留空则跑所有启用的目标。"""
        if self.runner.is_running():
            return {"ok": False, "message": "已有任务在执行,请稍候"}
        payload = payload or {}
        dry = bool(payload.get("dry"))
        slot: dict = {"dry": True} if dry else {}
        if payload.get("target"):
            slot["target"] = str(payload["target"])
        items = self.runner.slot_targets(slot)
        if not items:
            return {"ok": False, "message": "没有可执行的签到目标,请先到「签到目标」页配置"}
        ok = self.runner.spawn("manual", slot or None)
        if not ok:
            return {"ok": False, "message": "启动失败"}
        names = "、".join(tmod.label(t) for t in items)
        return {"ok": True, "dry": dry, "targets": [t["id"] for t in items],
                "message": (f"演练已开始(不点击、不关客户端):{names}" if dry
                            else f"已开始试领(完整流程):{names}")}

    def run_target(self, payload=None) -> dict:
        """面板「立即试跑这一项」:等价于 run_once + 指定 target。"""
        payload = payload or {}
        return self.run_once({**payload, "target": payload.get("target") or payload.get("id") or ""})

    # ---------- 签到目标 ----------

    def get_targets(self, payload=None) -> dict:
        cfg = self.cfg.all()
        items = tmod.all_targets(cfg)
        return {
            "targets": [self._target_view(t, cfg) for t in items],
            "anchors": list(ANCHORS),
            "flows": list(tmod.FLOWS),
            "verifies": list(tmod.VERIFIES),
            "once_modes": list(tmod.ONCE_MODES),
            "next_step_id": f"step{len((items[0]['steps'] if items else [])) + 1}",
        }

    def save_targets(self, payload=None) -> dict:
        """整表保存目标列表(面板一次提交全部,顺序即执行顺序)。"""
        incoming = (payload or {}).get("targets")
        if not isinstance(incoming, list):
            return {"ok": False, "message": "缺少 targets 列表"}
        cleaned = tmod.normalize_all(incoming)
        if not cleaned:
            return {"ok": False, "message": "至少需要一个签到目标"}
        ids = {t["id"] for t in cleaned}
        cfg = self.cfg.all()
        slots = []
        for slot in cfg["schedule"].get("slots") or []:
            if isinstance(slot, dict) and str(slot.get("target") or "") not in ("", *ids):
                # 场次指向已删除的目标:清掉引用,让它回落到"跑所有启用的目标"
                slot = {**slot, "target": ""}
            slots.append(slot)
        self.cfg.patch({"targets": cleaned, "schedule": {"slots": slots}})
        self.scheduler.reload()
        self.bus.publish("state_change", {})
        logger.info(f"签到目标已更新,共 {len(cleaned)} 个")
        return {"ok": True, "targets": [self._target_view(t, self.cfg.all()) for t in cleaned]}

    def delete_target(self, payload=None) -> dict:
        tid = str((payload or {}).get("target") or (payload or {}).get("id") or "")
        cfg = self.cfg.all()
        items = [t for t in tmod.all_targets(cfg) if t["id"] != tid]
        if len(items) == len(tmod.all_targets(cfg)):
            return {"ok": False, "message": f"目标 {tid} 不存在"}
        return self.save_targets({"targets": items})

    def detect_target(self, payload=None) -> dict:
        """探测/校验某个目标的 exe 路径。

        只接受真实存在、且不是本程序自身的可执行文件 —— 打包后我们的进程名里
        也含 "zcode",当年就是被自己的路径污染了配置才导致无限重开。
        """
        payload = payload or {}
        cfg = self.cfg.all()
        target = tmod.find(cfg, str(payload.get("target") or ""))
        if not target:
            return {"ok": False, "message": "未找到该签到目标"}
        path = str(payload.get("path") or "").strip()
        if not path:
            path = zcode.autodetect_exe(
                Path(target["exe"]).stem or target["window_title_match"] or "zcode") or ""
        if not path:
            return {"ok": False, "message": "未探测到该应用,请手动填写完整路径(含 .exe)"}
        if not Path(path).is_file():
            return {"ok": False, "message": f"路径不存在:{path}"}
        if zcode.is_own_exe(path):
            return {"ok": False, "message": "这个路径就是助手自己,不能作为签到目标"}
        self._patch_target(target["id"], {"exe": path})
        running = bool(zcode.pids_of_exe(path))
        return {"ok": True, "path": path, "running": running,
                "message": f"已设置:{path}" + ("" if running else "(当前未运行,到点会自动启动)")}

    def probe_target(self, payload=None) -> dict:
        """检查目标窗口能否被找到,并把每个录制点换算成屏幕坐标预览。"""
        payload = payload or {}
        cfg = self.cfg.all()
        target = tmod.find(cfg, str(payload.get("target") or ""))
        if not target:
            return {"ok": False, "message": "未找到该签到目标"}
        if isinstance(payload.get("steps"), list):
            target = tmod.normalize({**target, "steps": payload["steps"]}, target["id"]) or target
        if isinstance(payload.get("post_steps"), list):
            target = (tmod.normalize({**target, "post_steps": payload["post_steps"]}, target["id"])
                      or target)
        if not target["exe"] or not Path(target["exe"]).is_file():
            return {"ok": False, "message": "目标程序路径无效,请先在「签到目标」里设置 exe"}
        win = zcode.find_target_window(target)
        if win is None:
            return {"ok": False, "running": False,
                    "message": f"{tmod.label(target)} 没在运行(可点「启动并截图」先跑起来)"}
        rect = zcode.effective_rect(win["hwnd"]) or win["rect"]
        win["rect"] = list(rect)
        def preview(steps, prefix: str = "") -> list[dict]:
            out = []
            for index, step in enumerate(steps or [], start=1):
                point = resolve(rect, step)
                out.append({
                    "index": index, "label": step.get("label") or f"{prefix}第 {index} 步",
                    "anchor": step.get("anchor"), "dx": step.get("dx"), "dy": step.get("dy"),
                    "screen": list(point) if point else None,
                    "ok": bool(point) and rect[0] <= point[0] <= rect[0] + rect[2]
                    and rect[1] <= point[1] <= rect[1] + rect[3],
                })
            return out

        points = preview(target.get("steps"))
        # 补关点同样按窗口矩形换算:录制时量到的位置,过一天窗口挪了就可能点空
        post_points = preview(target.get("post_steps"), "补关")
        pid = zcode.window_pid(win["hwnd"])
        # 提权的目标点不动:鼠标注入会被系统静默丢弃,提前说,别等试运行没反应再猜
        elevated = zcode.runs_elevated(pid)
        return {"ok": True, "running": True, "window": win["title"], "rect": rect,
                "pid": pid, "points": points, "post_points": post_points, "elevated": elevated,
                "message": (f"窗口:{win['title'] or win['hwnd']},{len(points)} 个点击点"
                            + (f",{len(post_points)} 个补关点" if post_points else ""))
                + (";它以管理员身份运行,助手权限不够点不进去——请用「以管理员身份运行」启动助手"
                   if elevated else "")}

    def _patch_target(self, target_id: str, changes: dict) -> dict:
        """改动单个目标的其他字段(exe/步骤等),整表写回。"""
        items = tmod.all_targets(self.cfg.all())
        for item in items:
            if item["id"] == target_id:
                item.update({k: v for k, v in changes.items() if k != "id"})
                break
        cleaned = tmod.normalize_all(items)
        self.cfg.patch({"targets": cleaned})
        self.bus.publish("state_change", {})
        return cleaned

    # ---------- 位置录制 ----------

    def _target_shot(self, target: dict, launch: bool = False):
        """置顶(必要时启动)目标窗口并截一张图,返回 (截图, 窗口, 错误说明)。

        必须用程序自己截的这张图来量坐标:窗口矩形含 -8,-8 这样的不可见边框偏移,
        用户从外部截图软件量到的像素换算不过来,历史上因此点偏过。
        """
        if not target.get("exe") or not Path(target["exe"]).is_file():
            return None, None, "目标程序路径无效,请先设置 exe 路径"
        win = zcode.find_target_window(target)
        if win is None and launch:
            zcode.launch(target["exe"])
            win = zcode.wait_target_window(
                target, float(self.cfg.get("retry", "open_timeout_s", default=30)))
            if win is not None:
                wait = float(target.get("startup_wait_s") or 0)
                if wait > 0:
                    logger.info(f"{tmod.label(target)} 已启动,等待 {wait:.0f}s 界面加载…")
                    time.sleep(wait)
        if win is None:
            return None, None, (f"{tmod.label(target)} 未运行:点「启动并截图」,"
                                "或先手动打开它再截图")
        stem = (Path(target["exe"]).stem or "zcode").lower()
        ok, reason = zcode.verify_client_window(win, target["exe"], stem)
        if not ok:
            return None, win, f"窗口校验未通过:{reason}"

        zcode.foreground(win["hwnd"])
        if target.get("fullscreen", True):
            zcode.maximize(win["hwnd"])
            time.sleep(0.6)
        deadline = time.time() + 12          # 界面还在加载时等它渲染出来
        shot = None
        while True:
            fresh = zcode.effective_rect(win["hwnd"]) or win["rect"]
            win["rect"] = list(fresh)
            shot = capture.grab_region(tuple(fresh))
            if shot.uniform_ratio <= 0.98 or time.time() >= deadline:
                break
            time.sleep(0.8)
        return shot, win, ""

    def capture_target(self, payload=None) -> dict:
        """截一张目标窗口图给录制页当底图,并带回换算所需的窗口矩形。"""
        payload = payload or {}
        target = tmod.find(self.cfg.all(), str(payload.get("target") or ""))
        if not target:
            return {"ok": False, "message": "未找到该签到目标"}
        if payload.get("launch") and self.runner.is_running():
            return {"ok": False, "message": "有任务正在执行,先等它结束"}
        shot, win, message = self._target_shot(target, launch=bool(payload.get("launch")))
        if shot is None:
            return {"ok": False, "message": message}
        return {
            "ok": True,
            "target": target["id"],
            "image": _data_uri(shot.png, "image/png"),
            "rect": list(shot.rect),
            "size": [shot.width, shot.height],
            "window": win["title"],
            "uniform_ratio": round(shot.uniform_ratio, 3),
            "message": f"{win['title'] or win['hwnd']} · {shot.width}x{shot.height}",
        }

    def record_point(self, payload=None) -> dict:
        """把录制页上的一次点击(图片像素)转成一步坐标。

        前端只负责"在这张图上点了一下",锚点与偏移由后端按窗口矩形算,
        这样换分辨率/换显示器时判定规则只有一处。
        """
        payload = payload or {}
        rect = payload.get("rect")
        try:
            img_x = float(payload.get("x"))
            img_y = float(payload.get("y"))
        except (TypeError, ValueError):
            return {"ok": False, "message": "缺少点击坐标"}
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            return {"ok": False, "message": "缺少窗口矩形(请先截图)"}
        x, y, w, h = (float(v) for v in rect)
        if not (0 <= img_x <= w and 0 <= img_y <= h):
            return {"ok": False, "message": f"点击落在窗口图之外({img_x:.0f},{img_y:.0f})"}
        step = from_click(rect, img_x, img_y)
        step["label"] = str(payload.get("label") or "").strip() or f"第 {int(payload.get('index') or 1)} 步"
        step["wait_after_s"] = float(payload.get("wait_after_s") or 0.8)
        step["screen"] = [int(round(x + img_x)), int(round(y + img_y))]
        return {"ok": True, "step": step}

    # ---------- 场次 ----------

    def get_schedule(self, payload=None) -> dict:
        cfg = self.cfg.all()
        # 顺带把目标名单给出:场次表每行都有「跑哪个目标」的下拉,
        # 少一次往返就少一次「下拉还没准备好」的空窗
        return {
            "slots": cfg["schedule"].get("slots") or [],
            "targets": [{"id": t["id"], "name": t["name"], "enabled": t["enabled"]}
                        for t in tmod.all_targets(cfg)],
        }

    def save_schedule(self, payload=None) -> dict:
        slots = self._clean_slots((payload or {}).get("slots"))
        self.cfg.patch({"schedule": {"slots": slots}})
        self.scheduler.reload()
        self.bus.publish("state_change", {})
        logger.info(f"场次表已更新,共 {len(slots)} 个场次")
        return {"ok": True, "slots": slots}

    def _clean_slots(self, raw) -> list[dict]:
        out: list[dict] = []
        seen: set[tuple] = set()
        ids = {t["id"] for t in tmod.all_targets(self.cfg.all())}
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            time_text = str(item.get("time") or "").strip()
            if parse_hhmm(time_text) is None:
                continue
            # days 为空表示每天;同一时间可以排在不同星期/不同目标
            # (例如周五 19:00 与周六 19:00,或 19:00 同时跑 ZCode 与 Trae),
            # 所以去重键是「时间 + 星期 + 目标」
            days = parse_days(item.get("days"))
            target = str(item.get("target") or "").strip()
            if target and target not in ids:
                logger.warning(f"场次 {time_text} 引用了不存在的目标 {target},已改为跑全部目标")
                target = ""
            key = (time_text, tuple(days) if days else (), target)
            if key in seen:
                continue
            seen.add(key)
            entry: dict = {"time": time_text, "enabled": bool(item.get("enabled", True))}
            if days:
                entry["days"] = days
            if target:
                entry["target"] = target
            for key_name in ("attempts", "retry_gap_s"):
                value = item.get(key_name)
                if value in (None, "", "null"):
                    entry[key_name] = None
                else:
                    try:
                        entry[key_name] = max(1, int(float(value)))
                    except (TypeError, ValueError):
                        entry[key_name] = None
            out.append(entry)
        return out

    # ---------- 设置 ----------

    def get_settings(self, payload=None) -> dict:
        return {
            "app": self.cfg.get("app") or {},
            "retry": self.cfg.get("retry") or {},
            "schedule": {"keep_awake": self.cfg.get("schedule", "keep_awake", default=True)},
            "notify": self.cfg.get("notify") or {},
            "data": self.cfg.get("data") or {},
            "config_path": str(CONFIG_PATH),
        }

    def save_settings(self, payload=None) -> dict:
        payload = payload or {}
        patch: dict = {}
        for section in ("app", "retry", "schedule", "notify", "data"):
            value = payload.get(section)
            if isinstance(value, dict):
                patch[section] = value
        if patch:
            self.cfg.patch(patch)
            self.scheduler.reload()
            # 「场次执行期防休眠」是常驻开关,改完要立刻生效,不能等下次启动
            if isinstance(patch.get("schedule"), dict) and "keep_awake" in patch["schedule"]:
                import core.awake as awake
                want = bool(patch["schedule"]["keep_awake"])
                if awake.set_always(want):
                    logger.info(f"防休眠已{'开启' if want else '关闭'}")
                else:
                    logger.warning("切换防休眠失败,系统可能按原设置进入睡眠")
            self.bus.publish("state_change", {})
        return {"ok": True, "settings": self.get_settings()}

    def get_vision_config(self, payload=None) -> dict:
        """识别配置。api_key 不回传明文(改用尾号提示),见 _masked_vision()。"""
        data = {
            "vision": self._masked_vision(),
            "verify_wait_s": self.cfg.get("retry", "verify_wait_s", default=30),
            "success_keywords": self.cfg.get("success_keywords") or [],
            "claimed_keywords": self.cfg.get("claimed_keywords") or [],
            "failure_keywords": self.cfg.get("failure_keywords") or [],
            "loading_keywords": self.cfg.get("loading_keywords") or [],
        }
        return data

    def _masked_vision(self) -> dict:
        """vision 配置去掉明文 Key,只留尾号提示。

        面板与后端虽同机,但明文不该进前端:页面可能被投屏/共享/截图,本地
        HTTP 桥也曾被任意来源访问过(现已加 Host/Origin 校验,掩码作纵深防御)。
        前端拿 api_key_hint 显示「已保存(尾号 xxxx)」,输入框留空提交即保持不变。
        """
        vision = dict(self.cfg.get("vision") or {})
        saved = (vision.get("api_key") or "").strip()
        vision["api_key"] = ""
        vision["api_key_hint"] = saved[-4:] if saved else ""
        # 出厂默认(地址/模型)回填给面板:配置里留空只是"用默认"的写法,
        # 直接下发空白会让用户以为自己的模型 id 没保存住。
        defaults = DEFAULTS.get("vision") or {}
        for key in ("base_url", "model"):
            if not str(vision.get(key) or "").strip():
                vision[key] = defaults.get(key, "")
        return vision

    def save_vision_config(self, payload=None) -> dict:
        payload = payload or {}
        patch: dict = {}
        vision = payload.get("vision")
        if isinstance(vision, dict):
            incoming = dict(vision)
            incoming.pop("api_key_hint", None)     # 展示用字段,不落盘
            key = incoming.get("api_key")
            if key is None:
                incoming["api_key"] = ""           # 显式清除(面板「清除 Key」按钮)
            elif not str(key).strip():
                incoming.pop("api_key", None)      # 留空 = 不修改,保留已存 Key
            else:
                incoming["api_key"] = str(key).strip()
            # 地址/模型留空 = 不修改,和 api_key 同一套约定(面板没有"清空模型"的入口,
            # 空白只会是"这一格我没动")。配合 _masked_vision 的默认值回填,前端永远
            # 收到的是真正在用的 id 并原样提交回来,所以用户填过的模型不会被悄悄抹掉。
            for name in ("base_url", "model"):
                if name in incoming and not str(incoming[name] or "").strip():
                    incoming.pop(name, None)
            patch["vision"] = incoming
        if "verify_wait_s" in payload:
            patch.setdefault("retry", {})["verify_wait_s"] = payload["verify_wait_s"]
        if "max_attempts" in payload:
            patch.setdefault("retry", {})["max_attempts"] = payload["max_attempts"]
        for key in ("success_keywords", "claimed_keywords", "failure_keywords", "loading_keywords"):
            value = payload.get(key)
            if isinstance(value, list):
                patch[key] = [str(k).strip() for k in value if str(k).strip()]
        if patch:
            self.cfg.patch(patch)
        return {"ok": True, "vision": self._masked_vision()}

    def get_prompts(self, payload=None) -> dict:
        target = tmod.find(self.cfg.all(), str((payload or {}).get("target") or ""))
        return {"locate": LOCATE_PROMPT, "verify": VERIFY_PROMPT,
                "verify_active": verify_prompt_for(target), "target": (target or {}).get("id") or ""}

    # ---------- 探测与测试 ----------

    def diagnose_shortcut(self, payload=None) -> dict:
        """诊断开始菜单快捷方式的解析链路(打包后验证 win32com 是否可用)。

        自动探测有两条路径:运行中进程(优先)与开始菜单 .lnk(兜底)。
        客户端没运行时才会走后者,而它依赖函数内延迟导入的 win32com——
        PyInstaller 容易漏掉。这里主动跑一次,把结论暴露给调用方。
        """
        import core.zcode_ctrl as zc

        lnks: list[str] = []
        for d in zc._start_menu_dirs():
            try:
                lnks.extend(str(p) for p in d.rglob("*.lnk") if "zcode" in p.stem.lower())
            except OSError:
                continue

        result = {
            "ok": False,
            "shortcuts": lnks,
            "resolved": None,
            "message": "",
        }
        if not lnks:
            result["message"] = "本机开始菜单里没有 ZCode 快捷方式,该链路不会被用到"
            return result

        target = zc._resolve_lnk(Path(lnks[0]))
        if target:
            result["ok"] = True
            result["resolved"] = target
            result["message"] = f"快捷方式解析正常:{target}"
            logger.info(f"快捷方式解析正常:{lnks[0]} → {target}")
        else:
            result["message"] = (
                "快捷方式解析失败(win32com 可能未打包)。"
                "客户端正在运行时不影响使用;未运行时会探测不到 ZCode 路径。"
            )
            logger.warning("快捷方式解析失败:.lnk 链路不可用")
        return result

    def test_vision_connection(self, payload=None) -> dict:
        result = self.vision.test_connection()
        logger.info(f"视觉连接测试:{result.get('message')}")
        return result

    def test_notify(self, payload=None) -> dict:
        """发一条测试通知,把每个渠道的真实结果回报给面板。

        webhook 失败在《设置》页原本是静默的(只写 debug 日志),用户只能干等;
        这里直接返回失败原因,配错地址/密钥当场就能看出来。
        """
        payload = payload or {}
        section = self.cfg.get("notify") or {}
        if payload.get("url") is not None or payload.get("desktop") is not None:
            # 允许用尚未保存的输入值直接试,免得"改一下→保存→试"来回折腾
            if payload.get("url") is not None:
                section = dict(section, webhook_url=str(payload.get("url") or "").strip())
            if payload.get("desktop") is not None:
                section = dict(section, desktop=bool(payload.get("desktop")))
            self.cfg.patch({"notify": section})

        from core import notify as notify_mod

        title = "ZCode 福利助手 · 通知测试"
        message = "如果你收到这条消息,说明通知链路已打通。"
        results = notify_mod.send(self.cfg, title, message)   # type: ignore[attr-defined]
        logger.info(f"通知测试:桌面={results.get('desktop')},webhook={results.get('webhook')}")
        return {"ok": True, "results": results}

    def test_locate(self, payload=None) -> dict:
        """视觉定位自检:截目标窗口图 → 跑一次定位提示词 → 回传结果与框。"""
        payload = payload or {}
        target = tmod.find(self.cfg.all(), str(payload.get("target") or ""))
        if not target:
            return {"ok": False, "error": "没有配置任何签到目标"}
        label = tmod.label(target)
        shot = None
        if (payload.get("source") or "window") == "window":
            shot, win, message = self._target_shot(target, launch=bool(payload.get("launch")))
            if shot is not None:
                source_label = f"窗口:{win['title'] or win['hwnd']}"
            else:
                source_label = f"整屏({label}:{message})"
        else:
            source_label = "整屏"
        if shot is None:
            shot = capture.grab_fullscreen()

        result = self.vision.locate(shot.png)
        box = None
        raw_box = result.get("button_box")
        if isinstance(raw_box, (list, tuple)):
            box = normalize_box(raw_box, shot.width, shot.height)
        return {
            "ok": not result.get("error"),
            "result": result,
            "target": target["id"],
            "source": source_label,
            "image": _data_uri(shot.png, "image/png"),
            "box": box,
            "size": [shot.width, shot.height],
            "error": result.get("error"),
        }

    def test_verify(self, payload=None) -> dict:
        """结果校验自检:按当前目标的提示词判一次画面,用于校准词表。"""
        payload = payload or {}
        target = tmod.find(self.cfg.all(), str(payload.get("target") or ""))
        if not target:
            return {"ok": False, "error": "没有配置任何签到目标"}
        if payload.get("source") == "screen":
            shot = capture.grab_fullscreen()
        else:
            shot, _win, message = self._target_shot(target, launch=bool(payload.get("launch")))
            if shot is None:
                return {"ok": False, "error": message}
        result = self.vision.verify(shot.png, target)
        verdict = judge_verify(
            result, self.runner.validate_cfg_for(target), clicked=bool(payload.get("clicked", True)))
        return {"ok": not result.get("error"), "verdict": verdict, "result": result,
                "target": target["id"], "image": _data_uri(shot.png, "image/png"),
                "size": [shot.width, shot.height], "error": result.get("error")}

    # ---------- 日志与记录 ----------

    def get_logs(self, payload=None) -> dict:
        payload = payload or {}
        items = get_logs(
            limit=int(payload.get("limit") or 300),
            level=payload.get("level") or None,
            keyword=payload.get("keyword") or "",
        )
        return {"items": items}

    def list_sessions(self, payload=None) -> dict:
        limit = int((payload or {}).get("limit") or 50)
        return {"items": self.store.list_sessions(limit=limit)}

    def get_session_detail(self, payload=None) -> dict:
        session_id = (payload or {}).get("id") or ""
        data = self.store.load(session_id)
        if data is None:
            return {"ok": False, "message": "记录不存在"}
        return {"ok": True, "session": data}

    def get_shot(self, payload=None) -> dict:
        payload = payload or {}
        path = self.store.shot_path(payload.get("session") or "", payload.get("name") or "")
        if path is None:
            return {"ok": False, "message": "截图不存在"}
        data = path.read_bytes()
        mime = "image/png"
        if payload.get("thumb"):
            try:
                data = capture.thumbnail(data, int(payload.get("max_width") or 520))
                mime = "image/jpeg"
            except Exception:
                pass
        return {"ok": True, "image": _data_uri(data, mime)}

    # ---------- 其他 ----------

    def cleanup(self, payload=None) -> dict:
        action = (payload or {}).get("action")
        if action == "shots":
            count = self.store.clear_all()
            return {"ok": True, "message": f"已清除 {count} 个场次的截图"}
        if action == "claims":
            target = (payload or {}).get("target")
            count = self.runner.claims.clear(str(target) if target else None)
            self.bus.publish("state_change", {})
            return {"ok": True, "message": f"已清除 {count} 条领取记账,后续场次将正常执行"}
        if action == "logs":
            count = 0
            for log_file in LOG_DIR.glob("*.log"):
                try:
                    log_file.unlink()
                    count += 1
                except OSError:
                    pass
            return {"ok": True, "message": f"已删除 {count} 个日志文件"}
        if action == "reset":
            self.cfg.reset()
            self.scheduler.reload()
            self.bus.publish("state_change", {})
            return {"ok": True, "message": "配置已重置(保留 ZCode 路径与 API Key)"}
        return {"ok": False, "message": "未知操作"}

    def open_data_dir(self, payload=None) -> dict:
        try:
            os.startfile(str(DATA_DIR))
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def quit_app(self, payload=None) -> dict:
        """退出整个程序(面板「设置 → 退出程序」;正在执行的任务会被中断)。"""
        logger.warning("收到退出指令,程序将在 1 秒后结束")
        threading.Timer(1.0, lambda: os._exit(0)).start()
        return {"ok": True, "message": "程序即将退出,可直接关闭本页面"}

"""签到执行器:用户描述的状态机,支持多个签到目标与两种点击方式。

到点 → 逐个目标:打开客户端 → 定位(视觉找按钮 / 按录制坐标) → 点击 → 校验;
未成功则关闭客户端、等若干秒重开,最多 N 次;全失败则收尾等下一场。

两条铁律(都是实测踩出来的):
- 动手前先确认窗口真的属于目标 exe,且用户没在敲键盘;宁可不点。
- 坐标来自"程序自己截的那张窗口图",运行时用**当下**的窗口矩形换算,
  绝不信任何历史句柄/历史尺寸 —— 窗口挪了坐标就跟着挪。

设计对照:技术方案 §5 流程 / §7 异常处理。watchdog 防窗口卡死吊住流程。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from loguru import logger

import core.awake as awake
import core.capture as capture
import core.clicker as clicker
import core.coords as coords
import core.targets as tmod
import core.validate as validate
import core.zcode_ctrl as zcode
from core.claims import ClaimStore
from core.config import ConfigStore
from core.events import EventBus
from core.notify import notify
from core.storage import SessionStore
from core.vision import VisionClient

# 视觉流程的五个阶段(面板流程页按这个画进度条)
STEPS_VISION = ["打开客户端", "识别按钮", "点击领取", "校验结果", "收尾"]
# 固定坐标流程:没有"识别按钮",取而代之的是按录制点依次点击
STEPS_FIXED = ["打开客户端", "按坐标点击", "校验结果", "收尾"]


def steps_for(target: dict) -> list[str]:
    return STEPS_FIXED if (target or {}).get("flow") == tmod.FLOW_STEPS else STEPS_VISION


class Runner:
    def __init__(self, cfg: ConfigStore, bus: EventBus, store: SessionStore, vision: VisionClient,
                 claims: ClaimStore | None = None):
        self.cfg = cfg
        self.bus = bus
        self.store = store
        self.vision = vision
        self.claims = claims or ClaimStore()
        self._lock = threading.Lock()
        self.current: dict | None = None
        self._client_pid: int | None = None
        self._client_exe: str = ""       # 已确认的客户端进程路径,关闭时用于二次校验

    # ---------- 对外 ----------

    def is_running(self) -> bool:
        return self._lock.locked()

    def spawn(self, trigger: str = "manual", slot: dict | None = None) -> bool:
        """异步启动一次领取(不阻塞调用线程)。"""
        if self.is_running():
            logger.warning("已有领取任务在执行,忽略本次触发")
            return False
        threading.Thread(
            target=self.run_session, args=(trigger, slot), daemon=True, name="runner"
        ).start()
        return True

    def run_session(self, trigger: str = "schedule", slot: dict | None = None) -> dict | None:
        if not self._lock.acquire(blocking=False):
            logger.warning("已有领取任务在执行,忽略本次触发")
            return None
        # 执行期间额外保活一次:即使配置成非常驻,跑的这一段也不会睡
        awake.start_execution()
        try:
            return self._run(trigger, slot)
        except Exception as exc:
            logger.exception(f"领取流程异常终止:{exc}")
            return None
        finally:
            awake.stop_execution()
            self.current = None
            self._client_pid = None
            self._client_exe = ""
            self._lock.release()
            self.bus.publish("state_change", {})

    # ---------- 主流程 ----------

    def _run(self, trigger: str, slot: dict | None) -> dict:
        slot = slot or {}
        cfg = self.cfg.all()
        items = self._slot_targets(cfg, slot)
        if not items:
            wanted = str(slot.get("target") or "").strip()
            reason = (f"场次指定的目标「{wanted}」不存在或已停用" if wanted
                      else "没有启用的签到目标,请到「签到目标」页添加或启用")
            logger.warning(reason)
            return {"status": "failed", "summary": reason}
        self._targets_total = len(items)

        results: list[dict] = []
        for index, target in enumerate(items, start=1):
            self._target_index = index
            results.append(self._run_target(trigger, slot, target, cfg))
            # 每个目标之间留一点缓冲,免得上一个客户端刚关就抢着开下一个
            if index < len(items):
                time.sleep(1.0)

        status = "success" if any(r["status"] == "success" for r in results) else (
            results[-1]["status"] if results else "failed")
        summary = ";".join(f"{r.get('target_name')}:{r['status']}" for r in results)
        return {
            "status": status,
            "summary": summary,
            "results": results,
            "session_id": results[-1].get("session_id") if results else None,
        }

    def _slot_targets(self, cfg: dict, slot: dict) -> list[dict]:
        """本场次要跑的目标:slot.target 指定单个,留空则跑所有启用的目标。"""
        wanted = str(slot.get("target") or "").strip()
        items = tmod.all_targets(cfg)
        if wanted:
            picked = next((t for t in items if t["id"] == wanted), None)
            return [picked] if picked and picked["enabled"] else []
        return tmod.enabled_targets(cfg)

    def slot_targets(self, slot: dict | None = None) -> list[dict]:
        """给面板/测试用:某场次实际会跑哪些目标。"""
        return self._slot_targets(self.cfg.all(), slot or {})

    def _run_target(self, trigger: str, slot: dict, target: dict, cfg: dict) -> dict:
        retry_cfg = cfg["retry"]
        name = tmod.label(target)
        # 演练可由配置总开关开启,也可由单次任务覆盖(面板「演练一次」按钮)
        dry = bool(slot.get("dry")) or bool(cfg["app"].get("dry_run"))
        max_attempts = int(slot.get("attempts") or retry_cfg["max_attempts"])
        gap = float(
            slot["retry_gap_s"] if slot.get("retry_gap_s") is not None else retry_cfg["retry_gap_s"]
        )
        watchdog_s = float(retry_cfg.get("watchdog_s") or 150)

        # 有周期的福利(每天一次 / 每周一次)本周期已领到,后面的场次再跑也变不出福利,
        # 只会白开客户端、白调两次视觉 —— 直接收尾为「已领取,本场跳过」。
        # once=none 时完全不记账,每个场次都照跑;手动试领不受此限(用户主动要求就该执行)。
        skip_reason = self._skip_reason(trigger, slot, cfg, target)
        if skip_reason:
            return self._finish_skipped(trigger, slot, target, skip_reason)

        session = self.store.begin(trigger, slot, target)
        status, summary = "failed", ""
        abort = threading.Event()
        phases = steps_for(target)

        self.current = {
            "session_id": session["id"],
            "trigger": trigger,
            "slot": slot.get("time"),
            "target": target["id"],
            "target_name": name,
            "flow": target["flow"],
            "steps": phases,
            "target_index": getattr(self, "_target_index", 1),
            "targets_total": getattr(self, "_targets_total", 1),
            "attempt": 0,
            "max_attempts": max_attempts,
            "step": phases[0],
            "started_at": session["started_at"],
        }
        self.bus.publish(
            "exec_start",
            {
                "session_id": session["id"],
                "trigger": trigger,
                "slot": slot.get("time"),
                "target": target["id"],
                "target_name": name,
                "flow": target["flow"],
                "steps": phases,
                "max_attempts": max_attempts,
                "dry_run": dry,
            },
        )
        logger.info(
            f"开始{'演练' if dry else '执行'}「{name}」领取"
            f"(触发:{trigger},场次:{slot.get('time') or '手动'},方式:{target['flow']},"
            f"最多 {max_attempts} 次,重试间隔 {gap:.0f}s)"
        )

        watchdog: threading.Timer | None = None

        def arm_watchdog() -> None:
            """按次重置超时。看门狗是防单次卡死,不是给整场设总时限。"""
            nonlocal watchdog
            if watchdog is not None:
                watchdog.cancel()
            watchdog = threading.Timer(watchdog_s, self._on_watchdog, args=(abort, session))
            watchdog.daemon = True
            watchdog.start()

        # 是否保留客户端窗口:拿到结论(成功/当期没有可领)就留着不打扰用户,
        # 失败或中止才关掉
        keep_client = False
        try:
            for attempt_no in range(1, max_attempts + 1):
                if abort.is_set():
                    status, summary = "aborted", "看门狗超时,已强制中止"
                    break

                self.current["attempt"] = attempt_no
                attempt = self.store.begin_attempt(session, attempt_no)
                self._publish_attempt(attempt_no, "running", session["id"],
                                      f"开始第 {attempt_no} 次尝试", attempt)
                arm_watchdog()   # 每次尝试重新计时:单次超时上限,而非整场总预算

                result, reason = self._one_attempt(session, attempt, dry, abort, cfg, target)
                if result == "success":
                    # 浮窗还盖在结果页上:不关掉它,下一场开场第一下点的就是浮窗本身,
                    # 既领不到也认不出界面。补关只在结论拿到之后做,自己成不成都不改结果
                    try:
                        self._close_popups(session, attempt, dry, abort, cfg, target)
                    except Exception as exc:
                        logger.warning(f"领取后补关出了意外(不影响本次结果):{exc}")
                if not dry:
                    # 置前用的 ALT 脉冲若留下残留,当场抬起来:不然用户下一次点击
                    # 就变成 Alt+点击、菜单栏自己激活,而且这种状态只有人肉能察觉
                    stuck = zcode.release_stuck_modifiers()
                    if stuck:
                        logger.warning(f"修饰键残留已抬起:{','.join(stuck)}")
                attempt["result"] = result
                self.store.save(session)
                self._publish_attempt(attempt_no, result, session["id"], reason, attempt)

                if result in ("success", "claimed"):
                    status, summary = "success", reason
                    keep_client = True
                    # 记下本周期已领到:后续场次将跳过。演练不记,免得一次演练就把真机会"用掉"
                    if not dry:
                        self.claims.mark(
                            target["id"], target.get("once") or tmod.ONCE_NONE,
                            slot=slot.get("time") or "", source=trigger)
                    break
                if result == "manual":
                    status, summary = "need_manual", reason
                    break
                if result == "aborted":
                    status, summary = "aborted", reason
                    break

                # 未成功(含结果弹窗明确报"领取失败"):关闭客户端 → 等 gap 秒 → 重开
                self._close_client(dry)
                if dry:
                    status, summary = "dry_run", f"演练模式已完成一次尝试({reason})"
                    break
                if abort.is_set():
                    status, summary = "aborted", "看门狗超时,已强制中止"
                    break
                if attempt_no < max_attempts:
                    logger.info(f"第 {attempt_no} 次未成功({reason}),{gap:.0f} 秒后重开重试")
                    if abort.wait(gap):
                        status, summary = "aborted", "看门狗超时,已强制中止"
                        break
            else:
                status, summary = "failed", f"{max_attempts} 次尝试均未成功"
        finally:
            if watchdog is not None:
                watchdog.cancel()
            if not keep_client:      # 成功/无需领取时保留客户端窗口,不打扰用户;失败或中止才关掉
                self._close_client(dry)
            self.store.finish(session, status, summary)
            self.bus.publish(
                "step_update",
                {
                    "session_id": session["id"],
                    "attempt": (self.current or {}).get("attempt", 0),
                    "step": "收尾",
                    "status": "ok",
                    "detail": summary,
                },
            )
            self._notify_result(status, summary, slot, dry, target)
            self.bus.publish(
                "session_end",
                {
                    "session_id": session["id"],
                    "status": status,
                    "summary": summary,
                    "slot": slot.get("time"),
                    "target": target["id"],
                    "target_name": name,
                    "trigger": trigger,
                },
            )
            logger.info(f"「{name}」本次领取结束:{status} — {summary}")

        return {
            "status": status,
            "summary": summary,
            "session_id": session["id"],
            "target": target["id"],
            "target_name": name,
            "dry_run": dry,
        }

    # ---------- 周期间隔 ----------

    def _skip_reason(self, trigger: str, slot: dict, cfg: dict, target: dict) -> str:
        """该场次是否应当因为"本周期已领到"而跳过;不需要跳过则返回空串。"""
        mode = target.get("once") or tmod.ONCE_NONE
        if mode == tmod.ONCE_NONE:
            return ""
        if trigger != "schedule":        # 手动/演练是用户主动要求,不受周期限制
            return ""
        if slot.get("ignore_period"):    # 单场可显式豁免(目前仅供测试)
            return ""
        state = self.claims.state(target["id"], mode)
        if not state["claimed"]:
            return ""
        when = state["claimed_at"] or "本周期早些时候"
        src = "手动试领" if state["claimed_source"] == "manual" else (
            f"{state['claimed_slot']} 场次" if state["claimed_slot"] else "定时领取"
        )
        unit = "每天只有一次机会" if mode == tmod.ONCE_DAILY else "一周只有一次机会"
        return (f"{tmod.label(target)} 本周期({state['label']})已于 {when} 通过{src}领取成功;"
                f"{unit},本场按已领取跳过")

    def _finish_skipped(self, trigger: str, slot: dict, target: dict, reason: str) -> dict:
        """跳过场次:只记一条状态,不碰客户端、不调模型、不发通知。"""
        session = self.store.begin(trigger, slot, target)
        self.store.finish(session, "skipped", reason)
        logger.info(f"场次 {slot.get('time') or '手动'} 的 {target['id']} 跳过:{reason}")
        self.bus.publish("exec_start", {
            "session_id": session["id"], "trigger": trigger,
            "slot": slot.get("time"), "target": target["id"],
            "target_name": tmod.label(target),
            "max_attempts": 0, "dry_run": False, "skipped": True,
        })
        self.bus.publish("session_end", {
            "session_id": session["id"], "status": "skipped", "summary": reason,
            "slot": slot.get("time"), "target": target["id"],
            "target_name": tmod.label(target), "trigger": trigger,
        })
        return {"status": "skipped", "summary": reason, "session_id": session["id"],
                "target": target["id"], "target_name": tmod.label(target)}

    # ---------- 单次尝试 ----------

    def _one_attempt(self, session: dict, attempt: dict, dry: bool, abort: threading.Event,
                     cfg: dict, target: dict) -> tuple[str, str]:
        phases = steps_for(target)
        # 0) 用户正在用电脑就先别抢前台:等一个键鼠停顿,等不到就放弃本场
        if not dry and self._wait_user_idle(cfg, abort):
            if abort.is_set():
                return "aborted", "看门狗超时,已强制中止"
            logger.warning("检测到键鼠持续活动(电脑正在使用中),本场跳过以免打断操作")
            return "manual", "检测到电脑正在使用中,已跳过本场(避免抢窗口打断操作);可稍后手动试领"

        # 1) 打开客户端
        self._set_step(phases[0])
        try:
            win = self._open_client(dry, cfg, target)
        except Exception as exc:
            self._step(session, attempt, phases[0], "fail", str(exc))
            return "retry", f"打开客户端失败:{exc}"
        self._step(session, attempt, phases[0], "ok", f"窗口就绪:{win['title'] or win['hwnd']}")
        if abort.is_set():
            return "aborted", "看门狗超时,已强制中止"

        rect = self._position_window(win, cfg, dry, target)

        if target["flow"] == tmod.FLOW_STEPS:
            return self._run_fixed(session, attempt, dry, abort, cfg, target, win, rect, phases)
        return self._run_vision(session, attempt, dry, abort, cfg, target, win, rect, phases)

    # ---------- 流程 A:视觉找按钮 ----------

    def _run_vision(self, session: dict, attempt: dict, dry: bool, abort: threading.Event,
                    cfg: dict, target: dict, win: dict, rect, phases: list[str]) -> tuple[str, str]:
        # 2) 截图 + 视觉定位(等界面渲染完成)
        self._set_step(phases[1])
        try:
            shot, not_ready = self._grab_ready(
                win, rect, cfg, dry, target, "定位截图",
                timeout=float(cfg["retry"].get("app_ready_timeout_s") or 20),
            )
        except Exception as exc:
            self._step(session, attempt, "截图", "fail", str(exc))
            return "retry", f"截图失败:{exc}"

        if not_ready == "locked":
            self._step(session, attempt, "截图", "fail", "检测到锁屏/安全桌面")
            return "manual", "电脑处于锁屏状态,本场跳过(解锁后请等下一场)"
        if not_ready == "blank":
            self._step(session, attempt, "截图", "fail",
                       f"画面持续为纯色(单色占比 {shot.uniform_ratio:.2f}),界面未渲染")
            return "retry", "客户端界面长时间未渲染出来(纯色画面),重开重试"

        attempt["images"]["locate"] = self.store.save_shot(
            session, shot.png, f"attempt-{attempt['n']}-locate.png"
        )
        self.store.save(session)
        self._publish_attempt(attempt["n"], "running", session["id"], "已截图,正在识别", attempt)
        self._step(session, attempt, "截图", "ok",
                   f"{shot.width}x{shot.height},单色占比 {shot.uniform_ratio:.2f}")

        if not (cfg["vision"].get("api_key") or "").strip():
            self._step(session, attempt, "识别按钮", "fail", "未配置 API Key")
            return "manual", "未配置 DeepSeek API Key,请到「识别」页填写后重试"

        locate = self.vision.locate(shot.png)
        attempt["locate"] = locate
        self.store.save(session)
        verdict = validate.judge_locate(locate, self._validate_cfg(cfg, target))
        note = locate.get("notes") or locate.get("error") or ""

        # 卡片不在画面上:按用户设定的策略,先原地等 no_card_wait_s 秒再复查一次——
        # 卡片可能比主界面晚渲染。复查时出现了就直接接着点;仍不在则按普通「重试」
        # 走关闭-重开循环,由尝试次数兜底,不再单独判"当期无可领"。
        if verdict == validate.NO_CARD and not dry:
            raw_no_card_wait = cfg["retry"].get("no_card_wait_s")
            no_card_wait = max(float(5 if raw_no_card_wait is None else raw_no_card_wait), 0.0)
            if abort.wait(no_card_wait):
                return "aborted", "看门狗超时,已强制中止"
            probe_shot, probe_bad = self._grab_ready(
                win, rect, cfg, dry, target, "复查福利卡片",
                timeout=float(cfg["retry"].get("verify_ready_timeout_s") or 8),
            )
            if probe_bad == "locked":
                return "manual", "电脑处于锁屏状态,本场跳过(解锁后请等下一场)"
            probe = self.vision.locate(probe_shot.png)
            probe_verdict = validate.judge_locate(probe, self._validate_cfg(cfg, target))
            # 记入 attempt.images,面板的截图回放才能看到复查过程
            attempt["images"]["card-probe"] = self.store.save_shot(
                session, probe_shot.png, f"attempt-{attempt['n']}-card-probe.png"
            )
            self.store.save(session)
            if probe_verdict == validate.CLAIM:
                # 卡片只是晚出来了:改用这张的结果继续走点击流程。
                # shot 也要一起换掉 —— 点击要用"量出按钮坐标的那张图"的尺寸与窗口矩形,
                # 否则期间的窗口位移会让归一化坐标换算到错误位置。
                locate, verdict, shot = probe, validate.CLAIM, probe_shot
                note = probe.get("notes") or ""
                logger.info("福利卡片在复查时出现,继续领取")
            elif probe_verdict in (validate.ALREADY, validate.MANUAL):
                verdict = probe_verdict
                note = probe.get("notes") or probe.get("error") or ""

        self._step(session, attempt, "识别按钮",
                   "ok" if verdict in (validate.CLAIM, validate.ALREADY) else "fail",
                   f"判定:{verdict};{note}")

        if verdict == validate.MANUAL:
            return "manual", f"识别到需人工处理的界面:{note or '未知'}"
        if verdict == validate.ALREADY:
            return "claimed", "页面显示已领取,无需重复领取"
        if verdict == validate.NO_CARD:
            return "retry", (
                f"等待 {cfg['retry'].get('no_card_wait_s')}s 复查后仍未见福利卡片,"
                "关闭客户端重开再找"
            )
        if verdict != validate.CLAIM:
            return "retry", f"未找到可点击的领取按钮:{note or '未知'}"

        box = validate.normalize_box(locate.get("button_box"), shot.width, shot.height)
        if box is None:
            return "retry", "模型返回的按钮位置无效"

        # 3) 点击(动手前再确认两件事:用户没在用电脑、客户端确实在前台)
        self._set_step(phases[2])
        if dry:
            self._step(session, attempt, "点击领取", "ok", "演练模式:跳过实际点击")
        else:
            if self._wait_user_idle(cfg, abort):
                if abort.is_set():
                    return "aborted", "看门狗超时,已强制中止"
                self._step(session, attempt, "点击领取", "fail", "点击前检测到键鼠活动,已放弃")
                return "manual", "点击前检测到电脑正在使用中,已跳过本次点击"
            if not self._ensure_foreground(win, cfg):
                self._step(session, attempt, "点击领取", "fail", "客户端窗口不在前台,已跳过点击")
                return "retry", "客户端窗口未能置前,跳过点击以避免误点到其他窗口"
            blocked = self._foreground_unreachable(win, target)
            if blocked:
                self._step(session, attempt, "点击领取", "fail", blocked)
                return "manual", blocked
            try:
                clicker.click_norm(shot.rect, box, humanize=bool(cfg["vision"].get("humanize_mouse")))
            except clicker.ClickBlocked as exc:
                message = self._blocked_message(win, target, str(exc))
                self._step(session, attempt, "点击领取", "fail",
                           f"没点到「{locate.get('button_label') or '领取'}」:{message}")
                return "manual", message
            self._step(session, attempt, "点击领取", "ok",
                       f"已点击「{locate.get('button_label') or '领取'}」")

        # 4) 校验
        self._set_step(phases[3])
        return self._verify_phase(win, cfg, dry, session, attempt, abort, target, phases)

    # ---------- 流程 B:固定坐标(录制) ----------

    def _run_fixed(self, session: dict, attempt: dict, dry: bool, abort: threading.Event,
                   cfg: dict, target: dict, win: dict, rect, phases: list[str]) -> tuple[str, str]:
        """按录制的坐标依次点击。每一步都用当下的窗口矩形换算,并留一张留档截图。"""
        step_list = target.get("steps") or []
        if not step_list:
            return "manual", f"「{tmod.label(target)}」是固定坐标流程但没有录制任何点击点"
        self._set_step(phases[1])
        for index, step in enumerate(step_list, start=1):
            label = step.get("label") or f"第 {index} 步"
            # 重新摆放/置顶:上一步点完之后窗口可能被系统弹层遮挡,矩形也可能已变
            rect = self._position_window(win, cfg, dry, target)
            point = coords.resolve(rect, step)
            if point is None:
                self._step(session, attempt, phases[1], "fail",
                           f"{label}:坐标无法解析({step.get('anchor')} {step.get('dx')},{step.get('dy')})")
                return "retry", f"点击点「{label}」的坐标无法解析"
            if not dry:
                if self._wait_user_idle(cfg, abort):
                    if abort.is_set():
                        return "aborted", "看门狗超时,已强制中止"
                    self._step(session, attempt, phases[1], "fail",
                               f"点击「{label}」前检测到键鼠活动,已放弃")
                    return "manual", f"点击「{label}」前检测到电脑正在使用中,已跳过本次点击"
                if not self._ensure_foreground(win, cfg):
                    self._step(session, attempt, phases[1], "fail",
                               f"{label}:窗口不在前台,已跳过点击")
                    return "retry", "客户端窗口未能置前,跳过点击以避免误点到其他窗口"
                if index == 1:
                    # 只有把窗口摆到最前之后,这次权限判定量的才是"要点的那个窗口"
                    blocked = self._foreground_unreachable(win, target)
                    if blocked:
                        self._step(session, attempt, phases[1], "fail", blocked)
                        return "manual", blocked
            if dry:
                self._step(session, attempt, phases[1], "ok",
                           f"演练模式:跳过点击「{label}」→ 屏幕 {point}")
            else:
                try:
                    clicker.click_screen(point[0], point[1],
                                         humanize=bool(cfg["vision"].get("humanize_mouse")))
                except clicker.ClickBlocked as exc:
                    message = self._blocked_message(win, target, str(exc))
                    self._step(session, attempt, phases[1], "fail",
                               f"第 {index} 步「{label}」没点到:{message}")
                    return "manual", message
                self._step(session, attempt, phases[1], "ok",
                           f"已点击「{label}」({step['anchor']} +{step['dx']},-{step['dy']})→ 屏幕 {point}")
            wait = max(float(step.get("wait_after_s") or 0), 0.0)
            if wait and abort.wait(wait):
                return "aborted", "看门狗超时,已强制中止"
            # 留档:每一步之后的画面,出错时能一眼看出是哪一步没跳出预期界面
            try:
                shot = capture.grab_region(rect)
                name = f"attempt-{attempt['n']}-step{index}.png"
                attempt["images"][f"step{index}"] = self.store.save_shot(session, shot.png, name)
                self.store.save(session)
            except Exception as exc:
                logger.warning(f"第 {index} 步留档截图失败:{exc}")

        self._step(session, attempt, phases[1], "ok", f"{len(step_list)} 个点击点已按序执行")

        # 校验
        self._set_step(phases[2])
        if (target.get("verify") or tmod.VERIFY_VISION) == tmod.VERIFY_NONE:
            return "success", f"已按录制的 {len(step_list)} 个位置完成点击(未做视觉校验)"
        return self._verify_phase(win, cfg, dry, session, attempt, abort, target, phases)

    # ---------- 领取后补关 ----------

    def _close_popups(self, session: dict, attempt: dict, dry: bool, abort: threading.Event,
                      cfg: dict, target: dict) -> None:
        """领取成功之后,按录制好的「补关点」再点一下,把结果浮窗关掉。

        这一步只在结论已经拿到之后执行,所以它自己成不成都不能改动结果:
        积分到账了,把补关失败算成领取失败会白白重开客户端再领一次。
        """
        post = target.get("post_steps") or []
        if not post:
            return
        phase = steps_for(target)[-1]
        win = zcode.find_target_window(target)
        if win is None:
            self._step(session, attempt, phase, "fail",
                       f"要补关,但找不到「{tmod.label(target)}」窗口:浮窗留着"
                       "(不影响本次结果,下一场可能被它挡住)")
            return
        for index, step in enumerate(post, start=1):
            label = step.get("label") or f"补关第 {index} 步"
            rect = self._position_window(win, cfg, dry, target)
            point = coords.resolve(rect, step)
            if point is None:
                self._step(session, attempt, phase, "fail",
                           f"{label}:坐标无法解析({step.get('anchor')} {step.get('dx')},{step.get('dy')}),"
                           "浮窗留着(不影响本次结果)")
                return
            if dry:
                self._step(session, attempt, phase, "ok",
                           f"演练模式:跳过补关点击「{label}」→ 屏幕 {point}")
                continue
            if self._wait_user_idle(cfg, abort):
                self._step(session, attempt, phase, "fail",
                           f"补关「{label}」前检测到键鼠活动,已放弃(不影响本次结果)")
                return
            if not self._ensure_foreground(win, cfg):
                self._step(session, attempt, phase, "fail",
                           f"补关「{label}」:窗口不在前台,已放弃(不影响本次结果)")
                return
            try:
                clicker.click_screen(point[0], point[1],
                                     humanize=bool(cfg["vision"].get("humanize_mouse")))
            except clicker.ClickBlocked as exc:
                self._step(session, attempt, phase, "fail",
                           f"补关「{label}」没点到:{exc}(不影响本次结果)")
                return
            self._step(session, attempt, phase, "ok",
                       f"领取后补点「{label}」({step['anchor']} +{step['dx']},-{step['dy']})→ 屏幕 {point}")
            wait = max(float(step.get("wait_after_s") or 0), 0.0)
            if wait and abort.wait(wait):
                return
            # 留档:补关之后的画面,能直接看出浮窗是否真的被关掉
            try:
                shot = capture.grab_region(rect)
                name = f"attempt-{attempt['n']}-post{index}.png"
                attempt["images"][f"post{index}"] = self.store.save_shot(session, shot.png, name)
                self.store.save(session)
            except Exception as exc:
                logger.warning(f"补关第 {index} 步留档截图失败:{exc}")

    # ---------- 校验 ----------

    def _verify_phase(self, win: dict, cfg: dict, dry: bool, session: dict, attempt: dict,
                      abort: threading.Event, target: dict, phases: list[str]) -> tuple[str, str]:
        # 点击后的判定策略(按用户设定):等待加载动画 verify_wait_s 秒(默认 30s),
        # 然后截图判定一次——成功/已领取则收尾;失败、仍在加载、或没有任何结论,
        # 一律按未成功进入"关客户端 → 等间隔 → 重开"的重试循环,由尝试次数兜底。
        verify, verdict, _shot = self._await_verify(
            win, cfg, dry, session, attempt, abort, target)

        # "aborted" 只可能来自等待期间看门狗超时;此时不要当成未成功再去重试
        if verdict == "aborted":
            return "aborted", "看门狗超时,已强制中止"
        if verify is None:                   # 连一张校验截图都没拿到
            self._step(session, attempt, phases[2], "fail", "未能截到校验图")
            return "retry", "点击后未能截到校验图"

        name = tmod.label(target)
        note2 = verify.get("notes") or verify.get("error") or ""
        self._step(session, attempt, phases[2],
                   "ok" if verdict in (validate.SUCCESS, validate.ALREADY) else "fail",
                   f"判定:{verdict};{note2}")

        if verdict in (validate.SUCCESS, validate.ALREADY):
            hit = verify.get("keywords") or []
            return "success", f"{name} 领取成功({verdict}{',命中:' + '/'.join(map(str, hit)) if hit else ''})"
        if verdict == validate.FAILED:
            hit = verify.get("keywords") or []
            return "retry", f"弹窗提示领取失败({',命中:' + '/'.join(map(str, hit)) if hit else ''})"
        if verdict == validate.PENDING:
            return "retry", (
                f"点击后等待 {cfg['retry'].get('verify_wait_s')}s,界面仍在加载中未拿到结果,"
                "按未成功重试(若实际已领到,重开后页面会显示已领取)"
            )
        return "retry", f"点击后未检测到成功弹窗:{note2 or '未知'}"

    def _await_verify(self, win: dict, cfg: dict, dry: bool, session: dict,
                      attempt: dict, abort: threading.Event, target: dict
                      ) -> tuple[dict | None, str, "capture.Capture | None"]:
        """点击后等待 verify_wait_s 秒,再截图判定一次。

        返回 (校验结果, 判定, 校验截图);看门狗中止时校验结果为 None。
        演练模式不点击,等不来结果弹窗,不空等、直接截一张判定即收。
        """
        retry_cfg = cfg["retry"]
        raw_wait = retry_cfg.get("verify_wait_s")
        wait = max(float(30 if raw_wait is None else raw_wait), 0.0)
        ready_timeout = float(retry_cfg.get("verify_ready_timeout_s") or 8)

        rect = self._position_window(win, cfg, dry, target)
        if not dry and wait > 0:
            logger.info(f"已点击,等待加载动画 {wait:.0f}s 后判定结果…")
            self._publish_attempt(
                attempt["n"], "running", session["id"],
                f"已点击,等待加载动画({wait:.0f}s)", attempt,
            )
            if abort.wait(wait):
                logger.warning("等待领取结果期间看门狗超时,中止本场")
                return None, "aborted", None

        shot, _ = self._grab_ready(win, rect, cfg, dry, target, "校验截图", timeout=ready_timeout)
        attempt["images"]["after"] = self.store.save_shot(
            session, shot.png, f"attempt-{attempt['n']}-after.png"
        )
        verify = self.vision.verify(shot.png, target)
        attempt["verify"] = verify
        self.store.save(session)
        verdict = validate.judge_verify(verify, self._validate_cfg(cfg, target), clicked=not dry)
        return verify, verdict, shot

    def _validate_cfg(self, cfg: dict, target: dict) -> dict:
        """把目标自带的词表覆盖到全局词表上,交给 core.validate 判定。"""
        merged = dict(cfg)
        for key in ("success_keywords", "claimed_keywords", "failure_keywords", "loading_keywords"):
            merged[key] = tmod.keywords_for(target, cfg, key)
        return merged

    def validate_cfg_for(self, target: dict) -> dict:
        """给面板自检用:返回该目标实际生效的词表配置。"""
        return self._validate_cfg(self.cfg.all(), target)

    # ---------- 输入与前台保护 ----------

    def _wait_user_idle(self, cfg: dict, abort: threading.Event) -> bool:
        """用户正在用键鼠时先等一等:等到停顿返回 False,一直没停返回 True。

        自动化会抢前台并注入点击,用户正在打字/拖拽时既打断操作,也可能点错窗口。
        retry.user_idle_s = 0 可关闭该保护。
        """
        retry_cfg = cfg["retry"]
        need = float(retry_cfg.get("user_idle_s") or 0)
        wait = float(retry_cfg.get("user_idle_wait_s") or 0)
        if need <= 0:
            return False
        deadline = time.time() + max(wait, 0.0)
        while True:
            idle = zcode.idle_seconds()
            if idle is None:            # 读不到就放行,避免因系统差异卡死流程
                return False
            if idle >= need:
                return False
            if abort.is_set() or time.time() >= deadline:
                return True
            time.sleep(0.25)

    def _ensure_foreground(self, win: dict, cfg: dict) -> bool:
        """点击前确认窗口真的在前台;不在就再置前一次。

        窗口没在前台时,点击坐标会落到用户当前正在用的窗口上——宁可不点。
        目标自己弹出的菜单/浮层算"在它前台",不该为此抢焦点(见 owns_foreground)。
        """
        hwnd = win["hwnd"]
        if zcode.owns_foreground(hwnd):
            return True
        zcode.foreground(hwnd)
        if not zcode.owns_foreground(hwnd):
            return False
        time.sleep(float(cfg["retry"].get("focus_settle_s") or 0.0))
        return True

    # ---------- 鼠标能不能真的动 ----------

    def _foreground_unreachable(self, win: dict, target: dict) -> str:
        """窗口已经站到最前了,再确认一下我们够不够得着它的鼠标。

        提权进程的客户区不接收低权限进程注入鼠标,而且系统是静默丢弃的:截图看得见、
        日志写着"已点击"、屏幕上一下都没发生。事前问一次进程权限,比事后猜管用;
        真要漏判,click_screen 落不定鼠标也会报错,不会盲按。
        """
        top = zcode.foreground_hwnd()
        if not zcode.runs_elevated(zcode.window_pid(top or win["hwnd"])):
            return ""
        return self._blocked_message(win, target, "系统拒绝了鼠标注入")

    def _blocked_message(self, win: dict, target: dict, detail: str) -> str:
        name = tmod.label(target)
        reason = detail or "系统拒绝了鼠标注入"
        if zcode.runs_elevated(zcode.window_pid(win["hwnd"])):
            return (f"{reason}:「{name}」是以管理员身份运行的,助手权限比它低,点不进去。"
                    "请退出助手,右键「以管理员身份运行」ZCodeAssistant 后再试;"
                    "或者让该客户端不要以管理员身份运行")
        return (f"{reason}:鼠标没能移到目标点。请确认「{name}」没有以管理员身份运行,"
                "或把助手也以管理员身份重新启动后再试")

    # ---------- 客户端生命周期 ----------

    def _position_window(self, win: dict, cfg: dict, dry: bool, target: dict):
        """摆放客户端窗口:按目标设置最大化(全屏),返回用于截图换算的矩形。

        非最大化时不做移动:目标只记「相对窗口某条边的偏移」,窗口在哪都能用,
        把窗口强推到固定位置反而会在多显示器/DPI 不同的机器上跑到屏幕外。

        矩形取客户区而不是窗口矩形:录制用的截图原点、点击换算用的原点必须是同一个框。
        窗口矩形带一圈 DWM 不可见边框(最大化时左上各 -8),截图又被屏幕边界夹回 (0,0),
        两边一对不上,每个录制点都会整体偏左 8 像素、贴顶部的点还偏上 8 像素。

        已经在最前、已经最大化就什么都不做:这一步点完往往会弹出账户菜单或领取浮层,
        再 SetForegroundWindow/SW_MAXIMIZE 一次就把它们关了,下一步没东西可点。
        """
        hwnd = win["hwnd"]
        if not dry:
            if not zcode.owns_foreground(hwnd):
                zcode.foreground(hwnd)
            if target.get("fullscreen", True) and not zcode.is_maximized(hwnd):
                zcode.maximize(hwnd)
                time.sleep(0.35)
        # 读完重新取一次,避免最小化状态下的无效坐标
        fresh = zcode.effective_rect(hwnd)
        if fresh:
            win["rect"] = list(fresh)
        return tuple(win["rect"])

    def _grab_ready(self, win: dict, rect, cfg: dict, dry: bool, target: dict, label: str,
                    timeout: float) -> tuple["capture.Capture", str | None]:
        """等界面渲染完成再截图,返回 (截图, 未就绪原因)。

        未就绪原因:None=已就绪;"locked"=锁屏/安全桌面;"blank"=持续纯色(没渲染出来)。
        纯色画面不再直接判"锁屏跳过"——客户端加载期的闪屏本身就是纯色,
        真正锁屏交给系统 API 判定,避免把"正在加载"误杀成本场跳过。
        """
        retry_cfg = cfg["retry"]
        deadline = time.time() + max(float(timeout), 0.0)
        poll = max(float(retry_cfg.get("ready_poll_s") or 1.0), 0.2)
        ratio_max = float(retry_cfg.get("uniform_ratio_max") or 0.98)

        attempt_index = 0
        while True:
            attempt_index += 1
            if attempt_index > 1:
                rect = self._position_window(win, cfg, dry, target)   # 再置顶/复位一次
            shot = capture.grab_region(rect)

            if attempt_index == 1 and capture.desktop_locked():
                logger.warning(f"{label}:检测到锁屏/安全桌面,放弃本场")
                return shot, "locked"

            if shot.uniform_ratio <= ratio_max:
                if attempt_index > 1:
                    logger.info(f"{label}:界面已渲染完成(等待 {attempt_index - 1} 次轮询)")
                return shot, None

            if time.time() >= deadline:
                logger.warning(
                    f"{label}:等待 {timeout:.0f}s 后画面仍为纯色(单色占比 {shot.uniform_ratio:.2f})"
                )
                return shot, "blank"
            logger.info(
                f"{label}:画面还是纯色(单色占比 {shot.uniform_ratio:.2f}),"
                f"等待界面加载…(最长 {timeout:.0f}s)"
            )
            time.sleep(poll)

    def _open_client(self, dry: bool, cfg: dict, target: dict) -> dict:
        name = tmod.label(target)
        exe = str(target.get("exe") or "").strip()
        if exe and not Path(exe).is_file():
            logger.warning(f"{name} 配置的程序路径已失效,尝试重新探测:{exe}")
            exe = ""

        # 只接受"进程校验通过"的窗口:标题里恰好含目标名字的其他窗口(本程序自己的
        # 提示框、面板网页、项目目录窗口)不会被误当成客户端
        probe = {**target, "exe": exe}
        win = zcode.find_target_window(probe) if (exe or target["find_by"] == "title") else None

        if win is None:
            if not exe:
                exe = self._autodetect(target)
                if exe:
                    self._save_exe(target["id"], exe)
            if not exe:
                raise RuntimeError(f"未找到 {name} 的程序路径,请在「签到目标」页指定")
            if dry:
                raise RuntimeError(f"演练模式:{name} 未运行且不自动启动")
            zcode.launch(exe)
            timeout = float(cfg["retry"]["open_timeout_s"])
            win = zcode.wait_target_window(probe, timeout)
            if win is None:
                raise TimeoutError(
                    f"{timeout:.0f}s 内未等到 {name} 的主窗口"
                    "(仅接受属于该进程、且尺寸足够大的窗口)"
                )
            # 刚启动的客户端需要加载时间,此时画面常为纯色;先等界面出来
            startup_wait = float(target.get("startup_wait_s") or 0)
            if startup_wait > 0:
                if dry:
                    logger.info(f"演练模式:跳过 {name} 启动等待 {startup_wait:.0f}s")
                else:
                    logger.info(f"{name} 已启动,等待 {startup_wait:.0f}s 让界面加载…")
                    time.sleep(startup_wait)
        else:
            logger.info(f"{name} 已在运行:{win.get('title') or win['hwnd']}")

        # 未配置路径时(按标题找目标)退而求其次:进程名要含目标关键字
        hint = (Path(exe).stem if exe else str(target.get("window_title_match") or "")).lower()
        ok, reason = zcode.verify_client_window(win, exe, hint or "zcode")
        if not ok:
            raise RuntimeError(f"窗口校验未通过,已中止以免误操作:{reason}")
        # 只信刚刚校验过的那个句柄对应的 PID,并且随后再取一次窗口:
        # 从枚举到这里的几毫秒里窗口可能已经关闭,句柄一旦被复用,拿到的 PID 就是别人的进程
        pid = zcode.window_pid(win["hwnd"])
        if not pid or not zcode.is_alive(pid):
            raise RuntimeError(f"窗口在确认进程前已消失(PID {pid}),本次重试")
        self._client_pid = pid
        self._client_exe = exe
        logger.info(f"已确认 {name} 进程(PID {pid}):{reason}")

        if not dry:
            time.sleep(float(cfg["retry"].get("settle_s") or 2.0))
        return win

    def _close_client(self, dry: bool) -> None:
        if dry:
            return
        pid = self._client_pid
        if pid and zcode.is_alive(pid):
            if not zcode.kill_tree(pid, expected_exe=self._client_exe):
                logger.warning("关闭客户端未执行(可能出于安全校验被拒),继续后续流程")
        self._client_pid = None
        self._client_exe = ""

    def _autodetect(self, target: dict) -> str:
        """按目标名探测 exe —— 只在目标没配路径时用,且永远不会返回本程序自己的路径。"""
        hint = Path(str(target.get("exe") or "")).stem or str(target.get("window_title_match") or "")
        found = zcode.autodetect_exe(hint or "zcode") or ""
        if found:
            logger.info(f"已探测到 {tmod.label(target)}:{found}")
        return found

    def _save_exe(self, target_id: str, exe: str) -> None:
        """把探测到的路径写回该目标,下一场就不用再扫一遍磁盘。"""
        items = tmod.all_targets(self.cfg.all())
        for item in items:
            if item["id"] == target_id:
                item["exe"] = exe
        self.cfg.patch({"targets": items})
        logger.info(f"已把探测到的路径写入目标 {target_id}:{exe}")

    # ---------- 辅助 ----------

    def _on_watchdog(self, abort: threading.Event, session: dict) -> None:
        if abort.is_set():
            return
        logger.error("看门狗超时:强制中止本次领取并关闭客户端")
        abort.set()
        self.bus.publish(
            "need_manual",
            {"reason": "看门狗超时,已强制中止本次领取", "session_id": session.get("id")},
        )
        try:
            self._close_client(dry=False)
        except Exception:
            pass

    def _notify_result(self, status: str, summary: str, slot: dict, dry: bool, target: dict) -> None:
        if dry:
            return
        label = f"{tmod.label(target)}[{slot.get('time') or '手动'}]"
        if status == "success":
            notify(self.cfg, f"{label} 领取成功", summary)
        elif status == "failed":
            notify(self.cfg, f"{label} 领取失败", f"{summary},请手动查看")
        elif status == "need_manual":
            notify(self.cfg, f"{label} 需要人工处理", summary)

    def _set_step(self, step: str) -> None:
        if self.current is not None:
            self.current["step"] = step

    def _step(self, session: dict, attempt: dict, step: str, status: str, detail: str = "") -> None:
        self.store.add_step(session, attempt, step, status, detail)
        if status == "fail":
            logger.warning(f"[{step}] {detail}")
        elif detail:
            logger.info(f"[{step}] {detail}")
        self.bus.publish(
            "step_update",
            {
                "session_id": session["id"],
                "attempt": attempt["n"],
                "step": step,
                "status": status,
                "detail": detail,
            },
        )

    def _publish_attempt(self, n: int, status: str, session_id: str,
                         message: str, attempt: dict) -> None:
        images = [
            {"session": session_id, "name": name}
            for name in (attempt.get("images") or {}).values()
        ]
        self.bus.publish(
            "attempt_update",
            {
                "session_id": session_id,
                "attempt": n,
                "status": status,
                "message": message,
                "images": images,
            },
        )

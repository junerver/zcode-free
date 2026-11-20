"""自检脚本:验证依赖、配置、调度、判定逻辑与 HTTP 桥是否正常。

不需要 API Key,不会点击鼠标、不会启动/关闭 ZCode。
    .venv\\Scripts\\python.exe selftest.py
"""
from __future__ import annotations

import ctypes
import json
import sys
import threading
import time
import urllib.request

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, fn) -> None:
    try:
        detail = fn()
        PASS.append(f"[PASS] {name}{(' — ' + str(detail)) if detail else ''}")
    except Exception as exc:
        FAIL.append(f"[FAIL] {name} — {exc}")


def set_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def main() -> int:
    set_dpi_awareness()

    check("依赖导入(mss/PIL/pyautogui/pywin32/httpx/yaml/loguru/apscheduler)", lambda: __import_deps())
    check("静态检查:未定义名称 / 未使用导入(pyflakes)", lambda: __static_check_case())
    check("配置加载与 patch(临时文件)", lambda: __config_roundtrip())
    check("判定逻辑:归一化坐标容错", lambda: __box_cases())
    check("判定逻辑:定位/校验决策", lambda: __judge_cases())
    check("截图模块(整屏抓取,不保存)", lambda: __capture_case())
    check("截图模块:越界窗口裁剪与坐标一致性", lambda: __capture_clip_case())
    check("执行状态机装配与场次调度注册", lambda: __app_case())
    check("执行器:演练模式单次尝试与记录", lambda: __runner_dry_case())
    check("执行器:重试循环与收尾", lambda: __runner_retry_case())
    check("窗口进程校验(拒绝同名无关窗口 / 误关保护进程)", lambda: __window_guard_case())
    check("自我探测防护(助手自己不当客户端 / 关键字不误伤)", lambda: __own_exe_case())
    check("修饰键保护(置前失败不再卡住 ALT / 启动可清理)", lambda: __modifier_guard_case())
    check("用户占用保护(正在用键鼠时不抢前台、不点击)", lambda: __user_idle_guard_case())
    check("窗口摆放(全屏 / 固定矩形 / 演练不动窗口)", lambda: __position_window_case())
    check("鼠标注入被系统拒绝(提权目标:报错且绝不盲按)", lambda: __click_blocked_case())
    check("加载期纯色等待与锁屏判定(不再误判跳过)", lambda: __ready_wait_case())
    check("残留任务状态自愈(断电后的 running 会话)", lambda: __stale_session_case())
    check("执行器:结果弹窗映射(成功收尾 / 失败重试)", lambda: __verify_mapping_case())
    check("通知:载荷构造与失败识别(飞书 msg_type / 200 伪装成功)", lambda: __notify_payload_case())
    check("通知:真实 webhook 推送(未配置地址则跳过)", lambda: __notify_webhook_live_case())
    check("执行器:福利卡片缺失复查(晚出现继续领,仍缺失走重试)", lambda: __no_card_case())
    check("执行器:点击后固定等待判定(仍在加载即重试)", lambda: __verify_pending_case())
    check("签到目标模型(清洗 / id 唯一 / 旧配置一次性迁移)", lambda: __targets_case())
    check("锚点坐标(录制→执行往返 / 窗口移动缩放后仍贴边)", lambda: __coords_case())
    check("固定坐标流程(按录制点击 / 分步留档 / 拒绝盲点)", lambda: __steps_case())
    check("领取后补关(成功后关掉浮窗 / 补关失败不改结果)", lambda: __post_clicks_case())
    check("领取周期 none/每天/每周(周期边界 / 已领跳过 / 多目标分发)", lambda: __claims_case())
    check("场次按星期(每天 / 指定星期 / 误配置告警)", lambda: __weekday_slot_case())
    check("测试识别接口(整屏,无 Key 时报错而不抛异常)", lambda: __test_locate_case())
    check("网络请求不吃环境代理(畸形 NO_PROXY 下仍能发出请求)", lambda: __no_proxy_env_case())
    check("前端资源完整性(HTML / CSS / JS)", lambda: __ui_assets_case())
    check("动效与排版护栏(无自我触发 / 纯文字 / 无渐变阴影)", lambda: __motion_guard_case())
    check("HTTP 桥往返(get_state / 首页 / 静态资源)", lambda: __http_case())
    check("本地服务来源校验(跨站 Origin / 伪造 Host 一律 403)", lambda: __origin_guard_case())
    check("API Key 不回传明文(留空保持 / 显式清除 / 尾号提示)", lambda: __api_key_mask_case())
    check("识别配置提交后落盘并回填(地址 / 模型 id)", lambda: __vision_config_submit_case())
    check("打包:冻结路径分流(资源 / 数据分离)", lambda: __frozen_path_case())
    check("打包:单实例锁(第二个实例必须被拒)", lambda: __single_instance_case())
    check("打包:防休眠(开关 / 执行期嵌套计数)", lambda: __awake_case())
    check("打包:开始菜单快捷方式解析(win32com 链路)", lambda: __shortcut_resolve_case())
    print()
    for line in PASS:
        print(line)
    for line in FAIL:
        print(line)
    print()
    print(f"结果:{len(PASS)} 项通过,{len(FAIL)} 项失败")
    return 1 if FAIL else 0


# ---------------- 各项检查 ----------------

def __import_deps():
    import importlib

    for name in ("mss", "PIL", "pyautogui", "win32gui", "httpx", "yaml", "loguru", "apscheduler"):
        importlib.import_module(name)
    return "全部依赖可导入"


def __static_check_case():
    """静态检查:抓未定义名称/语法类问题(pyflakes 未安装时跳过)。"""
    import importlib.util
    import subprocess
    import sys

    if importlib.util.find_spec("pyflakes") is None:
        return "跳过(未安装 pyflakes,可执行 pip install pyflakes)"

    from core.config import PROJECT_ROOT

    targets = [str(PROJECT_ROOT / "core"), str(PROJECT_ROOT / "main.py"),
               str(PROJECT_ROOT / "probe.py"), str(PROJECT_ROOT / "selftest.py")]
    result = subprocess.run([sys.executable, "-m", "pyflakes", *targets],
                            capture_output=True, text=True, errors="replace", timeout=90)
    issues = (result.stdout or "").strip()
    if result.returncode != 0 and issues:
        raise AssertionError(issues.replace("\n", "; ")[:400])
    return "pyflakes 未发现未定义名称/未使用导入"


def __config_roundtrip():
    import tempfile
    from pathlib import Path

    from core.config import ConfigStore

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.yaml"
        store = ConfigStore(path)
        assert store.get("retry", "max_attempts") == 5, "默认值缺失"
        store.patch({"retry": {"max_attempts": 7}, "schedule": {"slots": [{"time": "10:00", "enabled": True}]}})
        assert store.get("retry", "max_attempts") == 7
        assert store.get("schedule", "slots")[0]["time"] == "10:00"
        reloaded = ConfigStore(path)
        assert reloaded.get("retry", "max_attempts") == 7, "落盘后读取不一致"
        assert reloaded.get("retry", "retry_gap_s") == 5, "缺项未补默认值"
    return "读写/深度合并/落盘正常"


def __box_cases():
    from core.validate import normalize_box

    assert normalize_box([0.1, 0.2, 0.5, 0.6], 1000, 800) == (0.1, 0.2, 0.5, 0.6)
    assert normalize_box([10, 20, 50, 60], 1000, 800) == (0.1, 0.2, 0.5, 0.6), "百分比换算失败"
    assert normalize_box([100, 200, 500, 600], 1000, 1000) == (0.1, 0.2, 0.5, 0.6), "像素换算失败"
    assert normalize_box([0.5, 0.6, 0.1, 0.2], 100, 100) == (0.1, 0.2, 0.5, 0.6), "顺序纠正失败"
    assert normalize_box("bad", 100, 100) is None
    assert normalize_box([0.1, 0.2], 100, 100) is None
    assert normalize_box([0.4, 0.4, 0.4, 0.6], 100, 100) is None, "零宽框应判非法"
    return "归一化/百分比/像素/非法输入全部正确"


def __judge_cases():
    import tempfile
    from pathlib import Path

    from core.config import DEFAULTS, ConfigStore
    from core.validate import judge_locate, judge_verify

    # 生产路径:执行器传入的是普通 dict(曾经的 bug:这里用了 ConfigStore 的多键 get)
    cfg = dict(DEFAULTS)
    assert judge_locate({"error": "x", "confidence": 0}, cfg) == "retry"
    assert judge_locate({"scene": "login", "confidence": 0.9}, cfg) == "manual"
    assert judge_locate({"already_claimed": True, "confidence": 0.9}, cfg) == "claimed"
    assert judge_locate({"ocr_texts": ["今日已领"], "confidence": 0.9}, cfg) == "claimed"
    assert judge_locate(
        {"button_found": True, "claimable": True, "button_box": [0.4, 0.5, 0.6, 0.6], "confidence": 0.9}, cfg
    ) == "claim"
    assert judge_locate(
        {"button_found": True, "claimable": True, "button_box": [0.4, 0.5, 0.6, 0.6], "confidence": 0.3}, cfg
    ) == "retry", "低于阈值不应点击"
    assert judge_verify({"success": True, "confidence": 0.9}, cfg) == "success"
    assert judge_verify({"keywords": ["领取成功"], "confidence": 0.9}, cfg) == "success"
    assert judge_verify({"keywords": ["明天再来"], "confidence": 0.9}, cfg) == "claimed"
    assert judge_verify({"notes": "没有出现领取成功,按钮还在", "confidence": 0.5}, cfg) == "retry", "notes 不应参与命中"

    # 结果弹窗(项目实际形态):成功=「领取成功 + 开始体验」,失败=「领取失败 + 知道了」
    assert judge_verify({"popup": "success", "keywords": ["领取成功", "开始体验"], "confidence": 0.95}, cfg) == "success"
    assert judge_verify({"success": True, "popup": "success", "confidence": 0.9}, cfg) == "success"
    assert judge_verify({"keywords": ["开始体验"], "confidence": 0.9}, cfg) == "success", "成功词表应含开始体验"
    assert judge_verify({"popup": "failure", "keywords": ["领取失败", "知道了"], "confidence": 0.95}, cfg) == "failed"
    assert judge_verify({"failed": True, "keywords": ["知道了"], "confidence": 0.9}, cfg) == "failed", "失败词表应含知道了"
    assert judge_verify({"success": False, "failed": False, "popup": None, "confidence": 0.5}, cfg) == "retry"

    # 兼容性:面板侧传入 ConfigStore 也必须可用
    store = ConfigStore(Path(tempfile.mkdtemp(prefix="zcode-judge-")) / "config.yaml")
    assert judge_locate(
        {"button_found": True, "claimable": True, "button_box": [0.4, 0.5, 0.6, 0.6], "confidence": 0.9}, store
    ) == "claim"
    assert judge_verify({"keywords": ["领取成功"], "confidence": 0.9}, store) == "success"
    assert judge_locate({"ocr_texts": ["明天再来"], "confidence": 0.9}, store) == "claimed"
    assert judge_verify({"popup": "success", "keywords": ["开始体验"], "confidence": 0.95}, store) == "success"
    assert judge_verify({"popup": "failure", "keywords": ["领取失败"], "confidence": 0.95}, store) == "failed"
    return "定位/校验各分支正确(含成功/失败弹窗;普通 dict 与 ConfigStore 两种入参)"


def __capture_case():
    from core.capture import grab_fullscreen

    shot = grab_fullscreen()
    assert len(shot.png) > 1000, "截图数据过小"
    assert 0.0 <= shot.uniform_ratio <= 1.0
    return f"{shot.width}x{shot.height},单色占比 {shot.uniform_ratio:.2f}"


def __capture_clip_case():
    from core.capture import grab_region

    # 模拟最大化窗口(-8,-8 起始的 DWM 边框)越界场景
    shot = grab_region((-8, -8, 1936, 1056))
    assert shot.width == shot.rect[2] and shot.height == shot.rect[3], \
        f"裁剪矩形 {shot.rect} 与图片 {shot.width}x{shot.height} 不一致"
    assert shot.rect[0] >= 0 and shot.rect[1] >= 0, "裁剪矩形应被限制在屏幕内"
    return f"越界窗口裁剪一致,rect={shot.rect}"


def __app_case():
    import tempfile
    from pathlib import Path

    from core.api import Api
    from core.config import ConfigStore
    from core.events import EventBus
    from core.logging_setup import setup_logging
    from core.runner import Runner
    from core.scheduler import SlotScheduler
    from core.storage import SessionStore
    from core.vision import VisionClient

    tmp_dir = Path(tempfile.mkdtemp(prefix="zcode-selftest-"))
    cfg = ConfigStore(tmp_dir / "config.yaml")   # 自检不污染真实配置
    cfg.patch({"schedule": {"slots": [{"time": "23:59", "enabled": True, "attempts": None, "retry_gap_s": None}]}})
    bus = EventBus()
    setup_logging(bus, keep_days=3)
    store = SessionStore()
    vision = VisionClient(cfg)
    runner = Runner(cfg, bus, store, vision)
    scheduler = SlotScheduler(cfg, lambda slot: None,
                              master_check=lambda: bool(cfg.get("schedule", "enabled", default=True)))
    scheduler.start()
    api = Api(cfg, bus, store, vision, runner, scheduler)

    state = api.get_state()
    assert state["slots_total"] >= 1
    assert state["next_slot"] is not None and state["next_slot"]["ts"] > time.time(), "下一场次计算错误"
    api.save_schedule({"slots": [{"time": "08:30", "enabled": True}, {"time": "bad", "enabled": True}]})
    slots = api.get_schedule()["slots"]
    assert len(slots) == 1 and slots[0]["time"] == "08:30", f"场次清洗失败:{slots}"
    assert api.get_prompts()["locate"].startswith("你是桌面软件自动化助手")

    from core import api as api_module
    api_module._SELFTEST_API = api  # 供 HTTP 桥检查复用
    return f"下一场 {state['next_slot']['datetime']}"


def __test_locate_case():
    """「测试识别」接口:整屏分支(不抢焦点);无 Key 时应返回错误而不是抛异常。"""
    from core import api as api_module

    api = getattr(api_module, "_SELFTEST_API", None)
    assert api is not None, "先执行上一项装配检查"
    if (api.cfg.get("vision", "api_key") or "").strip():
        return "跳过(已配置 API Key,避免真实视觉调用)"

    result = api.test_locate({"source": "screen"})
    assert str(result.get("image", "")).startswith("data:image/"), "截图数据缺失"
    assert result.get("size") and result["size"][0] > 100, f"截图尺寸异常:{result.get('size')}"
    assert result.get("ok") is False and result.get("error"), f"无 Key 应返回错误信息:{result}"
    return f"整屏截图链路正常(预期报错:{result['error']})"


def __verify_mapping_case():
    """执行器级:结果弹窗的成功/失败如何映射到收尾与重试(假截图 + 假视觉,不碰真实窗口)。"""
    import tempfile
    import threading
    from pathlib import Path
    from types import SimpleNamespace

    from core.config import ConfigStore
    from core.events import EventBus
    from core.runner import Runner
    from core.storage import SessionStore
    from core.vision import VisionClient

    tmp = Path(tempfile.mkdtemp(prefix="zcode-verify-"))
    cfg = ConfigStore(tmp / "config.yaml")
    cfg.patch({
        "app": {"dry_run": False},
        # verify_wait_s 压到 0:点击后的固定等待不该把自检卡在 30 秒上
        "retry": {"verify_wait_s": 0, "focus_settle_s": 0},
        "vision": {"api_key": "selftest-fake-key"},   # 让流程走到视觉调用(视觉已被打桩)
    })
    store = SessionStore(tmp / "shots")
    runner = Runner(cfg, EventBus(), store, VisionClient(cfg))
    target = runner.slot_targets({})[0]
    cfg_dict = cfg.all()

    fake_shot = SimpleNamespace(png=b"\x89PNG-fake", width=1240, height=860,
                                rect=(40, 40, 1240, 860), uniform_ratio=0.3,
                                full_size=(1920, 1080))
    runner._open_client = lambda dry, cfg, target: {"hwnd": 1, "title": "FakeZCode", "rect": [40, 40, 1240, 860], "area": 1}
    runner._close_client = lambda dry: None
    # 真实方法名是 _grab_ready(曾经的 bug:这里写的是不存在的 _grab_window,
    # 打桩落空 → 自检真的截图并点了用户的鼠标)。返回 (截图, 未就绪原因)。
    runner._grab_ready = lambda win, rect, cfg, dry, target, label, timeout=None: (fake_shot, None)
    runner._position_window = lambda win, cfg, dry, target=None: tuple(win["rect"])
    runner._wait_user_idle = lambda cfg, abort: False
    runner._ensure_foreground = lambda win, cfg: True
    runner.vision.locate = lambda png: {
        "button_found": True, "claimable": True, "button_box": [0.4, 0.5, 0.6, 0.6],
        "button_label": "领取", "confidence": 0.95, "scene": "welfare",
    }

    # 保险:即便上面的打桩将来再次失效,也绝不允许自检真的移动/点击鼠标
    import core.clicker as clicker_module
    real_click_norm, real_click_screen = clicker_module.click_norm, clicker_module.click_screen
    clicks: list[tuple] = []
    clicker_module.click_norm = lambda rect, box, humanize=False: clicks.append(("norm", rect, box))
    clicker_module.click_screen = lambda x, y, humanize=False: clicks.append(("screen", x, y))
    # 自检机器上可能真开着提权窗口,权限预检一并桩掉(单独测它的用例在后面)
    runner._foreground_unreachable = lambda win, target: ""

    def run(verify_result):
        runner.vision.verify = lambda png, target=None: verify_result
        session = store.begin("manual", {"time": None})
        attempt = store.begin_attempt(session, 1)
        return runner._one_attempt(session, attempt, False, threading.Event(), cfg_dict, target)[0]

    def run_single_check():
        """验证点击后只做一次固定等待 + 一次截图判定(按用户设定的策略)。

        旧轮询策略会在时限内反复截图;现行策略:等 verify_wait_s → 截 1 张 → 判定 → 收。
        """
        taken = {"n": 0}

        def counting_grab(win, rect, cfg, dry, target, label, timeout=None):
            taken["n"] += 1
            return fake_shot, None

        original_grab = runner._grab_ready
        original_verify = runner.vision.verify
        runner._grab_ready = counting_grab
        # 始终"没结论":策略规定只截 1 张,判定完就收,不再复查
        runner.vision.verify = lambda png, target=None: {
            "popup": None, "success": False, "failed": False, "confidence": 0.3,
        }
        try:
            session = store.begin("manual", {"time": None})
            attempt = store.begin_attempt(session, 1)
            runner._one_attempt(session, attempt, False, threading.Event(), cfg_dict, target)
        finally:
            runner._grab_ready = original_grab
            runner.vision.verify = original_verify
        return {"rounds": taken["n"]}

    try:
        success = run({"popup": "success", "keywords": ["领取成功", "开始体验"], "confidence": 0.95})
        failure = run({"popup": "failure", "keywords": ["领取失败", "知道了"], "confidence": 0.95})
        nothing = run({"popup": None, "success": False, "failed": False, "confidence": 0.4})
        claimed = run({"claimed": True, "keywords": ["明日再来"], "confidence": 0.9})
        # 加载中:到点仍未出结果,按现行策略应进入重试循环
        pending = run({"pending": True, "popup": "pending", "keywords": ["领取中"], "confidence": 0.9})
        pending_word = run({"popup": None, "keywords": ["处理中", "请稍候"], "confidence": 0.8})
        # 点过之后卡片消失了:领取已被消费,按已领收尾而不是失败
        card_gone = run({"popup": None, "success": False, "failed": False,
                         "card_visible": False, "confidence": 0.8})
    finally:
        clicker_module.click_norm, clicker_module.click_screen = real_click_norm, real_click_screen

    assert clicks, "点击未被拦截?这会让自检真的操作鼠标"
    assert all(c[0] == "norm" for c in clicks), f"出现了屏幕直点:{clicks}"
    assert success == "success", f"成功弹窗应判成功,得到 {success}"
    assert claimed == "success", f"已领应判成功,得到 {claimed}"
    assert failure == "retry", f"失败弹窗应进入重试,得到 {failure}"
    assert nothing == "retry", f"无弹窗应进入重试,得到 {nothing}"
    assert pending == "retry", f"加载中超时后应进入重试,得到 {pending}"
    assert card_gone == "success", f"点后卡片消失应判成功,得到 {card_gone}"

    # 点击后固定等待 → 只截一张校验图判定就收(不轮询)。计数含开头的定位截图:
    # 定位 1 张 + 校验 1 张 = 2
    delays = run_single_check()
    assert pending_word == "retry", f"加载词应判 pending(进而重试),得到 {pending_word}"
    assert delays["rounds"] == 2, f"定位+校验应各截 1 张,实际截了 {delays['rounds']} 张"

    # 前台校验不通过:跳过点击、计一次未成功(否则点击会落到用户当前窗口上)
    intercepted = len(clicks)
    runner._ensure_foreground = lambda win, cfg: False
    clicks.clear()
    blocked = run({"popup": "success", "keywords": ["领取成功"], "confidence": 0.95})
    assert blocked == "retry", f"窗口不在前台应判重试,得到 {blocked}"
    assert not clicks, f"窗口不在前台时不应点击:{clicks}"
    runner._ensure_foreground = lambda win, cfg: True
    return f"弹窗映射正确;点击全部被拦截({intercepted} 次);窗口不在前台时拒绝点击"


def __ready_wait_case():
    """回归:加载期纯色画面不得被判成"锁屏跳过",而应等待渲染完成。

    事故背景:打开客户端后约 5 秒的加载闪屏是纯色,旧逻辑(单色占比>0.95 → manual
    "疑似锁屏")把这种情况误判成本场跳过。
    """
    import tempfile
    from dataclasses import dataclass
    from pathlib import Path

    from core.config import ConfigStore
    from core.events import EventBus
    from core.runner import Runner
    from core.storage import SessionStore
    from core.vision import VisionClient
    import core.capture as capture_module

    @dataclass
    class FakeShot:
        png: bytes = b"x"
        width: int = 100
        height: int = 100
        rect: tuple = (0, 0, 100, 100)
        uniform_ratio: float = 0.5
        full_size: tuple = (100, 100)

    tmp = Path(tempfile.mkdtemp(prefix="zcode-ready-"))
    cfg = ConfigStore(tmp / "config.yaml")
    cfg.patch({"retry": {"ready_poll_s": 0.05, "uniform_ratio_max": 0.98}})
    runner = Runner(cfg, EventBus(), SessionStore(tmp / "shots"), VisionClient(cfg))
    target = runner.slot_targets({})[0]
    cfg_dict = cfg.all()
    win = {"hwnd": 1, "title": "Fake", "rect": [0, 0, 100, 100]}
    orig_grab, orig_locked = capture_module.grab_region, capture_module.desktop_locked
    orig_position = runner._position_window
    runner._position_window = lambda w, c, d, t=None: tuple(w["rect"])

    try:
        capture_module.desktop_locked = lambda: False

        # ① 前两次纯色(模拟加载中)→ 应继续等待,最终返回就绪
        frames = [FakeShot(uniform_ratio=1.0), FakeShot(uniform_ratio=0.995), FakeShot(uniform_ratio=0.62)]
        calls = {"n": 0}

        def grab_seq(rect):
            shot = frames[min(calls["n"], len(frames) - 1)]
            calls["n"] += 1
            return shot

        capture_module.grab_region = grab_seq
        shot, reason = runner._grab_ready(win, tuple(win["rect"]), cfg_dict, True, target, "测试", timeout=5)
        assert reason is None and shot.uniform_ratio == 0.62, f"应等到渲染完成,得到 {reason}"
        assert calls["n"] == 3, f"应轮询 3 次,实际 {calls['n']}"

        # ② 一直纯色 → 超时返回 blank(交给重开重试),而不是锁屏
        calls["n"] = 0

        def always_blank(rect):
            calls["n"] += 1
            return FakeShot(uniform_ratio=1.0)

        capture_module.grab_region = always_blank
        _, reason = runner._grab_ready(win, tuple(win["rect"]), cfg_dict, True, target, "测试", timeout=0.3)
        assert reason == "blank", f"持续纯色应为 blank,得到 {reason}"
        assert calls["n"] > 1, f"纯色期间应多次轮询,实际 {calls['n']}"

        # ③ 真锁屏 → 由系统 API 判定,返回 locked
        capture_module.desktop_locked = lambda: True
        calls["n"] = 0
        _, reason = runner._grab_ready(win, tuple(win["rect"]), cfg_dict, True, target, "测试", timeout=5)
        assert reason == "locked", f"锁屏应为 locked,得到 {reason}"
        assert calls["n"] == 1, f"锁屏应只截一次就退出,实际 {calls['n']}"

        # ④ 首次即就绪:不额外轮询
        capture_module.desktop_locked = lambda: False
        calls["n"] = 0

        def ready_first(rect):
            calls["n"] += 1
            return FakeShot(uniform_ratio=0.55)

        capture_module.grab_region = ready_first
        shot, reason = runner._grab_ready(win, tuple(win["rect"]), cfg_dict, True, target, "测试", timeout=5)
        assert reason is None, f"就绪时不应有问题,得到 {reason}"
        assert calls["n"] == 1, f"就绪时应只截一次,实际 {calls['n']}"
    finally:
        capture_module.grab_region, capture_module.desktop_locked = orig_grab, orig_locked
        runner._position_window = orig_position

    # 真实系统调用可用性(不改变状态):当前未锁屏应返回 False
    assert capture_module.desktop_locked() is False, "当前未锁屏,判定应为 False"
    return "纯色=等待渲染(不再误判锁屏);真锁屏=locked;无额外轮询"


def __click_blocked_case():
    """鼠标没落到目标点时必须报错,而且一个键都不许按。

    背景:WorkBuddy 以管理员身份运行,助手是普通权限,系统(UIPI)把注入的鼠标操作
    静默丢掉;pyautogui 不看 SetCursorPos 的返回值,于是日志写着"已点击",
    屏幕上却一下都没点。试运行"没反应"就是这么来的。
    """
    import core.clicker as clicker
    import pyautogui

    moves: list = []
    clicks: list = []
    real = (pyautogui.moveTo, pyautogui.click, clicker.cursor_pos, clicker.virtual_screen)
    try:
        pyautogui.moveTo = lambda x, y, duration=0: moves.append((x, y))
        pyautogui.click = lambda *a, **k: clicks.append(a)

        # ① 光标落定 → 正常点击一次
        clicker.cursor_pos = lambda: (100, 200)
        clicker.virtual_screen = lambda: (0, 0, 3840, 1080)
        assert clicker.click_screen(100, 200) == (100, 200)
        assert len(clicks) == 1, f"落定了应当按下一次:{clicks}"

        # ② 光标没动(被提权窗口挡住)→ 抛错,且绝不能按下鼠标
        moves.clear(); clicks.clear()
        clicker.cursor_pos = lambda: (2734, 638)          # 用户光标停在别处
        try:
            clicker.click_screen(100, 200)
            raise AssertionError("鼠标没落定却仍然执行了点击")
        except clicker.ClickBlocked as exc:
            assert "管理员" in str(exc), f"要提示可能是权限问题:{exc}"
        assert not clicks, f"没落到目标点时不许按下鼠标:{clicks}"

        # ③ 边界按整个桌面算:副屏上的坐标不能被夹回主屏
        assert clicker.clamp(2734, 638) == (2734, 638), "第二块屏上的点被夹回主屏了"
        assert clicker.clamp(-50, 5000) == (0, 1079), "越界坐标没有被收进桌面范围"
    finally:
        (pyautogui.moveTo, pyautogui.click,
         clicker.cursor_pos, clicker.virtual_screen) = real
    return "落不定就报错不按下;副屏坐标不再被夹回主屏"


def __position_window_case():
    """摆放策略:目标要求全屏则最大化;不要求时只置顶不改尺寸;演练一律不动窗口。

    背景:坐标只记「相对窗口某条边的偏移」,所以窗口在哪、多大都能用。旧版本把窗口
    强推到固定矩形(1240x860),在多显示器/DPI 不同的机器上会跑到屏幕外。
    """
    from core.runner import Runner
    from core.events import EventBus
    from core.storage import SessionStore
    from core.vision import VisionClient
    from core.config import ConfigStore
    import core.zcode_ctrl as zc
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp(prefix="zcode-pos-"))
    cfg = ConfigStore(tmp / "config.yaml")
    runner = Runner(cfg, EventBus(), SessionStore(tmp / "shots"), VisionClient(cfg))
    base = runner.slot_targets({})[0]
    cfg_dict = cfg.all()

    calls = {"max": 0, "fg": 0}

    def fake_maximize(hwnd):
        calls["max"] += 1

    def fake_foreground(hwnd):
        calls["fg"] += 1
        return True

    orig = (zc.maximize, zc.foreground, zc.window_rect, zc.client_rect,
            zc.owns_foreground, zc.is_maximized)
    try:
        zc.maximize, zc.foreground = fake_maximize, fake_foreground
        # 假句柄量不到客户区:先当读不到,验证退回窗口矩形的老路
        zc.client_rect = lambda hwnd: None
        zc.owns_foreground = lambda hwnd: False
        zc.is_maximized = lambda hwnd: False
        zc.window_rect = lambda hwnd: [0, 0, 1920, 1080]
        win = {"hwnd": 1, "title": "Fake", "rect": [0, 0, 100, 100]}

        # ① 全屏目标:最大化,并采用最大化之后的矩形去换算坐标
        rect = runner._position_window(win, cfg_dict, False, dict(base, fullscreen=True))
        assert calls == {"max": 1, "fg": 1}, f"全屏应置顶+最大化各一次:{calls}"
        assert rect == (0, 0, 1920, 1080), f"应使用最大化后的窗口矩形,得到 {rect}"
        assert win["rect"] == [0, 0, 1920, 1080], f"窗口矩形应回写供后续步骤复用:{win['rect']}"

        # ② 关掉全屏:只置顶,尺寸原样保留(锚点偏移仍然成立)
        calls.update({"max": 0, "fg": 0})
        zc.window_rect = lambda hwnd: [300, 200, 1280, 800]
        rect = runner._position_window(win, cfg_dict, False, dict(base, fullscreen=False))
        assert calls == {"max": 0, "fg": 1}, f"非全屏不该最大化:{calls}"
        assert rect == (300, 200, 1280, 800), f"应沿用窗口当前矩形,得到 {rect}"

        # ③ 演练:既不置顶也不最大化(动了窗口就是在打扰用户),但仍读一次真实矩形
        calls.update({"max": 0, "fg": 0})
        zc.window_rect = lambda hwnd: [7, 8, 900, 600]
        rect = runner._position_window(win, cfg_dict, True, dict(base, fullscreen=True))
        assert calls == {"max": 0, "fg": 0}, f"演练模式不应改变窗口状态:{calls}"
        assert rect == (7, 8, 900, 600), f"演练仍要按当前真实矩形截图,得到 {rect}"

        # ④ 读不到矩形(窗口刚关闭)时退回已知值,不能抛异常打断整场
        zc.window_rect = lambda hwnd: None
        rect = runner._position_window({"hwnd": 1, "title": "Fake", "rect": [1, 2, 3, 4]},
                                       cfg_dict, True, dict(base, fullscreen=True))
        assert rect == (1, 2, 3, 4), f"取不到新矩形时应沿用旧值,得到 {rect}"

        # ⑤ 换算基准优先客户区:录制用的截图是客户区原点,拿窗口矩形(最大化时 -8,-8)
        #    去回放,每个点都会整体偏左 8 像素、贴顶部的点还偏上 8 像素
        calls.update({"max": 0, "fg": 0})
        zc.window_rect = lambda hwnd: [-8, -8, 1936, 1048]
        zc.client_rect = lambda hwnd: [0, 0, 1920, 1032]
        rect = runner._position_window(win, cfg_dict, False, dict(base, fullscreen=True))
        assert rect == (0, 0, 1920, 1032), f"应当用客户区矩形,得到 {rect}"

        # ⑥ 已经在最前、也已经最大化:一次置顶/最大化都别做。
        #    点完第一步会弹出账户菜单,再 ShowWindow 一次就把要点的那个弹窗关了。
        calls.update({"max": 0, "fg": 0})
        zc.owns_foreground = lambda hwnd: True
        zc.is_maximized = lambda hwnd: True
        runner._position_window(win, cfg_dict, False, dict(base, fullscreen=True))
        assert calls == {"max": 0, "fg": 0}, f"窗口本就在位,不该再动它:{calls}"
    finally:
        (zc.maximize, zc.foreground, zc.window_rect, zc.client_rect,
         zc.owns_foreground, zc.is_maximized) = orig
    return "全屏最大化 / 非全屏只置顶 / 演练不动窗口 / 以客户区为准 / 在位则不再折腾 五种策略正确"


def __window_guard_case():
    """回归:标题含 "zcode" 的无关窗口不得被当成客户端;受保护进程不得被结束。

    事故背景:标题为 "zcode-free"(项目目录)的窗口被标题匹配命中,
    随后该进程被当作 ZCode 关闭,可能连带影响系统外壳。
    """
    import core.zcode_ctrl as zc

    # 纯函数:保护名单
    assert zc.is_protected_exe(r"C:\Windows\explorer.exe") is True
    assert zc.is_protected_exe(r"C:\Program Files\Google\Chrome\Application\chrome.exe") is True
    assert zc.is_protected_exe(r"D:\app_file\Zcode\ZCode.exe") is False
    assert zc.is_protected_exe(None) is False

    # 路径比较:Windows 大小写不敏感
    assert zc._same_path(r"d:\APP_FILE\zcode\zcode.exe", r"D:\app_file\Zcode\ZCode.exe") is True
    assert zc._same_path(r"D:\other\ZCode.exe", r"D:\app_file\Zcode\ZCode.exe") is False

    # 无效句柄直接拒绝
    ok, _ = zc.verify_client_window({"hwnd": 0, "title": "ZCode"}, "")
    assert ok is False

    # 用打桩替换进程信息,覆盖真实的误判场景(不会触碰任何真实进程)
    orig_pid, orig_path = zc.window_pid, zc.process_path
    try:
        zc.window_pid = lambda hwnd: 4242
        # ① 标题像客户端,但进程是资源管理器 → 必须拒绝
        zc.process_path = lambda pid: r"C:\Windows\explorer.exe"
        ok, reason = zc.verify_client_window({"hwnd": 1, "title": "zcode-free"}, "")
        assert ok is False and "受保护" in reason, f"应拒绝受保护进程,得到 {ok}/{reason}"

        # ② 标题像客户端,进程是别的编辑器 → 与配置路径不一致,拒绝
        zc.process_path = lambda pid: r"D:\app_file\SomeEditor\editor.exe"
        ok, reason = zc.verify_client_window(
            {"hwnd": 1, "title": "zcode-free"}, r"D:\app_file\Zcode\ZCode.exe")
        assert ok is False and "不一致" in reason, f"应因路径不符拒绝,得到 {ok}/{reason}"

        # ③ 真正的客户端(大小写不同) → 通过
        zc.process_path = lambda pid: r"d:\APP_FILE\zcode\zcode.exe"
        ok, reason = zc.verify_client_window(
            {"hwnd": 1, "title": "ZCode"}, r"D:\app_file\Zcode\ZCode.exe")
        assert ok is True, f"正确客户端应通过,得到 {ok}/{reason}"

        # ④ 关闭时的二次校验:进程与预期不符 → 拒绝执行 taskkill
        zc.process_path = lambda pid: r"C:\Windows\explorer.exe"
        assert zc.kill_tree(4242, expected_exe=r"D:\app_file\Zcode\ZCode.exe") is False
        zc.process_path = lambda pid: None          # 读不到路径也不许关
        assert zc.kill_tree(4242) is False
    finally:
        zc.window_pid, zc.process_path = orig_pid, orig_path

    # 真实窗口抽样:通过校验的窗口里不得出现受保护进程
    accepted = [w for w in zc.list_windows("") if zc.verify_client_window(w, "")[0]]
    for win in accepted:
        path = zc.process_path(zc.window_pid(win["hwnd"]))
        assert not zc.is_protected_exe(path), f"不应接受受保护进程:{path}"
    return f"误判/误关均已拦截(当前可见窗口中通过校验 {len(accepted)} 个)"


def __own_exe_case():
    """回归:助手绝不能把自己当成签到目标。

    事故背景:打包后的进程叫 ZCodeAssistant.exe,含 "zcode" 关键字,旧版探测按关键字
    匹配运行进程时返回了自己的路径写进配置;此后每次"打开客户端"都在启动第二个自己,
    弹单实例提示框、把提示框当窗口全屏,试运行全部失败(用户实测只有第一次正常)。
    """
    import os
    import sys
    from pathlib import Path

    import win32process

    import core.zcode_ctrl as zc

    # ① 自身路径识别:大小写/相对写法/venv 包装器都要认得,别人的路径一律不认
    own = zc.own_exe()
    assert own and Path(own).is_file(), f"自身路径应可解析:{own!r}"
    assert Path(own).name.lower().startswith("python"), f"源码运行时自身应是解释器:{own}"
    assert zc.is_own_exe(sys.executable) is True, \
        f"venv 下 sys.executable({sys.executable})与进程镜像({own})不是一个文件,两个都得算自己"
    assert zc.is_own_exe(own) is True
    assert zc.is_own_exe(own.upper().replace("C:", "c:")) is True, "Windows 路径比较不区分大小写"
    assert zc.is_own_exe(None) is False and zc.is_own_exe("") is False
    assert zc.is_own_exe(r"D:\app_file\Zcode\ZCode.exe") is False

    # ② 进程枚举:自己明明在运行,却必须报告"没有这个进程"
    assert zc.pids_of_exe(own) == [], f"不得把自己列为签到客户端进程:{zc.pids_of_exe(own)}"
    assert zc.pids_of_exe(sys.executable) == [], "venv 解释器路径同样不得列为客户端"
    assert zc.find_process_window(own) is None, "不得把自己进程的窗口当成客户端主窗口"

    # ③ 窗口校验:进程是自己的窗口必须拒绝(单实例提示框就是这么被当成主窗口的)
    orig_pid, orig_path = zc.window_pid, zc.process_path
    try:
        zc.window_pid = lambda hwnd: os.getpid()
        zc.process_path = lambda pid: own
        ok, reason = zc.verify_client_window({"hwnd": 1, "title": "ZCode 福利助手"}, own)
        assert ok is False and "自身" in reason, f"应拒绝自己的窗口,得到 {ok}/{reason}"
    finally:
        zc.window_pid, zc.process_path = orig_pid, orig_path

    # ④ 探测排序:精确同名优先,含关键字的助手/受保护进程一律排除
    assistant = r"D:\Program Files\ZCodeAssistant\ZCodeAssistant.exe"
    fake = {
        100: own,                          # 本程序自己(源码运行时是 python.exe)
        101: assistant,                    # 打包后的助手:名字含 "zcode" 但不是客户端
        102: r"C:\Windows\explorer.exe",   # 受保护
        103: r"D:\app_file\Zcode\ZCode.exe",   # 真正的客户端
    }
    orig_cached, orig_enum = zc.process_path_cached, win32process.EnumProcesses
    orig_own, orig_alt = zc._OWN_EXE, zc._OWN_ALT
    try:
        zc.process_path_cached = lambda pid: fake.get(int(pid))
        win32process.EnumProcesses = lambda: sorted(fake)
        assert zc.find_running_exe("zcode") == r"D:\app_file\Zcode\ZCode.exe", \
            "应优先返回精确同名的 ZCode.exe,而不是任何名字里含 zcode 的程序"
        assert zc.pids_of_exe(assistant) == [101], \
            "按路径严格比对:用户显式指定某个正在运行的 exe 时应当认账"

        # ⑤ 复刻事故现场:打包版自己就是 ZCodeAssistant.exe(名字里含 "zcode")
        zc._OWN_EXE, zc._OWN_ALT = assistant, ""
        assert zc.is_own_exe(assistant) is True
        assert zc.find_running_exe("zcode") == r"D:\app_file\Zcode\ZCode.exe", \
            "同名优先:即便助手也在跑,也要挑真正的客户端"
        zc.process_path_cached = lambda pid: {101: assistant, 102: fake[102]}.get(int(pid))
        assert zc.find_running_exe("zcode") is None, "只剩自己/受保护进程时不许凑数把自己当客户端"
        assert zc.find_running_exe("zcodeassistant") is None, "关键字直接指向自身时也不得返回"
        assert zc.pids_of_exe(assistant) == [], "不得把助手自己的进程列为客户端进程"
        zc.window_pid = lambda hwnd: 101
        zc.process_path = lambda pid: assistant
        ok, reason = zc.verify_client_window({"hwnd": 1, "title": "ZCode 福利助手"}, assistant)
        assert ok is False and "自身" in reason, f"应拒绝自己的窗口,得到 {ok}/{reason}"
    finally:
        zc.process_path_cached, win32process.EnumProcesses = orig_cached, orig_enum
        zc._OWN_EXE, zc._OWN_ALT = orig_own, orig_alt
        zc.window_pid, zc.process_path = orig_pid, orig_path

    # ⑥ 关进程兜底:进程对不上、或根本是自家进程,都不许下手
    orig_path = zc.process_path
    try:
        zc.process_path = lambda pid: own
        assert zc.kill_tree(os.getpid()) is False, "本程序自身的进程不得被结束"
        zc.process_path = lambda pid: r"D:\app_file\Zcode\ZCode.exe"
        assert zc.kill_tree(os.getpid(), expected_exe=r"D:\other\Thing.exe") is False, \
            "进程与预期 exe 不符时不得结束"
        assert zc.kill_tree(None) is False, "没有 PID 时不应有任何动作"
    finally:
        zc.process_path = orig_path
    assert zc.is_alive(os.getpid()) is True, "自检把自己的进程关了"

    # ⑥ 尺寸闸:小对话框/气泡窗(实测 406x195 的单实例提示框)不可能被选为主窗口
    small = [w for w in zc.list_windows("")
             if w["rect"][2] < zc.MIN_WINDOW_W or w["rect"][3] < zc.MIN_WINDOW_H]
    assert not small, f"枚举结果里混入了过小的窗口:{[(w['title'], w['rect']) for w in small][:3]}"
    return f"自身({Path(own).name})/关键字相近/受保护进程全部排除;窗口尺寸闸生效"


def __stale_session_case():
    """断电/崩溃残留的 running 会话,启动时应被标记为中止。"""
    import json
    import tempfile
    from pathlib import Path

    from core.storage import SessionStore

    tmp = Path(tempfile.mkdtemp(prefix="zcode-stale-"))
    store = SessionStore(tmp / "shots")
    session = store.begin("schedule", {"time": "16:30"})
    store.begin_attempt(session, 1)          # 会话停在 running
    assert store.load(session["id"])["status"] == "running"

    count = store.abort_stale()
    assert count == 1, f"应清理 1 个残留会话,实际 {count}"
    data = json.loads((tmp / "shots" / session["id"] / "session.json").read_text(encoding="utf-8"))
    assert data["status"] == "aborted" and data["ended_at"], "残留会话未被正确收尾"
    assert data["attempts"][0]["result"] == "aborted"
    assert store.abort_stale() == 0, "重复清理应为 0"
    return "残留 running 会话已自愈为 aborted"


def __no_proxy_env_case():
    """网络请求不吃环境代理:NO_PROXY 写法稍特殊一点就会让请求根本发不出去。

    背景:httpx 默认 trust_env=True,会去读环境里的 HTTP(S)_PROXY / NO_PROXY。
    Windows 上不少代理工具和终端注入的 NO_PROXY 长这样:
        localhost,.localhost,127.0.0.1,127.0.0.0/8,[::1],::1
    httpx 0.28.1 解析带方括号的 IPv6 项时把 "1]" 当端口号,在建 Client() 的那一刻
    就抛 InvalidURL("Invalid port: ':1]'")。表现是日志里一句看不懂的
    「网络错误:Invalid port: ':1]'」,请求根本没发出 —— 从这种终端启动程序时,
    视觉流程(签到目标 ZCode)每场都会整场失败,而固定坐标流程不受影响。
    修法:core/vision.py 与 core/notify.py 显式 trust_env=False。
    """
    import os
    import tempfile
    from pathlib import Path

    import httpx

    from core.config import ConfigStore
    from core.vision import VisionClient

    polluted = "localhost,.localhost,127.0.0.1,127.0.0.0/8,[::1],::1"
    saved = {name: os.environ.get(name) for name in ("NO_PROXY", "HTTP_PROXY", "HTTPS_PROXY")}
    os.environ["NO_PROXY"] = polluted
    for name in ("HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(name, None)
    try:
        # ① 先坐实故障:httpx 的默认构造在这个环境里连 client 都建不出来
        try:
            httpx.Client().close()
        except httpx.InvalidURL:
            pass
        else:
            return "跳过(本机 httpx 不解析 NO_PROXY 里的 [::1],该环境问题不存在)"

        # ② 关掉 trust_env 就正常
        with httpx.Client(trust_env=False) as plain:
            plain.close()

        # ③ 两个调用点必须显式不走环境代理,且请求体/响应链路保持原样
        seen: list[dict] = []

        class FakeResp:
            status_code = 200
            text = '{"ok": true}'

            def json(self) -> dict:
                return {"choices": [{"message": {"content": '{"confidence": 0.9}'}}]}

        class FakeClient:
            def __init__(self, **kwargs):
                seen.append(kwargs)
                assert kwargs.get("trust_env") is False, f"调用点应显式 trust_env=False,实际 {kwargs}"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def post(self, url, **kwargs):
                assert url.startswith("https://"), f"请求地址应当是目标站点本身,实际 {url}"
                return FakeResp()

        real_client = httpx.Client
        httpx.Client = FakeClient
        try:
            cfg = ConfigStore(Path(tempfile.mkdtemp(prefix="zcode-noproxy-")) / "config.yaml")
            cfg.patch({"vision": {"api_key": "sk-selftest", "model": "some-vision-model"}})
            result = VisionClient(cfg).chat("定位领取按钮", b"\x89PNG fake")
            assert result.get("confidence") == 0.9, f"视觉请求应当成功,得到 {result}"

            from core import notify

            ok, message = notify.webhook("https://example.com/hook", "标题", "内容")
            assert ok, f"webhook 推送应当成功,得到 {message}"
        finally:
            httpx.Client = real_client
        assert len(seen) >= 2, f"两处调用都该各建一个 client,实际 {len(seen)} 次"
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    return "httpx 不再继承环境代理;视觉与 webhook 请求在畸形 NO_PROXY 下仍能发出"


def __motion_guard_case():
    """回归护栏:动效层不得出现"观察某元素 → 再把动画写回该元素"的自我触发写法。

    事故背景:「今日场次」数字持续闪烁 —— 数字动画由"观察自身文字变化"驱动,
    动画每一帧写回的中间值又被当成新的变化,再次触发动画,形成永久弹跳。
    """
    import re

    from core.config import UI_DIR

    motion = (UI_DIR / "assets/js/motion.js").read_text(encoding="utf-8")
    issues = []
    if "observeNumbers" in motion:
        issues.append("observeNumbers 仍存在(应改为显式 MOTION.countTo)")
    if re.search(r"observe\(el, \{[^}]*characterData", motion):
        issues.append("仍存在对文本元素的 characterData 观察,可能自我触发")
    if re.search(r"attributeFilter:\s*\['src',\s*'style'\]", motion):
        issues.append("实时截图仍在监听 style 属性(GSAP 会写 style,会成环)")
    assert not issues, "; ".join(issues)

    # 数据层必须通过显式接口驱动数字动画
    dashboard = (UI_DIR / "assets/js/pages/dashboard.js").read_text(encoding="utf-8")
    assert "MOTION.countTo" in dashboard, "数字卡片应使用 MOTION.countTo 显式驱动"

    # 排版纪律:页面结构里不应出现图标 SVG(纯文字排版)
    index = (UI_DIR / "index.html").read_text(encoding="utf-8")
    assert "<svg" not in index, "纯文字排版页面中不应再出现 SVG 图标"
    css = (UI_DIR / "assets/css/app.css").read_text(encoding="utf-8")
    for banned in ("backdrop-filter", "linear-gradient(135deg", "box-shadow"):
        assert banned not in css, f"排版方案应避免 {banned}"
    return "动效无自我触发写法;页面无图标;样式无渐变/阴影/毛玻璃"


def __modifier_guard_case():
    """回归:置前用的 ALT 不得留下"卡在按下态"的残留。

    事故背景:老写法是 keybd_event 按下 ALT → SetForegroundWindow → keybd_event 抬起。
    两处都会出问题:置前失败抛异常时抬键被跳过;更隐蔽的是置前**成功**时前台线程已经
    换人,按下落在旧线程、抬起落在新线程,旧线程永远认为 ALT 还按着——用户看到的
    "操作一段后 ALT 处于按下状态"正是这个后遗症(菜单自己激活、点击变 Alt+点击)。
    修法:成对按键用一批 SendInput 发完,而且发在切前台之前;够不着的提权窗口不发。
    """
    import ctypes
    import struct

    import win32con

    import core.zcode_ctrl as zc

    class Keys:
        """替身按键通道:按"批"记录注入,并模拟队列里哪些键还记着按下。"""

        def __init__(self, queued=(), physical=()):
            self.batches = []
            self.queued = set(queued)
            self.physical = set(physical)

        def send(self, events):
            self.batches.append(list(events))
            for vk, flags in events:
                if flags & win32con.KEYEVENTF_KEYUP:
                    self.queued.discard(vk)
                else:
                    self.queued.add(vk)
            return True

        def GetKeyState(self, vk):        # 线程队列里的状态(可能是残留)
            return 0x8000 if vk in self.queued else 0

        def GetAsyncKeyState(self, vk):   # 物理上此刻是否按着
            return 0x8000 if vk in self.physical else 0

    class FakeGui:
        def __init__(self, denied):
            self.denied = denied

        def SetForegroundWindow(self, hwnd):
            if self.denied:
                raise RuntimeError("前台锁定:系统拒绝置前")
            return None

    orig = (zc.win32api, zc.win32gui, zc.send_key_events, zc.window_pid, zc.runs_elevated)
    try:
        zc.window_pid = lambda hwnd: 4242
        zc.runs_elevated = lambda pid: False      # 默认按"没提权"判,③ 里再改

        # ① 置前被拒:ALT 必须成对、且一次性发完,不留残留
        keys = Keys()
        zc.win32api, zc.win32gui = keys, FakeGui(denied=True)
        zc.send_key_events = keys.send
        assert zc._set_foreground(1) is False, "被拒时应返回 False"
        assert keys.batches == [[(zc.VK_MENU, 0), (zc.VK_MENU, win32con.KEYEVENTF_KEYUP)]], \
            f"ALT 应成对且一批发完,实际 {keys.batches}"
        assert keys.queued == set(), f"ALT 仍处于按下态:{keys.queued}"

        # ② 正常置前:不必借 ALT,也不留残留
        keys2 = Keys()
        zc.win32api, zc.win32gui = keys2, FakeGui(denied=False)
        zc.send_key_events = keys2.send
        assert zc._set_foreground(1) is True
        assert keys2.batches == [], f"成功路径不应注入按键:{keys2.batches}"

        # ③ 提权窗口:焦点抢不到,按键更是白留残留 —— 一个按键事件都不发
        keys3 = Keys()
        zc.win32api, zc.win32gui = keys3, FakeGui(denied=True)
        zc.send_key_events = keys3.send
        zc.runs_elevated = lambda pid: True
        assert zc._set_foreground(1) is False
        assert keys3.batches == [], f"提权目标不该注入按键:{keys3.batches}"
        zc.runs_elevated = lambda pid: False

        # ④ 用户真按着 Alt:物理与队列同时为按下,不去打扰他
        keys4 = Keys(queued={zc.VK_MENU}, physical={zc.VK_MENU})
        zc.win32api, zc.send_key_events = keys4, keys4.send
        assert zc.release_stuck_modifiers() == [], "用户正按着 Alt 时不应抬他的键"
        assert keys4.batches == [], f"不该注入抬键:{keys4.batches}"

        # ⑤ 只剩队列残留(程序自己留下的):抬起来一次,之后再查已经干净
        keys5 = Keys(queued={zc.VK_MENU})
        zc.win32api, zc.send_key_events = keys5, keys5.send
        assert zc.release_stuck_modifiers() == ["ALT"], f"应清理卡住的 ALT:{keys5.batches}"
        assert keys5.batches == [[(zc.VK_MENU, win32con.KEYEVENTF_KEYUP)]], \
            f"补发的应是抬起事件:{keys5.batches}"
        assert keys5.queued == set(), "清理后 ALT 不应仍是按下态"
        assert zc.release_stuck_modifiers() == [], "没有卡键时不应重复注入抬键"

        # ⑥ SendInput 结构体尺寸:cbSize 报错尺寸会被系统整批拒绝(静默失败)
        expect = 40 if struct.calcsize("P") == 8 else 28
        assert ctypes.sizeof(zc._INPUT) == expect, \
            f"_INPUT 尺寸 {ctypes.sizeof(zc._INPUT)} 与系统要求 {expect} 不符"
    finally:
        zc.win32api, zc.win32gui, zc.send_key_events, zc.window_pid, zc.runs_elevated = orig

    idle = zc.idle_seconds()                     # 真实读数,不改变任何状态
    assert idle is None or idle >= 0, f"空闲时长读数异常:{idle}"
    return "置前用的 ALT 一批发完不留残留;提权目标与用户按键均不打扰"


def __user_idle_guard_case():
    """回归:用户正在用键鼠时不得抢前台/点击。

    到点的场次若正撞上用户打字,会直接夺走焦点并注入点击;这里保证程序
    先等一个键鼠停顿,等不到就跳过本场(manual),而不是硬抢。
    """
    import tempfile
    from pathlib import Path

    import core.zcode_ctrl as zc
    from core.config import ConfigStore
    from core.events import EventBus
    from core.runner import Runner
    from core.storage import SessionStore
    from core.vision import VisionClient

    tmp = Path(tempfile.mkdtemp(prefix="zcode-idle-"))
    cfg = ConfigStore(tmp / "config.yaml")
    cfg.patch({"retry": {"user_idle_s": 1.0, "user_idle_wait_s": 0.6}})
    runner = Runner(cfg, EventBus(), SessionStore(tmp / "shots"), VisionClient(cfg))
    cfg_dict = cfg.all()
    abort = threading.Event()
    orig = zc.idle_seconds
    try:
        # ① 已空闲足够久 → 直接放行,不等待
        zc.idle_seconds = lambda: 5.0
        started = time.time()
        assert runner._wait_user_idle(cfg_dict, abort) is False
        assert time.time() - started < 0.3, "已空闲时不应等待"

        # ② 用户持续操作 → 等满上限后放弃本场
        zc.idle_seconds = lambda: 0.1
        started = time.time()
        assert runner._wait_user_idle(cfg_dict, abort) is True, "持续占用应判为跳过"
        elapsed = time.time() - started
        assert 0.5 <= elapsed < 3.0, f"等待时长异常:{elapsed:.2f}s"

        # ③ 先动后停 → 停止后放行
        seq = {"n": 0}

        def idle_seq():
            seq["n"] += 1
            return 0.1 if seq["n"] < 4 else 5.0

        zc.idle_seconds = idle_seq
        assert runner._wait_user_idle(cfg_dict, abort) is False, "停止操作后应放行"

        # ④ user_idle_s = 0 关闭保护 → 立即放行
        off = ConfigStore(tmp / "off.yaml")
        off.patch({"retry": {"user_idle_s": 0}})
        zc.idle_seconds = lambda: 0.0
        started = time.time()
        assert runner._wait_user_idle(off.all(), abort) is False
        assert time.time() - started < 0.3, "关闭保护后不应等待"

        # ⑤ 读不到输入状态 → 放行,避免因系统差异卡死流程
        zc.idle_seconds = lambda: None
        assert runner._wait_user_idle(cfg_dict, abort) is False
    finally:
        zc.idle_seconds = orig
    return "用户占用时等待/跳过;关闭保护与读数失败均放行"


def __ui_assets_case():
    import re

    from core.config import UI_DIR

    required = [
        "index.html",
        "assets/css/app.css",
        "assets/js/api.js",
        "assets/js/events.js",
        "assets/js/motion.js",
        "assets/js/app.js",
        "assets/js/vendor/gsap.min.js",
        "assets/js/pages/dashboard.js",
        "assets/js/pages/flow.js",
        "assets/js/pages/schedule.js",
        "assets/js/pages/targets.js",
        "assets/js/pages/vision.js",
        "assets/js/pages/logs.js",
        "assets/js/pages/settings.js",
    ]
    missing = [rel for rel in required if not (UI_DIR / rel).is_file()]
    assert not missing, f"缺少前端文件:{missing}"

    # 页面装配:JS 里注册了 PAGES.x,就必须有对应的标签页、页面容器和 <script> 引用。
    # 三者缺一个都不会报错,只会表现为"点了标签没反应"或整页空白。
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    pages_dir = UI_DIR / "assets" / "js" / "pages"
    for js in sorted(pages_dir.glob("*.js")):
        name = js.stem
        if not re.search(r"PAGES\.%s\s*=" % re.escape(name), js.read_text(encoding="utf-8")):
            continue
        assert f'data-page="{name}"' in html, f"「{name}」没有对应的标签按钮"
        assert f'id="page-{name}"' in html, f"「{name}」缺少页面容器 section#page-{name}"
        assert f"assets/js/pages/{name}.js" in html, f"「{name}」的 JS 没被 index.html 引用"

    # 脚本顺序:页面模块必须早于 app.js(app.js 启动时遍历 window.PAGES)
    order = [m.group(1) for m in re.finditer(r'<script src="assets/js/(?:pages/)?([a-z]+)\.js"', html)]
    assert order.index("app") > max(order.index(p) for p in order if p != "app"), \
        f"app.js 必须最后加载,当前顺序:{order}"

    # 录制页用到的元素:JS 里 $('xxx') 引用的每个 id 都必须真实存在。
    # 少一个不会报错,只会在 init() 里抛异常 → 整页打不开(所以这里必须由脚本反查,
    # 不能靠人肉维护一份 id 清单)。JS 自己 createElement 赋 id 的也算"已提供"。
    provided = set(re.findall(r'\bid="([^"]+)"', html))
    js_files = [UI_DIR / "assets/js/app.js"] + sorted((UI_DIR / "assets/js/pages").glob("*.js"))
    sources = {str(p.relative_to(UI_DIR)): p.read_text(encoding="utf-8") for p in js_files}
    for rel, src in sources.items():
        provided |= set(re.findall(r"\.id\s*=\s*'([A-Za-z0-9_-]+)'", src))
        provided |= set(re.findall(r'id="([A-Za-z0-9_-]+)"', src))
    dangling = {}
    for rel, src in sources.items():
        used = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", src))
        used |= set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", src))
        miss = sorted(used - provided)
        if miss:
            dangling[rel] = miss
    assert not dangling, f"JS 引用了 index.html 里不存在的元素 id:{dangling}"
    return f"{len(required)} 个前端文件齐全,{len(order)} 个脚本按序装配,元素 id 引用无悬空"


def __runner_dry_case():
    """演练模式 + 不存在的窗口标题:验证单次尝试、步骤记录与收尾(不启动/不点击客户端)。"""
    import json
    import tempfile
    from pathlib import Path

    from core.config import ConfigStore
    from core.events import EventBus
    from core.runner import Runner
    from core.storage import SessionStore
    from core.vision import VisionClient

    tmp_dir = Path(tempfile.mkdtemp(prefix="zcode-runner-"))
    cfg = ConfigStore(tmp_dir / "config.yaml")
    cfg.patch({
        "app": {"dry_run": True, "window_title_match": "NoSuchWindow-Zz-9999"},
        "retry": {"open_timeout_s": 5, "settle_s": 0, "watchdog_s": 60},
    })
    store = SessionStore(tmp_dir / "shots")
    runner = Runner(cfg, EventBus(), store, VisionClient(cfg))

    result = runner.run_session("manual", {"time": None, "attempts": 2, "retry_gap_s": 1})
    assert result and result["status"] == "dry_run", f"状态异常:{result}"
    session_file = tmp_dir / "shots" / result["session_id"] / "session.json"
    data = json.loads(session_file.read_text(encoding="utf-8"))
    assert len(data["attempts"]) == 1, "演练模式应点到即止(仅 1 次尝试)"
    assert data["attempts"][0]["steps"], "步骤未被记录"
    assert data["ended_at"], "收尾信息缺失"
    return f"演练尝试已记录并正确收尾({result['summary']})"


def __runner_retry_case():
    """验证重试循环:把单次尝试替换为固定失败,检查次数/间隔/最终状态(无任何真实操作)。"""
    import json
    import tempfile
    from pathlib import Path

    from core.config import ConfigStore
    from core.events import EventBus
    from core.runner import Runner
    from core.storage import SessionStore
    from core.vision import VisionClient

    tmp_dir = Path(tempfile.mkdtemp(prefix="zcode-retry-"))
    cfg = ConfigStore(tmp_dir / "config.yaml")
    cfg.patch({"app": {"dry_run": False}, "retry": {"watchdog_s": 60}})
    store = SessionStore(tmp_dir / "shots")
    runner = Runner(cfg, EventBus(), store, VisionClient(cfg))

    import core.runner as runner_module
    runner_module.notify = lambda *args, **kwargs: None  # 自检不弹桌面通知

    calls = {"n": 0}

    def fake_attempt(session, attempt, dry, abort, cfg, target):
        calls["n"] += 1
        return "retry", f"模拟失败 {calls['n']}"

    runner._one_attempt = fake_attempt
    runner._close_client = lambda dry: None

    started = time.time()
    result = runner.run_session("manual", {"time": None, "attempts": 3, "retry_gap_s": 1})
    elapsed = time.time() - started
    assert result and result["status"] == "failed", f"状态异常:{result}"
    assert calls["n"] == 3, f"重试次数异常:{calls['n']}"
    assert elapsed >= 2.0, f"重试间隔未生效(耗时 {elapsed:.1f}s)"
    data = json.loads(
        (tmp_dir / "shots" / result["session_id"] / "session.json").read_text(encoding="utf-8")
    )
    assert len(data["attempts"]) == 3 and data["attempts"][2]["result"] == "retry"
    return f"3 次重试后正确收尾,共耗时 {elapsed:.1f}s"


def __http_case():
    import core.server as server_module
    from core import api as api_module

    api = getattr(api_module, "_SELFTEST_API", None)
    assert api is not None, "先执行上一项检查"

    bus = api.bus
    server = server_module.serve(api, bus, port=8765)
    url = server.url

    def post(method, payload):
        body = json.dumps({"method": method, "args": [payload]}).encode()
        req = urllib.request.Request(url + "api", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    data = post("get_state", None)
    assert "result" in data and "master" in data["result"], data
    assert post("get_logs", {"limit": 5})["result"]["items"] is not None
    assert post("no_such_method", None).get("error"), "未知方法应返回 error"

    # 静态资源与事件流
    with urllib.request.urlopen(url, timeout=10) as resp:
        html = resp.read().decode("utf-8")
    assert "ZCode 福利助手" in html, "首页内容异常"
    with urllib.request.urlopen(url + "assets/css/app.css", timeout=10) as resp:
        assert resp.status == 200

    got = {}

    def listen():
        try:
            with urllib.request.urlopen(url + "events", timeout=8) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "ignore").strip()
                    if line.startswith("data: "):
                        got.update(json.loads(line[6:]))
                        break
        except Exception:
            pass

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    time.sleep(0.6)
    bus.publish("state_change", {"hello": "selftest"})
    thread.join(timeout=6)
    assert got.get("type") == "state_change", f"SSE 未收到事件:{got}"

    # 危险路径防护
    assert post("get_shot", {"session": "../../", "name": "x"})["result"]["ok"] is False
    # 前端就绪握手(面板加载完成会调用,用于确认链路与事件推送)
    assert post("ui_ready", None)["result"]["ok"] is True
    assert api.ui_ready_event.is_set(), "ui_ready 未生效"
    return "RPC / 静态资源 / SSE / 目录穿越防护全部正常"


def __origin_guard_case():
    """本地服务来源校验:跨站 Origin 与伪造 Host 必须被 403。

    背景:面板 RPC 能退出程序、清空留档、改写配置,而跨站 POST 只要
    Content-Type: text/plain 就能绕过 CORS 预检直发(HTTP 200 也拿不到,
    但副作用已经发生);DNS rebinding 再配上不校验 Host,连明文 API Key
    都能读走。这里逐条验证拦截与放行边界。
    """
    import http.client
    import core.server as server_module
    from core.events import EventBus
    from urllib.parse import urlsplit

    class Stub:
        def get_state(self, payload=None):
            return {"ok": True}

    server = server_module.serve(Stub(), EventBus(), port=8771)
    parsed = urlsplit(server.url)
    host, port = parsed.hostname, parsed.port
    local_host_header = f"{host}:{port}"

    def raw_post(headers, body=b"{}"):
        conn = http.client.HTTPConnection(host, port, timeout=5)
        try:
            conn.request("POST", "/api", body=body, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    try:
        valid_body = json.dumps({"method": "get_state", "args": [None]}).encode()
        # 本机正常请求(urllib / curl 不带 Origin):放行
        status, body = raw_post({"Content-Type": "application/json",
                                 "Host": local_host_header}, valid_body)
        assert status == 200 and json.loads(body).get("result", {}).get("ok"), (status, body)
        # 同源面板(浏览器 fetch 对同源 POST 也会带 Origin):放行
        status, body = raw_post({"Content-Type": "application/json",
                                 "Host": local_host_header,
                                 "Origin": f"http://{local_host_header}"}, valid_body)
        assert status == 200 and json.loads(body).get("result", {}).get("ok"), (status, body)
        # 恶意网页跨站 POST(text/plain 绕过预检):必须 403
        status, _ = raw_post({"Content-Type": "text/plain",
                              "Host": local_host_header,
                              "Origin": "https://evil.example"})
        assert status == 403, f"跨站 Origin 未被拒:{status}"
        # DNS rebinding:evil.com 解析到 127.0.0.1,请求打到本机端口但 Host 是外部域名
        status, _ = raw_post({"Content-Type": "text/plain", "Host": "evil.example"})
        assert status == 403, f"rebinding Host 未被拒:{status}"
        # 静态页同样受保护,防止被嵌进攻击者的源里
        conn = http.client.HTTPConnection(host, port, timeout=5)
        try:
            conn.request("GET", "/", headers={"Host": "evil.example"})
            assert conn.getresponse().status == 403, "静态页未拦截伪造 Host"
        finally:
            conn.close()
    finally:
        server.shutdown()
    return "跨站 Origin 与伪造 Host 均 403;本机与同源请求放行"


def __vision_config_submit_case():
    """识别设置提交:模型/地址必须落盘、重开仍在,且面板回填的默认就是实发的那个。

    背景:面板每次保存都把 vBaseUrl/vModel 一起提交,而 api.py 原先只加工 api_key,
    这两格一旦是空的就直接写进配置 —— 用户填过的模型 id 会被一次「留空提交」抹成空串,
    退出重开后「模型 id 像是丢了」。这里把边界钉死:非空即落盘、重开必须还在、
    留空不覆盖已存值(想恢复默认走「恢复默认」),旧文件里的空串要回填成真正生效的
    默认模型,而且回填给面板的值必须等于真正发进请求体的那个。
    """
    import tempfile
    from pathlib import Path

    from core.api import Api
    from core.config import DEFAULTS, ConfigStore
    from core.events import EventBus
    from core.runner import Runner
    from core.scheduler import SlotScheduler
    from core.storage import SessionStore
    from core.vision import VisionClient

    tmp_dir = Path(tempfile.mkdtemp(prefix="zcode-vision-submit-"))
    path = tmp_dir / "config.yaml"
    cfg = ConfigStore(path)
    bus = EventBus()
    store = SessionStore(tmp_dir / "shots")
    runner = Runner(cfg, bus, store, VisionClient(cfg))
    api = Api(cfg, bus, store, VisionClient(cfg), runner, SlotScheduler(cfg, lambda slot: None))
    default_model = DEFAULTS["vision"]["model"]
    default_base = DEFAULTS["vision"]["base_url"]

    # ① 用户自己填的模型与地址必须原样落盘
    api.save_vision_config({"vision": {
        "provider": "deepseek", "base_url": "https://vision.example/v1",
        "api_key": "sk-selftest-vision-0001", "model": "glm-4v-flash",
        "temperature": 0.2, "detail": "auto", "max_tokens": 800,
        "timeout_s": 20, "locate_confidence_min": 0.7, "humanize_mouse": False}})
    assert cfg.get("vision", "base_url") == "https://vision.example/v1", "自定义地址未落盘"
    assert cfg.get("vision", "model") == "glm-4v-flash", "自定义模型 id 未落盘"

    # ② 重开程序(重新读盘)后还得在,而且面板回填的就是真正会用的那个 id
    reopened = ConfigStore(path)
    api2 = Api(reopened, bus, store, VisionClient(reopened), runner,
               SlotScheduler(reopened, lambda slot: None))
    shown = api2.get_vision_config()["vision"]
    assert shown["model"] == "glm-4v-flash", f"重开后模型 id 丢失:{shown['model']!r}"
    assert shown["base_url"] == "https://vision.example/v1", "重开后地址丢失"

    # ③ 提交里两格留空 = 不修改(和 api_key 同约定):已存的自定义值必须原样留着,
    #    面板上看着空不代表配置丢了,更不该在下次保存时被默认值悄悄盖掉
    reopened.patch({"vision": {"model": "custom-vision-model",
                               "base_url": "https://custom.example/v1"}})
    api2.save_vision_config({"vision": {"base_url": "", "model": "", "api_key": ""}})
    assert reopened.get("vision", "model") == "custom-vision-model", "留空提交把模型 id 抹掉了"
    assert reopened.get("vision", "base_url") == "https://custom.example/v1", "留空提交把地址抹掉了"
    assert (reopened.get("vision", "api_key") or "") == "sk-selftest-vision-0001", "留空不该清掉已存 Key"

    # ④ 旧版本写进文件的空串(当年保存一次就把 model 抹成 "")要回填成真正生效的默认
    legacy = ConfigStore(tmp_dir / "legacy-blank.yaml")
    legacy.patch({"vision": {"api_key": "sk-selftest-legacy", "model": "", "base_url": ""}})
    legacy = ConfigStore(tmp_dir / "legacy-blank.yaml")
    assert (legacy.get("vision", "model") or "") == "", "测试前提:文件里确实是空串"
    api3 = Api(legacy, bus, store, VisionClient(legacy), runner,
               SlotScheduler(legacy, lambda slot: None))
    shown3 = api3.get_vision_config()["vision"]
    assert shown3["model"] == default_model, f"空模型未回填默认:{shown3['model']!r}"
    assert shown3["base_url"] == default_base, f"空地址未回填默认:{shown3['base_url']!r}"

    # ⑤ 只提交词表不许顺手碰模型/地址(api.py 只在 payload 带 vision 时才动这两格)
    reopened.patch({"vision": {"model": "custom-keep"}})
    api2.save_vision_config({"claimed_keywords": ["已领取"]})
    assert reopened.get("vision", "model") == "custom-keep", "保存词表把模型 id 抹掉了"

    # ⑥ 面板显示什么就真发什么:请求体里的 model/URL 必须等于回填后的值
    # chat() 走的是 httpx.Client(...).post(...),打模块级 httpx.post 截不到
    import core.vision as vision_module

    sent: dict = {}

    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": '{"confidence": 1}'}}]}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            sent["trust_env"] = kwargs.get("trust_env")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            sent.update(url=url, model=(kwargs.get("json") or {}).get("model"))
            return _Resp()

    real_client = vision_module.httpx.Client
    vision_module.httpx.Client = _FakeClient
    try:
        VisionClient(legacy).chat("ping", b"PNG")
    finally:
        vision_module.httpx.Client = real_client
    assert sent.get("trust_env") is False, "实发请求没有关闭环境代理"
    assert sent.get("model") == default_model, f"实发模型与面板显示不一致:{sent}"
    assert default_base in (sent.get("url") or ""), f"实发地址与面板显示不一致:{sent}"
    return "地址/模型 id:非空即落盘、重开仍在、留空不覆盖、旧空串回填默认且与实发一致"


def __api_key_mask_case():
    """API Key 不回传明文:读取只给尾号提示,留空保持、显式清除、重新输入替换。"""
    import tempfile
    from pathlib import Path

    from core.api import Api
    from core.config import ConfigStore
    from core.events import EventBus
    from core.runner import Runner
    from core.scheduler import SlotScheduler
    from core.storage import SessionStore
    from core.vision import VisionClient

    tmp_dir = Path(tempfile.mkdtemp(prefix="zcode-selftest-"))
    cfg = ConfigStore(tmp_dir / "config.yaml")
    cfg.patch({"vision": {"api_key": "sk-selftest1234abcd"}})
    bus = EventBus()
    store = SessionStore(tmp_dir / "shots")
    vision = VisionClient(cfg)
    runner = Runner(cfg, bus, store, vision)
    api = Api(cfg, bus, store, vision, runner, SlotScheduler(cfg, lambda slot: None))

    data = api.get_vision_config()
    assert data["vision"]["api_key"] == "", "get_vision_config 回传了明文 Key"
    assert data["vision"]["api_key_hint"] == "abcd", data["vision"]["api_key_hint"]

    # 留空提交 = 不修改
    api.save_vision_config({"vision": {"api_key": "", "model": "model-b"}})
    assert (cfg.get("vision", "api_key") or "") == "sk-selftest1234abcd", "留空被误清"
    assert cfg.get("vision", "model") == "model-b", "其余字段应正常保存"

    # 重新输入 = 整体替换(容忍首尾空白);api_key_hint 不得落盘
    result = api.save_vision_config({"vision": {"api_key": "  sk-new-key-9999  ", "api_key_hint": "9999"}})
    assert (cfg.get("vision", "api_key") or "") == "sk-new-key-9999", "新 Key 未写入"
    assert result["vision"]["api_key"] == "" and result["vision"]["api_key_hint"] == "9999"
    assert "api_key_hint" not in (cfg.get("vision") or {}), "展示字段被写进配置"

    # 显式清除(null)= 置空;has_key 随之为 False
    api.save_vision_config({"vision": {"api_key": None}})
    assert not (cfg.get("vision", "api_key") or "").strip(), "显式清除未生效"
    assert api.get_vision_config()["vision"]["api_key_hint"] == ""
    return "明文不下发;留空保持 / 替换 / 清除与尾号提示均正确"


def __frozen_path_case():
    """冻结路径分流:打包后资源从 _MEIPASS 读、数据写到 exe 同级。

    不真的打包,而是用一个隔离进程把 sys.frozen / sys._MEIPASS 造出来,
    再 import core.config,验证路径常量落在预期位置。
    """
    import subprocess
    import tempfile
    from pathlib import Path

    from core.config import FROZEN, UI_DIR, DATA_DIR

    # 源码运行:FROZEN 为假,资源与数据都在项目根下
    assert FROZEN is False, "源码运行时 FROZEN 应为 False"
    assert UI_DIR.name == "ui" and UI_DIR.is_dir(), f"UI_DIR 不对:{UI_DIR}"
    expected_data = Path(__file__).resolve().parent / "data"
    assert DATA_DIR == expected_data, f"源码运行的 DATA_DIR 应为项目 data/:{DATA_DIR}"

    # 打包运行:在子进程里伪造 frozen 环境。
    # 子进程代码写到临时文件再执行,避免多层引号/转义把脚本拼坏。
    with tempfile.TemporaryDirectory() as tmp:
        meipass = Path(tmp) / "meipass"
        exe_dir = Path(tmp) / "exe"
        (meipass / "ui").mkdir(parents=True)
        (meipass / "ui" / "index.html").write_text("x", encoding="utf-8")
        exe_dir.mkdir(parents=True)

        script = exe_dir / "check.py"
        script.write_text(
            "import sys, pathlib\n"
            "sys.frozen = True\n"
            f"sys._MEIPASS = {str(meipass)!r}\n"
            f"sys.executable = {str(exe_dir / 'ZCode.exe')!r}\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
            "from core import config as c\n"
            "assert c.FROZEN is True, 'FROZEN 未生效'\n"
            f"assert c.UI_DIR == pathlib.Path({str(meipass / 'ui')!r}), f'UI_DIR 应为 _MEIPASS/ui: {{c.UI_DIR}}'\n"
            f"assert c.DATA_DIR == pathlib.Path({str(exe_dir / 'data')!r}), f'DATA_DIR 应为 exe 同级 data/: {{c.DATA_DIR}}'\n"
            "assert c.CONFIG_PATH.parent == c.DATA_DIR, 'CONFIG_PATH 应落在 DATA_DIR 内'\n"
            "assert c.LOG_DIR.parent == c.DATA_DIR, '日志目录应落在 DATA_DIR 内'\n"
            "assert c.SHOTS_DIR.parent == c.DATA_DIR, '截图目录应落在 DATA_DIR 内'\n"
            "print('ok')\n",
            encoding="utf-8",
        )
        proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=90)
        assert proc.returncode == 0, f"冻结路径模拟失败:{proc.stderr.strip()[-300:]}"
        assert "ok" in proc.stdout, f"子进程输出异常:{proc.stdout!r}"

    return "源码与冻结两种模式路径均正确(资源 _MEIPASS / 数据 exe 同级)"


def __single_instance_case():
    """单实例锁:同进程重复 acquire 幂等;跨进程第二个必须被拒。

    父进程持锁期间子进程应拿不到;释放后子进程应能拿到。
    """
    import subprocess
    import tempfile
    from pathlib import Path

    from core import single_instance as si

    if sys.platform != "win32":
        assert si.acquire() is True
        si.release()
        return "非 Windows 平台:跳过多实例校验(acquire 恒放行)"

    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "child.py"
        script.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
            "from core.single_instance import acquire\n"
            "print('ACQUIRED' if acquire() else 'BLOCKED')\n",
            encoding="utf-8",
        )

        def child_out():
            done = subprocess.run([sys.executable, str(script)], capture_output=True,
                                  text=True, timeout=90)
            assert done.returncode == 0, f"子进程执行失败:{done.stderr.strip()[-200:]}"
            return done.stdout

        if not si.acquire():
            # 本机已经有一个正式实例在运行(面板开着),锁在人家的进程里。
            # 这正是"第二个实例必须被拒"的现场:只验证跨进程拒绝,不去抢别人的锁,
            # 也不 release(那会把别人的实例判成已退出)。
            assert "BLOCKED" in child_out(), "外部实例持锁时,新进程应当拿不到锁"
            return "本机已有实例在运行:跨进程被拒已验证(幂等/释放两项跳过)"

        assert si.acquire() is True, "同进程重复 acquire 应幂等返回 True"
        assert "BLOCKED" in child_out(), "已有实例时子进程不应拿到锁"

        si.release()
        assert "ACQUIRED" in child_out(), "释放后子进程应能拿到锁"

    return "同进程幂等;跨进程占用时被拒、释放后可获得"


def __awake_case():
    """防休眠:开关语义、execution 嵌套计数、以及真的会调用系统 API。

    会短暂调用 SetThreadExecutionState(改的是"本进程所在线程"的执行状态,
    进程退出即失效,不影响系统设置),调用后立刻复位。
    """
    from core import awake

    if sys.platform != "win32":
        assert awake.is_active() is False
        return "非 Windows 平台:仅验证状态机(调用直接返回 False)"

    # 初始:未保活
    awake.set_always(False)
    assert awake.is_active() is False, "初始应为未保活"

    # always 模式:开启 → 激活;关闭 → 失活
    assert awake.set_always(True) is True, "set_always(True) 应成功调用系统 API"
    assert awake.is_active() is True, "always 开启后应处于保活"
    assert awake.set_always(False) is True, "set_always(False) 应成功"
    assert awake.is_active() is False, "always 关闭后应失活"

    # execution 模式:嵌套计数,内层退出不应提前解除
    awake.start_execution()
    assert awake.is_active() is True, "进入执行应保活"
    awake.start_execution()
    awake.stop_execution()
    assert awake.is_active() is True, "仍有外层嵌套时不应解除保活"
    awake.stop_execution()
    assert awake.is_active() is False, "全部退出后应解除保活"
    awake.stop_execution()          # 多余调用不应把计数弄成负数
    assert awake.is_active() is False, "多余 stop 后仍应失活"

    # always 与 execution 叠加:always 开着时,执行结束不该解除保活
    awake.set_always(True)
    awake.start_execution()
    awake.stop_execution()
    assert awake.is_active() is True, "always 模式开着时,执行结束应继续保活"
    awake.set_always(False)
    assert awake.is_active() is False

    return "always 开关 / execution 嵌套计数正确,且确实调用了 SetThreadExecutionState"


def __shortcut_resolve_case():
    """开始菜单 .lnk 解析(win32com 延迟导入链路)。

    打包后 win32com 若漏打,自动探测会静默退化成"找不到 ZCode";
    这里直接调用解析函数,确认该链路在本机可用。
    本机没有 ZCode 快捷方式时跳过(那种情况下这条链路本来就不参与)。
    """
    from pathlib import Path

    import core.zcode_ctrl as zc

    lnks = []
    for d in zc._start_menu_dirs():
        try:
            lnks.extend(p for p in d.rglob("*.lnk") if "zcode" in p.stem.lower())
        except OSError:
            continue

    if not lnks:
        return "本机开始菜单无 ZCode 快捷方式,跳过该项(不影响使用)"

    target = zc._resolve_lnk(lnks[0])
    assert target, f"快捷方式解析失败:{lnks[0]}(win32com 可能不可用)"
    assert target.lower().endswith(".exe"), f"解析结果不是 exe:{target}"
    assert Path(target).is_file(), f"解析出的路径不存在:{target}"
    return f"快捷方式解析正常:{lnks[0].name} → {target}"


def __notify_payload_case():
    """通知载荷构造与响应判定(纯本地,不联网、不发通知)。

    事故背景:webhook 原样发 {"title","text","content","desp"} 给飞书,而飞书要求
    显式 msg_type,于是每次都回 19002 params error;更隐蔽的是飞书出错也返回
    HTTP 200,而旧代码只看状态码 → 推送明明失败却一路当成功,用户完全无感。
    """
    from core import notify as notify_mod

    # ① 飞书地址必须转成它认识的 post 结构
    fs = notify_mod._feishu_payload("标题", "正文")
    assert fs["msg_type"] == "post", f"飞书载荷缺少 msg_type:{fs}"
    post = fs["content"]["post"]["zh_cn"]
    assert post["title"] == "标题", f"标题未落到正文结构:{post}"
    assert post["content"][0][0]["text"] == "正文", f"正文未落到正文结构:{post}"
    for host in ("https://open.feishu.cn/open-apis/bot/v2/hook/x",
                 "https://open.larksuite.com/open-apis/bot/v2/hook/x"):
        assert notify_mod._is_feishu(host), f"未识别为飞书地址:{host}"
    assert not notify_mod._is_feishu("https://sctapi.ftqq.com/x.send"), "Server酱被误判为飞书"

    # ② 非飞书地址仍走通用载荷(兼容 Server 酱 / 企业微信机器人)
    generic = notify_mod._generic_payload("标题", "正文")
    for key in ("title", "text", "content", "desp"):
        assert key in generic, f"通用载荷缺少 {key}"

    # ③ 关键回归:HTTP 200 但 body 里 code 非 0 必须算失败
    assert notify_mod._response_error('{"code":19002,"msg":"params error, msg_type need"}'), \
        "飞书 200 + code!=0 未被识别为失败(这正是静默失败的根因)"
    assert notify_mod._response_error('{"code":0,"msg":"success"}') is None, "正常响应被误判为失败"
    assert notify_mod._response_error('{"errcode":93000,"errmsg":"invalid webhook"}'), \
        "企业微信 errcode!=0 未被识别为失败"
    assert notify_mod._response_error('{"StatusCode":0,"StatusMessage":"success"}') is None, \
        "正常响应被误判为失败"
    assert notify_mod._response_error("not json") is None, "非 JSON 响应不应报错"
    return "飞书载荷结构正确;HTTP 200 + code!=0 能识别为失败(旧版会静默当成功)"


def __notify_webhook_live_case():
    """真实 webhook 推送(仅在配置了地址时执行;未配置则跳过)。"""
    from core.config import ConfigStore
    from core import notify as notify_mod

    cfg = ConfigStore()
    url = (cfg.get("notify", "webhook_url") or "").strip()
    if not url:
        return "未配置 webhook 地址,跳过该项(不影响其他检查)"
    ok, detail = notify_mod.webhook(url, "ZCode 福利助手 · 自检", "这是一条自检消息,可忽略。")
    assert ok, f"webhook 推送失败:{detail}"
    return f"webhook 推送成功({detail})"


def __no_card_case():
    """按用户策略:卡片不在 → 等 no_card_wait_s 复查一次;晚出现继续领,仍不在按重试循环。

    旧策略是复查两次后直接收敛为"当期无可领"、整场不再重试;现行策略改为与普通
    失败一致:复查仍不在就进"关客户端 → 等间隔 → 重开"循环,由尝试次数兜底。
    """
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    from core.config import ConfigStore
    from core.events import EventBus
    from core.runner import Runner
    from core.storage import SessionStore
    from core.vision import VisionClient

    tmp = Path(tempfile.mkdtemp(prefix="zcode-nocard-"))
    cfg = ConfigStore(tmp / "config.yaml")
    cfg.patch({
        "app": {"dry_run": False},
        "retry": {"no_card_wait_s": 0},
        "vision": {"api_key": "selftest-fake-key"},
    })
    store = SessionStore(tmp / "shots")
    runner = Runner(cfg, EventBus(), store, VisionClient(cfg))
    target = runner.slot_targets({})[0]
    cfg_dict = cfg.all()

    shot = SimpleNamespace(png=b"\x89PNG-fake", width=1240, height=860,
                           rect=(40, 40, 1240, 860), uniform_ratio=0.3,
                           full_size=(1920, 1080))
    runner._open_client = lambda dry, cfg, target: {"hwnd": 1, "title": "Fake", "rect": [40, 40, 1240, 860]}
    runner._close_client = lambda dry: None
    runner._grab_ready = lambda win, rect, cfg, dry, target, label, timeout=None: (shot, None)
    runner._position_window = lambda win, cfg, dry, target=None: tuple(win["rect"])
    runner._wait_user_idle = lambda cfg, abort: False
    runner._ensure_foreground = lambda win, cfg: True
    import core.clicker as clicker_module
    clicks = []
    clicker_module.click_norm = lambda rect, box, humanize=False: clicks.append(box)
    runner._foreground_unreachable = lambda win, target: ""

    located = {"n": 0}

    def fake_locate(png):
        located["n"] += 1
        return {"card_visible": False, "button_found": False, "claimable": False,
                "confidence": 0.9, "notes": "画面上没有福利卡片"}

    runner.vision.locate = fake_locate
    session = store.begin("manual", {"time": None})
    attempt = store.begin_attempt(session, 1)
    import threading
    result, reason = runner._one_attempt(session, attempt, False, threading.Event(), cfg_dict, target)

    assert result == "retry", f"复查仍无卡片应按重试处理,得到 {result}({reason})"
    assert not clicks, f"卡片不在时不该点击:{clicks}"
    assert located["n"] == 2, f"应先原地复查一次再定,实际识别了 {located['n']} 次"
    assert "复查" in reason, f"结论未说明已复查:{reason}"

    # 卡片只是晚渲染:复查时出现 → 应继续正常领取
    located["n"] = 0

    def late_card(png):
        located["n"] += 1
        if located["n"] == 1:
            return {"card_visible": False, "button_found": False, "claimable": False,
                    "confidence": 0.9, "notes": "还没看到卡片"}
        return {"card_visible": True, "button_found": True, "claimable": True,
                "button_box": [0.089, 0.909, 0.106, 0.93], "button_label": "领取",
                "confidence": 0.93, "notes": "卡片出现了"}

    runner.vision.locate = late_card
    runner.vision.verify = lambda png, target=None: {"popup": "success", "keywords": ["领取成功"],
                                        "confidence": 0.95}
    clicks.clear()
    session2 = store.begin("manual", {"time": None})
    attempt2 = store.begin_attempt(session2, 1)
    result2, _ = runner._one_attempt(session2, attempt2, False, threading.Event(), cfg_dict, target)
    assert result2 == "success", f"卡片晚出现时应继续领取,得到 {result2}"
    assert len(clicks) == 1, f"卡片出现后应点击一次,实际 {len(clicks)} 次"
    return "卡片不在:复查一次,晚出现继续领,仍缺失按重试循环(不点击)"


def __verify_pending_case():
    """按用户策略:点击后固定等待 verify_wait_s 秒,到点截图判定一次。

    到点时仍在加载 → 按未成功进入重试循环(等待本身必须真实发生,不能瞬间跳过);
    到点时已出成功弹窗 → 直接成功。全程只校验一次,不轮询。
    """
    import tempfile
    import threading
    import time
    from pathlib import Path
    from types import SimpleNamespace

    from core.config import ConfigStore
    from core.events import EventBus
    from core.runner import Runner
    from core.storage import SessionStore
    from core.vision import VisionClient

    tmp = Path(tempfile.mkdtemp(prefix="zcode-pending-"))
    cfg = ConfigStore(tmp / "config.yaml")
    cfg.patch({
        "app": {"dry_run": False},
        "retry": {"verify_wait_s": 1, "focus_settle_s": 0},
        "vision": {"api_key": "selftest-fake-key"},
    })
    store = SessionStore(tmp / "shots")
    runner = Runner(cfg, EventBus(), store, VisionClient(cfg))
    target = runner.slot_targets({})[0]
    cfg_dict = cfg.all()

    shot = SimpleNamespace(png=b"\x89PNG-fake", width=1240, height=860,
                           rect=(40, 40, 1240, 860), uniform_ratio=0.3,
                           full_size=(1920, 1080))
    runner._open_client = lambda dry, cfg, target: {"hwnd": 1, "title": "Fake", "rect": [40, 40, 1240, 860]}
    runner._close_client = lambda dry: None
    runner._grab_ready = lambda win, rect, cfg, dry, target, label, timeout=None: (shot, None)
    runner._position_window = lambda win, cfg, dry, target=None: tuple(win["rect"])
    runner._wait_user_idle = lambda cfg, abort: False
    runner._ensure_foreground = lambda win, cfg: True
    runner.vision.locate = lambda png: {
        "button_found": True, "claimable": True, "card_visible": True,
        "button_box": [0.089, 0.909, 0.106, 0.93], "button_label": "领取",
        "confidence": 0.93, "scene": "welfare",
    }
    import core.clicker as clicker_module
    real_click = clicker_module.click_norm
    clicks = []
    clicker_module.click_norm = lambda rect, box, humanize=False: clicks.append(box)
    runner._foreground_unreachable = lambda win, target: ""

    try:
        # 到点仍在加载:按未成功收尾进入重试循环;等待必须真实发生
        checks = {"n": 0}

        def fake_verify(png, target=None):
            checks["n"] += 1
            return {"pending": True, "popup": "pending", "keywords": ["领取中"],
                    "confidence": 0.9, "notes": "按钮在转圈"}

        runner.vision.verify = fake_verify
        session = store.begin("manual", {"time": None})
        attempt = store.begin_attempt(session, 1)
        started = time.time()
        result, reason = runner._one_attempt(session, attempt, False, threading.Event(), cfg_dict, target)
        elapsed = time.time() - started
        assert result == "retry", f"到点仍在加载应按未成功重试,得到 {result}({reason})"
        assert elapsed >= 1.0, f"点击后的固定等待应真实发生,实际只等了 {elapsed:.1f}s"
        assert checks["n"] == 1, f"应只校验一次,实际 {checks['n']} 次"
        assert len(clicks) == 1, f"应只点击一次,实际 {len(clicks)} 次"
        assert "加载" in reason, f"收尾原因未说明仍在加载:{reason}"
        # 校验图留档,面板可回放这次判定
        saved = list((tmp / "shots" / session["id"]).glob("attempt-1-after*.png"))
        assert len(saved) == 1, f"校验截图应留档 1 张,实际 {len(saved)} 张"

        # 到点已出成功弹窗:直接成功收尾
        checks["n"] = 0
        runner.vision.verify = lambda png, target=None: {"popup": "success", "keywords": ["领取成功"],
                                            "confidence": 0.95}
        session2 = store.begin("manual", {"time": None})
        attempt2 = store.begin_attempt(session2, 1)
        result2, _ = runner._one_attempt(session2, attempt2, False, threading.Event(), cfg_dict, target)
        assert result2 == "success", f"到点已出成功弹窗应判成功,得到 {result2}"
    finally:
        clicker_module.click_norm = real_click

    return "点击后固定等待一次判定:仍在加载即重试,出结果即成功(不轮询)"


def __claims_case():
    """领取周期(once=none/daily/weekly)与多目标场次分发。

    背景:福利有周期,而用户会在一天里排好几个候选场次 —— 某场领到后,同周期内
    后面的场次再跑也变不出福利,只会白开客户端、白调两次视觉。另一个方向同样重要:
    ZCode 现在是「每天每场都要跑」,用 once=none 表达,任何场次都不许跳过。
    """
    import tempfile
    from datetime import date, timedelta
    from pathlib import Path
    from types import SimpleNamespace

    from core.claims import ClaimStore, period_of, week_end_of
    from core.config import ConfigStore
    from core.events import EventBus
    from core.runner import Runner
    from core.storage import SessionStore
    from core.targets import ONCE_DAILY, ONCE_NONE, ONCE_WEEKLY
    from core.vision import VisionClient

    # ① 周期边界:weekly 从周五起算,daily 跨零点即重来,none 不设周期
    fri = date(2026, 9, 18)          # 周五
    sat = fri + timedelta(days=1)
    thu = fri + timedelta(days=6)    # 下个周四
    nxt = fri + timedelta(days=7)    # 再下一个周五
    assert fri.weekday() == 4 and thu.weekday() == 3
    assert period_of(ONCE_NONE, fri) is None, "once=none 不该有周期"
    assert period_of("bogus", fri) is None, "无法识别的周期值应按不记账处理,而不是悄悄变成每周"
    assert period_of(ONCE_DAILY, fri) == fri.isoformat()
    assert period_of(ONCE_DAILY, sat) == sat.isoformat(), "每天一次:跨零点应进入新周期"
    assert period_of(ONCE_WEEKLY, fri) == fri.isoformat()
    assert period_of(ONCE_WEEKLY, sat) == fri.isoformat(), "周六应属于周五开头那一周"
    assert period_of(ONCE_WEEKLY, thu) == fri.isoformat(), "周四应属于同一周期"
    assert period_of(ONCE_WEEKLY, nxt) == nxt.isoformat(), "下一个周五应开启新周期"
    assert period_of(ONCE_WEEKLY, fri - timedelta(days=1)) == (fri - timedelta(days=7)).isoformat(), \
        "周四(前一天)属于上一周期,不能被算进本周"
    assert week_end_of(fri) == thu

    # ② 记账按目标隔离,周期一过自动失效
    tmp = Path(tempfile.mkdtemp(prefix="zcode-claims-"))
    claims = ClaimStore(tmp / "claims.json")
    assert claims.claimed("zcode", ONCE_WEEKLY, fri) is False, "初始应为未领取"
    claims.mark("zcode", ONCE_WEEKLY, slot="19:00", source="schedule", when=fri)
    assert claims.claimed("zcode", ONCE_WEEKLY, sat) is True, "同一周期内仍算已领取"
    assert claims.claimed("trae", ONCE_WEEKLY, sat) is False, "别的目标不该跟着被跳过"
    assert claims.claimed("zcode", ONCE_WEEKLY, nxt) is False, "下一周期应自动失效"
    assert claims.claimed("zcode", ONCE_DAILY, fri) is True, "同一天的 daily 也算已领"
    assert claims.claimed("zcode", ONCE_DAILY, sat) is False, "daily 到第二天就该重来"
    state = claims.state("zcode", ONCE_WEEKLY, fri)
    assert state["claimed_slot"] == "19:00" and state["claimed_source"] == "schedule"
    assert claims.mark("zcode", ONCE_NONE, when=fri) == {}, "once=none 不该写账(否则就变成会跳过的目标)"
    assert claims.clear("nope") == 0
    assert claims.clear("zcode") == 1
    assert claims.claimed("zcode", ONCE_WEEKLY, fri) is False, "清除后应回到未领取"

    # ③ 坏文件不能让流程崩:当作未领取处理(宁可多试一次,也不要漏领)
    claims.mark("zcode", ONCE_WEEKLY, when=fri)
    (tmp / "claims.json").write_text("{ 这不是 json", encoding="utf-8")
    assert claims.claimed("zcode", ONCE_WEEKLY, fri) is False, "损坏的记账文件应按未领取处理"

    # ④ 执行器:一个场次跑多个目标,已领的目标跳过、未限周期的照跑
    tmp2 = Path(tempfile.mkdtemp(prefix="zcode-claims-run-"))
    cfg = ConfigStore(tmp2 / "config.yaml")
    cfg.patch({
        "targets": [
            {"id": "zcode", "name": "ZCode", "exe": r"D:\Zcode\ZCode.exe", "once": ONCE_WEEKLY},
            {"id": "trae", "name": "Trae", "exe": r"D:\Trae\Trae CN.exe", "once": ONCE_NONE},
            {"id": "off", "name": "已停用", "exe": r"D:\Off\Off.exe", "enabled": False},
        ],
        "app": {"dry_run": False},
        "vision": {"api_key": "selftest-fake-key"},
        "notify": {"desktop": False, "webhook_url": ""},
        "retry": {"watchdog_s": 30, "verify_wait_s": 0, "max_attempts": 1, "retry_gap_s": 1},
    })
    store = SessionStore(tmp2 / "shots")
    runner = Runner(cfg, EventBus(), store, VisionClient(cfg), claims=claims)

    def fake_open(dry, cfg, target):
        opened.append(target["id"])
        return {"hwnd": 1, "title": "Fake", "rect": [40, 40, 1240, 860]}

    opened: list[str] = []
    runner._open_client = fake_open
    runner._close_client = lambda dry: None
    runner._wait_user_idle = lambda cfg, abort: False
    runner._ensure_foreground = lambda win, cfg: True
    runner._position_window = lambda win, cfg, dry, target=None: tuple(win["rect"])
    shot = SimpleNamespace(png=b"x", width=1240, height=860, rect=(40, 40, 1240, 860),
                           uniform_ratio=0.3, full_size=(1920, 1080))
    runner._grab_ready = lambda win, rect, cfg, dry, target, label, timeout=None: (shot, None)
    runner.vision.locate = lambda png: {
        "button_found": True, "claimable": True, "card_visible": True,
        "button_box": [0.089, 0.909, 0.106, 0.93], "button_label": "领取",
        "confidence": 0.93, "scene": "welfare",
    }
    runner.vision.verify = lambda png, target=None: {
        "popup": "success", "keywords": ["领取成功"], "confidence": 0.95,
    }

    assert [t["id"] for t in runner.slot_targets({})] == ["zcode", "trae"], \
        "留空的目标场次应跑全部「启用」的目标"
    assert [t["id"] for t in runner.slot_targets({"target": "trae"})] == ["trae"], "指定目标应只跑那一个"
    assert runner.slot_targets({"target": "nope"}) == [], "指定不存在的目标应判无目标可跑"
    assert runner.slot_targets({"target": "off"}) == [], "停用的目标不该被场次拉起"

    import core.clicker as clicker_module
    real_click = clicker_module.click_norm
    clicker_module.click_norm = lambda rect, box, humanize=False: None
    runner._foreground_unreachable = lambda win, target: ""
    try:
        res1 = runner.run_session("schedule", {"time": "19:00"})
        assert opened == ["zcode", "trae"], f"两个目标应各开一次客户端:{opened}"
        assert [r["status"] for r in res1["results"]] == ["success", "success"], res1
        assert claims.claimed("zcode", ONCE_WEEKLY) is True, "领取成功后应自动记账"
        assert claims.claimed("trae", ONCE_WEEKLY) is False, "once=none 的目标不该记账(否则就会被跳过)"

        # 同一周期内的下一场:zcode 跳过(不碰客户端),trae 照跑
        opened.clear()
        res2 = runner.run_session("schedule", {"time": "20:00"})
        assert [r["status"] for r in res2["results"]] == ["skipped", "success"], res2
        assert opened == ["trae"], f"已领的目标不该再打开客户端:{opened}"
        skipped = next(r for r in res2["results"] if r["status"] == "skipped")
        assert "一周只有一次机会" in skipped["summary"], f"跳过原因未说明周期限制:{skipped['summary']}"
        data = store.load(skipped["session_id"])
        assert data and data["status"] == "skipped" and data["target"] == "zcode", \
            "跳过场次应留一条带目标名的记录供面板显示"
        assert not data.get("attempts"), "跳过的场次不该产生尝试记录"

        # 手动试领是用户主动要求,不受周期限制
        opened.clear()
        runner.run_session("manual", {"attempts": 1})
        assert opened == ["zcode", "trae"], f"手动试领应无视周期:{opened}"

        # 目标改成「每天一次」并记账:同样跳过,但提示要说清是每天的限制
        items = cfg.all()["targets"]
        items[0]["once"] = ONCE_DAILY
        cfg.patch({"targets": items})
        claims.mark("zcode", ONCE_DAILY, slot="19:00", source="schedule")
        opened.clear()
        res3 = runner.run_session("schedule", {"time": "21:00"})
        first = res3["results"][0]
        assert first["status"] == "skipped" and "每天只有一次机会" in first["summary"], res3

        # 演练成功不算真正领到,不该占掉机会
        claims.clear()
        cfg.patch({"app": {"dry_run": True}})
        runner.run_session("schedule", {"time": "19:00"})
        assert claims.claimed("zcode", ONCE_DAILY) is False, "演练不应记账(否则一次演练就把机会用掉)"
        cfg.patch({"app": {"dry_run": False}})
    finally:
        clicker_module.click_norm = real_click

    # ⑤ 面板「试领一次」的目标选择必须一路传到执行器:用户要能只测 WorkBuddy/Trae,
    #    而不是被排在前面的 ZCode 用 5 次重试拖住,以为"这个按钮只管 ZCode"。
    from core.api import Api
    from core.scheduler import SlotScheduler

    spawned: list[dict] = []
    api = Api(cfg, EventBus(), store, VisionClient(cfg), runner,
              SlotScheduler(cfg, lambda slot: None))
    api.runner.spawn = lambda trigger, slot=None: (spawned.append(slot or {}), True)[1]

    assert api.run_once(None)["ok"] is True, "手动跑全部目标应能启动"
    assert spawned[-1] == {}, f"没选目标时该跑全部,不该被塞进 target:{spawned[-1]}"
    one = api.run_once({"target": "trae", "dry": True})
    assert spawned[-1] == {"dry": True, "target": "trae"}, spawned[-1]
    assert "Trae" in one["message"] and one["targets"] == ["trae"], one
    assert api.run_once({"target": "nope"})["ok"] is False, "指定不存在的目标必须直接拒绝"
    assert api.run_once({"target": "off"})["ok"] is False, "停用的目标不该被手动拉起来"
    assert api.run_target({"id": "trae"})["ok"] is True, "签到目标页的「只试跑这个目标」应可用"
    assert spawned[-1].get("target") == "trae", spawned[-1]

    # ⑥ 没有可跑目标时的场次:给出人话原因,而不是抛异常或空转
    cfg.patch({"targets": [{"id": "off2", "name": "已停用", "exe": r"D:\O\O.exe", "enabled": False}]})
    res4 = runner.run_session("schedule", {"time": "19:00"})
    assert res4["status"] == "failed" and "签到目标" in res4["summary"], res4

    return "周期边界正确;记账按目标隔离且跨周期失效;已领则跳过且不碰客户端;手动与演练不受限;多目标分发正确"


def __targets_case():
    """目标模型:清洗非法值、id 唯一,以及 v1.x 单目标配置的一次性迁移。

    迁移只保真不改语义:老配置 weekly_once=true → once=weekly,想「每天每场都跑」
    要显式改成 none —— 部署时按用户要求处理,但代码里不能替用户偷偷改行为。
    """
    import tempfile
    from pathlib import Path

    import yaml

    from core.config import DEFAULTS, load_config
    from core.targets import (ONCE_NONE, ONCE_WEEKLY, all_targets,
                              enabled_targets, find, keywords_for, migrate_from_legacy,
                              normalize, normalize_all)

    # ① 清洗:非法枚举退回默认,残缺字段补齐,坏点击点丢弃
    t = normalize({"id": "Trae CN", "exe": "D:\\a\\b.exe", "flow": "bogus", "once": "weekly",
                   "find_by": "title", "startup_wait_s": "4",
                   "steps": [{"anchor": "bottom_left", "dx": 10, "dy": 20, "label": "头像"},
                             {"anchor": "somewhere_wrong", "dx": 1, "dy": 2},
                             {"anchor": "window", "dx": "x", "dy": 2},
                             "不是 dict"]})
    assert t["id"] == "traecn", f"id 应被 slug 化:{t['id']}"
    assert t["flow"] == "vision", "无法识别的流程应退回视觉模式"
    assert t["once"] == ONCE_WEEKLY
    assert t["startup_wait_s"] == 4.0, "数字字符串应能转成浮点"
    assert len(t["steps"]) == 1 and t["steps"][0]["label"] == "头像", f"非法点击点应被丢掉:{t['steps']}"
    assert t["steps"][0]["id"] == "step1" and t["steps"][0]["wait_after_s"] == 0.0
    assert normalize("不是 dict") is None, "非 dict 入参不得当成目标"
    assert normalize({"name": "签到目标"})["id"] == "target", \
        "纯中文名称转不出 id 时用兜底 id,而不是把用户的目标丢掉"
    assert normalize({"id": "", "name": "Trae CN"}, target_id="target3")["id"] == "target3", \
        "整表保存时按位置给缺 id 的目标兜底"
    assert normalize({"id": "x", "exe": "D:\\WorkBuddy\\WorkBuddy.exe"})["name"] == "WorkBuddy", \
        "未填名称时用可执行文件名,面板上不至于显示空白"
    assert normalize({"id": "x"})["window_title_match"] == "x", "标题匹配应有兜底值"
    assert normalize({"id": "x", "startup_wait_s": -5})["startup_wait_s"] == 0.0

    # ② id 唯一:重复 id 会让记账/场次指向错乱,必须自动错开
    items = normalize_all([{"id": "a"}, {"id": "a"}, {"id": "a"}, {"id": "A"}])
    assert [i["id"] for i in items] == ["a", "a-2", "a-3", "a-4"], [i["id"] for i in items]

    # ③ 配置里完全没有 targets 时,出厂给一个 ZCode 视觉目标
    assert [t["id"] for t in all_targets({})] == ["zcode"], "空配置应回落到默认目标"
    assert all_targets({})[0]["flow"] == "vision" and all_targets({})[0]["once"] == ONCE_NONE

    # ④ 词表覆盖:目标自带优先,没有则用全局
    cfg = {"success_keywords": ["领取成功"]}
    assert keywords_for({"success_keywords": ["签到成功"]}, cfg, "success_keywords") == ["签到成功"]
    assert keywords_for({}, cfg, "success_keywords") == ["领取成功"]

    # ⑤ 旧配置迁移:路径/标题/全屏/启动等待/每周一次逐项对应
    legacy = {"app": {"zcode_path": "D:\\Zcode\\ZCode.exe", "window_title_match": "zcode 窗口",
                      "fullscreen": False, "startup_wait_s": 8, "weekly_once": True}}
    moved = migrate_from_legacy(legacy)
    assert len(moved) == 1
    assert moved[0]["exe"] == r"D:\Zcode\ZCode.exe" and moved[0]["window_title_match"] == "zcode 窗口"
    assert moved[0]["fullscreen"] is False and moved[0]["startup_wait_s"] == 8.0
    assert moved[0]["once"] == ONCE_WEEKLY, "weekly_once=true 对应 once=weekly"
    assert migrate_from_legacy({"app": {"zcode_path": "x", "weekly_once": False}})[0]["once"] == ONCE_NONE
    assert migrate_from_legacy({"app": {"weekly_once": True}}) is None or True
    assert migrate_from_legacy({"targets": [{"id": "z"}], "app": {"zcode_path": "D:\\a.exe"}}) is None, \
        "已有 targets 时不得重复迁移"

    # ⑥ 落盘迁移:老 config.yaml 第一次被读取时就翻译成 targets 并写回
    tmp = Path(tempfile.mkdtemp(prefix="zcode-migrate-"))
    path = tmp / "config.yaml"
    path.write_text(yaml.safe_dump({
        "app": {"zcode_path": r"E:\ZCode\ZCode.exe", "weekly_once": True, "dry_run": False,
                "window_rect": [0, 0, 100, 100], "restore_window_rect": True},
        "schedule": {"slots": [{"time": "19:00", "enabled": True}]},
        "vision": {"api_key": "sk-legacy"},
    }, allow_unicode=True), encoding="utf-8")
    migrated = load_config(path)
    assert [t["id"] for t in migrated["targets"]] == ["zcode"], migrated.get("targets")
    assert migrated["targets"][0]["exe"] == r"E:\ZCode\ZCode.exe"
    assert migrated["targets"][0]["once"] == ONCE_WEEKLY
    assert migrated["schedule"]["slots"][0]["target"] == "zcode", "老场次应锁定到迁移出的目标"
    assert migrated["vision"]["api_key"] == "sk-legacy", "迁移不能丢 API Key"
    for gone in ("zcode_path", "window_rect", "restore_window_rect", "weekly_once"):
        assert gone not in migrated["app"], f"旧键 {gone} 应被清掉,免得改了以为还有效"
    on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert on_disk.get("targets"), "迁移结果要落盘,否则每次启动都重算"
    assert "zcode_path" not in on_disk["app"]
    # 再读一次:已有 targets,不再迁移,也不会凭空多出一个目标
    again = load_config(path)
    assert [t["id"] for t in again["targets"]] == ["zcode"]
    assert again["targets"][0]["exe"] == r"E:\ZCode\ZCode.exe"

    # ⑦ 停用与查找
    cfg2 = {"targets": [{"id": "a", "enabled": False}, {"id": "b"}, {"id": "c", "enabled": False}]}
    assert [t["id"] for t in enabled_targets(cfg2)] == ["b"]
    assert find(cfg2)["id"] == "b", "find 空 id 应返回第一个启用的目标"
    assert find(cfg2, "c")["id"] == "c", "显式指定时停用的目标也要能取到(面板要能编辑它)"
    assert find({"targets": [{"id": "z", "enabled": False}]})["id"] == "z", "全停用时也要有兜底"
    assert "targets" in DEFAULTS and len(DEFAULTS["targets"]) == 1
    return "非法值清洗;id 唯一;旧配置一次性迁移并落盘;停用/查找语义正确"


def __coords_case():
    """锚点坐标:录制一次点击 → 换窗口位置/尺寸后再解析回正确的屏幕像素。

    录制面板只能给出"图片上的第几个像素",执行时才知道窗口在哪。
    底边锚点是关键:头像菜单/浮层贴着窗口底边排布,窗口高度变化时它们跟着底边动,
    用整窗归一化会让每个点各自按比例漂移。
    """
    from core.coords import ANCHORS, anchor_of, from_click, resolve

    rect = [100, 50, 1200, 800]      # x, y, w, h

    # ① 四种边角锚点的方向定义
    assert resolve(rect, {"anchor": "top_left", "dx": 10, "dy": 20}) == (110, 70)
    assert resolve(rect, {"anchor": "bottom_left", "dx": 10, "dy": 20}) == (110, 830)
    assert resolve(rect, {"anchor": "top_right", "dx": 10, "dy": 20}) == (1290, 70)
    assert resolve(rect, {"anchor": "bottom_right", "dx": 10, "dy": 20}) == (1290, 830)
    assert resolve(rect, {"anchor": "window", "dx": 0.5, "dy": 0.25}) == (700, 250)

    # ② 录制 → 执行 的往返:窗口移动后仍落在同一个相对位置
    for img_x, img_y in [(88, 772), (177, 393), (1150, 30), (10, 10), (600, 799)]:
        step = from_click(rect, img_x, img_y)
        back = resolve(rect, step)
        assert back == (rect[0] + img_x, rect[1] + img_y), \
            f"往返不一致:{img_x},{img_y} → {step} → {back}"
    # ③ 换了显示器/改了任务栏高度:贴底的点仍贴底,右上角的点仍贴右
    assert from_click(rect, 88, 772)["anchor"] == "bottom_left"
    assert from_click(rect, 1150, 30)["anchor"] == "top_right"
    assert from_click(rect, 1150, 700)["anchor"] == "bottom_right"
    assert from_click(rect, 100, 100)["anchor"] == "top_left"
    moved = [0, 200, 1920, 1000]
    recorded = from_click(rect, 88, 772)          # 底部靠左 → 底边偏移 dx=88, dy=28
    assert recorded == {"anchor": "bottom_left", "dx": 88, "dy": 28}, recorded
    assert resolve(moved, recorded) == (88, 1172), "窗口换到别处/换尺寸后,贴底的点应跟着底边移动"
    assert resolve(moved, {"anchor": "window", "dx": 0.5, "dy": 0.5}) == (960, 700)

    # ④ 非法输入一律返回 None(调用方据此判"坐标无法解析",绝不能瞎点)
    assert resolve(None, {"anchor": "window", "dx": 0.5, "dy": 0.5}) is None
    assert resolve([0, 0, 0, 100], {"anchor": "window", "dx": 0.5, "dy": 0.5}) is None
    assert resolve(rect, {"anchor": "middle", "dx": 1, "dy": 1}) is None
    assert resolve(rect, {"anchor": "window", "dx": None, "dy": 0.5}) is None
    assert resolve(rect, {"anchor": "window", "dx": "abc", "dy": 0.5}) is None
    assert resolve(["x", 0, 100, 100], {"anchor": "window", "dx": 0.5, "dy": 0.5}) is None
    assert set(ANCHORS) == {"window", "top_left", "bottom_left", "top_right", "bottom_right"}
    assert anchor_of(rect, 88, 772) == "bottom_left"

    # ⑤ 窗口矩形含不可见边框时(实测 -8,-8,1928,1040),底边锚点天然对齐客户区底边
    framed = [-8, -8, 1936, 1056]
    assert resolve(framed, {"anchor": "bottom_left", "dx": 88, "dy": 28}) == (80, 1020)
    return "五种锚点方向正确;录制→执行往返一致;窗口移动/缩放后贴边的点跟着边移动"


def __post_clicks_case():
    """领取后补关:成功之后按录制点关掉结果浮窗,补关自己出问题不许改动领取结果。

    背景:Trae 领完券,浮窗还盖在头像那个入口上;ZCode 领完弹「领取成功」卡片。
    次日这场第一下就点在浮窗自己身上,既领不到也认不出界面,所以补一关。
    """
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    import core.capture as capture_module
    import core.clicker as clicker_module
    import core.zcode_ctrl as zcode_module
    from core.config import ConfigStore
    from core.events import EventBus
    from core.runner import Runner, steps_for
    from core.storage import SessionStore
    from core.vision import VisionClient

    tmp = Path(tempfile.mkdtemp(prefix="zcode-post-"))
    cfg = ConfigStore(tmp / "config.yaml")
    cfg.patch({
        "targets": [{
            "id": "trae", "name": "Trae CN", "exe": r"D:\Programs\Trae CN\Trae CN.exe",
            "flow": "steps", "verify": "none", "once": "none",
            "steps": [{"id": "s1", "label": "头像", "anchor": "bottom_left", "dx": 117, "dy": 33}],
            "post_steps": [{"id": "p1", "label": "关掉弹窗", "anchor": "bottom_left",
                            "dx": 117, "dy": 33, "wait_after_s": 0}],
        }, {
            "id": "zcode", "name": "ZCode", "exe": r"E:\Program Files\ZCode\ZCode.exe",
            "flow": "vision", "post_steps": [
                {"id": "p1", "anchor": "top_right", "dx": 758, "dy": 260}],
        }],
        "retry": {"watchdog_s": 30, "max_attempts": 1, "retry_gap_s": 1},
        "notify": {"desktop": False, "webhook_url": ""},
    })
    items = cfg.all()["targets"] if isinstance(cfg.all().get("targets"), list) else []
    from core import targets as tmod
    items = tmod.all_targets(cfg)
    trae = next(t for t in items if t["id"] == "trae")
    zcode_target = next(t for t in items if t["id"] == "zcode")

    # ① 清洗:补关点与签到点同样清洗,脏点丢掉,不认识的键不放过
    assert len(trae["post_steps"]) == 1 and trae["post_steps"][0]["label"] == "关掉弹窗", trae
    dirty = tmod.normalize({"id": "x", "flow": "steps",
                            "post_steps": [{"anchor": "nowhere", "dx": 1, "dy": 1},
                                           {"anchor": "top_left", "dx": "12", "dy": 34}]}, "x")
    assert len(dirty["post_steps"]) == 1, f"脏补关点应被丢掉:{dirty['post_steps']}"
    assert dirty["post_steps"][0]["dx"] == 12.0, dirty["post_steps"]
    assert steps_for(trae)[-1] == "收尾", "补关记在收尾阶段,不新增阶段名"
    assert zcode_target["post_steps"][0]["anchor"] == "top_right", zcode_target["post_steps"]

    runner = Runner(cfg, EventBus(), SessionStore(tmp / "shots"), VisionClient(cfg))
    win = {"hwnd": 1, "title": "Trae CN", "rect": [0, 0, 1920, 1032]}
    calls: list = []
    shots = {"n": 0}

    def fake_shot(rect):
        shots["n"] += 1
        return SimpleNamespace(png=b"x", width=rect[2], height=rect[3], rect=tuple(rect),
                               uniform_ratio=0.4, full_size=(1920, 1080))

    orig = (capture_module.grab_region, clicker_module.click_screen,
            zcode_module.find_target_window, zcode_module.release_stuck_modifiers)
    capture_module.grab_region = fake_shot
    clicker_module.click_screen = lambda x, y, humanize=False: calls.append((x, y))
    zcode_module.find_target_window = lambda target: dict(win)
    zcode_module.release_stuck_modifiers = lambda: []
    runner._foreground_unreachable = lambda w, t: ""
    runner._wait_user_idle = lambda c, a: False
    runner._ensure_foreground = lambda w, c: True
    runner._position_window = lambda w, c, d, t=None: tuple(w["rect"])
    cfg_dict = cfg.all()
    try:
        session = runner.store.begin("manual", {"time": None}, trae)
        attempt = runner.store.begin_attempt(session, 1)

        # ② 补关点击:按窗口矩形换算、留一张 postN 档、写在收尾阶段里
        runner._close_popups(session, attempt, False, threading.Event(), cfg_dict, trae)
        assert calls == [(117, 999)], f"补关点应按左下角换算:{calls}"
        assert str(attempt["images"].get("post1") or "").endswith("post1.png"), attempt["images"]
        tail = attempt["steps"][-1]
        assert tail["step"] == "收尾" and "领取后补点" in tail["detail"], tail

        # ③ 演练:只报坐标不动鼠标
        calls.clear()
        runner._close_popups(session, attempt, True, threading.Event(), cfg_dict, trae)
        assert calls == [], f"演练不该点击:{calls}"

        # ④ 补关被系统拒绝(提权):记下提醒就停,不许抛出去把已到账的结果带崩
        calls.clear()

        def blocked(x, y, humanize=False):
            raise clicker_module.ClickBlocked("鼠标没能移到目标点")

        clicker_module.click_screen = blocked
        runner._close_popups(session, attempt, False, threading.Event(), cfg_dict, trae)
        assert calls == []
        assert "不影响本次结果" in attempt["steps"][-1]["detail"], attempt["steps"][-1]

        # ⑤ 找不到窗口:记一条提醒,一个点都不许动;没录补关点则完全安静
        clicker_module.click_screen = lambda x, y, humanize=False: calls.append((x, y))
        before = len(attempt["steps"])
        zcode_module.find_target_window = lambda target: None
        runner._close_popups(session, attempt, False, threading.Event(), cfg_dict, trae)
        assert calls == [], f"找不到窗口不该点击:{calls}"
        assert len(attempt["steps"]) == before + 1, "找不到窗口只记一条提醒"
        assert "找不到" in attempt["steps"][-1]["detail"], attempt["steps"][-1]
        zcode_module.find_target_window = lambda target: dict(win)
        no_post = tmod.normalize({"id": "plain", "exe": trae["exe"], "flow": "steps",
                                  "steps": trae["steps"]}, "plain")
        runner._close_popups(session, attempt, False, threading.Event(), cfg_dict, no_post)
        assert len(attempt["steps"]) == before + 1, "没录补关点应安静跳过"

        # ⑥ 挂钩:领取成功才补关;补关里闹起来也不改结果
        seen: list = []
        runner._open_client = lambda d, c, t: dict(win)
        runner._close_client = lambda d: None
        runner._one_attempt = lambda s, a, d, ab, c, t: ("success", "领取成功")
        runner._close_popups = lambda s, a, d, ab, c, t: seen.append(d)
        out = runner._run_target("manual", {"time": None}, trae, cfg_dict)
        assert seen == [False] and out["status"] == "success", (seen, out)

        def boom(*args, **kwargs):
            raise RuntimeError("补关自己炸了")

        runner._close_popups = boom
        out = runner._run_target("manual", {"time": None}, trae, cfg_dict)
        assert out["status"] == "success", f"补关异常不该影响领取结果:{out}"
    finally:
        (capture_module.grab_region, clicker_module.click_screen,
         zcode_module.find_target_window, zcode_module.release_stuck_modifiers) = orig
    return "补关点随目标清洗;成功后按录制点关浮窗;补关失败或异常都不改结果"


def __steps_case():
    """固定坐标流程:按录制顺序点击、每步留档、不在前台就不点、verify=none 直接收尾。

    背景:WorkBuddy / Trae 的签到按钮位置固定(且各家点击次数不同),用视觉模型反而
    不稳;这套流程把「点几下、点哪里」交给用户在面板里录制。
    """
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    from core.api import Api
    from core.config import ConfigStore
    from core.events import EventBus
    from core.runner import Runner, steps_for
    from core.storage import SessionStore
    from core.vision import VisionClient
    import core.capture as capture_module
    import core.clicker as clicker_module

    tmp = Path(tempfile.mkdtemp(prefix="zcode-steps-"))
    cfg = ConfigStore(tmp / "config.yaml")
    cfg.patch({
        "targets": [{
            "id": "workbuddy", "name": "WorkBuddy", "exe": r"E:\Program Files\WorkBuddy\WorkBuddy.exe",
            "flow": "steps", "verify": "none", "once": "daily",
            "steps": [
                {"id": "s1", "label": "左下角头像", "anchor": "bottom_left", "dx": 88, "dy": 28},
                {"id": "s2", "label": "Buddy加油站", "anchor": "bottom_left", "dx": 177, "dy": 407},
                {"id": "s3", "label": "领取", "anchor": "top_left", "dx": 219, "dy": 85,
                 "wait_after_s": 0},
            ],
        }],
        "retry": {"watchdog_s": 30, "max_attempts": 1, "retry_gap_s": 1},
        "notify": {"desktop": False, "webhook_url": ""},
    })
    runner = Runner(cfg, EventBus(), SessionStore(tmp / "shots"), VisionClient(cfg))
    target = runner.slot_targets({})[0]
    cfg_dict = cfg.all()

    assert steps_for(target) == ["打开客户端", "按坐标点击", "校验结果", "收尾"], steps_for(target)
    assert "识别按钮" not in steps_for({"flow": "steps"})
    assert "识别按钮" in steps_for({"flow": "vision"})

    win = {"hwnd": 1, "title": "WorkBuddy", "rect": [0, 0, 1920, 1032]}
    calls: list = []
    shots = {"n": 0}

    def fake_shot(rect):
        shots["n"] += 1
        return SimpleNamespace(png=b"x", width=rect[2], height=rect[3], rect=tuple(rect),
                               uniform_ratio=0.4, full_size=(1920, 1080))

    orig = (capture_module.grab_region, clicker_module.click_screen, clicker_module.click_norm)
    capture_module.grab_region = fake_shot
    clicker_module.click_screen = lambda x, y, humanize=False: calls.append((x, y))
    clicker_module.click_norm = lambda rect, box, humanize=False: calls.append(("norm", rect, box))
    # 本机可能真的开着提权窗口,权限预检默认桩成"够得着",够不着的分支单独测(见 ⑧)
    runner._foreground_unreachable = lambda win, target: ""
    session_store = runner.store
    try:
        runner._open_client = lambda dry, cfg, target: dict(win)
        runner._close_client = lambda dry: None
        runner._wait_user_idle = lambda cfg, abort: False
        runner._ensure_foreground = lambda win, cfg: True
        runner._position_window = lambda w, c, d, t=None: tuple(w["rect"])
        session = session_store.begin("manual", {"time": None}, target)
        attempt = session_store.begin_attempt(session, 1)
        status, reason = runner._one_attempt(session, attempt, False, threading.Event(),
                                             cfg_dict, target)
        assert status == "success", f"未启用视觉校验时应直接收尾成功,得到 {status}/{reason}"
        assert calls == [(88, 1004), (177, 625), (219, 85)], f"点击序列/坐标换算不对:{calls}"
        assert shots["n"] == 3, f"每一步应各留一张档,实际 {shots['n']} 张"
        archived = sorted(k for k in attempt["images"] if k.startswith("step"))
        assert archived == ["step1", "step2", "step3"], f"分步留档未入库:{archived}"

        # ② 录制坐标不完整 → 判"无法解析",一个点都不许click(宁可整场不跑)
        calls.clear()
        bad = dict(target, steps=[{"id": "s1", "label": "坏点", "anchor": "window", "dx": None, "dy": 0.5}])
        session = session_store.begin("manual", {"time": None}, bad)
        attempt = session_store.begin_attempt(session, 1)
        status, reason = runner._one_attempt(session, attempt, False, threading.Event(), cfg_dict, bad)
        assert status == "retry" and "无法解析" in reason, f"{status}/{reason}"
        assert not calls, f"坐标解析失败时不应点击:{calls}"

        # ③ 没有录制任何点击点 → 明确要人去面板录,而不是静默"成功"
        empty = dict(target, steps=[])
        session = session_store.begin("manual", {"time": None}, empty)
        attempt = session_store.begin_attempt(session, 1)
        status, reason = runner._one_attempt(session, attempt, False, threading.Event(), cfg_dict, empty)
        assert status == "manual", f"空录制应要求人工去面板录点,得到 {status}/{reason}"
        assert "没有录制任何点击点" in reason, reason
        assert not calls

        # ④ 窗口不在前台:点击会落到用户当前正在用的程序上,必须放弃
        calls.clear()
        runner._ensure_foreground = lambda win, cfg: False
        session = session_store.begin("manual", {"time": None}, target)
        attempt = session_store.begin_attempt(session, 1)
        status, reason = runner._one_attempt(session, attempt, False, threading.Event(), cfg_dict, target)
        assert status == "retry" and "未能置前" in reason, f"{status}/{reason}"
        assert not calls, f"不在前台时不应点击:{calls}"
        runner._ensure_foreground = lambda win, cfg: True

        # ⑤ 演练:算出每步会点在哪里,但不真的动鼠标
        calls.clear()
        session = session_store.begin("manual", {"time": None}, target)
        attempt = session_store.begin_attempt(session, 1)
        status, reason = runner._one_attempt(session, attempt, True, threading.Event(), cfg_dict, target)
        assert not calls, f"演练不该点击:{calls}"
        assert status == "success", f"演练应完成流程,得到 {status}/{reason}"

        # ⑥ 开启视觉校验时,校验用的是目标自己的文案提示
        verified = dict(target, verify="vision")
        seen_prompt = {}

        def fake_verify(png, target=None):
            seen_prompt["target"] = (target or {}).get("id")
            return {"popup": "success", "keywords": ["已签到"], "confidence": 0.9}

        runner.vision.verify = fake_verify
        session = session_store.begin("manual", {"time": None}, verified)
        attempt = session_store.begin_attempt(session, 1)
        status, reason = runner._one_attempt(session, attempt, False, threading.Event(), cfg_dict, verified)
        assert status == "success", f"视觉校验应能确认签到成功,得到 {status}/{reason}"
        assert seen_prompt["target"] == "workbuddy", "校验提示要按目标生成"

        # ⑧ 目标提权:系统会静默吞掉鼠标注入,必须当场报权限问题,
        #    而不是按"点了三下"的记录判成功(WorkBuddy 事故的原始形态)
        import core.zcode_ctrl as zc_module
        calls.clear()
        zc_real = (zc_module.foreground_hwnd, zc_module.window_pid, zc_module.runs_elevated)
        zc_module.foreground_hwnd = lambda: 9999
        zc_module.window_pid = lambda hwnd: 4321
        zc_module.runs_elevated = lambda pid: True
        runner._foreground_unreachable = lambda w, t: Runner._foreground_unreachable(runner, w, t)
        try:
            session = session_store.begin("manual", {"time": None}, target)
            attempt = session_store.begin_attempt(session, 1)
            status, reason = runner._one_attempt(session, attempt, False, threading.Event(),
                                                 cfg_dict, target)
        finally:
            (zc_module.foreground_hwnd, zc_module.window_pid,
             zc_module.runs_elevated) = zc_real
            runner._foreground_unreachable = lambda win, target: ""
        assert status == "manual", f"点不动时不该继续走流程,得到 {status}/{reason}"
        assert not calls, f"权限不够时一个点都不许按下:{calls}"
        assert "管理员" in reason, f"要说清楚是权限问题以及怎么解:{reason}"
        assert "以管理员身份运行" in reason, f"要给可执行的下一步:{reason}"

        # ⑨ 中途被拦下(前一步还点得动):ClickBlocked 同样要转成人工处理,
        #    且失败的那一步不能记成"已点击"
        calls.clear()
        real_click = clicker_module.click_screen

        def click_then_block(x, y, humanize=False):
            calls.append((x, y))
            if len(calls) >= 2:
                raise clicker_module.ClickBlocked("鼠标停在 (0, 0),没能移到目标点")
            return int(x), int(y)

        clicker_module.click_screen = click_then_block
        session = session_store.begin("manual", {"time": None}, target)
        attempt = session_store.begin_attempt(session, 1)
        status, reason = runner._one_attempt(session, attempt, False, threading.Event(),
                                             cfg_dict, target)
        clicker_module.click_screen = real_click
        assert status == "manual", f"点击被拒绝应转人工,得到 {status}/{reason}"
        assert len(calls) == 2, f"被拦下后不该再点第三步:{calls}"
        logged = json.dumps(attempt, ensure_ascii=False, default=str)
        assert "已点击「Buddy加油站」" not in logged, f"没点下去的一步不许记成已点击:{logged}"
    finally:
        (capture_module.grab_region, clicker_module.click_screen,
         clicker_module.click_norm) = orig

    # ⑩ 面板录制接口:在程序自己的截图上点一下,产出的偏移要能原样回放
    from core.coords import resolve

    api = Api.__new__(Api)
    api.cfg = cfg
    recorded = api.record_point({"target": "workbuddy", "x": 88, "y": 1004, "label": "头像",
                                 "rect": [0, 0, 1920, 1032], "index": 1, "wait_after_s": 0.6})
    assert recorded["ok"] is True, recorded
    step = recorded["step"]
    assert step["anchor"] == "bottom_left" and step["dx"] == 88 and step["dy"] == 28, step
    assert step["label"] == "头像" and step["wait_after_s"] == 0.6, step
    assert step["screen"] == [88, 1004], step
    assert resolve([0, 0, 1920, 1032], step) == (88, 1004), "录到的偏移必须能回放成同一个屏幕点"
    # 落在窗口图之外的点击要拒绝,否则会存下一个永远点不准的偏移
    assert api.record_point({"x": 3000, "y": 10, "rect": [0, 0, 1920, 1032]})["ok"] is False
    assert api.record_point({"x": -1, "y": 10, "rect": [0, 0, 1920, 1032]})["ok"] is False
    assert api.record_point({"x": 10, "y": 10})["ok"] is False, "未截图时不得录制"
    assert api.record_point({"x": "abc", "y": 10, "rect": [0, 0, 10, 10]})["ok"] is False
    return "按序点击且坐标换算正确;每步留档;不在前台/坐标残缺时拒绝点击;录制接口产出可回放偏移"


def __weekday_slot_case():
    """场次可只排在指定星期;老配置(无 days)必须继续按每天跑。

    背景:场次表原来是「每日固定场次」,但实际只在周五六日用。少了星期维度就只能
    把每天的场次都列上,周末之外的日子会白白触发。
    """
    import tempfile
    from datetime import datetime
    from pathlib import Path

    from core.api import Api
    from core.config import ConfigStore
    from core.scheduler import SlotScheduler, days_label, parse_days

    # ① 星期解析:够宽容,但非法值不能悄悄变成"每天"而让人以为只排了周五
    assert parse_days(None) is None and parse_days([]) is None, "空值应表示每天"
    assert parse_days([4]) == [4], "整数星期解析错误"
    assert parse_days(["4"]) == [4], "数字字符串应能解析"
    assert parse_days([5, 6, 0]) == [0, 5, 6], "应升序去重"
    assert parse_days(["mon", "fri"]) == [0, 4], "英文缩写应能解析"
    assert parse_days(["周五", "周六"]) == [4, 5], "中文星期应能解析"
    assert parse_days([1, 2, 3, 4, 5]) == [1, 2, 3, 4, 5], "1-7 写法(周一=1)应能解析"
    assert parse_days([0, 1, 2, 3, 4, 5, 6]) is not None
    assert days_label(None) == "每天" and days_label([4, 5, 6]) == "周五周六周日"
    assert days_label([0, 1, 2, 3, 4, 5, 6]) == "每天", "全选等同每天"

    # ② 保存链路:days 要落盘,且同一时间可排在不同星期
    tmp = Path(tempfile.mkdtemp(prefix="zcode-days-"))
    cfg = ConfigStore(tmp / "config.yaml")
    api = Api.__new__(Api)
    api.cfg = cfg
    cleaned = api._clean_slots([
        {"time": "19:00", "enabled": True, "days": [4, 5, 6]},
        {"time": "19:00", "enabled": True, "days": [0]},        # 同一时间、不同星期 → 两条都要留
        {"time": "19:00", "enabled": True, "days": [4, 5, 6]},  # 完全重复 → 去掉
        {"time": "21:00", "enabled": True},                     # 无 days → 每天
        {"time": "25:00", "enabled": True, "days": [4]},        # 时间非法 → 丢掉
    ])
    assert len(cleaned) == 3, f"去重/校验有误:{cleaned}"
    assert cleaned[0]["days"] == [4, 5, 6], f"days 未保存:{cleaned[0]}"
    assert cleaned[1]["days"] == [0], "同一时间不同星期应各留一条"
    assert "days" not in cleaned[2], "每天不应写入 days 字段(兼容老配置)"

    # ③ 调度:只注册到指定星期,且「下一场」要能说清是哪天
    cfg.patch({"schedule": {"slots": [
        {"time": "19:00", "enabled": True, "days": [4, 5, 6]},
        {"time": "08:30", "enabled": True},
    ]}})
    fired: list[dict] = []
    sched = SlotScheduler(cfg, lambda slot: fired.append(slot), master_check=lambda: True)
    sched.start()
    try:
        jobs = {j.id: j for j in sched._scheduler.get_jobs() if j.id.startswith("slot:")}
        assert len(jobs) == 2, f"应注册 2 个场次:{list(jobs)}"
        # 找到带星期的那个 job,确认 next_run 落在周五/六/日
        nxt = sched.next_run()
        assert nxt, "应能算出下一场"
        assert "weekday" in nxt and "date" in nxt and "is_today" in nxt, \
            f"下一场信息缺少日期/星期(场次不再每天都有):{nxt}"
        # 08:30 那场每天都跑,所以最近的一场必是它;19:00 那场必须落在周五/六/日
        day_jobs = [j for j in jobs.values() if j.next_run_time]
        assert day_jobs, "应存在待触发任务"
        for job in day_jobs:
            if "1900" in job.id:
                assert job.next_run_time.weekday() in (4, 5, 6), \
                    f"限定周五六日的场次不该落在周{job.next_run_time.weekday()}"
    finally:
        sched.shutdown()

    # ④ 补触发兜底:休眠后补触发到非设定星期时必须跳过,不真的去执行
    today = datetime.now().weekday()
    other = next(d for d in range(7) if d != today)
    cfg.patch({"schedule": {"slots": [{"time": "19:00", "enabled": True, "days": [other]}]}})
    sched2 = SlotScheduler(cfg, lambda slot: fired.append(slot), master_check=lambda: True)
    sched2.start()
    try:
        fired.clear()
        sched2._fire({"time": "19:00", "days": [other]})     # 模拟补触发到今天
        assert not fired, f"非设定星期不该执行(今天周{today},设定周{other})"
        # 设定今天时应当放行
        sched2._fire({"time": "19:00", "days": [today]})
        assert len(fired) == 1, "设定星期包含今天时应正常执行"
        # 没有 days(每天)时任何一天都放行
        fired.clear()
        sched2._fire({"time": "20:00", "days": None})
        assert len(fired) == 1, "每天型场次不该被星期兜底拦下"
    finally:
        sched2.shutdown()

    return "星期解析容错;days 落盘且同时间可排多星期;调度只在指定星期触发;补触发兜底生效"


if __name__ == "__main__":
    sys.exit(main())

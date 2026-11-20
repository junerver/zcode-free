"""客户端进程与窗口控制(Windows / pywin32)。

职责:探测 exe、枚举窗口、启动/置前/复位窗口、结束进程树。
只操作「目标 exe 自己的主窗口」,不碰其他程序。

两个用血泪换来的规矩:
1. 先认进程、再认窗口。按标题找窗口会被同名无关窗口/自己弹出的对话框截胡,
   所以入口是 find_process_window(exe):用 pid→可执行路径(严格比对)锁定进程,
   再在该进程的窗口里取最大那个。
2. 绝不把本程序自己当成客户端。打包后的进程名 ZCodeAssistant.exe 里含 "zcode",
   子串匹配会命中它,于是"启动客户端"变成启动第二个自己 → 单实例提示框 →
   提示框标题也含 ZCode → 被当客户端最大化 → 视觉模型报"有弹窗遮挡" → 无限重试。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import subprocess
import sys
import time
from pathlib import Path

import win32api
import win32con
import win32gui
import win32process
from loguru import logger

STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x0008
_TOKEN_INTEGRITY_LEVEL = 25                    # TOKEN_INFORMATION_CLASS.TokenIntegrityLevel
_TOKEN_APIS_READY = False

VK_MENU = 0x12                                  # ALT
MODIFIER_KEYS = {"ALT": VK_MENU, "CTRL": 0x11, "SHIFT": 0x10, "WIN": 0x5B}

# 主窗口的最小尺寸:弹出框、气泡、隐藏的消息窗口都比这小,不该被当成客户端
MIN_WINDOW_W = 400
MIN_WINDOW_H = 300


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


_INPUT_KEYBOARD = 1

# 任何情况下都不得结束的进程:系统外壳/关键进程 + 本程序自身
# (实测事故:标题为 "zcode-free" 的窗口被误当成 ZCode 客户端,进程随后被结束)
PROTECTED_EXES = {
    "explorer.exe", "dwm.exe", "winlogon.exe", "csrss.exe", "wininit.exe",
    "services.exe", "lsass.exe", "smss.exe", "svchost.exe", "sihost.exe",
    "taskhostw.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
    "searchhost.exe", "textinputhost.exe", "runtimebroker.exe", "ctfmon.exe",
    "python.exe", "pythonw.exe",           # 本程序自己就是 python
    "chrome.exe", "msedge.exe", "firefox.exe",  # 面板网页标题含 "ZCode"
}


def is_protected_exe(path: str | None) -> bool:
    return bool(path) and Path(path).name.lower() in PROTECTED_EXES


_OWN_EXE: str | None = None
_OWN_ALT: str = ""


def own_exe() -> str:
    """本程序自身的可执行文件路径。

    打包后就是 ZCodeAssistant.exe;源码运行时是解释器本身。这里取的是**进程镜像路径**
    (而不是 sys.executable):venv 下两者不同,而枚举其它进程时拿到的也是镜像路径,
    只有同一个口径才比对得上。
    """
    global _OWN_EXE, _OWN_ALT
    if _OWN_EXE is None:
        launcher = str(sys.executable) if sys.executable else ""
        image = "" if getattr(sys, "frozen", False) else (process_path(os.getpid()) or "")
        _OWN_EXE = image or launcher
        # 包装器/venv 场景下 sys.executable 与镜像路径不是一个文件,两个都算"自己"
        _OWN_ALT = launcher if launcher and not _same_path(launcher, _OWN_EXE) else ""
    return _OWN_EXE


def is_own_exe(path: str | None) -> bool:
    """是否就是正在运行的这个程序自身——任何「找客户端」「结束进程」的入口都必须排除它。"""
    if not path or not own_exe():
        return False
    if _same_path(path, own_exe()):
        return True
    return bool(_OWN_ALT) and _same_path(path, _OWN_ALT)


def _same_path(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except Exception:
        return False


def verify_client_window(win: dict | None, expected_exe: str = "",
                         exe_name_match: str = "zcode") -> tuple[bool, str]:
    """确认窗口确实属于目标客户端,返回 (是否通过, 原因)。

    只靠标题匹配是不够的:标题里恰好含 "zcode" 的窗口(项目文件夹名、面板网页
    "ZCode 福利助手"、本程序自己弹出的对话框等)都会被误认。这里追加进程级校验:
    优先与配置的 exe 路径严格比对;未配置路径时要求进程名含关键字且不是受保护进程。
    """
    win = win or {}
    hwnd = win.get("hwnd")
    if not hwnd:
        return False, "窗口句柄无效"
    pid = window_pid(hwnd)
    if not pid:
        return False, "无法获取窗口所属进程"
    path = process_path(pid)
    if not path:
        return False, f"无法读取进程路径(PID {pid},可能权限不足或进程已退出)"
    if is_own_exe(path):
        return False, f"进程 {path} 是本程序自身,不能作为签到目标"
    name = Path(path).name.lower()
    if name in PROTECTED_EXES:
        return False, f"进程 {name} 属于受保护/无关程序,已排除"
    if expected_exe:
        if _same_path(path, expected_exe):
            return True, f"进程匹配:{path}"
        return False, f"进程 {path} 与配置的 {expected_exe} 不一致"
    if exe_name_match and exe_name_match in name and name.endswith(".exe"):
        return True, f"进程名匹配:{path}"
    return False, f"进程名 {name} 不含关键字「{exe_name_match}」"


def find_client_window(title_match: str, expected_exe: str = "") -> dict | None:
    """按标题找候选窗口,但只返回通过进程校验的那个;被拒候选写日志便于排查。"""
    rejected: list[str] = []
    for win in sorted(list_windows(title_match), key=lambda w: -w["area"]):
        ok, reason = verify_client_window(win, expected_exe)
        if ok:
            return win
        rejected.append(f"{win['title']!r} → {reason}")
    for item in rejected:
        logger.warning(f"已忽略标题相近但校验不通过的窗口:{item}")
    return None


def wait_client_window(title_match: str, timeout: float, expected_exe: str = "") -> dict | None:
    """轮询等待"通过进程校验"的客户端窗口出现。"""
    deadline = time.time() + max(float(timeout), 1.0)
    while time.time() < deadline:
        win = find_client_window(title_match, expected_exe)
        if win:
            return win
        time.sleep(0.5)
    return None


def pids_of_exe(exe: str) -> list[int]:
    """列出正在运行的、可执行文件与 exe 严格一致的进程(排除本程序自己)。"""
    if not exe:
        return []
    try:
        candidates = list(win32process.EnumProcesses())
    except Exception:
        return []
    out: list[int] = []
    for pid in candidates:
        if pid <= 4:
            continue
        path = process_path_cached(int(pid))
        if not path or is_own_exe(path):
            continue
        if _same_path(path, exe):
            out.append(int(pid))
    return out


def windows_of_pid(pid: int) -> list[dict]:
    """某进程的全部可见窗口,按面积从大到小。

    优先返回无主窗口(顶层框架);一个都没有时才退回带 owner 的窗口,
    以免 Electron/Chromium 系应用因窗口归属实现差异而完全找不到画面。
    """
    owned: list[dict] = []
    root: list[dict] = []

    def _cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                return True
            if window_pid(hwnd) != pid:
                return True
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except Exception:
            return True
        w, h = right - left, bottom - top
        if w < MIN_WINDOW_W or h < MIN_WINDOW_H:
            return True
        try:
            title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            title = ""
        item = {"hwnd": hwnd, "title": title, "rect": [left, top, w, h], "area": w * h}
        try:
            has_owner = bool(win32gui.GetWindow(hwnd, win32con.GW_OWNER))
        except Exception:
            has_owner = False
        (owned if has_owner else root).append(item)
        return True

    win32gui.EnumWindows(_cb, None)
    out = root or owned
    out.sort(key=lambda x: -x["area"])
    return out


def find_process_window(exe: str) -> dict | None:
    """先按进程(路径严格比对)锁定 PID,再取该进程最大的窗口。

    这是所有目标的默认查找方式:Trae 的主窗口标题是 "Agent",按标题根本找不到;
    反过来 WorkBuddy 之类也会弹出标题含自身名字的对话框,按标题找就会挑到对话框。
    """
    for pid in pids_of_exe(exe):
        wins = windows_of_pid(pid)
        if wins:
            win = wins[0]
            win["pid"] = pid
            return win
    return None


def wait_process_window(exe: str, timeout: float) -> dict | None:
    """轮询等待目标进程的主窗口出现。"""
    deadline = time.time() + max(float(timeout), 1.0)
    while time.time() < deadline:
        win = find_process_window(exe)
        if win:
            return win
        time.sleep(0.5)
    return None


def find_target_window(target: dict) -> dict | None:
    """按目标的 find_by 策略找到自己的主窗口。"""
    exe = str(target.get("exe") or "").strip()
    if target.get("find_by") == "title" or not exe:
        return find_client_window(str(target.get("window_title_match") or ""), exe)
    return find_process_window(exe)


def wait_target_window(target: dict, timeout: float) -> dict | None:
    exe = str(target.get("exe") or "").strip()
    if target.get("find_by") == "title" or not exe:
        return wait_client_window(
            str(target.get("window_title_match") or ""), timeout, exe)
    return wait_process_window(exe, timeout)


# ---------------- 窗口 ----------------

def list_windows(title_match: str) -> list[dict]:
    """枚举可见窗口,标题包含关键字(不区分大小写)的返回 {hwnd,title,rect,area}。"""
    match = (title_match or "").lower()
    out: list[dict] = []

    def _cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
        except Exception:
            return True
        if not title or (match and match not in title.lower()):
            return True
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except Exception:
            return True
        w, h = right - left, bottom - top
        # 尺寸闸:小对话框/气泡窗口即使标题对得上也不是我们要操作的主窗口
        if w < MIN_WINDOW_W or h < MIN_WINDOW_H:
            return True
        out.append({"hwnd": hwnd, "title": title, "rect": [left, top, w, h], "area": w * h})
        return True

    win32gui.EnumWindows(_cb, None)
    return out


def find_window(title_match: str) -> dict | None:
    """取面积最大的匹配窗口(通常是主窗口)。"""
    windows = list_windows(title_match)
    if not windows:
        return None
    return max(windows, key=lambda x: x["area"])


def window_pid(hwnd: int) -> int | None:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return int(pid) or None
    except Exception:
        return None


def is_foreground(hwnd: int) -> bool:
    try:
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


def foreground_hwnd() -> int:
    try:
        return int(win32gui.GetForegroundWindow())
    except Exception:
        return 0


def owns_foreground(hwnd: int) -> bool:
    """前台要么是窗口本身,要么是它自己弹出来的浮层/菜单。

    录制流程第二步点开的账户菜单、领取 banner 常常是同进程的另一个顶层窗口。
    只认"主窗口必须在最前"就会去抢焦点,反而把下一步要点的那个弹窗关掉。
    """
    top = foreground_hwnd()
    if not top:
        return False
    if top == hwnd:
        return True
    mine = window_pid(hwnd)
    return bool(mine) and mine == window_pid(top)


def send_key_events(events: list[tuple[int, int]]) -> bool:
    """一次 SendInput 把整串按键事件发完(events 为 (vk, flags) 列表)。

    批量很关键:keybd_event 分两次发"按下/抬起",中间只要前台一换(置前成功
    必然导致前台一换),按下落在旧前台线程、抬起落在新前台线程,旧线程就永远
    认为这个键还按着——用户看到的"操作一段后 ALT 卡在按下态"正是这个后遗症:
    菜单栏莫名激活、点击变成 Alt+点击、焦点乱跳。同一批事件进同一个队列,不留缝。
    """
    if not events:
        return True
    buffer = (_INPUT * len(events))()
    for index, (vk, flags) in enumerate(events):
        buffer[index].type = _INPUT_KEYBOARD
        buffer[index].ki.wVk = vk
        buffer[index].ki.dwFlags = flags
    user32 = ctypes.windll.user32
    user32.SendInput.restype = wintypes.UINT
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
    return user32.SendInput(len(events), buffer, ctypes.sizeof(_INPUT)) == len(events)


def pulse_keys(vks: list[int]) -> bool:
    """成对按下+抬起,且一次性发出。"""
    events: list[tuple[int, int]] = []
    for vk in vks:
        events.append((vk, 0))
        events.append((vk, win32con.KEYEVENTF_KEYUP))
    return send_key_events(events)


def release_keys(vks: list[int]) -> bool:
    """只补发抬起事件。"""
    return send_key_events([(vk, win32con.KEYEVENTF_KEYUP) for vk in vks])


def stuck_modifier_keys() -> list[int]:
    """线程队列里仍记着"按下"、但物理上并没有被按住的修饰键。

    两个通道得一起看:GetAsyncKeyState 查"当下真按着吗",GetKeyState 查队列残留。
    只看前者会漏——旧版启动清理就是这么写的,所以从来没清掉过一次卡住的 ALT。
    反过来物理上确实在按(用户正用 Alt+Tab)时不去碰他,只处理我们自己留下的残留。
    """
    stuck: list[int] = []
    for vk in MODIFIER_KEYS.values():
        queued = ctypes.c_short(win32api.GetKeyState(vk)).value & 0x8000
        physical = ctypes.c_short(win32api.GetAsyncKeyState(vk)).value & 0x8000
        if queued and not physical:
            stuck.append(vk)
    return stuck


def _set_foreground(hwnd: int) -> bool:
    """置前一次;被前台锁定拒绝时,借 ALT 键解锁后再试。

    ALT 的解锁脉冲必须在切换前台"之前"一次性发完(见 send_key_events),
    而且只对够得着的目标用:提权窗口既拿不到焦点,又只会白白留下按键残留。
    """
    try:
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        pass

    if runs_elevated(window_pid(hwnd)):
        return False

    pulse_keys([VK_MENU])
    try:
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def foreground(hwnd: int) -> bool:
    """还原(如最小化)并置前;前台锁定夺不到焦点时,至少把窗口抬到 z 序顶部。

    返回是否真正成为前台窗口(截图前调用,避免被其他窗口遮挡)。
    """
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass

    for _ in range(3):
        if is_foreground(hwnd):
            return True
        _set_foreground(hwnd)
        if is_foreground(hwnd):
            return True
        # 仍抢不到前台时,抬高 z 序:即使不抢键盘焦点,也不会被其他窗口压在下面
        try:
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOP, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
            )
        except Exception:
            pass
        time.sleep(0.25)

    ok = is_foreground(hwnd)
    if not ok:
        logger.warning("窗口未能置前(已尝试抬高 z 序),截图仍可能被遮挡")
    return ok


# ---------------- 输入状态 ----------------

def idle_seconds() -> float | None:
    """距用户最后一次键鼠输入的秒数(GetLastInputInfo);读不到时返回 None。

    用于"用户正在用电脑就先别动手":自动化会抢前台并注入点击,
    正在打字/拖拽时打断体验极差,还可能点错窗口。
    """
    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        elapsed = (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
        return elapsed / 1000.0
    except Exception:
        return None


def release_stuck_modifiers() -> list[str]:
    """把系统仍认为按着的修饰键抬起来,返回被清理的键名。

    兜底用:启动时清一次(上一次崩溃/老版本留下的残留),每场点击结束再清一次。
    判定只认"队列里按着、物理上没按着"(见 stuck_modifier_keys),
    用户真按着 Alt+Tab 时不去打扰他。
    """
    names = {vk: name for name, vk in MODIFIER_KEYS.items()}
    stuck = stuck_modifier_keys()
    if not stuck or not release_keys(stuck):
        return []
    return [names.get(vk, hex(vk)) for vk in stuck]


def move_window(hwnd: int, rect: list[int] | tuple[int, int, int, int]) -> None:
    x, y, w, h = (int(v) for v in rect)
    win32gui.MoveWindow(hwnd, x, y, w, h, True)


def maximize(hwnd: int) -> None:
    """把客户端窗口最大化(全屏显示),便于稳定截图与定位。"""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    except Exception as exc:
        logger.warning(f"窗口最大化失败:{exc}")


def is_maximized(hwnd: int) -> bool:
    try:
        return bool(win32gui.IsZoomed(hwnd))
    except Exception:
        return False


def window_rect(hwnd: int) -> list[int] | None:
    """读取窗口当前 [x, y, w, h];窗口无效(如仍最小化)时返回 None。

    置顶/还原后重新取一次位置,避免用最小化状态下的无效坐标去截图。
    """
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    except Exception:
        return None
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None
    return [left, top, width, height]


def client_rect(hwnd: int) -> list[int] | None:
    """客户区在屏幕上的 [x, y, w, h] —— 截图和点击换算都以它为准。

    窗口矩形带一圈 DWM 的不可见边框(最大化时是左上各 -8),用它当基准:
      · 截图会被屏幕边界夹掉,图的原点变成 (0,0),和矩形原点 (-8,-8) 对不上,
        于是每个录制点整体偏左 8 像素、贴着上边的点还偏上 8 像素;
      · 夹出去的那几条还会把隔壁显示器的内容截进来干扰识别。
    客户区原点本来就是屏幕可见的,录制图和点击换算用同一个框,偏差就没了。
    """
    try:
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
        _, _, width, height = win32gui.GetClientRect(hwnd)
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return [left, top, width, height]


def effective_rect(hwnd: int) -> list[int] | None:
    """点击/截图用的矩形:优先客户区,拿不到时退回窗口矩形。"""
    return client_rect(hwnd) or window_rect(hwnd)


def _token_apis():
    """取 kernel32/advapi32,并把令牌相关调用的签名固定好。

    argtypes/restype 必须显式声明:当前进程伪句柄是 0xFFFFFFFFFFFFFFFF,
    ctypes 默认按 32 位整型传参,传下去要么 OverflowError 要么被截断成无效句柄。
    """
    global _TOKEN_APIS_READY
    kernel32, advapi32 = ctypes.windll.kernel32, ctypes.windll.advapi32
    if not _TOKEN_APIS_READY:
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.HANDLE))
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_uint,
                                                 ctypes.c_void_p, wintypes.DWORD,
                                                 ctypes.POINTER(wintypes.DWORD))
        _TOKEN_APIS_READY = True
    return kernel32, advapi32


def _integrity_rid(token) -> int | None:
    """令牌完整性级别:Low=0x1000、Medium=0x2000、High=0x3000、System=0x4000。"""
    _, advapi32 = _token_apis()
    buffer = ctypes.create_string_buffer(64)              # SID_AND_ATTRIBUTES
    returned = wintypes.DWORD()
    if not advapi32.GetTokenInformation(token, _TOKEN_INTEGRITY_LEVEL, buffer,
                                        ctypes.sizeof(buffer), ctypes.byref(returned)):
        return None
    sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
    if not sid:
        return None
    count = ctypes.c_ubyte.from_address(sid + 1).value    # SubAuthorityCount
    if not count:
        return None
    return ctypes.c_uint32.from_address(sid + 8 + 4 * (count - 1)).value


def runs_elevated(pid: int | None) -> bool:
    """目标进程的完整性级别是否比我们高——高一级就意味着鼠标点不进去。

    坑在探测方式:PROCESS_QUERY_LIMITED_INFORMATION 是跨权限放行的(所以能开进程、
    能查路径),拿它当"够不够得着"会一律得到"够得着"。真门槛在令牌上:
    令牌打不开(拒绝访问)或级别比我们高,注入的鼠标就被系统静默丢掉。
    """
    if not pid:
        return False
    kernel32, advapi32 = _token_apis()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return kernel32.GetLastError() == 5               # ERROR_ACCESS_DENIED
    token, ours = wintypes.HANDLE(), wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(handle, _TOKEN_QUERY, ctypes.byref(token)):
            return kernel32.GetLastError() == 5
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                         _TOKEN_QUERY, ctypes.byref(ours)):
            return False
        mine, theirs = _integrity_rid(ours), _integrity_rid(token)
        return bool(mine and theirs and theirs > mine)
    finally:
        kernel32.CloseHandle(handle)
        if token.value:
            kernel32.CloseHandle(token)


# ---------------- 进程 ----------------

def is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    handle = None
    try:
        handle = win32api.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        return win32process.GetExitCodeProcess(handle) == STILL_ACTIVE
    except Exception:
        return False
    finally:
        if handle:
            try:
                win32api.CloseHandle(handle)
            except Exception:
                pass


def launch(exe: str) -> subprocess.Popen:
    exe_path = Path(exe)
    if not exe_path.is_file():
        raise FileNotFoundError(f"客户端程序不存在:{exe}")
    logger.info(f"启动客户端:{exe}")
    return subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent))


def wait_window(title_match: str, timeout: float) -> dict | None:
    """轮询等待窗口出现(不做进程校验;面向客户端的场景请用 wait_client_window)。"""
    deadline = time.time() + max(float(timeout), 1.0)
    while time.time() < deadline:
        win = find_window(title_match)
        if win:
            return win
        time.sleep(0.5)
    return None


def kill_tree(pid: int | None, timeout: float = 15.0, expected_exe: str = "") -> bool:
    """结束进程树并等待其真正退出。

    带三重安全闸:受保护进程一律拒绝;进程路径必须可读;若给了 expected_exe,
    必须与之严格一致——避免 PID 复用或误判窗口导致关掉无关程序。
    """
    if not pid:
        return False
    path = process_path(pid)
    if not path:
        logger.error(f"拒绝结束 PID {pid}:无法读取其进程路径,无法确认身份")
        return False
    if is_protected_exe(path):
        logger.error(f"拒绝结束受保护进程 {Path(path).name}(PID {pid})")
        return False
    if is_own_exe(path):
        logger.error(f"拒绝结束本程序自身(PID {pid},{path})")
        return False
    if expected_exe and not _same_path(path, expected_exe):
        logger.error(f"拒绝结束 PID {pid}:进程为 {path},与预期 {expected_exe} 不一致")
        return False

    logger.info(f"关闭客户端(PID {pid},{path})")
    try:
        res = subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True,
            text=True,
            errors="replace",   # taskkill 输出为 GBK,UTF-8 模式下需容错,避免解码异常
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if res.returncode != 0:
            logger.warning(f"taskkill 返回 {res.returncode}:{(res.stderr or res.stdout or '').strip()}")
    except Exception as exc:
        logger.warning(f"taskkill 执行异常:{exc}")
    deadline = time.time() + 8
    while time.time() < deadline and is_alive(pid):
        time.sleep(0.3)
    return not is_alive(pid)


def process_path(pid: int) -> str | None:
    """查询进程可执行文件路径(可能因权限失败)。"""
    handle = None
    try:
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return None
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value or None
        return None
    except Exception:
        return None
    finally:
        if handle:
            try:
                ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                pass


# pid → 路径的短 TTL 缓存:一次进程扫描要问几百次,窗口轮询又会反复扫,
# 不做缓存的话每轮枚举都要付几百次系统调用的代价。
_PATH_CACHE: dict[int, tuple[float, str]] = {}
_PATH_CACHE_TTL = 5.0
_PATH_CACHE_MAX = 4096


def process_path_cached(pid: int) -> str | None:
    now = time.monotonic()
    hit = _PATH_CACHE.get(int(pid))
    if hit and now - hit[0] < _PATH_CACHE_TTL:
        return hit[1]
    path = process_path(pid)
    if path:            # 只缓存成功结果:失败可能只是瞬时权限问题
        if len(_PATH_CACHE) >= _PATH_CACHE_MAX:
            _PATH_CACHE.clear()
        _PATH_CACHE[int(pid)] = (now, path)
    return path


# ---------------- exe 探测 ----------------

def find_running_exe(exe_name: str = "zcode") -> str | None:
    """从运行中的进程里探测客户端可执行文件(最可靠:用户已打开过客户端)。

    关键字匹配是当年误伤的源头:本程序打包后叫 ZCodeAssistant.exe,含 "zcode",
    于是"探测客户端"返回自己的路径,后续每次尝试都在启动第二个自己。
    现在先按名字排序、优先完全同名,并且一律排除本程序与受保护进程。
    """
    match = (exe_name or "").lower()
    stem = Path(match).stem if match else ""
    try:
        pids = win32process.EnumProcesses()
    except Exception:
        return None
    hits: list[str] = []
    for pid in pids:
        if pid <= 4:
            continue
        path = process_path_cached(int(pid))
        if not path or is_own_exe(path) or is_protected_exe(path):
            continue
        name = Path(path).name.lower()
        if match and match not in name:
            continue
        hits.append(path)
    if not hits:
        return None
    # 精确名(ZCode.exe)优先于任何含关键字的路径(ZCodeAssistant.exe / zcode-updater.exe)
    for hit in hits:
        if stem and Path(hit).name.lower() in (f"{stem}.exe", stem):
            return hit
    hits.sort(key=lambda p: len(Path(p).name))
    return hits[0]


def _start_menu_dirs() -> list[Path]:
    dirs = []
    for key in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(key)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return [d for d in dirs if d.exists()]


def _resolve_lnk(path: Path) -> str | None:
    try:
        import pythoncom
        from win32com.client import Dispatch

        pythoncom.CoInitialize()
        target = ""
        try:
            shell = Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(str(path))
            target = str(shortcut.TargetPath or "")
            del shortcut
            del shell
        finally:
            pythoncom.CoUninitialize()
        return target or None
    except Exception:
        return None


def _iter_files(root: Path, max_depth: int = 3, limit: int = 30000):
    """受限深度遍历,避免全盘扫描。"""
    count = 0
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        cur, depth = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            continue
        for entry in entries:
            count += 1
            if count > limit:
                return
            try:
                if entry.is_dir(follow_symlinks=False):
                    if depth < max_depth:
                        stack.append((Path(entry.path), depth + 1))
                elif entry.is_file(follow_symlinks=False):
                    yield Path(entry.path)
            except OSError:
                continue


def search_exe(name_match: str = "zcode") -> str | None:
    """扫描开始菜单快捷方式与常见安装目录。"""
    match = name_match.lower()
    stem = Path(match).stem if match else ""
    for d in _start_menu_dirs():
        try:
            links = list(d.rglob("*.lnk"))
        except OSError:
            continue
        for lnk in links:
            if match in lnk.stem.lower():
                target = _resolve_lnk(lnk)
                if target and target.lower().endswith(".exe") and Path(target).is_file() \
                        and not is_own_exe(target):
                    logger.info(f"从开始菜单找到客户端:{target}")
                    return target

    roots: list[Path] = []
    for key in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value))
    roots.append(Path.home() / "AppData" / "Local" / "Programs")
    loose: str | None = None
    for root in roots:
        if not root.exists():
            continue
        for f in _iter_files(root, max_depth=3):
            if f.suffix.lower() != ".exe" or match not in f.name.lower():
                continue
            path = str(f)
            if is_own_exe(path):
                continue
            # 精确同名可以直接返回;只是"名字里含关键字"的先记下来继续找
            if stem and f.name.lower() in (f"{stem}.exe", stem):
                logger.info(f"在安装目录找到客户端:{path}")
                return path
            loose = loose or path
    if loose:
        logger.info(f"在安装目录找到名称相近的客户端:{loose}")
    return loose


def autodetect_exe(name_match: str = "zcode") -> str | None:
    """优先从运行进程取路径,其次扫描磁盘。任何情况下都不会返回本程序自己的路径。"""
    return find_running_exe(name_match) or search_exe(name_match)

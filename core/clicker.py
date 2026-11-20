"""鼠标点击:把坐标换算成屏幕物理像素后点击。

pyautogui.FAILSAFE = True:把鼠标甩到屏幕左上角可紧急中止。

pyautogui 不检查 SetCursorPos 的返回值,光标没动它也当成功;而低权限进程给
提权窗口注入鼠标时,系统(UIPI)正是这么处理的——静默丢弃、不留错误码。
所以这里每次点击都读回光标位置确认真的落到了,落不到就抛 ClickBlocked。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

import pyautogui
from loguru import logger

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

_SM_XVIRT, _SM_YVIRT, _SM_CXVIRT, _SM_CYVIRT = 76, 77, 78, 79
# 允许的微调误差:humanize 的移动是按步走的,末点可能差一两像素
_LAND_TOLERANCE = 4


class ClickBlocked(RuntimeError):
    """鼠标没能落到目标点:多半是目标客户端以管理员身份运行,助手权限不够。"""


def _user32():
    return ctypes.windll.user32


def virtual_screen() -> tuple[int, int, int, int]:
    """整个桌面的 (x, y, w, h),含所有显示器。

    pyautogui.size() 只报主屏,直接把第二块屏上的坐标夹回主屏,
    所以边界要按虚拟屏幕算,否则多显示器用户点不到副屏上的客户端。
    """
    u = _user32()
    x, y = u.GetSystemMetrics(_SM_XVIRT), u.GetSystemMetrics(_SM_YVIRT)
    w, h = u.GetSystemMetrics(_SM_CXVIRT), u.GetSystemMetrics(_SM_CYVIRT)
    if w <= 0 or h <= 0:  # 取不到(极少数远程桌面)时退回主屏
        return 0, 0, pyautogui.size()[0], pyautogui.size()[1]
    return x, y, w, h


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    if not _user32().GetCursorPos(ctypes.byref(pt)):
        return 0, 0
    return pt.x, pt.y


def clamp(x: float, y: float) -> tuple[int, int]:
    vx, vy, vw, vh = virtual_screen()
    return (max(vx, min(int(round(x)), vx + vw - 1)),
            max(vy, min(int(round(y)), vy + vh - 1)))


def click_screen(x: float, y: float, humanize: bool = False) -> tuple[int, int]:
    """先移过去、确认光标真的落定了,才按下鼠标。"""
    px, py = clamp(x, y)
    if humanize:
        pyautogui.moveTo(px, py, duration=0.3)
    else:
        pyautogui.moveTo(px, py, duration=0.05)

    # 顺序很重要:确认之前绝不能 click,否则光标被卡在别处就会误点无关窗口
    gx, gy = cursor_pos()
    if abs(gx - px) > _LAND_TOLERANCE or abs(gy - py) > _LAND_TOLERANCE:
        raise ClickBlocked(
            f"鼠标停在 ({gx}, {gy}),没能移到 ({px}, {py});"
            "目标客户端很可能以管理员身份运行,助手权限不够,点不进去")

    pyautogui.click()
    logger.info(f"已点击屏幕坐标 ({px}, {py})")
    return px, py


def click_norm(rect: tuple[int, int, int, int], box: tuple[float, float, float, float],
               humanize: bool = False) -> tuple[int, int]:
    """box 为归一化 (x1,y1,x2,y2),rect 为窗口屏幕坐标 (x,y,w,h)。"""
    x, y, w, h = rect
    center_x = (box[0] + box[2]) / 2.0
    center_y = (box[1] + box[3]) / 2.0
    return click_screen(x + center_x * w, y + center_y * h, humanize=humanize)

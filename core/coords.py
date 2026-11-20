"""步骤坐标解析:锚点 + 偏移 → 屏幕像素。

锚点模型(rect 为窗口屏幕矩形 [x, y, w, h]):
  window        dx/dy 为相对窗口的归一化值(0~1),随窗口等比缩放
  top_left      dx 向右、dy 向下,单位像素
  bottom_left   dx 向右、dy 向上,单位像素
  top_right     dx 向左、dy 向下,单位像素
  bottom_right  dx 向左、dy 向上,单位像素

底边锚点是给「贴着底边弹出的菜单/浮层」用的:WorkBuddy 的头像行、Trae 的账户面板
都以窗口底边为基准排布,窗口高度一变(非最大化、换显示器、任务栏改高度),
整窗归一化会让每个点按比例各自漂移,而底边偏移保持不动。
"""
from __future__ import annotations

ANCHORS = ("window", "top_left", "bottom_left", "top_right", "bottom_right")

# 录制时按点击落在窗口的那个象限自动选锚点
_AUTO_SPLIT_Y = 0.55


def resolve(rect, step: dict) -> tuple[int, int] | None:
    """把一步的锚点+偏移换算成屏幕物理像素;无法解析时返回 None。"""
    if not rect:
        return None
    try:
        x, y, w, h = (int(v) for v in rect)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None

    anchor = str(step.get("anchor") or "window")
    if anchor not in ANCHORS:
        return None
    try:
        dx = float(step.get("dx"))
        dy = float(step.get("dy"))
    except (TypeError, ValueError):
        return None

    if anchor == "window":
        px, py = x + dx * w, y + dy * h
    elif anchor == "top_left":
        px, py = x + dx, y + dy
    elif anchor == "bottom_left":
        px, py = x + dx, y + h - dy
    elif anchor == "top_right":
        px, py = x + w - dx, y + dy
    else:
        px, py = x + w - dx, y + h - dy

    if px != px or py != py:              # NaN
        return None
    return int(round(px)), int(round(py))


def anchor_of(rect, img_x: float, img_y: float) -> str:
    """按点击位置挑一个最稳的锚点(下半部贴底、右半部贴右)。"""
    _, _, w, h = (int(v) for v in rect)
    left = img_x <= w * 0.5
    if img_y >= h * _AUTO_SPLIT_Y:
        return "bottom_left" if left else "bottom_right"
    return "top_left" if left else "top_right"


def from_click(rect, img_x: float, img_y: float) -> dict:
    """把「在程序自己截的窗口图上的一次点击」转成一步的坐标描述。

    入参是图片像素坐标;截图裁剪矩形与窗口矩形同源,所以 1 图片像素 == 1 屏幕像素。
    """
    x, y, w, h = (int(v) for v in rect)
    anchor = anchor_of(rect, img_x, img_y)
    if anchor == "top_left":
        dx, dy = img_x, img_y
    elif anchor == "top_right":
        dx, dy = w - img_x, img_y
    elif anchor == "bottom_left":
        dx, dy = img_x, h - img_y
    else:
        dx, dy = w - img_x, h - img_y
    return {"anchor": anchor, "dx": int(round(dx)), "dy": int(round(dy))}

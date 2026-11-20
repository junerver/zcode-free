"""按目标、按周期的「这一周期已领到」记账。

为什么需要它:签到福利有周期。ZCode 的周末福利一周只有一次机会,WorkBuddy / Trae
是每天一次。用户会在一天里排好几个候选场次,只要其中一场领到,同一周期内后面的
场次再跑也变不出福利 —— 白开一次客户端、截两次图、调两次视觉 API,还可能重复点击。
`once` 就是干这个的:none 表示「每个场次都照常执行,不跳过」。

周期定义:
  daily   当天 00:00 起,跨零点即重来
  weekly  周五 00:00 到下一个周五(福利周从周五算起,和用户的使用习惯一致)

状态存 data/claims.json,与截图留档分开放:
- 数据目录不可写时(打包后放在 Program Files)不影响主流程,只是退化为"多试几次";
- 文件损坏/内容非法一律当作"还没领过",宁可多试一次也不要漏领。
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from loguru import logger

from core.config import DATA_DIR
from core.targets import ONCE_DAILY, ONCE_NONE, ONCE_WEEKLY

CLAIMS_PATH = DATA_DIR / "claims.json"

# 周期起点:周五。date.weekday() 里周一=0 … 周日=6,故周五=4。
WEEK_START_WEEKDAY = 4

_DAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _as_date(when) -> date:
    if isinstance(when, datetime):
        return when.date()
    if isinstance(when, date):
        return when
    return datetime.now().date()


def week_start_of(when: date | datetime | None = None) -> date:
    """返回 when 所属周期(福利周)的起点日期 —— 该周的周五。"""
    day = _as_date(when)
    # 相对本周五偏移:周五(4)→0,周六(5)→1,…周四(3)→6
    offset = (day.weekday() - WEEK_START_WEEKDAY) % 7
    return day - timedelta(days=offset)


def week_end_of(when: date | datetime | None = None) -> date:
    """本周期的最后一天(周四),用于界面上说明"到哪天为止"。"""
    return week_start_of(when) + timedelta(days=6)


def week_label(when: date | datetime | None = None) -> str:
    """周期标签,如 "09-18(周五) ~ 09-24(周四)"。"""
    start = week_start_of(when)
    end = week_end_of(when)
    return (f"{start.strftime('%m-%d')}({_DAY_NAMES[start.weekday()]}) ~ "
            f"{end.strftime('%m-%d')}({_DAY_NAMES[end.weekday()]})")


def period_of(mode: str, when: date | datetime | None = None) -> str | None:
    """当前周期的标识;once=none(或不认识的值)返回 None,表示不做周期记账。"""
    day = _as_date(when)
    if mode == ONCE_DAILY:
        return day.isoformat()
    if mode == ONCE_WEEKLY:
        return week_start_of(day).isoformat()
    return None


def period_label(mode: str, when: date | datetime | None = None) -> str:
    if mode == ONCE_DAILY:
        return f"{_as_date(when).isoformat()}(当天)"
    if mode == ONCE_WEEKLY:
        return week_label(when)
    return "不限周期"


class ClaimStore:
    """记录每个目标「本周期是否已领到」。线程安全,读写失败不抛给调用方。"""

    def __init__(self, path: Path = CLAIMS_PATH):
        self.path = Path(path)
        self._lock = threading.RLock()

    # ---------- 读 ----------

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logger.warning(f"读取领取记账失败(按未领取处理):{exc}")
            return {}

    def state(self, target_id: str, mode: str = ONCE_NONE,
              when: date | datetime | None = None) -> dict:
        """某目标当前周期的状态。

        period 与记录里的不一致(或 mode 改过)说明那条记录属于过去的周期,不再算已领取。
        """
        period = period_of(mode, when)
        recorded = self._load().get(str(target_id))
        recorded = recorded if isinstance(recorded, dict) else {}
        claimed = bool(recorded.get("claimed")) and period is not None \
            and str(recorded.get("period") or "") == period
        return {
            "target": str(target_id),
            "mode": mode if mode in (ONCE_DAILY, ONCE_WEEKLY) else ONCE_NONE,
            "period": period or "",
            "label": period_label(mode, when),
            "claimed": claimed,
            "claimed_at": (recorded.get("claimed_at") or "") if claimed else "",
            "claimed_slot": (recorded.get("claimed_slot") or "") if claimed else "",
            "claimed_source": (recorded.get("claimed_source") or "") if claimed else "",
        }

    def claimed(self, target_id: str, mode: str = ONCE_NONE,
                when: date | datetime | None = None) -> bool:
        return bool(self.state(target_id, mode, when)["claimed"])

    def summary(self, targets, when: date | datetime | None = None) -> dict:
        """给面板首页用:{target_id: 状态}。"""
        return {
            t["id"]: self.state(t["id"], t.get("once", ONCE_NONE), when)
            for t in targets
        }

    # ---------- 写 ----------

    def mark(self, target_id: str, mode: str = ONCE_NONE, slot: str = "",
             source: str = "", when: date | datetime | None = None) -> dict:
        """记下"本周期已领到"。mode=none 时不记账(每个场次都照跑)。"""
        period = period_of(mode, when)
        if period is None:
            return {}
        payload = {
            "period": period,
            "mode": mode,
            "label": period_label(mode, when),
            "claimed": True,
            "claimed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "claimed_slot": str(slot or ""),
            "claimed_source": str(source or ""),
        }
        with self._lock:
            data = self._load()
            data[str(target_id)] = payload
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info(f"{target_id} 本周期({payload['label']})已记账,后续场次将跳过")
            except OSError as exc:
                # 写不进去不影响本次结果,只是下次开机会重试
                logger.warning(f"写入领取记账失败(后续场次仍会照常执行):{exc}")
        return payload

    def clear(self, target_id: str | None = None) -> int:
        """清掉记账(面板「清除领取记录」用,便于手动重试)。返回清除条数。"""
        with self._lock:
            data = self._load()
            if target_id is None:
                count = len(data)
                data = {}
            else:
                count = 1 if data.pop(str(target_id), None) is not None else 0
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info(f"已清除 {count} 条领取记账")
            except OSError as exc:
                logger.warning(f"清除领取记账失败:{exc}")
                return 0
            return count

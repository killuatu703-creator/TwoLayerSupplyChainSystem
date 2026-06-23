from __future__ import annotations

from dataclasses import dataclass, field

from .models import Schedule


@dataclass(slots=True)
class Emulator:
    """保存 schedule 快照，作用类似原 Smalltalk 系统中的 Emulator。"""

    schedule_history: list[Schedule] = field(default_factory=list)

    def record_current_data(self, schedule: Schedule) -> None:
        """记录当前排产结果。"""
        self.schedule_history.append(schedule)

    @property
    def latest_schedule(self) -> Schedule | None:
        """取得最近一次保存的 schedule。"""
        if not self.schedule_history:
            return None
        return self.schedule_history[-1]

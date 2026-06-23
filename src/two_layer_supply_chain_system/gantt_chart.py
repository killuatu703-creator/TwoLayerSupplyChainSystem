from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from .models import Schedule


class GanttChartViewer:
    """根据 Schedule 生成甘特图 PNG。"""

    def __init__(self, output_dir: Path) -> None:
        """创建图片输出目录。"""
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def output(self, schedule: Schedule, filename: str, title: str) -> None:
        """把每个 ScheduleItem 画成横向条形图。"""
        path = self.output_dir / filename
        resources = sorted({item.resource_name for item in schedule.items})
        jobs = sorted({item.job_name for item in schedule.items})
        if not resources:
            return

        # 每个 resource 占甘特图中的一行。
        resource_positions = {resource: idx for idx, resource in enumerate(resources)}
        # 按 Job 名分配颜色，同一个 Job 的所有工序颜色相同。
        colors = {
            job_name: plt.cm.tab20(idx % 20)
            for idx, job_name in enumerate(jobs)
        }

        height = max(3.5, 1.2 + len(resources) * 0.8)
        fig, ax = plt.subplots(figsize=(13, height))

        for item in schedule.items:
            # 一道工序对应甘特图上的一条横条。
            y = resource_positions[item.resource_name]
            start = item.start_time
            duration = item.finish_time - item.start_time
            ax.barh(
                y,
                duration,
                left=start,
                height=0.55,
                color=colors[item.job_name],
                edgecolor="black",
                linewidth=0.5,
            )
            label = f"{item.job_name}-Op{item.operation_number}"
            # 工序太短时不显示文字，避免图上文字重叠。
            if duration >= max(180, schedule.makespan * 0.025):
                ax.text(
                    start + duration / 2,
                    y,
                    label,
                    ha="center",
                    va="center",
                    fontsize=7,
                    color="black",
                )

        ax.set_title(title)
        ax.set_xlabel("Time [s]")
        ax.set_ylabel("Resource")
        ax.set_yticks(list(resource_positions.values()))
        ax.set_yticklabels(resources)
        ax.grid(axis="x", linestyle="--", alpha=0.35)
        ax.set_axisbelow(True)

        legend_items = [
            Patch(facecolor=colors[job_name], edgecolor="black", label=job_name)
            for job_name in jobs
        ]
        ax.legend(
            handles=legend_items,
            loc="upper center",
            bbox_to_anchor=(0.5, -0.18),
            ncol=min(6, max(1, len(jobs))),
            fontsize=7,
            frameon=False,
        )

        fig.tight_layout()
        fig.savefig(path, dpi=180, bbox_inches="tight")
        plt.close(fig)

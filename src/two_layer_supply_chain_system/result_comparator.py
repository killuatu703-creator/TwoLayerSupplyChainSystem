from __future__ import annotations

import csv
from pathlib import Path

from .models import Schedule


class ResultComparator:
    # 负责把实验结果输出成 CSV，方便 PPT 和结果比较。

    def __init__(self, output_dir: Path) -> None:
        # 创建结果输出目录。
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def output_summary(self, rows: list[dict[str, object]]) -> None:
        # 输出 summary.csv，记录各方案的总体评价值。
        path = self.output_dir / "summary.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "case",
                    "makespan",
                    "gross_tardiness",
                    "weighted_tardiness",
                    "total_flow_time",
                    "ga_flow_time_weight",
                    "ga_objective",
                    "total_penalty",
                    "outsourcing_charge",
                    "total_loss",
                    "loss_without_outsourcing",
                    "loss_reduction",
                    "net_profit",
                    "outsourced_jobs",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)

    def output_schedule(self, name: str, schedule: Schedule) -> None:
        # 输出每个工序的开始/结束时间，用于检查排产结果。
        path = self.output_dir / name
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "job_name",
                    "resource_name",
                    "operation_number",
                    "start_time",
                    "finish_time",
                ],
            )
            writer.writeheader()
            for item in schedule.items:
                writer.writerow(
                    {
                        "job_name": item.job_name,
                        "resource_name": item.resource_name,
                        "operation_number": item.operation_number,
                        "start_time": item.start_time,
                        "finish_time": item.finish_time,
                    }
                )

    def output_decisions(
        self,
        decisions: list[dict[str, object]],
        filename: str = "outsourcing_decisions.csv",
    ) -> None:
        # 输出外注决策表，包括中标供应商、报价和 tabu list。
        path = self.output_dir / filename
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "job_name",
                    "winner",
                    "winner_due_date",
                    "winner_price",
                    "loss_reduction",
                    "negotiation_round",
                    "requested_due_date",
                    "requested_price",
                    "tabu_jobs",
                    "candidate_count",
                    "evaluated_count",
                    "all_offers",
                ],
            )
            writer.writeheader()
            for decision in decisions:
                offers = decision["offers"]
                # all_offers 用一行文字保存 S2/S3 的全部报价，方便直接查看。
                writer.writerow(
                    {
                        "job_name": decision["job_name"],
                        "winner": decision["winner"],
                        "winner_due_date": decision["winner_due_date"],
                        "winner_price": decision["winner_price"],
                        "loss_reduction": decision.get("loss_reduction", ""),
                        "negotiation_round": decision.get("negotiation_round", ""),
                        "requested_due_date": decision.get("requested_due_date", ""),
                        "requested_price": decision.get("requested_price", ""),
                        "tabu_jobs": decision.get("tabu_jobs", ""),
                        "candidate_count": decision.get("candidate_count", ""),
                        "evaluated_count": decision.get("evaluated_count", ""),
                        "all_offers": "; ".join(
                            f"{offer.supplier_name}: due={offer.due_date}, price={offer.price}, feasible={offer.feasible}"
                            for offer in offers
                        ),
                    }
                )

    def output_negotiation_history(
        self,
        decisions: list[dict[str, object]],
        filename: str,
    ) -> None:
        # 多段階交渉の各 round の offer を CSV に出力する。
        #
        # outsourcing_decisions_*.csv は最終的に選ばれた外注先だけを見る表，
        # negotiation_history_*.csv は交渉途中の全 offer を見る表。
        # PPT では後者を見ることで，round ごとに希望条件がどう緩和されたかを確認できる。
        #
        path = self.output_dir / filename
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "selected_job",
                    "winner",
                    "job_name",
                    "negotiation_round",
                    "requested_due_date",
                    "requested_price",
                    "supplier",
                    "offer_due_date",
                    "offer_price",
                    "feasible_due_date",
                    "feasible_price",
                    "feasible",
                    "loss_reduction",
                ],
            )
            writer.writeheader()
            for decision in decisions:
                history = decision.get("negotiation_history", [])
                if not isinstance(history, list):
                    continue
                for row in history:
                    if not isinstance(row, dict):
                        continue
                    writer.writerow(
                        {
                            "selected_job": decision.get("job_name", ""),
                            "winner": decision.get("winner", ""),
                            "job_name": row.get("job_name", ""),
                            "negotiation_round": row.get("negotiation_round", ""),
                            "requested_due_date": row.get("requested_due_date", ""),
                            "requested_price": row.get("requested_price", ""),
                            "supplier": row.get("supplier", ""),
                            "offer_due_date": row.get("offer_due_date", ""),
                            "offer_price": row.get("offer_price", ""),
                            "feasible_due_date": row.get("feasible_due_date", ""),
                            "feasible_price": row.get("feasible_price", ""),
                            "feasible": row.get("feasible", ""),
                            "loss_reduction": row.get("loss_reduction", ""),
                        }
                    )

    def summary_row(
        self,
        case: str,
        schedule: Schedule,
        baseline_loss: int | None = None,
        flow_time_weight: float = 0.1,
    ) -> dict[str, object]:
        # 把一个 Schedule 转换成 summary.csv 的一行。
        loss_reduction = "" if baseline_loss is None else baseline_loss - schedule.total_loss
        return {
            "case": case,
            "makespan": schedule.makespan,
            "gross_tardiness": schedule.gross_tardiness,
            "weighted_tardiness": schedule.weighted_tardiness,
            "total_flow_time": schedule.total_flow_time,
            "ga_flow_time_weight": flow_time_weight,
            "ga_objective": round(schedule.ga_objective(flow_time_weight), 2),
            "total_penalty": schedule.total_penalty,
            "outsourcing_charge": schedule.outsourcing_charge,
            "total_loss": schedule.total_loss,
            "loss_without_outsourcing": "" if baseline_loss is None else baseline_loss,
            "loss_reduction": loss_reduction,
            "net_profit": schedule.net_profit,
            "outsourced_jobs": sum(1 for job in schedule.jobs if job.outsourced_to),
        }

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from two_layer_supply_chain_system.actors import Client, OutsourcingSupplier, Supplier
from two_layer_supply_chain_system.ga2003 import GA2003, GAParameter
from two_layer_supply_chain_system.gantt_chart import GanttChartViewer
from two_layer_supply_chain_system.models import OrderInformation, Resource
from two_layer_supply_chain_system.result_comparator import ResultComparator
from two_layer_supply_chain_system.schedule_platform import (
    SchedulePlatform,
    SchedulePlatformForOutsourcing,
)

DISASTER_START_TIME = 3000
DISASTER_END_TIME = 20000
S1_JOB_COUNT = 16
SUPPLIER_INITIAL_JOB_COUNT = 8
RESOURCE_COUNT = 4


def sample_orders() -> list[OrderInformation]:
    """生成 S1 接到的订单数据，也就是本实验中的外注候补 Job。"""
    orders: list[OrderInformation] = []
    for idx in range(S1_JOB_COUNT):
        # 这里暂时用固定公式生成测试数据，所以每次运行的 S1J 都相同。
        name = f"S1J{idx + 1:02d}"
        price = 8500 + (idx % 6) * 700
        cost = 5000 + (idx % 4) * 500
        due_date = 2900 + idx * 260 + (idx % 3) * 120
        process = 220 + (idx % 4) * 35
        orders.append(
            OrderInformation(
                name_of_job=name,
                operations=[
                    (f"R{resource_idx}", process + resource_idx * 20)
                    for resource_idx in range(1, RESOURCE_COUNT + 1)
                ],
                duedate_of_job=due_date,
                price_of_job=price,
                cost_of_job=cost,
                delay_penalty_of_job=(8 + idx % 5) if idx < 8 else (28 + idx % 6),
                release_time_of_job=idx * 70,
            )
        )
    return orders


def supplier_initial_orders(supplier_name: str) -> list[OrderInformation]:
    """生成 S2/S3 原本已有的初期 Job。"""
    orders: list[OrderInformation] = []
    for idx in range(SUPPLIER_INITIAL_JOB_COUNT):
        # S2J01/S3J01 这种初期 Job 也是固定规则生成，方便重复实验。
        name = f"{supplier_name}J{idx + 1:02d}"
        price = 7000 + (idx % 5) * 500
        cost = 4100 + (idx % 4) * 350
        due_date = 2600 + idx * 360
        process = 170 + (idx % 3) * 40
        orders.append(
            OrderInformation(
                name_of_job=name,
                operations=[
                    (f"R{resource_idx}", process + resource_idx * 15)
                    for resource_idx in range(1, RESOURCE_COUNT + 1)
                ],
                duedate_of_job=due_date,
                price_of_job=price,
                cost_of_job=cost,
                delay_penalty_of_job=6 + idx % 4,
                release_time_of_job=idx * 90,
            )
        )
    return orders


def resources(prefix: str = "R") -> list[Resource]:
    """生成生产资源 R1, R2, ...。"""
    return [Resource(f"{prefix}{idx}") for idx in range(1, RESOURCE_COUNT + 1)]


# S1 只看加权纳期迟れ，S2/S3 额外考虑 total_flow_time。
S1_FLOW_TIME_WEIGHT = 0.0
OUTSOURCING_SUPPLIER_FLOW_TIME_WEIGHT = 0.1


def ga(seed: int, flow_time_weight: float) -> GA2003:
    """生成 GA 排产器。seed 用来固定随机结果，保证每次实验可复现。"""
    return GA2003(
        GAParameter(
            population_size=36,
            generations=70,
            mutation_rate=0.2,
            crossover_rate=0.85,
            flow_time_weight=flow_time_weight,
            seed=seed,
        )
    )


def build_without_outsourcing(orders: list[OrderInformation]):
    """构建不外注的基准情况：S1 自己处理全部订单。"""
    platform = SchedulePlatform(
        resources(),
        ga_engine=ga(1, flow_time_weight=S1_FLOW_TIME_WEIGHT),
        disaster_start_time=DISASTER_START_TIME,
        disaster_end_time=DISASTER_END_TIME,
    )
    supplier = Supplier("S1", platform, mark_up=0.25)
    client = Client("ClientA", orders)
    schedule = client.construction_of_scm(supplier)  # type: ignore[arg-type]
    return schedule


def build_with_outsourcing(orders: list[OrderInformation], rule: str = "GREEDY"):
    """构建有外注的情况，并按照 GREEDY 或 TABU 规则选择外注 Job。"""
    outsourcing_limit = len(orders)
    s1_platform = SchedulePlatformForOutsourcing(
        resources(),
        ga_engine=ga(2, flow_time_weight=S1_FLOW_TIME_WEIGHT),
        disaster_start_time=DISASTER_START_TIME,
        disaster_end_time=DISASTER_END_TIME,
        outsourcing_flag=rule,
        out_restriction=outsourcing_limit,
    )
    s1 = OutsourcingSupplier("S1", s1_platform, mark_up=0.25)

    # S2/S3 是外注候补供应商，各自先有自己的初期生产任务。
    s2 = Supplier(
        "S2",
        SchedulePlatform(
            resources(),
            ga_engine=ga(3, flow_time_weight=OUTSOURCING_SUPPLIER_FLOW_TIME_WEIGHT),
        ),
        mark_up=0.18,
        transportation_cost=80,
    )
    s3 = Supplier(
        "S3",
        SchedulePlatform(
            resources(),
            ga_engine=ga(4, flow_time_weight=OUTSOURCING_SUPPLIER_FLOW_TIME_WEIGHT),
        ),
        mark_up=0.22,
        transportation_cost=60,
        specialty_job_remainder=1,
        specialty_discount=500,
    )
    s1.outsources = [s2, s3]

    # 先给 S2/S3 加入初期 Job，并生成初期 schedule。
    for order in supplier_initial_orders("S2"):
        s2.receive_order(order)
    for order in supplier_initial_orders("S3"):
        s3.receive_order(order)
    initial_supplier_schedules = {
        "S2": s2.create_scheduling(),
        "S3": s3.create_scheduling(),
    }

    # S1 接收 client 的订单，先排出受到生产停止影响的 schedule。
    client = Client("ClientA", orders)
    client.construction_of_scm(s1)
    # 按 rule 执行外注交涉，返回每次外注成立/不成立的记录。
    decisions = s1.outsourcing_negotiation(max_contracts=outsourcing_limit)
    final_schedule = s1.create_scheduling()
    # 已外注的 Job 不再由 S1 加工，但为了统计 total_loss，需要放进最终结果中。
    final_schedule.jobs.extend(
        job
        for job in s1_platform.outsourced_job_list
        if job.job_name not in {scheduled.job_name for scheduled in final_schedule.jobs}
    )
    supplier_schedules = {
        "S2": s2.create_scheduling(),
        "S3": s3.create_scheduling(),
    }
    return final_schedule, decisions, initial_supplier_schedules, supplier_schedules


def main() -> None:
    """运行完整实验，并输出 CSV 结果和甘特图。"""
    output_dir = ROOT / "results"
    comparator = ResultComparator(output_dir)
    gantt_viewer = GanttChartViewer(output_dir)
    orders = sample_orders()

    # 先跑不外注的基准情况，用它作为 loss_reduction 的比较对象。
    without = build_without_outsourcing(orders)
    comparator.output_schedule("schedule_without_outsourcing.csv", without)
    gantt_viewer.output(
        without,
        "gantt_without_outsourcing.png",
        "Schedule without outsourcing",
    )

    baseline_loss = without.total_loss
    summary_rows = [
        comparator.summary_row(
            "GA_without_outsourcing",
            without,
            baseline_loss=baseline_loss,
            flow_time_weight=S1_FLOW_TIME_WEIGHT,
        )
    ]
    initial_supplier_output_done = False
    for rule in ["GREEDY", "TABU"]:
        # 分别运行 Greedy 和 Tabu Search，并输出各自的结果。
        (
            with_outsourcing,
            decisions,
            initial_supplier_schedules,
            supplier_schedules,
        ) = build_with_outsourcing(orders, rule=rule)

        if not initial_supplier_output_done:
            # S2/S3 的初期 schedule 对 Greedy/Tabu 相同，只输出一次。
            for supplier_name, schedule in initial_supplier_schedules.items():
                comparator.output_schedule(
                    f"schedule_{supplier_name.lower()}_initial.csv",
                    schedule,
                )
                gantt_viewer.output(
                    schedule,
                    f"gantt_{supplier_name.lower()}_initial.png",
                    f"{supplier_name} initial schedule",
                )
            initial_supplier_output_done = True

        # 输出 S1 外注后 schedule、外注决策表，以及 S2/S3 外注后的 schedule。
        comparator.output_schedule(
            f"schedule_with_outsourcing_{rule.lower()}.csv",
            with_outsourcing,
        )
        gantt_viewer.output(
            with_outsourcing,
            f"gantt_with_outsourcing_{rule.lower()}.png",
            f"Schedule with outsourcing ({rule})",
        )
        comparator.output_decisions(
            decisions,
            f"outsourcing_decisions_{rule.lower()}.csv",
        )
        for supplier_name, schedule in supplier_schedules.items():
            comparator.output_schedule(
                f"schedule_{supplier_name.lower()}_after_outsourcing_{rule.lower()}.csv",
                schedule,
            )
            gantt_viewer.output(
                schedule,
                f"gantt_{supplier_name.lower()}_after_outsourcing_{rule.lower()}.png",
                f"{supplier_name} schedule after outsourcing ({rule})",
            )
        summary_rows.append(
            comparator.summary_row(
                f"GA_with_outsourcing_{rule}",
                with_outsourcing,
                baseline_loss=baseline_loss,
                flow_time_weight=S1_FLOW_TIME_WEIGHT,
            )
        )

    comparator.output_summary(summary_rows)

    print("Simulation finished.")
    print(f"Results: {output_dir}")
    print(
        "Without outsourcing:",
        without.makespan,
        without.total_penalty,
        without.total_loss,
        without.net_profit,
    )
    for row in summary_rows[1:]:
        print(
            row["case"],
            row["makespan"],
            row["total_penalty"],
            row["outsourcing_charge"],
            row["total_loss"],
            row["loss_reduction"],
            row["net_profit"],
        )


if __name__ == "__main__":
    main()

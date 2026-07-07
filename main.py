from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from two_layer_supply_chain_system.actors import Client, OutsourcingSupplier, Supplier
from two_layer_supply_chain_system.excel_input import (
    ExperimentConfig,
    SupplierConfig,
    load_experiment_from_excel,
)
from two_layer_supply_chain_system.ga2003 import GA2003, GAParameter
from two_layer_supply_chain_system.gantt_chart import GanttChartViewer
from two_layer_supply_chain_system.models import Resource
from two_layer_supply_chain_system.result_comparator import ResultComparator
from two_layer_supply_chain_system.schedule_platform import (
    SchedulePlatform,
    SchedulePlatformForOutsourcing,
)

INPUT_FILE = ROOT / "input_data.xlsx"


def resources(config: ExperimentConfig, prefix: str = "R") -> list[Resource]:
    # 根据 Excel 中的 resource_count 生成生产资源。
    return [Resource(f"{prefix}{idx}") for idx in range(1, config.resource_count + 1)]


def ga(config: ExperimentConfig, seed: int, flow_time_weight: float) -> GA2003:
    # 根据 Excel 中的 GA 参数生成排产器。
    return GA2003(
        GAParameter(
            population_size=config.ga_population_size,
            generations=config.ga_generations,
            mutation_rate=config.ga_mutation_rate,
            crossover_rate=config.ga_crossover_rate,
            flow_time_weight=flow_time_weight,
            seed=seed,
        )
    )


def build_without_outsourcing(config: ExperimentConfig):
    # 构建不外注的基准情况：S1 自己处理全部订单。
    s1_config = config.supplier("S1")
    platform = SchedulePlatform(
        resources(config),
        ga_engine=ga(config, s1_config.ga_seed, s1_config.flow_time_weight),
        disaster_start_time=config.disaster_start_time,
        disaster_end_time=config.disaster_end_time,
    )
    supplier = Supplier("S1", platform, mark_up=s1_config.mark_up)
    client = Client("ClientA", config.s1_orders)
    schedule = client.construction_of_scm(supplier)  # type: ignore[arg-type]
    return schedule


def _build_outsource_supplier(
    config: ExperimentConfig, supplier_config: SupplierConfig
) -> Supplier:
    # 根据 Excel 设定创建 S2/S3 这样的外注候补供应商。
    return Supplier(
        supplier_config.supplier_name,
        SchedulePlatform(
            resources(config),
            ga_engine=ga(
                config,
                supplier_config.ga_seed,
                supplier_config.flow_time_weight,
            ),
        ),
        mark_up=supplier_config.mark_up,
        transportation_cost=supplier_config.transportation_cost,
        specialty_job_remainder=supplier_config.specialty_job_remainder,
        specialty_discount=supplier_config.specialty_discount,
    )


def build_with_outsourcing(config: ExperimentConfig, rule: str = "GREEDY"):
    # 构建有外注的情况，并按照 GREEDY、TABU 或 SA 规则选择外注 Job。
    orders = config.s1_orders
    outsourcing_limit = len(orders)
    s1_config = config.supplier("S1")
    s1_platform = SchedulePlatformForOutsourcing(
        resources(config),
        ga_engine=ga(config, s1_config.ga_seed, s1_config.flow_time_weight),
        disaster_start_time=config.disaster_start_time,
        disaster_end_time=config.disaster_end_time,
        outsourcing_flag=rule,
        out_restriction=outsourcing_limit,
    )
    s1 = OutsourcingSupplier("S1", s1_platform, mark_up=s1_config.mark_up)

    # S2/S3 等外注候补供应商，以及它们自己的初期 Job，都从 Excel 读取。
    outsource_suppliers: list[Supplier] = []
    initial_supplier_schedules = {}
    for supplier_config in config.suppliers.values():
        if supplier_config.role.lower() != "outsource":
            continue
        supplier = _build_outsource_supplier(config, supplier_config)
        for order in config.supplier_initial_orders.get(supplier.name, []):
            supplier.receive_order(order)
        initial_supplier_schedules[supplier.name] = supplier.create_scheduling()
        outsource_suppliers.append(supplier)
    s1.outsources = outsource_suppliers

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
        supplier.name: supplier.create_scheduling()
        for supplier in outsource_suppliers
    }
    return final_schedule, decisions, initial_supplier_schedules, supplier_schedules


def main() -> None:
    # 运行完整实验，并输出 CSV 结果和甘特图。
    config = load_experiment_from_excel(INPUT_FILE)
    output_dir = ROOT / "results"
    comparator = ResultComparator(output_dir)
    gantt_viewer = GanttChartViewer(output_dir)

    # 先跑不外注的基准情况，用它作为 loss_reduction 的比较对象。
    without = build_without_outsourcing(config)
    comparator.output_schedule("schedule_without_outsourcing.csv", without)
    gantt_viewer.output(
        without,
        "gantt_without_outsourcing.png",
        "Schedule without outsourcing",
    )

    baseline_loss = without.total_loss
    s1_config = config.supplier("S1")
    summary_rows = [
        comparator.summary_row(
            "GA_without_outsourcing",
            without,
            baseline_loss=baseline_loss,
            flow_time_weight=s1_config.flow_time_weight,
        )
    ]
    initial_supplier_output_done = False
    for rule in config.rules:
        # 分别运行 Excel 中指定的外注选择规则。
        (
            with_outsourcing,
            decisions,
            initial_supplier_schedules,
            supplier_schedules,
        ) = build_with_outsourcing(config, rule=rule)

        if not initial_supplier_output_done:
            # S2/S3 的初期 schedule 对各规则相同，只输出一次。
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
                flow_time_weight=s1_config.flow_time_weight,
            )
        )

    comparator.output_summary(summary_rows)

    print("Simulation finished.")
    print(f"Input: {INPUT_FILE}")
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

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(slots=True)
class Operation:
    # 一个 Job 中的一道工序。

    name_of_job: str
    name_of_resource: str
    process_time: int
    operation_number: int


@dataclass(slots=True)
class Job:
    # 生产对象。S1J/S2J/S3J 都会被转换成 Job 后参与排产。

    job_name: str
    operations: list[Operation]
    order_due_date: int
    job_price: int
    job_cost: int
    penalty: int
    client_name: str = "ClientA"
    release_time: int = 0
    finishing_time: int = 0
    client_due_date: int = 0
    outsourcing_due_date: int = 0
    outsourcing_price: int = 0
    outsourced_to: str | None = None

    def copy_for_outsourcing(self) -> "Job":
        # 复制 Job，避免外注试算时直接修改原始 Job。
        return Job(
            job_name=self.job_name,
            operations=[
                Operation(
                    name_of_job=op.name_of_job,
                    name_of_resource=op.name_of_resource,
                    process_time=op.process_time,
                    operation_number=op.operation_number,
                )
                for op in self.operations
            ],
            order_due_date=self.order_due_date,
            job_price=self.job_price,
            job_cost=self.job_cost,
            penalty=self.penalty,
            client_name=self.client_name,
            release_time=self.release_time,
        )


@dataclass(slots=True)
class Resource:
    # 生产资源，也就是甘特图中的 R1/R2/R3/R4。

    resource_name: str
    available_time: int = 0


@dataclass(slots=True)
class ScheduleItem:
    # 甘特图中的一条加工记录。

    job_name: str
    resource_name: str
    operation_number: int
    start_time: int
    finish_time: int


@dataclass(slots=True)
class Schedule:
    # 一次排产结果，包含所有工序记录和对应 Job 的完成时间。

    items: list[ScheduleItem] = field(default_factory=list)
    jobs: list[Job] = field(default_factory=list)

    @property
    def makespan(self) -> int:
        # 整个 schedule 的最后完成时间。
        return max((item.finish_time for item in self.items), default=0)

    @property
    def gross_tardiness(self) -> int:
        # 所有 Job 的普通纳期迟れ总和，不乘 penalty。
        return sum(max(0, job.client_due_date - job.order_due_date) for job in self.jobs)

    @property
    def total_penalty(self) -> int:
        # 加权纳期迟れ：迟れ时间乘以每个 Job 的 penalty。
        return sum(
            max(0, job.client_due_date - job.order_due_date) * job.penalty
            for job in self.jobs
        )

    @property
    def weighted_tardiness(self) -> int:
        # 当前模型中 weighted_tardiness 与 total_penalty 相同。
        return self.total_penalty

    @property
    def total_flow_time(self) -> int:
        # 滞留时间总和：从 release_time 到完成时间的时间长度。
        return sum(max(0, job.client_due_date - job.release_time) for job in self.jobs)

    def ga_objective(self, flow_time_weight: float = 0.1) -> float:
        # GA 的评价值：加权纳期迟れ + flow time 权重项。
        return self.weighted_tardiness + flow_time_weight * self.total_flow_time

    @property
    def gross_profit(self) -> int:
        # 不考虑罚金和外注费时的粗利润。
        return sum(job.job_price - job.job_cost for job in self.jobs)

    @property
    def outsourcing_charge(self) -> int:
        # 已经外注出去的 Job 的外注费用合计。
        return sum(job.outsourcing_price for job in self.jobs if job.outsourced_to)

    @property
    def total_loss(self) -> int:
        # 当前简化损失：纳期迟れ罚金 + 外注费用。
        return self.total_penalty + self.outsourcing_charge

    @property
    def net_profit(self) -> int:
        # 当前简化净利润。
        return self.gross_profit - self.total_penalty - self.outsourcing_charge

    def job_by_name(self, job_name: str) -> Job:
        # 按照 Job 名查找排产后的 Job。
        for job in self.jobs:
            if job.job_name == job_name:
                return job
        raise KeyError(job_name)


@dataclass(slots=True)
class OrderInformation:
    # Client/Supplier 接单时使用的订单数据，之后会转换成 Job。

    name_of_job: str
    operations: list[tuple[str, int]]
    duedate_of_job: int
    price_of_job: int
    cost_of_job: int
    delay_penalty_of_job: int
    name_of_client: str = "ClientA"
    release_time_of_job: int = 0

    def to_job(self) -> Job:
        # 把订单数据转换成可以排产的 Job 对象。
        return Job(
            job_name=self.name_of_job,
            operations=[
                Operation(self.name_of_job, resource, process_time, i + 1)
                for i, (resource, process_time) in enumerate(self.operations)
            ],
            order_due_date=self.duedate_of_job,
            job_price=self.price_of_job,
            job_cost=self.cost_of_job,
            penalty=self.delay_penalty_of_job,
            client_name=self.name_of_client,
            release_time=self.release_time_of_job,
        )


@dataclass(slots=True)
class Offer:
    # 外注供应商返回给 S1 的报价。

    supplier_name: str
    job_name: str
    due_date: int
    price: int
    feasible_due_date: bool = False
    feasible_price: bool = False

    @property
    def feasible(self) -> bool:
        # 纳期和价格两个条件都满足时，offer 才算可接受。
        return self.feasible_due_date and self.feasible_price


def clone_jobs(jobs: Iterable[Job]) -> list[Job]:
    # 批量复制 Job。
    return [job.copy_for_outsourcing() for job in jobs]

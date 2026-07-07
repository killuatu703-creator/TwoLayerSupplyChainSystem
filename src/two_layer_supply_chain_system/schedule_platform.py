from __future__ import annotations

import random
from typing import Iterable

from .ga2003 import GA2003
from .models import Job, Offer, Resource, Schedule, ScheduleItem


class SchedulePlatform:
    # 排产平台。保存 Job/Resource，并把 GA 给出的 Job 顺序转换成 schedule。
    # 可以把它理解成“工厂内部的排产器”：Job 列表在这里保存，Resource 的空闲时间也在这里计算。
    # S1、S2、S3 都会各自持有一个 SchedulePlatform。

    def __init__(
        self,
        resources: list[Resource],
        ga_engine: GA2003 | None = None,
        time_record: int = 0,
        disaster_start_time: int | None = None,
        disaster_end_time: int | None = None,
    ) -> None:
        self.resource_instance_list = resources
        self.ga_engine = ga_engine or GA2003()
        self.time_record = time_record
        self.disaster_start_time = disaster_start_time
        self.disaster_end_time = disaster_end_time
        self.job_instance_list: list[Job] = []
        self.finished_job_list: list[Job] = []
        self.present_order_job_name: str | None = None
        self.present_due_date: int = 0
        self.present_price: int = 0
        self.present_penalty: int = 0
        self.present_discount: int = 0

    def add_job_aps(self, job: Job) -> None:
        # 向平台加入一个待排产 Job。
        self.job_instance_list.append(job)

    def delete_job_aps(self, job_name: str) -> Job | None:
        # 从平台删除指定 Job。外注成立或 offer 撤销时会使用。
        for idx, job in enumerate(self.job_instance_list):
            if job.job_name == job_name:
                return self.job_instance_list.pop(idx)
        return None

    def part_of_estimate_of_reactive_aps(self) -> Schedule:
        # 调用 GA 重新排产，并保存完成后的 Job 列表。
        schedule = self.reschedule_by_ga_aps(self.job_instance_list)
        self.finished_job_list = schedule.jobs
        return schedule

    def reschedule_by_ga_aps(self, jobs: list[Job]) -> Schedule:
        # 用 GA 对当前 Job 列表进行排产。
        return self.ga_engine.evolve_for_reactive_aps(self, jobs)

    def build_schedule(self, sequence: Iterable[Job]) -> Schedule:
        # 把一个 Job 顺序实际展开成各 Resource 上的工序时间表。
        # sequence 只决定“哪个 Job 先进入排产”；每个 Job 内部仍按 Excel 中的 op1、op2、op3 顺序加工。
        # resource_times 记录每台 Resource 什么时候空闲，job_times/current_time 记录每个 Job 自己加工到哪里。
        resource_times = {r.resource_name: self.time_record for r in self.resource_instance_list}
        job_times: dict[str, int] = {}
        items: list[ScheduleItem] = []
        scheduled_jobs: list[Job] = []

        for original_job in sequence:
            # 每个 Job 的工序按 operations 顺序依次加工。
            # 例如某个 Job 的顺序是 R3 -> R1 -> R4，那么必须先完成 R3 工序，才能进入 R1 工序。
            job = original_job.copy_for_outsourcing()
            current_time = max(job.release_time, self.time_record)
            for operation in job.operations:
                resource_name = operation.name_of_resource
                resource_ready = resource_times.get(resource_name, self.time_record)
                start = max(current_time, resource_ready)
                finish = start + operation.process_time
                # 如果工序跨过 S1 的生产停止区间，就推迟到恢复后加工。
                # 例如本来 2500-3300 加工，但 3000-20000 停止生产，则该工序会改到 20000 以后开始。
                start, finish = self.apply_production_stop(start, finish, operation.process_time)
                resource_times[resource_name] = finish
                current_time = finish
                items.append(
                    ScheduleItem(
                        job_name=job.job_name,
                        resource_name=resource_name,
                        operation_number=operation.operation_number,
                        start_time=start,
                        finish_time=finish,
                    )
                )
            job.finishing_time = current_time
            job.client_due_date = current_time
            # client_due_date 在当前模型中用作实际完成/交货时间。
            job_times[job.job_name] = current_time
            scheduled_jobs.append(job)

        return Schedule(items=items, jobs=scheduled_jobs)

    def apply_production_stop(
        self,
        start: int,
        finish: int,
        process_time: int,
    ) -> tuple[int, int]:
        # 处理生产停止：与停止区间重叠的工序被推迟到恢复时刻之后。
        # 当前简化模型没有把一个工序拆成“停止前加工一半、恢复后继续加工”，而是整个工序推迟重做。
        # 这能表达生产停止造成的 schedule 延迟，但比论文里的完整动态处理更简单。
        if self.disaster_start_time is None or self.disaster_end_time is None:
            return start, finish
        if finish <= self.disaster_start_time or start >= self.disaster_end_time:
            return start, finish
        delayed_start = self.disaster_end_time
        return delayed_start, delayed_start + process_time


class SchedulePlatformForOutsourcing(SchedulePlatform):
    # S1 使用的外注扩展平台，额外管理外注成功/失败 Job。
    # outsourced_job_list 记录已经外注成立的 Job，canceled_outsourcing_list 记录没有成功外注的 Job。
    # 这些列表会影响下一轮外注候补生成，避免同一个 Job 被重复处理。

    def __init__(
        self,
        resources: list[Resource],
        ga_engine: GA2003 | None = None,
        time_record: int = 0,
        disaster_start_time: int | None = None,
        disaster_end_time: int | None = None,
        outsourcing_flag: str = "GREEDY",
        out_restriction: int = 12,
    ) -> None:
        super().__init__(
            resources,
            ga_engine,
            time_record,
            disaster_start_time=disaster_start_time,
            disaster_end_time=disaster_end_time,
        )
        self.outsourcing_flag = outsourcing_flag
        self.out_restriction = out_restriction
        self.outsourced_job_list: list[Job] = []
        self.canceled_outsourcing_list: list[str] = []

    def outsourcing_list(self, flag: str | None = None) -> list[Job]:
        # 生成外注候补 Job 列表，并按 FCFS/EDD/Greedy 等规则排序。
        # 当前 Greedy/Tabu/SA 的真正选择不是只看这个排序，而是先用这里得到候补集合，
        # 然后在 actors.py 中对候补 Job × S2/S3 的所有 offer 计算 ΔL。
        flag = flag or self.outsourcing_flag
        # 优先选择发生纳期迟れ的 Job。
        # client_due_date 是当前排产后的完成时间，order_due_date 是原订单纳期；完成时间晚于纳期就进入候补。
        candidates = [
            job
            for job in self.finished_job_list
            if job.job_name not in self.canceled_outsourcing_list
            and job.job_name not in {j.job_name for j in self.outsourced_job_list}
            and job.client_due_date > job.order_due_date
        ]
        if not candidates:
            # 如果没有迟れ Job，就从未外注、未取消的 Job 中继续选择。
            # 这是为了让实验在小规模数据下也能继续比较外注选择规则。
            candidates = [
                job
                for job in self.finished_job_list
                if job.job_name not in self.canceled_outsourcing_list
                and job.job_name not in {j.job_name for j in self.outsourced_job_list}
            ]

        flag = flag.upper()
        if flag == "FCFS":
            return self.sort_fcfs(candidates)
        if flag == "EDD":
            return self.sort_edd(candidates)
        if flag == "RAND":
            return self.collection_randomize(candidates)
        return self.sort_greedy(candidates)

    def select_outsourcing_job(self) -> Job | None:
        # 旧版 FCFS/EDD 等规则用：选择一个外注候补 Job。
        if len(self.outsourced_job_list) >= self.out_restriction:
            return None
        candidates = self.outsourcing_list(self.outsourcing_flag)
        if not candidates:
            return None
        return candidates[0]

    def sort_fcfs(self, jobs: list[Job]) -> list[Job]:
        # FCFS：按 release_time 早的 Job 优先。
        return sorted(jobs, key=lambda job: job.release_time)

    def sort_edd(self, jobs: list[Job]) -> list[Job]:
        # EDD：按纳期早的 Job 优先。
        return sorted(jobs, key=lambda job: job.order_due_date)

    def sort_greedy(self, jobs: list[Job]) -> list[Job]:
        # 简易 Greedy 排序：预估迟れ罚金越大的 Job 越优先。
        return sorted(
            jobs,
            key=lambda job: (
                -max(0, job.client_due_date - job.order_due_date) * job.penalty,
                job.order_due_date,
            ),
        )

    def collection_randomize(self, jobs: list[Job]) -> list[Job]:
        # 随机排序候补 Job。seed 固定为 11，方便复现实验。
        copied = list(jobs)
        random.Random(11).shuffle(copied)
        return copied

    def apply_outsourcing_contract(self, job: Job, offer: Offer) -> None:
        # 外注契约成立后，从 S1 删除该 Job，并记录外注纳期和外注费用。
        # 删除后，该 Job 不再占用 S1 的 Resource；但为了计算 total_loss 和输出结果，仍会保存在 outsourced_job_list 中。
        local_job = self.delete_job_aps(job.job_name)
        target = local_job or job.copy_for_outsourcing()
        target.outsourcing_due_date = offer.due_date
        target.client_due_date = offer.due_date
        target.outsourcing_price = offer.price
        target.outsourced_to = offer.supplier_name
        self.outsourced_job_list.append(target)
        self.finished_job_list = [
            scheduled for scheduled in self.finished_job_list if scheduled.job_name != job.job_name
        ]
        self.finished_job_list.append(target)

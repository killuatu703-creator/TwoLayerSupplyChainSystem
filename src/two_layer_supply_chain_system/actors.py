from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
import random
import re
from dataclasses import dataclass, field

from .emulator import Emulator
from .models import Job, Offer, OrderInformation, Schedule
from .schedule_platform import SchedulePlatform, SchedulePlatformForOutsourcing


def _make_offer_in_process(task: tuple["Supplier", Job]) -> Offer | None:
    """ProcessPoolExecutor 用の関数：子进程中让供应商计算 offer。"""
    # 每个任务是一个 (supplier, job)，子进程会调用 supplier.make_offer(job)。
    supplier, job = task
    return supplier.make_offer(job)


@dataclass(slots=True)
class CandidateEvaluation:
    """一个候补外注方案的评价结果。"""

    job: Job
    offer: Offer
    loss_reduction: int


@dataclass(slots=True)
class Client:
    """客户。负责把订单交给 S1。"""

    name: str
    job_information_oc: list[OrderInformation]
    permit_t: float = 0.0
    permit_p: float = 0.0
    mitigate_t: float = 0.1
    mitigate_p: float = 0.1

    def construction_of_scm(self, supplier: "OutsourcingSupplier") -> Schedule:
        """把所有订单交给供应商，并让供应商排产。"""
        for order in self.job_information_oc:
            supplier.receive_order(order)
        return supplier.create_scheduling()


@dataclass(slots=True)
class Supplier:
    """普通供应商。S2/S3 用这个类来接收订单、排产并返回 offer。"""

    name: str
    platform: SchedulePlatform
    emulator: Emulator = field(default_factory=Emulator)
    mark_up: float = 0.25
    transportation_cost: int = 300
    specialty_job_remainder: int | None = None
    specialty_discount: int = 0
    bid_due_date: int = 0
    bid_price: int = 0
    delta_profit: int = 0

    def receive_order(self, order: OrderInformation) -> None:
        """接收订单，并加入自己的排产平台。"""
        self.platform.add_job_aps(order.to_job())

    def create_scheduling(self) -> Schedule:
        """调用 GA 生成当前 Job 列表的 schedule。"""
        schedule = self.platform.part_of_estimate_of_reactive_aps()
        self.data_copy_aps(schedule)
        return schedule

    def data_copy_aps(self, schedule: Schedule) -> None:
        """把当前 schedule 保存到 Emulator，模拟原系统的状态保存。"""
        self.emulator.record_current_data(schedule)

    def make_offer(self, order_job: Job) -> Offer | None:
        """对 S1 的外注候补 Job 进行试排产，并生成纳期/价格 offer。"""
        self.platform.present_order_job_name = order_job.job_name
        self.platform.present_due_date = order_job.order_due_date
        self.platform.present_price = order_job.job_price
        self.platform.present_penalty = order_job.penalty

        # 暂时把外注 Job 插入本供应商已有 Job 中，再用 GA 重新排产。
        candidate = order_job.copy_for_outsourcing()
        previous_jobs = list(self.platform.job_instance_list)
        self.platform.job_instance_list = previous_jobs + [candidate]
        schedule = self.create_scheduling()
        scheduled_job = schedule.job_by_name(candidate.job_name)

        self.make_value(order_job, scheduled_job)
        if self.delta_profit <= 0:
            # 如果接单后没有正收益，就撤回试插入的 Job，并返回 None。
            self.platform.job_instance_list = previous_jobs
            self.create_scheduling()
            return None
        return Offer(
            supplier_name=self.name,
            job_name=order_job.job_name,
            due_date=self.bid_due_date,
            price=self.bid_price,
        )

    def make_value(self, order_job: Job, scheduled_job: Job) -> None:
        """根据试排产结果计算报价和供应商收益。"""
        aggravation_cost = max(0, scheduled_job.client_due_date - order_job.order_due_date) * order_job.penalty
        discount_amount = self.specialty_discount_for(order_job)
        self.bid_due_date = scheduled_job.client_due_date
        self.bid_price = int(
            order_job.job_cost * (1 + self.mark_up)
            - discount_amount
            + self.transportation_cost
            + aggravation_cost
        )
        self.delta_profit = self.bid_price - order_job.job_cost - aggravation_cost

    def specialty_discount_for(self, order_job: Job) -> int:
        """给特定 Job 编号的订单折扣，用来让 S2/S3 的选择结果产生差异。"""
        if self.specialty_job_remainder is None:
            return 0
        match = re.search(r"(\d+)$", order_job.job_name)
        if not match:
            return 0
        job_number = int(match.group(1))
        if job_number % 2 == self.specialty_job_remainder:
            return self.specialty_discount
        return 0

    def contraction(self) -> None:
        """外注契约成立后，确认并保存最新 schedule。"""
        latest = self.emulator.latest_schedule
        if latest:
            self.data_copy_aps(latest)

    def rejection(self, job_name: str) -> None:
        """没有中标的供应商撤销试插入的外注 Job。"""
        self.platform.delete_job_aps(job_name)
        self.create_scheduling()


@dataclass(slots=True)
class OutsourcingSupplier(Supplier):
    """委托元供应商 S1。负责向 S2/S3 请求 offer，并决定外注先。"""

    outsources: list[Supplier] = field(default_factory=list)
    customer: Client | None = None
    random_seed: int = 13

    def negotiation_process(self, new_job_information: Job) -> tuple[Offer | None, list[Offer]]:
        """旧版 FCFS/EDD 用的单一 Job 外注交涉流程。"""
        t_order = self.send_order(new_job_information)
        self.parallel_reactive_scheduling()
        t_offers = self.get_offer(new_job_information)
        winner = self.comparison(t_offers, new_job_information)

        if winner is None:
            self.platform.canceled_outsourcing_list.append(new_job_information.job_name)
            return None, t_offers

        assert isinstance(self.platform, SchedulePlatformForOutsourcing)
        self.platform.apply_outsourcing_contract(new_job_information, winner)
        for supplier in self.outsources:
            if supplier.name == winner.supplier_name:
                supplier.make_offer(new_job_information)
                supplier.contraction()
            else:
                supplier.rejection(new_job_information.job_name)
        return winner, t_offers

    def apply_contract(self, job: Job, winner: Offer) -> None:
        """外注成立后，同时更新 S1 和中标供应商的状态。"""
        assert isinstance(self.platform, SchedulePlatformForOutsourcing)
        self.platform.apply_outsourcing_contract(job, winner)
        for supplier in self.outsources:
            if supplier.name == winner.supplier_name:
                supplier.make_offer(job)
                supplier.contraction()
            else:
                    supplier.rejection(job.job_name)

    def send_order(self, job: Job) -> Job:
        """把 S1 的 Job 复制成外注订单。"""
        return job.copy_for_outsourcing()

    def parallel_reactive_scheduling(self) -> None:
        """让所有外注候补供应商先根据当前 Job 列表排产。"""
        for supplier in self.outsources:
            supplier.create_scheduling()

    def get_offer(self, job: Job) -> list[Offer]:
        """对一个 Job，同时向 S2/S3 请求 offer。"""
        if not self.outsources:
            return []

        # 为每个外注供应商生成一个独立任务，例如 [(S2, job), (S3, job)]。
        tasks = [(supplier, job) for supplier in self.outsources]

        # 用多进程同时计算 S2/S3 的 GA 试排产和 offer。
        with ProcessPoolExecutor(max_workers=len(self.outsources)) as executor:
            offers = list(executor.map(_make_offer_in_process, tasks))

        # 不能接单的供应商会返回 None，只保留有效 offer。
        return [offer for offer in offers if offer is not None]

    def get_offers_for_jobs(self, jobs: list[Job]) -> dict[str, list[Offer]]:
        """对多个候补 Job 批量取得 S2/S3 的 offer，Greedy/Tabu 用。"""
        offers_by_job = {job.job_name: [] for job in jobs}
        if not jobs or not self.outsources:
            return offers_by_job

        tasks = [
            (supplier, job)
            for job in jobs
            for supplier in self.outsources
        ]
        with ProcessPoolExecutor(max_workers=len(self.outsources)) as executor:
            offers = list(executor.map(_make_offer_in_process, tasks))

        for offer in offers:
            if offer is not None:
                offers_by_job[offer.job_name].append(offer)
        return offers_by_job

    def check_match_order(self, an_order: Job, offers: list[Offer]) -> list[Offer]:
        """检查每个 offer 是否满足 S1 的纳期和价格要求。"""
        for offer in offers:
            offer.feasible_due_date = offer.due_date <= an_order.order_due_date
            offer.feasible_price = offer.price <= an_order.job_price
        return offers

    def best_offer(self, offers: list[Offer], key_name: str) -> list[Offer]:
        """在多个 offer 中按纳期或价格筛选最优集合。"""
        if key_name == "due_date":
            best = min(offer.due_date for offer in offers)
            return [offer for offer in offers if offer.due_date == best]
        if key_name == "price":
            best = min(offer.price for offer in offers)
            return [offer for offer in offers if offer.price == best]
        raise ValueError(key_name)

    def comparison(self, offers: list[Offer], an_order: Job) -> Offer | None:
        """旧版单一 Job 交涉中，从 S2/S3 offer 中选择最终外注先。"""
        checked = self.check_match_order(an_order, offers)
        feasible = [offer for offer in checked if offer.feasible]
        if not feasible:
            return None

        beneficial = [
            offer
            for offer in feasible
            if self.loss_reduction_for_offer(an_order, offer) > 0
        ]
        if not beneficial:
            return None

        best_delta = max(
            self.loss_reduction_for_offer(an_order, offer)
            for offer in beneficial
        )
        best_offers = [
            offer
            for offer in beneficial
            if self.loss_reduction_for_offer(an_order, offer) == best_delta
        ]
        best_offers = self.best_offer(best_offers, "due_date")
        best_offers = self.best_offer(best_offers, "price")
        return random.Random(self.random_seed).choice(best_offers)

    def best_greedy_evaluation(
        self,
        jobs: list[Job],
    ) -> tuple[CandidateEvaluation | None, dict[str, list[Offer]]]:
        """Greedy/Tabu 共通：评价所有候补 Job × Supplier，选 ΔL 最大的方案。"""
        offers_by_job = self.get_offers_for_jobs(jobs)
        evaluations: list[CandidateEvaluation] = []

        for job in jobs:
            checked = self.check_match_order(job, offers_by_job[job.job_name])
            for offer in checked:
                if not offer.feasible:
                    continue
                loss_reduction = self.loss_reduction_for_offer(job, offer)
                if loss_reduction > 0:
                    evaluations.append(
                        CandidateEvaluation(
                            job=job,
                            offer=offer,
                            loss_reduction=loss_reduction,
                        )
                    )

        if not evaluations:
            return None, offers_by_job

        best = max(
            evaluations,
            key=lambda item: (
                item.loss_reduction,
                -item.offer.due_date,
                -item.offer.price,
                item.job.job_name,
            ),
        )
        return best, offers_by_job

    def tabu_jobs(
        self,
        candidates: list[Job],
        rng: random.Random,
        tabu_length: int = 2,
        tabu_cooldown: dict[str, int] | None = None,
    ) -> set[str]:
        """生成本轮 tabu list，并避开仍在 tabu tenure 冷却期内的 Job。"""
        if len(candidates) <= tabu_length:
            return set()
        tabu_cooldown = tabu_cooldown or {}
        selectable = [
            job for job in candidates if job.job_name not in tabu_cooldown
        ]
        if not selectable:
            return set()
        sample_size = min(tabu_length, len(selectable))
        return {job.job_name for job in rng.sample(selectable, k=sample_size)}

    def loss_reduction_for_offer(self, job: Job, offer: Offer) -> int:
        """当前简化版 ΔL：S1 自己生产损失 - 外注后损失。"""
        local_loss = max(0, job.client_due_date - job.order_due_date) * job.penalty
        outsourcing_loss = (
            max(0, offer.due_date - job.order_due_date) * job.penalty
            + offer.price
        )
        return local_loss - outsourcing_loss

    def outsourcing_negotiation(self, max_contracts: int = 12) -> list[dict[str, object]]:
        """按照 Greedy 或 Tabu Search 反复选择外注 Job。"""
        assert isinstance(self.platform, SchedulePlatformForOutsourcing)
        decisions: list[dict[str, object]] = []
        rng = random.Random(self.random_seed)
        # tabu_tenure=3 表示一个 Job 进入 tabu list 后，之后 3 轮不能再次进入。
        tabu_tenure = 3
        tabu_cooldown: dict[str, int] = {}

        for round_index in range(max_contracts):
            rule = self.platform.outsourcing_flag.upper()
            if rule in {"GREEDY", "TABU"}:
                candidates = self.platform.outsourcing_list("FCFS")
                if not candidates:
                    break
                tabu_jobs: set[str] = set()
                search_candidates = candidates
                if rule == "TABU" and round_index < 10:
                    # 前 10 轮使用 Tabu Search：随机排除 tabu list 内的 Job。
                    tabu_jobs = self.tabu_jobs(candidates, rng, tabu_cooldown=tabu_cooldown)
                    search_candidates = [
                        job for job in candidates if job.job_name not in tabu_jobs
                    ]
                if not search_candidates:
                    break

                # 对剩余候补 Job 计算所有 offer，并选择 ΔL 最大的外注方案。
                best, offers_by_job = self.best_greedy_evaluation(search_candidates)
                if best is None:
                    for job in search_candidates:
                        self.platform.canceled_outsourcing_list.append(job.job_name)
                    break

                self.apply_contract(best.job, best.offer)
                decisions.append(
                    {
                        "job_name": best.job.job_name,
                        "winner": best.offer.supplier_name,
                        "winner_due_date": best.offer.due_date,
                        "winner_price": best.offer.price,
                        "loss_reduction": best.loss_reduction,
                        "tabu_jobs": ", ".join(sorted(tabu_jobs)),
                        "candidate_count": len(candidates),
                        "evaluated_count": len(search_candidates),
                        "offers": offers_by_job[best.job.job_name],
                    }
                )
                self.create_scheduling()
                if rule == "TABU" and round_index < 10:
                    # 每轮结束后更新 tabu tenure：旧记录减 1，本轮 tabu Job 设为 3。
                    tabu_cooldown = {
                        job_name: remaining - 1
                        for job_name, remaining in tabu_cooldown.items()
                        if remaining > 1
                    }
                    for job_name in tabu_jobs:
                        tabu_cooldown[job_name] = tabu_tenure
                continue

            candidate = self.platform.select_outsourcing_job()
            if candidate is None:
                break
            winner, offers = self.negotiation_process(candidate)
            decisions.append(
                {
                    "job_name": candidate.job_name,
                    "winner": winner.supplier_name if winner else "NoContract",
                    "winner_due_date": winner.due_date if winner else "",
                    "winner_price": winner.price if winner else "",
                    "loss_reduction": self.loss_reduction_for_offer(candidate, winner) if winner else "",
                    "offers": offers,
                }
            )
            self.create_scheduling()
        return decisions

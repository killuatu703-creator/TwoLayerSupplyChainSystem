from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
import math
import os
import random
import re
from dataclasses import dataclass, field

from .emulator import Emulator
from .models import Job, Offer, OrderInformation, Schedule
from .schedule_platform import SchedulePlatform, SchedulePlatformForOutsourcing


def _make_offer_in_process(task: tuple["Supplier", Job]) -> Offer | None:
    # 多进程并列计算 offer 时使用的辅助函数。
    #
    # ProcessPoolExecutor 不能直接并列调用带有 self 的复杂方法，
    # 所以这里把一个任务整理成 (供应商对象, 候补 Job) 的形式。
    # 子进程收到任务后，会让对应的 S2/S3 执行 make_offer(job)，
    # 也就是把该 Job 临时插入自己的 schedule，并用 GA 重新排产后返回报价。
    #
    # task 的内容类似 (S2, S1J05) 或 (S3, S1J05)。每个任务可以交给不同进程独立计算。
    supplier, job = task
    return supplier.make_offer(job)


@dataclass(slots=True)
class CandidateEvaluation:
    # 一个候补外注方案的评价结果。
    #
    # 这里的“一个方案”指的是某个 Job 外注给某个 Supplier，
    # 例如「S1J05 -> S2」。同一个 Job 给 S2 和给 S3 会被看作两个不同方案。
    # loss_reduction 保存该方案对应的 ΔL，数值越大表示越值得外注。
    #

    job: Job
    offer: Offer
    loss_reduction: int


@dataclass(slots=True)
class Client:
    # 客户角色。
    #
    # 当前 Python 版中 Client 逻辑被简化：它主要负责把 Excel 中读取到的订单交给 S1。
    # 原论文中 Client 会按照订单投入间隔逐次投入订单，并参与更复杂的交涉，
    # 这部分目前还没有完整复现。
    #

    name: str
    job_information_oc: list[OrderInformation]
    permit_t: float = 0.0
    permit_p: float = 0.0
    mitigate_t: float = 0.1
    mitigate_p: float = 0.1

    def construction_of_scm(self, supplier: "OutsourcingSupplier") -> Schedule:
        # 把 Client 持有的所有订单交给 S1，并生成 S1 的初期 schedule。
        #
        # 这里是当前模型的起点：S1 先接收全部订单并排产，
        # 之后才根据生产停止和纳期迟れ情况判断哪些 Job 需要考虑外注。
        #
        for order in self.job_information_oc:
            supplier.receive_order(order)
        return supplier.create_scheduling()


@dataclass(slots=True)
class Supplier:
    # 普通供应商角色，对应模型中的 S2/S3。
    #
    # S2/S3 自己也有初期 Job，因此收到 S1 的外注请求时，
    # 不是单独计算这个外注 Job，而是把外注 Job 临时插入自己的现有 Job 集合，
    # 再用 GA 重新排产。根据重新排产后的完成时间和成本生成 offer。
    #

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
        # 接收一个订单，并转换成 Job 加入自己的排产平台。
        #
        # 对 S1 来说，这是接收 Client 的订单；
        # 对 S2/S3 来说，这是读取初期 Job 时使用的入口。
        #
        self.platform.add_job_aps(order.to_job())

    def create_scheduling(self) -> Schedule:
        # 调用 GA 生成当前 Job 列表的 schedule。
        schedule = self.platform.part_of_estimate_of_reactive_aps()
        self.data_copy_aps(schedule)
        return schedule

    def data_copy_aps(self, schedule: Schedule) -> None:
        # 把当前 schedule 保存到 Emulator，模拟原系统的状态保存。
        self.emulator.record_current_data(schedule)

    def make_offer(self, order_job: Job) -> Offer | None:
        # 对 S1 的外注候补 Job 进行试排产，并生成 offer。
        #
        # 处理步骤：
        # 1. 先保存 S1 对该 Job 的希望纳期、价格和罚金信息。
        # 2. 把这个 Job 临时加入 S2/S3 自己的 Job 列表。
        # 3. 用 GA 重新排产，得到如果接单时的完成时间。
        # 4. 根据完成时间、成本、运输费和折扣计算报价。
        # 5. 如果接单后供应商收益不为正，就返回 None，表示不报价。
        #
        self.platform.present_order_job_name = order_job.job_name
        self.platform.present_due_date = order_job.order_due_date
        self.platform.present_price = order_job.job_price
        self.platform.present_penalty = order_job.penalty

        # 这里是“试算”而不是正式接单：先临时插入外注 Job，看加入后 schedule 会变成什么样。
        candidate = order_job.copy_for_outsourcing()
        previous_jobs = list(self.platform.job_instance_list)
        self.platform.job_instance_list = previous_jobs + [candidate]
        schedule = self.create_scheduling()
        scheduled_job = schedule.job_by_name(candidate.job_name)

        self.make_value(order_job, scheduled_job)
        if self.delta_profit <= 0:
            # 如果接单后没有正收益，就撤回刚才临时插入的 Job，并返回 None，表示该供应商不参加这次报价。
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
        # 根据试排产结果计算报价和供应商收益。
        #
        # bid_due_date 是供应商在试排产中得到的完成时间。
        # bid_price 是供应商给 S1 的报价，当前简化为：
        # 原 Job 成本 × 加价率 + 运输费 + 因迟れ产生的追加成本 - 特定折扣。
        # delta_profit 用来判断供应商接这个外注 Job 是否有正收益。
        #
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
        # 根据 Job 编号给特定供应商折扣。
        #
        # 这是当前实验中为了让 S2/S3 的报价出现差异而加入的简化设定。
        # 例如可以让 S2 对偶数编号 Job 更便宜，S3 对奇数编号 Job 更便宜，
        # 这样 Greedy、Tabu、SA 的选择结果更容易产生差异。
        #
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
        # 外注契约成立后的确认处理。
        #
        # 中标供应商在 make_offer 阶段已经把外注 Job 临时插入并排产，
        # 如果 S1 最终选择了该 offer，就把这次试排产结果正式保存下来。
        #
        latest = self.emulator.latest_schedule
        if latest:
            self.data_copy_aps(latest)

    def rejection(self, job_name: str) -> None:
        # 未中标供应商的撤销处理。
        #
        # S2/S3 在报价时都可能临时插入了外注 Job。
        # 如果该供应商没有中标，就需要把这个 Job 从自己的平台中删除，
        # 然后重新排产，恢复到没有接这个外注订单的状态。
        #
        deleted = self.platform.delete_job_aps(job_name)
        if deleted is not None:
            self.create_scheduling()


@dataclass(slots=True)
class OutsourcingSupplier(Supplier):
    # 委托元供应商角色，对应模型中的 S1。
    #
    # S1 既是一个可以自己生产的供应商，也是外注交涉的发起方。
    # 当生产停止导致部分 Job 可能纳期迟れ时，S1 会向 S2/S3 请求 offer，
    # 然后使用 Greedy、Tabu Search 或 SA 选择要外注的 Job 和外注对象。
    #

    outsources: list[Supplier] = field(default_factory=list)
    customer: Client | None = None
    random_seed: int = 13

    def negotiation_process(self, new_job_information: Job) -> tuple[Offer | None, list[Offer]]:
        # 旧版 FCFS/EDD 使用的单一 Job 外注交涉流程。
        #
        # 这个方法一次只处理一个候补 Job：
        # S1 发送订单 -> S2/S3 报价 -> S1 检查 offer -> 成立或取消。
        # 现在主要保留作比较，Greedy/Tabu/SA 使用的是后面的批量评价流程。
        #
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
        # 外注成立后，同时更新 S1 和 S2/S3 的状态。
        #
        # S1 侧：把该 Job 从自己要加工的 Job 列表中移除，并记录外注费用和外注对象。
        # 中标供应商侧：确认刚才的试排产结果。
        # 未中标供应商侧：撤销报价时临时插入的 Job。
        #
        assert isinstance(self.platform, SchedulePlatformForOutsourcing)
        self.platform.apply_outsourcing_contract(job, winner)
        for supplier in self.outsources:
            if supplier.name == winner.supplier_name:
                supplier.make_offer(job)
                supplier.contraction()
            else:
                supplier.rejection(job.job_name)

    def send_order(self, job: Job) -> Job:
        # 把 S1 的 Job 复制成外注订单。
        return job.copy_for_outsourcing()

    def parallel_reactive_scheduling(self) -> None:
        # 让所有外注候补供应商先根据当前 Job 列表排产。
        for supplier in self.outsources:
            supplier.create_scheduling()

    def get_offer(self, job: Job) -> list[Offer]:
        # 对单个候补 Job，同时向所有外注候补供应商请求 offer。
        #
        # 这里使用 ProcessPoolExecutor，把 S2 和 S3 的试排产计算拆到多个进程中。
        # 返回值是有效 offer 的列表；不能接单或收益不为正的供应商会返回 None，
        # 最后会被过滤掉。
        #
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
        # 对多个候补 Job 批量取得 S2/S3 的 offer。
        #
        # Greedy、Tabu Search、SA 都需要比较多个「Job × Supplier」组合，
        # 所以这里会把任务展开成：
        # (S2, S1J01), (S3, S1J01), (S2, S1J02), (S3, S1J02) ...
        # 然后用多进程并列计算所有试排产结果。
        # 返回值按 job_name 分组，方便后续计算每个 Job 的 ΔL。
        #
        offers_by_job = {job.job_name: [] for job in jobs}
        if not jobs or not self.outsources:
            return offers_by_job

        tasks = [
            (supplier, job)
            for job in jobs
            for supplier in self.outsources
        ]
        worker_count = min(len(tasks), os.cpu_count() or len(self.outsources))
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            offers = list(executor.map(_make_offer_in_process, tasks))

        for offer in offers:
            if offer is not None:
                offers_by_job[offer.job_name].append(offer)
        return offers_by_job

    def check_match_order(self, an_order: Job, offers: list[Offer]) -> list[Offer]:
        # 检查每个 offer 是否满足 S1 的基本接受条件。
        #
        # 当前简化版只检查两个条件：
        # 1. 供应商承诺的完成时间不能晚于 S1 对该 Job 的订单纳期。
        # 2. 供应商报价不能高于 S1 从 Client 那里获得的价格。
        # 这两个条件都满足时，offer.feasible 才会变为 True。
        #
        for offer in offers:
            offer.feasible_due_date = offer.due_date <= an_order.order_due_date
            offer.feasible_price = offer.price <= an_order.job_price
        return offers

    def best_offer(self, offers: list[Offer], key_name: str) -> list[Offer]:
        # 在多个 offer 中按纳期或价格筛选最优集合。
        if key_name == "due_date":
            best = min(offer.due_date for offer in offers)
            return [offer for offer in offers if offer.due_date == best]
        if key_name == "price":
            best = min(offer.price for offer in offers)
            return [offer for offer in offers if offer.price == best]
        raise ValueError(key_name)

    def comparison(self, offers: list[Offer], an_order: Job) -> Offer | None:
        # 旧版单一 Job 交涉中，从 S2/S3 offer 中选择最终外注先。
        #
        # 选择逻辑是：先筛掉不可行 offer，再筛掉 ΔL <= 0 的 offer。
        # 如果还有多个候补，就先选 ΔL 最大的，再用更早纳期和更低价格作为 tie-break。
        #
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

    def candidate_evaluations(
        self,
        jobs: list[Job],
    ) -> tuple[list[CandidateEvaluation], dict[str, list[Offer]]]:
        # 评价所有「候补 Job × Supplier」组合，并返回 ΔL 为正的方案。
        #
        # ΔL 表示“如果把这个 Job 外注出去，S1 的损失能减少多少”。
        # 只有 offer 可行，并且 ΔL > 0 时，才说明这个外注方案值得考虑。
        #
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
        return evaluations, offers_by_job

    def best_greedy_evaluation(
        self,
        jobs: list[Job],
    ) -> tuple[CandidateEvaluation | None, dict[str, list[Offer]]]:
        # Greedy/Tabu 共用的选择函数：评价所有候补方案，并选 ΔL 最大的方案。
        #
        # Greedy 会直接对所有候补 Job 使用这个函数；
        # Tabu Search 会先排除 tabu list 中的 Job，再对剩下的候补使用这个函数。
        #
        evaluations, offers_by_job = self.candidate_evaluations(jobs)
        if not evaluations:
            return None, offers_by_job
        return self.best_by_loss_reduction(evaluations), offers_by_job

    def best_by_loss_reduction(
        self, evaluations: list[CandidateEvaluation]
    ) -> CandidateEvaluation:
        # 从多个候补方案中选择最终方案。
        #
        # 第一优先级是 ΔL 最大，也就是 S1 的损失削减量最大。
        # 如果 ΔL 相同，则优先选择完成时间更早、报价更低的 offer。
        # 最后再用 Job 名称作为固定排序条件，保证结果容易复现。
        #
        return max(
            evaluations,
            key=lambda item: (
                item.loss_reduction,
                -item.offer.due_date,
                -item.offer.price,
                item.job.job_name,
            ),
        )

    def simulated_annealing_evaluation(
        self,
        evaluations: list[CandidateEvaluation],
        rng: random.Random,
        initial_temperature: float = 5000.0,
        cooling_rate: float = 0.85,
        iterations: int = 30,
    ) -> CandidateEvaluation:
        # SA 外注选择逻辑：用模拟退火在候补方案中搜索较好的外注方案。
        # 这里的一个“方案”就是 (Job, Supplier)，例如 S1J05 外注给 S2。
        # current 表示当前正在看的方案，neighbor 表示随机挑出的另一个候补方案。
        # 如果 neighbor 的 ΔL 更大，说明损失削减量更大，所以一定接受。
        # 如果 neighbor 的 ΔL 更小，说明它暂时更差，但 SA 会按照 exp(delta / temperature) 的概率接受。
        # temperature 越高，接受差方案的概率越高；temperature 逐轮乘以 cooling_rate，因此后期越来越保守。
        # 这样做的意义是：不要每次都像 Greedy 一样只选眼前最大 ΔL，而是允许探索其他可能路径。
        # 最后返回搜索过程中出现过的最佳方案 best。
        #
        current = rng.choice(evaluations)
        best = current
        temperature = initial_temperature

        for _ in range(iterations):
            neighbor = rng.choice(evaluations)
            delta = neighbor.loss_reduction - current.loss_reduction
            if delta >= 0:
                current = neighbor
            else:
                accept_probability = math.exp(delta / max(temperature, 1e-9))
                if rng.random() < accept_probability:
                    current = neighbor
            if current.loss_reduction > best.loss_reduction:
                best = current
            temperature *= cooling_rate

        # 注意：这里不强行退回到 Greedy 的全局最大 ΔL，而是使用 SA 搜索过程中遇到的最佳方案。
        return best

    def tabu_jobs(
        self,
        candidates: list[Job],
        rng: random.Random,
        tabu_length: int = 2,
        tabu_cooldown: dict[str, int] | None = None,
    ) -> set[str]:
        # 生成本轮 tabu list。
        # tabu list 中保存的是 Job 名称，不是 (Job, Supplier) 的组合。
        # 进入 tabu list 的 Job 在本轮不会被评价，也就是 S2/S3 都不会对它进行 offer 比较。
        # tabu_cooldown 用来避免同一个 Job 在短时间内反复进入 tabu list。
        # 当前设定中 tabu_length=2，表示每轮随机排除 2 个候补 Job。
        #
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
        # 计算当前简化版 ΔL。
        #
        # local_loss：S1 自己继续生产该 Job 时，因为纳期迟れ产生的罚金。
        # outsourcing_loss：外注后仍可能产生的迟れ罚金 + 支付给外注供应商的价格。
        # ΔL = local_loss - outsourcing_loss。
        # 如果 ΔL > 0，表示外注后 S1 的损失减少，因此该外注方案有意义。
        #
        local_loss = max(0, job.client_due_date - job.order_due_date) * job.penalty
        outsourcing_loss = (
            max(0, offer.due_date - job.order_due_date) * job.penalty
            + offer.price
        )
        return local_loss - outsourcing_loss

    def outsourcing_negotiation(self, max_contracts: int = 12) -> list[dict[str, object]]:
        # 按照指定规则反复进行外注交涉。
        # 一轮外注交涉的流程是：
        # 1. 从 S1 当前 schedule 中生成外注候补 Job。
        # 2. 对每个候补 Job，让 S2/S3 分别用 GA 试排产并返回 offer。
        # 3. 对每个 offer 计算 ΔL，只保留可行且 ΔL > 0 的方案。
        # 4. 根据 Greedy、Tabu Search 或 SA 选择本轮要执行的外注方案。
        # 5. 更新 S1/S2/S3 的 schedule，然后进入下一轮。
        # Greedy：直接选择 ΔL 最大的方案。
        # Tabu：先排除 tabu list 中的 Job，再选择 ΔL 最大的方案。
        # SA：在候补方案中进行模拟退火搜索，允许一定概率接受较差方案。
        #
        assert isinstance(self.platform, SchedulePlatformForOutsourcing)
        decisions: list[dict[str, object]] = []
        rng = random.Random(self.random_seed)
        # tabu_tenure=3 表示一个 Job 进入 tabu list 后，之后 3 轮不能再次进入。
        tabu_tenure = 3
        tabu_cooldown: dict[str, int] = {}

        for round_index in range(max_contracts):
            rule = self.platform.outsourcing_flag.upper()
            if rule in {"GREEDY", "TABU", "SA"}:
                candidates = self.platform.outsourcing_list("FCFS")
                if not candidates:
                    break
                tabu_jobs: set[str] = set()
                search_candidates = candidates
                if rule == "TABU" and round_index < 10:
                    # 前 10 轮使用 Tabu Search：先随机生成 tabu list，本轮不评价这些 Job。
                    tabu_jobs = self.tabu_jobs(candidates, rng, tabu_cooldown=tabu_cooldown)
                    search_candidates = [
                        job for job in candidates if job.job_name not in tabu_jobs
                    ]
                if not search_candidates:
                    break

                # 对剩余候补 Job 计算所有 S2/S3 offer，然后根据当前 rule 选择本轮外注方案。
                # offers_by_job 会保存每个 Job 收到的全部报价，最后写入 outsourcing_decisions_*.csv 方便检查交涉过程。
                if rule == "SA":
                    evaluations, offers_by_job = self.candidate_evaluations(search_candidates)
                    if not evaluations:
                        for job in search_candidates:
                            self.platform.canceled_outsourcing_list.append(job.job_name)
                        break
                    best = self.simulated_annealing_evaluation(evaluations, rng)
                else:
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
                    # 每轮结束后更新 tabu tenure：
                    # 1. 旧的冷却记录剩余轮数减 1，变成 0 的记录删除。
                    # 2. 本轮进入 tabu list 的 Job，以及本轮实际外注的 Job，接下来 3 轮内不再进入 tabu list。
                    tabu_cooldown = {
                        job_name: remaining - 1
                        for job_name, remaining in tabu_cooldown.items()
                        if remaining > 1
                    }
                    for job_name in tabu_jobs | {best.job.job_name}:
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

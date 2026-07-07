from __future__ import annotations

from dataclasses import dataclass
import random

from .models import Job, Schedule


@dataclass(slots=True)
class GAParameter:
    # GA 参数。当前实验中 main.py 会覆盖其中一部分数值。

    population_size: int = 30
    generations: int = 60
    mutation_rate: float = 0.15
    crossover_rate: float = 0.8
    due_weight: float = 1.0
    flow_time_weight: float = 0.1
    seed: int = 7


class GA2003:
    # 简化版 GA 排产器，参考原 Smalltalk 系统中的 GA2003 类。
    # 这里的 GA 不是决定“外注给谁”的算法，而是给 S1/S2/S3 各自做生产排产。
    # 外注选择算法会多次调用 S2/S3 的 GA，试算“如果接这个外注 Job，schedule 会变成什么样”。

    def __init__(self, parameter: GAParameter | None = None) -> None:
        # 保存 GA 参数，并用 seed 固定随机数。
        self.parameter = parameter or GAParameter()
        self.random = random.Random(self.parameter.seed)

    def evolve_for_reactive_aps(self, platform: "SchedulePlatform", jobs: list[Job]) -> Schedule:
        # 执行 GA 搜索，最后返回评价值最好的 schedule。
        # 基本流程是：生成初期集団 -> 选择亲代 -> 交叉 -> 变异 -> 评价 -> 更新最优解。
        # 当前模型中，一个个体就是一种 Job 加工顺序，例如 [S1J03, S1J01, S1J02]。
        if not jobs:
            return platform.build_schedule([])

        # 染色体在这里表示一个 Job 加工顺序。
        # 注意：Job 内部的 operation 顺序不会被 GA 改变，GA 只改变多个 Job 之间的投入顺序。
        # 每次评价染色体时，platform.build_schedule() 会把这个 Job 顺序展开成具体的甘特图 schedule。
        population = self.create_parent_generation(jobs)
        best = min(population, key=lambda seq: self.evaluate_aps(platform, seq))

        for _ in range(self.parameter.generations):
            children: list[list[Job]] = []
            while len(children) < self.parameter.population_size:
                # 选择两个亲代，再通过交叉和变异生成子代。
                # 亲代是当前集団中评价值比较好的 Job 顺序；子代是下一代要尝试的新 Job 顺序。
                # 交叉负责组合两个顺序的特征，变异负责随机交换 Job，避免搜索范围太窄。
                parent1 = self.tournament(platform, population)
                parent2 = self.tournament(platform, population)
                if self.random.random() < self.parameter.crossover_rate:
                    child = self.order_crossover(parent1, parent2)
                else:
                    child = list(parent1)
                self.mutate(child)
                children.append(child)

            population = children
            # 每一代都保存当前最好的 Job 顺序。
            # 评价值越小越好；如果 current_best 比历史 best 更小，就更新 best。
            current_best = min(population, key=lambda seq: self.evaluate_aps(platform, seq))
            if self.evaluate_aps(platform, current_best) < self.evaluate_aps(platform, best):
                best = current_best

        return platform.build_schedule(best)

    def create_parent_generation(self, jobs: list[Job]) -> list[list[Job]]:
        # 生成初期集団：把 Job 顺序随机打乱多次。
        # population_size 越大，初期候补顺序越多，但计算时间也越长。
        population = []
        for _ in range(self.parameter.population_size):
            seq = list(jobs)
            self.random.shuffle(seq)
            population.append(seq)
        return population

    def tournament(self, platform: "SchedulePlatform", population: list[list[Job]]) -> list[Job]:
        # 锦标赛选择：随机抽几个个体，选择评价值最小的。
        # 这样不会每次都只选全体最优，可以保留一定随机性，同时让好解更容易留下来。
        candidates = self.random.sample(population, k=min(3, len(population)))
        return min(candidates, key=lambda seq: self.evaluate_aps(platform, seq))

    def evaluate_aps(self, platform: "SchedulePlatform", sequence: list[Job]) -> float:
        # 把 Job 顺序转换成 schedule，并计算 GA 目的函数。
        # 当前 GA 目的函数 = 加权纳期迟れ * due_weight + total_flow_time * flow_time_weight。
        # 也就是说，GA 会同时考虑“晚交货的罚金”和“Job 在系统中停留的总时间”。
        schedule = platform.build_schedule(sequence)
        return (
            schedule.weighted_tardiness * self.parameter.due_weight
            + schedule.total_flow_time * self.parameter.flow_time_weight
        )

    def order_crossover(self, parent1: list[Job], parent2: list[Job]) -> list[Job]:
        # 顺序交叉：保留 parent1 的一段，其余位置按 parent2 顺序补齐。
        # 这样可以保证每个 Job 只出现一次，不会产生重复 Job 或缺失 Job 的非法染色体。
        size = len(parent1)
        if size < 2:
            return list(parent1)
        left, right = sorted(self.random.sample(range(size), 2))
        middle = parent1[left:right]
        middle_names = {job.job_name for job in middle}
        rest = [job for job in parent2 if job.job_name not in middle_names]
        return rest[:left] + middle + rest[left:]

    def mutate(self, sequence: list[Job]) -> None:
        # 变异：以一定概率交换两个 Job 的顺序。
        # 变异率太低时容易陷入固定顺序，太高时搜索会变得不稳定。
        if len(sequence) < 2 or self.random.random() >= self.parameter.mutation_rate:
            return
        i, j = self.random.sample(range(len(sequence)), 2)
        sequence[i], sequence[j] = sequence[j], sequence[i]


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schedule_platform import SchedulePlatform

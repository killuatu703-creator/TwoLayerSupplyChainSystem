# 两层供应链系统

这是一个参考 Sugimoto Smalltalk 系统结构写成的 Python 简化版两层供应链模拟项目。

目标是保留论文/原系统里的核心对象和流程：

- `Client`
- `Supplier`
- `OutsourcingSupplier`
- `OrderInformation`
- `Job`
- `Operation`
- `Resource`
- `Emulator`
- `SchedulePlatform`
- `SchedulePlatformForOutsourcing`
- `GA2003`
- `GAParameter`

本版本用于两周 zemi 展示，重点是：

1. 两层供应链基本模型
2. 主供应商和外包供应商都使用 GA 排产
3. 外包候选产品选择支持 `FCFS`、`EDD`、`GREEDY`
4. offer 比较参考原 Smalltalk 逻辑：先检查纳期和价格，再纳期优先、价格次之
5. S1 的生产停止时间段：3000 秒到 20000 秒
6. 输出无外包和外包后的比较结果，并计算 `loss_reduction = L_no_outsource - L_outsource`

## 运行方法

```bash
cd two_layer_supply_chain_system
python3 main.py
```

运行后会在 `results/` 目录输出：

- `summary.csv`
- `outsourcing_decisions.csv`
- `schedule_without_outsourcing.csv`
- `schedule_with_outsourcing.csv`

`summary.csv` 中的 `total_loss` 是简化版损失：

```text
total_loss = total_penalty + outsourcing_charge
```

`loss_reduction` 是论文中的损失削减量 `ΔL` 的简化实现：

```text
loss_reduction = loss_without_outsourcing - total_loss
```

## 项目结构

```text
two_layer_supply_chain_system/
  main.py
  src/two_layer_supply_chain_system/
    models.py
    ga2003.py
    emulator.py
    schedule_platform.py
    actors.py
    result_comparator.py
```

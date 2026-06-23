# TwoLayerSupplyChainSystem

Sugimoto の Smalltalk システムを参考にした，Python 版の簡易二層サプライチェーン外注シミュレーションです。

現在の実装は zemi 用の簡略モデルで，論文の全機能を完全再現したものではありません。主な目的は，S1 が生産停止後に外注候補 Job を選び，S2/S3 が GA で試排産して offer を返し，Greedy / Tabu Search で外注先を比較できるようにすることです。

## 実装内容

- S1 / S2 / S3 の supplier モデル
- S1 の生産停止時間帯: 3000[s] から 20000[s]
- S2 / S3 の初期 Job
- GA による簡易スケジューリング
- ProcessPoolExecutor による S2/S3 offer 計算の試作並列化
- Greedy による外注選択
- Tabu Search による外注選択
- tabu length / tabu tenure / tabu rounds の簡易設定
- summary CSV と schedule CSV の出力
- matplotlib による Gantt chart 出力

## 現在の簡易目的関数

GA の目的関数は次の形です。

```text
weighted_tardiness + flow_time_weight * total_flow_time
```

外注選択では，簡易版の損失削減量 `ΔL` を使っています。

```text
ΔL = local_loss - outsourcing_loss
```

現在の `total_loss` は簡略版で，主に納期遅れペナルティと外注費用を見ています。

```text
total_loss = total_penalty + outsourcing_charge
```

## 実行方法

```bash
python3 main.py
```

実行後，`results/` に CSV と Gantt chart PNG が出力されます。

## 主なファイル

```text
main.py
src/two_layer_supply_chain_system/
  models.py              # Job, Operation, Schedule, Offer などの基本データ
  ga2003.py              # GA による排産
  schedule_platform.py   # Schedule 生成，生産停止，外注候補管理
  actors.py              # Client, Supplier, OutsourcingSupplier, Greedy, Tabu Search
  emulator.py            # Schedule 履歴の保持
  result_comparator.py   # CSV 出力
  gantt_chart.py         # Gantt chart 出力
```

## 注意

論文版との差分として，輸送スケジュール，多段階交渉，SA，完全な損失関数，実験規模の再現などはまだ未実装です。

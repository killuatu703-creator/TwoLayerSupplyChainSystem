# TwoLayerSupplyChainSystem

Sugimoto の Smalltalk システムを参考にした，Python 版の二層サプライチェーン外注シミュレーションのプロトタイプです。

このプロジェクトは，論文システムの全機能を完全に再現したものではなく，zemi / 研究用に段階的に実装している簡略モデルです。現在は，S1 が生産停止後に外注候補 Job を選び，S2/S3 が GA で試排産して offer を返し，Greedy / Tabu Search / SA で外注先を比較できるようにすることを目的としています。

## 現在の位置づけ

```text
論文システムの完全再現
        ↑
現在：外注選択ルール比較用の Python プロトタイプ
        ↑
二層構造，GA排産，offer作成，外注選択，結果出力を実装
```

現在の実装では，論文で扱われている動的注文投入，輸送スケジュール，完全な損失関数などは簡略化しています。
多段階交渉については，納期・価格条件を round ごとに緩和する簡易版を試作実装しています。

## 実装内容

- S1 / S2 / S3 の二層サプライチェーン構造
- S1 の生産停止時間帯: 3000[s] から 20000[s]
- S2 / S3 の初期 Job
- GA による簡易スケジューリング
- S2/S3 による offer 作成
- ProcessPoolExecutor による offer 計算の試作並列化
- Greedy による外注選択
- Tabu Search による外注選択
- Simulated Annealing による外注選択
- 多段階交渉による納期・価格条件の緩和
- summary CSV と schedule CSV の出力
- negotiation history CSV の出力
- matplotlib による Gantt chart 出力
- Excel による初期データ入力

## 外注選択ルール

### Greedy

現在の外注候補 Job と S2/S3 の offer をすべて評価し，損失削減量 `ΔL` が最大となる `(Job, Supplier)` を選択します。

### Tabu Search

候補 Job の一部を tabu list に入れて一時的に評価対象から外し，残りの候補から `ΔL` が最大となる外注先を選択します。

現在の設定:

```text
tabu_length = 2
tabu_tenure = 3
tabu_rounds = 10
random_seed = 13
```

### Simulated Annealing

候補の中から `current` と `neighbor` を random に選び，`ΔL` が改善する場合は受け入れます。悪化する候補も温度に応じた確率で受け入れ，探索中で最も良かった候補を外注先として採用します。

現在の設定:

```text
initial_temperature = 5000.0
cooling_rate = 0.85
iterations = 30
random_seed = 13
```

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

## 多段階交渉

現在の多段階交渉では，S1 が各外注候補 Job について希望条件を設定し，S2/S3 の offer と比較します。

```text
offer_due_date <= requested_due_date
offer_price <= requested_price
```

両方を満たす offer は成立候補になります。条件を満たさない場合，次 round で S1 側の希望条件を緩和します。

```text
requested_due_date = requested_due_date * (1 + mitigate_t)
requested_price = requested_price * (1 + mitigate_p)
```

現在の設定:

```text
mitigate_t = 0.1
mitigate_p = 0.1
num_of_mitigation = 3
```

そのため，最大で `round 0` から `round 3` まで交渉します。各 round の offer は `results/negotiation_history_*.csv` に出力されます。

## Excel 入力

初期データは `input_data.xlsx` から読み込みます。

```text
settings                 # 生産停止時刻，resource 数，GA パラメータ，実行 rule
suppliers                # S1/S2/S3 の mark_up，輸送費，GA seed など
s1_orders                # S1 が受ける Job データ
supplier_initial_orders  # S2/S3 が最初から持つ Job データ
```

Job 数，納期，処理時間，GA パラメータなどを変更したい場合は，Python コードではなく `input_data.xlsx` を編集します。

`s1_orders` と `supplier_initial_orders` では，Job ごとの加工順序を以下の列で設定します。

```text
op1_resource, op1_time, op2_resource, op2_time, ...
```

例:

```text
S1J01: R3 -> R1 -> R4 -> R2
S1J02: R2 -> R4 -> R1 -> R3
```

これにより，現在の入力データは Flow shop 的な固定順序ではなく，Job ごとに加工順序を変えられる Job shop 形式になります。

## 実行方法

```bash
python3 main.py
```

実行後，`results/` に CSV と Gantt chart PNG が出力されます。

主な出力:

```text
results/summary.csv
results/outsourcing_decisions_greedy.csv
results/outsourcing_decisions_tabu.csv
results/outsourcing_decisions_sa.csv
results/negotiation_history_greedy.csv
results/negotiation_history_tabu.csv
results/negotiation_history_sa.csv
results/gantt_without_outsourcing.png
results/gantt_with_outsourcing_greedy.png
results/gantt_with_outsourcing_tabu.png
results/gantt_with_outsourcing_sa.png
results/gantt_s2_after_outsourcing_sa.png
results/gantt_s3_after_outsourcing_sa.png
```

## 主なファイル

```text
main.py
input_data.xlsx
src/two_layer_supply_chain_system/
  models.py              # Job, Operation, Schedule, Offer などの基本データ
  excel_input.py         # Excel 入力の読み込み
  ga2003.py              # GA による排産
  schedule_platform.py   # Schedule 生成，生産停止，外注候補管理
  actors.py              # Client, Supplier, OutsourcingSupplier, Greedy, Tabu, SA
  emulator.py            # Schedule 履歴の保持
  result_comparator.py   # CSV 出力
  gantt_chart.py         # Gantt chart 出力
```

## 論文版との差分

現在の実装では，以下の点をまだ簡略化しています。

- 注文投入タイミングは一括読み込みで，オーダー投入間隔は未再現
- Client-S1 間の中断，再開は未実装
- 納期 / 価格の緩和処理は簡易版
- 輸送スケジュールは未実装
- 論文の完全な損失関数ではなく，簡易版 `total_loss` を使用
- 実験規模は小規模な検証用設定
- SA / Tabu Search のパラメータ検証は今後の課題

## 今後の予定

- SA の温度，冷却率，反復回数の調整
- 実験回数を増やした結果確認
- 動的な注文投入処理の追加
- Client-S1 交渉の中断・再開処理の検討
- 多段階交渉の緩和率と round 数の設定根拠の検討
- 輸送スケジュールと損失関数の精緻化

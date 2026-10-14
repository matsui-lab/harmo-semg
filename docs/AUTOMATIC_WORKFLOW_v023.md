# 一括ハーモナイズ：利用手順と対応範囲 v023

## 今回の変更

`harmo harmonize-batch` で、限定取得、文書・ヘッダーからの意味付け、処理計画、確認票、連続EMG処理、周期曲線、診断的比較、成果物保存までをまとめて実行する。
取得済みデータを入力することもでき、入力・コード・成果物を照合して再開する。機能ごとのコマンドを毎回手動でつなぐ必要はない。
ソフトウェアの一括処理が終わったことと、科学的な比較を承認できることは区別する。

今回の実データ試験では、3研究・6記録を一括評価し、下肢歩行EMGの2記録を16曲線・8比較まで処理した。
さらにURLから4記録を取得して32曲線・48比較に到達した。同じデータセット内の追加記録を含む開発試験であり、未知研究での精度を示すものではない。

## 1. 最初の一括実行

この経路の検証環境はmacOS、Python 3.12、R、PhysioIO/PhysioCore、WFDBである。バッチの排他ロックはPOSIXを必要とする。
Harmo本体と公開データ用依存を導入し、Rのパッケージライブラリを利用可能にする。

```sh
pip install -e '.[public-reuse]'
```

`jobs.json` に版番号付きの公開PhysioNet URLを指定する。複数の場合はオブジェクトを配列へ追加する。

```json
[
  {"url": "https://physionet.org/content/taichidb/1.0.1/"}
]
```

```sh
harmo harmonize-batch --jobs jobs.json --output work \
  --max-records 4 --max-channels 16 --max-comparisons 128 \
  --max-bytes-per-url 134217728 \
  --r-library /absolute/path/to/R/library
```

未インストールの開発環境では `PYTHONPATH=src python -m harmo.cli` を `harmo` の代わりに使える。
読み取り・変換は既存のPhysioIO接続を使い、別の研究専用リーダーを生成しない。

### 取得済みデータを使う

URLの代わりに、既存の一括取得が生成したsummaryを渡す。この場合、新しいダウンロードは行わない。

```json
[
  {"summary": "/absolute/path/to/automatic.summary.json"}
]
```

パスは絶対パスを推奨する。相対パスはjobsファイルの位置ではなくコマンド実行ディレクトリ基準で解決される。

## 2. 結果を確認する

`work/batch-report-v001.json` が全体報告。再実行ごとに連番が増える。各ジョブの `output` が今回使用した処理結果を指す。

| 出力 | 内容 |
|---|---|
| `plan.json` | 記録・筋・側・課題、処理可否、採用／除外イベント、実行する数値処理と入力ハッシュ |
| `processing/diagnostic.json` | 周期曲線・比較・基準変更への感度、確認状態、保留理由 |
| `processing/curve-*.npz` | 個別周期、101点の位相、元波形上の周期開始・終了インデックス |
| `processing/curves.csv` | 記録別の中央値・四分位範囲 |
| `work-queue.json` | 不足する根拠と、その確認・解決に必要な作業 |
| `semantic-review/packet.json` | 原文・ヘッダー・記録範囲を結び付けた意味属性の確認票 |
| `semantic-review/decisions.json` | 未記入の属性確認欄 |
| `event-review.template.json` | 入力表現とイベント対応の未記入確認欄 |
| `completion.json` | 実行入力と成果物のハッシュ。科学的な利用許可ではない |

`processed-with-holds` は診断曲線を生成したが未解決条件がある状態。`held` は対応条件を満たさず処理を止めた状態。
`failed` は入出力・根拠整合性等の実行失敗で、他のジョブは継続する。
この診断経路の終了コード2は保留を伴う終了、1は失敗。`completed-with-holds` や曲線数を承認済み科学的比較と読み替えない。

最大件数での取得は研究全体の完全取得ではない。元の取得・目的判定の状態は `intake_status`、`upstream_assessment_status` に別途残る。
特に従来の全チャンネル候補評価が予算で未完了でも、今回の属性で絞った診断的比較は進められる。未完了を隠して承認するものではない。

## 3. 人の意味確認を後段へつなぐ

属性の確認方法は [v021](AUTOMATIC_WORKFLOW_v021.md) と同じ。原文と適用範囲を確認してdecisionsのconfirm/hold/rejectを記入し、実際の担当者名を入れる。
単に略語を展開するだけではなく、その研究のそのチャンネルを指す根拠を確認する。

```sh
python -m harmo.semantic_review apply \
  --packet /absolute/path/to/semantic-review/packet.json \
  --decisions /absolute/path/to/reviewed-decisions.json \
  --output /absolute/path/to/new-review-receipt
```

次のjobsに `semantic_review` を加え、新しい出力先で一括実行する。

```json
[
  {"summary": "/absolute/path/to/automatic.summary.json",
   "semantic_review": "/absolute/path/to/new-review-receipt/review.receipt.json"}
]
```

確認が筋・側・課題等の不足を解消すれば、その属性を使って処理計画からやり直す。明示的なhold/rejectや資料との矛盾は処理を止める。
原波形、元契約、信頼判定を上書きしない。元資料や確認票が変更されれば古い確認は受け付けない。

## 4. 電圧状態とイベントの対応を確認する

`event-review.template.json` を別ファイルへコピーして編集する。成果物ディレクトリ内の原テンプレートは変更しない。
`reviewer` を実際の担当者名にし、対象の `action`、`value`、`scope_reason`、`evidence` を記入する。

```json
{
  "record_id": "<テンプレート内の記録ID>",
  "field": "event_identity",
  "side": "right",
  "anchor": "lower-component",
  "action": "confirm",
  "value": "initial-contact",
  "scope_reason": "<この記録・側・電気状態に対応すると確認した理由>",
  "evidence": [{
    "path": "/absolute/path/to/source.html",
    "sha256": "<実ファイルのSHA-256>",
    "pointer": "<段落・表・行などの位置>",
    "quote": "<実際の資料に存在する引用>"
  }]
}
```

これは記入形式であって、Tai Chiデータの低い側がinitial-contactであることを意味しない。
イベントの対応値はinitial-contactまたはtoe-off。raw/rectified/envelopeの入力表現も別項目で確認する。
原文の存在とハッシュを機械的に検査するが、引用が主張を本当に支持するかは担当者の責任であり、ソフトウェアは論理的含意を証明しない。
複数の成分を同じ記録・側の同一イベントとして確認する曖昧な指定は拒否する。

jobsへ `event_review` を追加して新しい出力先へ渡す。意味属性の確認も使った場合、その `semantic_review` を保持する。
plan_idが変わる入力・コード・予算・プロファイル変更後は、新しいテンプレートで適用範囲を確認し直す。

確認後は異なる電圧符号化でも同じ確認済みイベントを基準に比較できる。未確認の場合は電気成分別の診断比較を保つ。
イベントの命名確認と、正解ラベルに対する検出精度の検証は別であり、scientific_approvalは有効にならない。
既に包絡化・整流済みと確認した入力を、生EMG用のフィルタへ流すこともない。

## 5. 必要ならローカルLLMを併用する

既存のOllamaサーバーと導入済みローカルモデルを明示する。

```sh
harmo harmonize-batch --jobs jobs.json --output work-with-candidates \
  --local-llm-model qwen3.5:9b --local-llm-port 11439 --max-llm-targets 32
```

モデルはダウンロードしない。クラウドモデルは拒否し、localhostへの候補生成だけを行う。失敗してもルールによる処理は継続する。
LLMの候補は自動確定しない。`local-llm/candidates.json` に問い合わせ対象・未処理数・構造／引用検査を保存する。

人が実際に確認した値との一致を比較する場合は、同じpacketに対応するdecisionsと候補を使う。

```sh
python -m harmo.review_metrics \
  --packet /absolute/path/to/semantic-review/packet.json \
  --decisions /absolute/path/to/reviewed-decisions.json \
  --candidates /absolute/path/to/local-llm/candidates.json \
  --output /absolute/path/to/review-metrics-v001.json
```

ルールとLLMは「モデルが問い合わせを行い、かつ人がconfirmした同一項目」で比較する。未確認項目を正解・不正解に数えない。
これは確認済み部分に限った値の一致率・カバレッジで、独立した正解率ではない。`active_seconds` は人が実作業を測った場合のみ入力し、時間削減を測るには別途対応する比較条件が必要。

## 6. 再開と予算

同じ引数に `--resume` を加える。成功・保留の双方について、入力・コード・全成果物が一致する場合だけ再利用する。
改変された成果物を無視して使用しない。失敗したジョブは再試行し、過去のattemptを残す。設定や確認を変える場合は新しい出力先を使う。
取得後の処理は再開できるが、ダウンロード途中の通信失敗からのバイト単位再開は保証しない。
LLMの失敗は明示的な再開で再試行する。未問い合わせ数が上限で残った場合は上限を増やして別の出力先で実行する。

- 1バッチ最大16ジョブ。既定の取得件数は各URL2記録、容量は各URL128 MiB。容量制限はバッチ全体ではない。
- 既定は各記録最大2,000万セル、各記録16個の解決済みEMGチャンネル、各基準500周期、各ジョブ256曲線・128比較。
- 意味確認票も各記録の最初の16チャンネルまで。全チャンネルが解決済み・確認済みであることを意味せず、省略項目数を報告する。
- 比較は同じデータセット・課題文脈・筋・側・共通イベント基準の組に限定。参加者・介入条件の対応付けや研究間プーリングは行わない。
- 同じサンプルファイルのハッシュを持つ記録同士は比較しない。内容が同じでも別の符号化・ファイル表現になった重複の検出は保証しない。周期上限・比較上限による省略件数を残す。

## 現段階の一般化範囲

研究名に依存しない共通経路だが、万能な自然言語理解・全形式の自動変換ではない。
公開・版番号付きPhysioNetの既存WFDB接続、2〜4状態の離散フットスイッチ、下肢歩行の対応範囲に限定する。
正式筋名の共通語彙は13種類。明示的な文章構文とヘッダー、対応表、または名前付き担当者の範囲確認を必要とする。短い略語、電極番号、筋群から個別筋への推測は自動確定しない。
床反力からのイベント推定、高密度アレイの筋対応、任意課題、PDFの自由解釈、Figshare/Zenodoを含む全リーダーのこの一括経路への接続、研究間の科学的比較は今回完成した範囲に含まれない。

したがって、入力から比較曲線までを動かすソフトウェア上の経路は追加できたが、一般の公開EMGを無確認で科学的に統合できる完成版とはまだ言えない。

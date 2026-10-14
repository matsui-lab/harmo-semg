# PhysioNetでの部分取得・意味付け・確認 v021

## 今回提供する範囲

公開・版番号付きPhysioNet URLから、指定件数の単一ファイルWFDB記録を取得し、PhysioIOで変換、WFDBで再検証して格納する。
索引または限定走査を使い、全体ファイル一覧の完成を待たない。ヘッダーが実際に参照するデータファイルだけを取得する。
この経路での対応はWFDBの16/61/80/212/24/32形式、明示的な単位・ゲイン、1記録最大2,000万セル等の既存制限内である。
全てのPhysioNetデータ・多段WFDB記録・全ファイル形式・認証付き公開データを網羅する意味ではない。

意味付けは二層に分ける。

- 自動：明示的な記録・チャンネル対応表、または対応する限定的な文章構文と長い筋名表記を、原資料のハッシュと記録・チャンネルの範囲付きで結ぶ。現在の文章規則の筋名語彙は前脛骨筋、腓腹筋外側頭、大腿直筋、外側広筋。短いTA等の略語や、電極アレイの番号から個別筋への推定は自動確定しない。
- 半自動：その他の表現は根拠付き確認票へ進み、必要ならローカルLLMで候補を生成する。担当者の確認は記録されるが、単位の再解釈、処理の実行、イベント基準の確認、科学的な利用許可は別の工程である。

研究名の条件分岐ではなく、索引・ヘッダー・文書の構文と表記に基づく処理。ただしこれ自体は、未知研究での正解率を保証しない。

## 1. URLを指定して取得・判定する

HarmoのPython環境、Rscript、PhysioIO/PhysioCoreとWFDB依存ライブラリが利用可能な環境で実行する。
`urls.json` は公開URLの配列。例：

```json
["https://physionet.org/content/taichidb/1.0.1/"]
```

```sh
python -m harmo.cli harmonize --urls urls.json --output work \
  --purpose activation-timing --purpose amplitude-pooling \
  --max-records 2 --max-bytes 134217728 --max-comparisons 128 \
  --r-library /absolute/path/to/R/library
```

複数URLも同じ配列に入れられる。件数は研究ごと、容量はバッチ全体の予算。一般経路には再開キャッシュが保証されていないため、この説明では新しい出力先での初回実行を扱う。
最大件数を省略すると全体一覧を作る従来経路になる。大規模データでは件数上限を指定する。
選択順はRECORDS索引順、索引がなければディレクトリ内の辞書順による深さ優先順であり、ランダム標本や研究全体を代表する標本ではない。

実行結果に示される `work/runs/<run-id>/automatic.summary.json` を確認する。
`selection.json` に選択範囲、`conversion.json` に読取りの数値検証、`purpose-assessment/assessment.json` に目的別の比較・保留が残る。
件数制限・保留時の終了コード2だけをダウンロード失敗と解釈しない。`exported: 0` は解析用の承認済み比較出力がないことを意味し、候補判定件数とは別である。

## 2. 根拠付き確認票を準備する

以下の `<summary>` は前の実行が出した実在の `automatic.summary.json` のパスに置き換える。

```sh
python -m harmo.semantic_review prepare --summary <summary> \
  --output review --max-channels 4
```

`packet.json` に原文、出典ハッシュ、記録・チャンネル、ルールの候補が入る。
`decisions.json` は未記入。既定では各記録の最初の4チャンネルだけを確認対象にし、省略項目数を明示する。
全チャンネルを確認するなら `--max-channels 256` 等、実データに合う値を指定する。
対象項目数は確認票の規模であり、人の作業時間や削減率ではない。

## 3. 必要ならローカルLLMに候補を生成させる

既に利用可能なOllamaのループバックサーバーとインストール済みモデルを使う。このコマンドはモデルをダウンロードせず、クラウドモデルを拒否する。

```sh
python -m harmo.semantic_review propose --packet review/packet.json \
  --output review-llm --model qwen3.5:9b --port 11439 --max-targets 32
```

対象数・入力文字数・生成トークン数・通信時間を制限し、未処理件数を報告する。
`candidates.json`、各問い合わせ・応答、モデル情報を保存する。引用元IDの検証が通っても意味の正解は保証しない。
この候補は契約、波形、信頼判定を変更しない。既存の `harmonize --local-llm-model` は単位確認用であり、この多項目候補生成とは別の経路。

## 4. 人が行う作業

`decisions.json` の対象を原資料と照合し、値と適用範囲を確認する。

1. センサー種別・筋・左右・単位・課題について、原文がその記録／チャンネルを指しているか読む。
2. 確認できたものだけ `action: "confirm"`、正規化した `value`、根拠の `evidence_ids`、適用根拠の `scope_reason` を記入する。
3. 不足は `hold`、誤りは `reject`、未確認は `unreviewed` とする。hold/rejectは再評価で出力を阻止する。既存根拠との矛盾を上書きして解消することはない。
4. `reviewer` に実際の担当者名を記入する。`active_seconds` は実作業を計測した場合のみ入力し、未測定ならnull。ツールの経過時間で代用しない。

例：

```json
{"target_id":"<票にあるID>","action":"confirm","value":"surface-emg",
 "evidence_ids":["p11","header"],"scope_reason":"本文の測定対象と、このヘッダーの筋チャンネルを照合した"}
```

筋名は現在の契約語彙の正式名、左右はleft/right/midline、センサーはsurface-emg/needle-emg/footswitch/electrogoniometer、課題はwalking/running/restがこの解決器の対応範囲。
未対応の活動や解剖学的領域は候補として保存できても自動的に利用可能な契約にはならない。上肢アレイを個別筋に置き換えない。
処理手順や振幅正規化をこの確認票のconfirmで実装済みにすることはできない。そこは検証済み処理・イベント定義を別途用意する必要がある。

## 5. 確認を保存し比較を再実行する

```sh
python -m harmo.semantic_review apply --packet review/packet.json \
  --decisions review/decisions.json --output reviewed
python -m harmo.semantic_review reassess --summary <summary> \
  --review reviewed/review.receipt.json --output reassessed --max-comparisons 128
```

applyは確認記録を保存する操作。元波形や元契約を上書きせず、reassess時に出典と確認ファイルのハッシュを再検証して反映する。
確認後に原資料や確認票が変われば古い確認は受け付けない。確認票を変更する場合は別版の確認記録を作る。
人の確認なしでルールを再評価する場合は `--review` を省略できる。

## 実証したことと残ること

3研究・6記録の部分取得・数値変換を実証。歩行データで26項目の根拠付き属性設定と32件のチャンネル×目的の比較判定に到達した。
科学的な承認済み出力は0件。GRABMyo/Hyserはチャンネル意味付けの保留、歩行データは意味・科学的妥当性の確認、共通イベント／処理条件等の不足が残る。
今回の3研究は開発に使用したため、独立した汎用性検証ではない。
一般のEMGデータを一切の判断なしで解析・統合できる完成版とはまだ言えない。

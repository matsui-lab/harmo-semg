# 根拠付き意味確認ワークスペース v001

## 到達点

固定の研究用モックではなく、既存のsemantic-review/1確認票から、筋・測定側・課題を1項目ずつ表示するローカル画面を生成する。値の修正、原文の参照、根拠IDの選択、対象への適用理由、確認者、confirm／holdを既存の判断形式へ保存できる。

追加した調査コントローラは、固定した資料の中を対象語・属性語で順位付けし、まず最大3資料をLLMへ渡す。候補が得られなければ最大12資料へ拡張する。問い合わせ回数、項目数、コンテキスト量には上限を設ける。同じ資料集合で同じ問いを繰り返さない。

これは保存済み資料内の限定的な適応処理であり、LLMが自由に検索・実行手順を選ぶエージェントの完成ではない。外部文献の追加取得、任意ツール実行、処理コード生成は行わない。引用の実在・形式検査を、意味的な正解の証明とは扱わない。

## 実行

Harmoの依存環境で、リポジトリ直下から実行する。outputには存在しない新しいディレクトリを指定する。

```sh
PYTHONPATH=src python -m harmo.semantic_workspace \
  --packet /absolute/path/to/semantic-review/packet.json \
  --output /absolute/path/to/new-workspace \
  --max-targets 8
```

生成されたreview.htmlをローカルブラウザで開く。画面自身はネットワーク通信を行わない。モデルを指定しない場合、LLMは実行しない。

既に稼働しているOllamaのローカルモデルを使う場合：

```sh
PYTHONPATH=src python -m harmo.semantic_workspace \
  --packet /absolute/path/to/semantic-review/packet.json \
  --output /absolute/path/to/new-workspace-with-llm \
  --model qwen3.5:9b --port 11439 \
  --max-targets 8 --max-calls 8
```

モデルの取得・起動はこのコマンドでは行わない。モデル名は導入済みの実際の名前を指定する。宛先は127.0.0.1のみで、クラウドモデルとリダイレクトは既存の検査で拒否する。モデルが利用できなくても、ルール候補と資料を確認する画面は生成する。

## 人が行う作業

1. 実際の確認者名を入力する。
2. 表示された記録・チャンネル・属性を確認する。候補をそのまま使うか修正する。
3. 資料を切り替え、定義と適用範囲を確認する。判断に用いた資料だけを根拠として選択する。
4. 当該対象へ適用する理由を記入し、確認または保留する。未解決のまま次へ移動することもできる。
5. 「判断をJSONに保存」でreviewed-decisions.jsonを保存する。未確定の入力欄はconfirmとして書き出さない。

閉じる前に保存する。ブラウザの永続ストレージへは保存しない。画面にはすべての出典本文を含むため、公開配布を前提としない。第三者との共有・アップロードは別途判断する。

## 既存処理へ反映

```sh
PYTHONPATH=src python -m harmo.semantic_review apply \
  --packet /absolute/path/to/semantic-review/packet.json \
  --decisions /absolute/path/to/reviewed-decisions.json \
  --output /absolute/path/to/new-receipt
```

この検査では、元資料・入力・packetの変更、担当者・理由・根拠の不足、対象不一致を検査する。ブラウザ上の確認だけで元カタログを書き換えない。

生成されたreview.receipt.jsonをharmonize-batchのジョブのsemantic_reviewへ指定して再実行する。詳細はAUTOMATIC_WORKFLOW_v023.mdの「人の意味確認を後段へつなぐ」を参照。独立に目的判定だけを行う場合はsemantic_review reassessの--reviewに指定する。

確認済みファイルで残りの項目に進む場合は、次のprepareで--decisionsに保存した判断ファイルを指定する。confirm／hold／reject済み項目を再度LLMへ送らない。元の判断はすべて保持する。

## 分離して扱う状態

- ルール候補とLLM候補は別表示。不一致を上書きで解消しない。
- 人が未確認の項目と、意味が不明の項目は同じではない。v001は未確認項目を表示対象とし、ルール候補があるだけで確認済みにしない。既存処理エンジンで自動解決済みの項目だけを除く統合は未実装。
- 科学的比較の承認はfalseのまま。筋名・左右の確認で同期・正規化・イベント定義を承認しない。
- 予算外の項目は未確認のまま保持する。画面の末尾を研究全体の確認完了とはしない。
- 処理履歴・イベント・単位の確認画面、実波形の表示、身体図、URL取得の画面はこの実装にはまだ接続していない。模式図を実データとして表示しない。

## 出力

- workspace.json：対象・候補・全資料・調査経過・未確認テンプレート。
- review.html：一項目ずつ操作できる画面。
- candidates.json：最後の応答を対象ごとに保持。失敗・未解決も残す。
- attempt-NNN：モデル・ランタイム情報、送信資料、要求・応答。

workspaceとGUIの表示は保存時点のスナップショット。入力更新の最終検査はapplyおよび再評価時に行う。

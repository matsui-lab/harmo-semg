"""Validate repeated additional outputs and assemble a bounded report."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET


def read(path):
    return json.loads(path.read_text())


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def stable(data):
    if isinstance(data, dict):
        return {k:stable(v) for k,v in data.items() if k not in ('created_at','generated_at','decided_at','decision_id')}
    if isinstance(data, list): return [stable(v) for v in data]
    return data


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--junit', type=Path, required=True)
    p.add_argument('--sources', type=Path, required=True)
    p.add_argument('--left-run', default='run3')
    p.add_argument('--right-run', default='run4')
    p.add_argument('--version', default='v002')
    a = p.parse_args()
    out = a.output
    def artifact(name):
        path = Path(name)
        return out / f'{path.stem}_{a.version}{path.suffix}'
    core, timing = read(out/a.left_run/'core_summary.json'), read(out/a.left_run/'T3_summary.json')
    suites = ET.parse(a.junit)
    tests = list(suites.iter('testcase'))
    failures = [t.attrib for t in tests if t.find('failure') is not None or t.find('error') is not None]
    skipped = [t.attrib for t in tests if t.find('skipped') is not None]
    shutil.copy2(a.junit, artifact('regression_tests.xml'))
    source_rows = read(a.sources/'source_verification.json')
    save(out/'source_verification.json', source_rows)
    repeated = []
    for name in ('T1_recomputed_coverage.json','T1_property_ledger.json','T2_purpose_comparison.json',
                 'E2_scope_controls.json','E2_state_combinations.json','E2_release_policies.json',
                 'core_summary.json','T3_support_comparison.json','T3_summary.json','T3_errors.json'):
        first, second = read(out/a.left_run/name), read(out/a.right_run/name)
        repeated.append(dict(file=name, equal_except_execution_metadata=stable(first)==stable(second),
            left_run=a.left_run, right_run=a.right_run,
            left_sha256=hashlib.sha256((out/a.left_run/name).read_bytes()).hexdigest(),
            right_sha256=hashlib.sha256((out/a.right_run/name).read_bytes()).hexdigest()))
    rows = read(out/a.left_run/'E2_state_combinations.json')
    masked = []
    for row in rows:
        result = row['detail']['assertion']
        findings = [f for g in row['detail']['check_groups'] for f in g['findings']]
        hidden = [f for f in findings if f['reason'] not in result['rationale'] and f['reason'] not in result['conditions']]
        if hidden:
            masked.append(dict(mismatch=row['mismatch'],missing=row['missing'],outstanding=row['outstanding'],
                final_status=result['status'],newly_retained_findings=hidden))
    save(artifact('explanation_retention_delta.json'), masked)
    source_ok = len(source_rows)==11 and all(x['verified'] for x in source_rows)
    integrity = all(read(out/run/'integrity.json')['protected_inputs_unchanged'] for run in (a.left_run,a.right_run))
    initial = read(out/'recovered_timing_summary.json')
    recovered = read(out/'recovered_timing_frozen_summary.json')
    replay = dict(initial_current_builder_statuses=dict(__import__('collections').Counter(r['comparison_status'] for r in initial['comparisons'])),
        initial_current_builder_all_resolved=initial['all_timing_pairs_resolved_comparable'],
        historical_commit=recovered['code_commit'], historical_code_clean=recovered['all_code_clean'],
        historical_replay_all_resolved=recovered['all_timing_pairs_resolved_comparable'],
        historical_recording_schema='1.1.0',
        unresolved_scope='T3 does not validate first-class task/anatomy schema 1.2.0; the registered demo is explicitly pinned to its historical input representation.')
    save(artifact('reconstruction_incident.json'), replay)
    results = dict(core=core, timing=timing, tests=dict(total=len(tests),failures=failures,skipped=skipped),
        repeated=repeated, protected_inputs_unchanged=integrity, source_assets_verified=source_ok,
        explanation_improved_control_cases=len(masked), reconstruction=replay,
        all_checks_passed=core['all_passed'] and timing['all_passed'] and not failures and not skipped and source_ok and integrity and recovered['all_timing_pairs_resolved_comparable'] and recovered['all_code_clean'] and all(r['equal_except_execution_metadata'] for r in repeated))
    save(artifact('SUMMARY.json'), results)
    coverage = [
        dict(question='RQ1', distinction='属性・出典・適用範囲', evidence=['run1/T1_property_ledger.json','run1/E2_scope_controls.json'],
             conclusion='限定した抽出claimと適用操作を表現・区別できる', boundary='抽出精度・人が設定した範囲の独立正解ではない'),
        dict(question='RQ2', distinction='元の記録・表現変更・信号処理', evidence=['regression_tests.xml','source_verification.json'],
             conclusion='時間・チャンネル・単位・欠測保持に関する既存テストを再実行', boundary='全形式・全処理の数値忠実性を新規に検証したものではない'),
        dict(question='RQ3', distinction='同じ記録に対する用途の違い', evidence=['run1/T2_purpose_comparison.json','run1/T3_support_comparison.json'],
             conclusion='活動時期と振幅の要求・制約を分離して出力', boundary='別用途で必ず状態が変わるとは要求しない'),
        dict(question='RQ4', distinction='不一致・不足・未達条件と出力方針', evidence=['run1/E2_state_combinations.json','run1/E2_release_policies.json','explanation_retention_delta.json'],
             conclusion='8組合せと2方針で状態と説明の区別を確認', boundary='臨床的な判定正答率ではない'),
        dict(question='RQ5', distinction='処理状態記述の保持・撤回・復元', evidence=['run1/T3_support_comparison.json','run1/T3_summary.json'],
             conclusion='20ペアで波形固定のまま目的別再評価と判断復元を確認', boundary='処理支持の記述全体の操作。文献引用だけの因果効果・独立生理検証ではない'),
    ]
    for item in coverage:
        item['evidence'] = [s.replace('run1/', a.left_run+'/').replace('regression_tests.xml', artifact('regression_tests.xml').name).replace('explanation_retention_delta.json', artifact('explanation_retention_delta.json').name) for s in item['evidence']]
    save(artifact('E1_representation_evidence_map.json'), coverage)
    text = f'''# 追加評価報告 v001

## 結論

歴史的入力を回復した後の限定的な追加確認成立：{results['all_checks_passed']}。これは設計・規則適合性の追加評価であり、独立した科学的判断精度の検証ではない。予備再構成の失敗は下記に別途保持する。

| 評価 | 対象と観測結果 |
|---|---|
| 根拠連結 | 8研究の登録属性は24→43、追加19。凍結集計と一致。出典・値・適用範囲付き台帳を作成。 |
| 根拠の範囲制御 | {core['scope_controls']}件中{core['scope_controls_passed']}件で期待した付与／非付与。人工的に操作した条件を含み、抽出正答率ではない。 |
| 用途変更 | 同じ6ペアについて活動時期6件は条件付き、振幅6件は未評価。100点実演の範囲を維持。 |
| 処理状態記述の撤回・復元 | {timing['evaluated_pairs']}/20ペア、{timing['evaluations']}評価。保持・復元時は活動時期を比較可能、撤回時は条件付き。振幅は全条件で未評価。 |
| 状態と方針 | 不一致・不足・条件の8組合せと、条件付き出力を認める／認めない2方針を確認。 |
| 説明保持の改善 | 8制御例のうち{len(masked)}例で旧集約出力が省いていた併存所見を詳細出力に保持。判断規則は変更していない。 |
| 判断の復元 | {timing['exact_restorations']}/{timing['exact_restoration_comparisons']}用途別比較で元の判断を完全復元。 |
| 回帰確認 | {len(tests)}テスト、失敗{len(failures)}、スキップ{len(skipped)}。旧比較器との一致はcore {core['unchanged_assertions']}/{core['regression_comparisons']}件、T3 {timing['unchanged_assertions']}/{timing['evaluations']}件。 |

## 再現・入力同一性

Study 38の10ファイルとGait120の全ZIPを再取得し、登録サイズ・MD5・保存済みSHA-256と照合した。分割取得したZIPも結合後の全体ハッシュで確認した。

追加評価はrun1とrun2に保存し、10種類の成果物を比較した。実行日時とそれに由来する判断識別子を除いた内容の一致は、SUMMARY.jsonのrepeatedを参照する。これは同じ実装・入力での反復確認であり、別評価者による独立検証ではない。

原稿、元の登録プロトコル、凍結したvalidation結果と既存の記録JSONのハッシュ保持：{integrity}。実装作業中の未コミット状態を隠さず、コードとプロトコルのハッシュを各runのintegrity.jsonに保存した。

## 変更した実装

比較器に詳細取得経路を追加した。最終状態を決める前の各評価群の所見を別記録で返す。既存の判定JSON形式・状態・優先順位は維持する。所見なしは科学的な合格とは呼ばず、選ばれていない用途は未選択として明示する。

## 予備再構成で見つかった失敗と修正

現行の実演スクリプトは、構造化した筋・課題情報を生成しないまま新形式1.2.0を指定していた。このため最初の再構成では20件すべてが未評価となった。また、未コミットのコード状態に対する再現性警告も出力保留の理由になった。失敗はrecovered_timing/とrecovered_timing_summary.jsonに保持した。

元の結果に記載されたcommit 993a0a820a84b1e645ce773af56c2be802ba39b0を別のクリーンなチェックアウトで実行し、同じ公開入力から登録時の記録形式1.1.0を回復した。その入力に現在の比較器を適用したものが今回のT3である。既存実演スクリプトも登録時の形式を明示的に固定し、回帰テストを追加した。

これは新形式への移行を完成させた修正ではない。T3の旧形式では構造化課題照合は実行対象外であり、課題選択は元の登録処理に依存する。したがって、T3を新しい意味情報モデル全体の妥当性評価として報告しない。修正は予備結果を見た後に行ったもので、プロトコルv004に経緯を記録した。

## 解釈の境界

- T1は凍結抽出結果の再集計と台帳化であり、全文抽出の新しい正確性評価ではない。比較元には解析者が維持した設定情報を含む。
- T2は標準化済み100点記録に対する要求変更。波形全体の科学的同等性は評価しない。
- T3は固定された波形・時間基準・評価資料の下で、右記録の処理状態記述全体を撤回・復元した制御比較。参照一覧だけの効果ではない。
- T3の出力判断は固定した評価資料と既存manifestを文脈として用いた方針レベルの再評価であり、操作した条件で配布用成果物は新たに生成していない。
- 処理互換性の支持は既存の生理学的対照に依存する。新規・独立の生理学的検証や標本数の増加ではない。
- 全チェック群の所見を保持するが、各群内部の全分岐の検証済み状態を網羅するものではない。
- 規則の科学的根拠と著者の運用上の選択はscientific_basis_v001.mdで区別した。判断一致率、作業時間の短縮、他製品への優越、任意用途への一般化は主張しない。

## 次の本文修正に使える主張

対象とした公開EMGでは、出典と適用範囲付きの情報を追加し、同一記録に対する目的別条件と、支持不足時に残る制約を明示できた。処理状態記述の撤回・復元に伴う再評価は、活動時期の支持と振幅の制限を区別した。併存する問題の説明は、最終状態を変えずに保持できた。

## 成果物

- SUMMARY.json：機械可読集計と反復比較。
- E1_representation_evidence_map.json：五つの問いと今回の証拠の対応。
- explanation_retention_delta.json：旧出力で省略されていた所見の具体例。
- scientific_basis_v001.md：科学的根拠・運用規則・未検証範囲。
- run1、run2：属性台帳、範囲制御、用途比較、状態組合せ、支持撤回・復元の全入力と出力。
- source_verification.json：公開入力の全体ハッシュ照合。
- recovered_timing_frozen/：登録commitから回復した波形・記録・支持資料・判定。
- reconstruction_incident.json：予備再構成の失敗と歴史的入力回復の区別。
- regression_tests.xml：回帰テストの実行記録。

再取得した元データはdownloads/additional_framework_v001_2026.09.12-r1/に保存した（既存のGit除外対象）。通常のコード管理へ約1.5 GBの公開アーカイブを混入させない。

本文・採用図・原稿版は未変更。コミット・プッシュは行っていない。
'''
    text = text.replace('報告 v001', '報告 '+a.version).replace('run1', a.left_run).replace('run2', a.right_run)
    for name in ('SUMMARY.json','E1_representation_evidence_map.json','explanation_retention_delta.json','reconstruction_incident.json','regression_tests.xml'):
        text = text.replace(name, artifact(name).name)
    text += '\n## 反復確認の修正履歴\n\n初回run1/run2では人工的な制御入力を別々に生成し、評価資料の生成日時とそのハッシュが異なっていた。T3にも出力判断の日時差があった。このため初回の反復集計は不一致となった。REPORT_v001.mdとSUMMARY.jsonはそのまま保存した。プロトコルv005で入力固定方法を明示し、run3の人工入力をrun4でも再利用した。比較ではcreated_at、generated_at、decided_at、decision_idのみを実行メタデータとして除外し、入力ハッシュと根拠参照は除外していない。判定規則と期待結果は変更していない。\n\n元の厳格な出力方針では、活動時期は処理記述の保持・復元時に許可、撤回時に拒否となった。振幅は全条件で未評価・保留であった。\n'
    (out/f'REPORT_{a.version}.md').write_text(text)
    files = {str(f.relative_to(out)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(out.rglob('*')) if f.is_file() and f.name!=artifact('FILES_SHA256.json').name}
    save(artifact('FILES_SHA256.json'),files)
    print(json.dumps({k:v for k,v in results.items() if k not in ('core','timing','repeated')},ensure_ascii=False,indent=2))


if __name__ == '__main__': main()

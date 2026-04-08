# repos_diff_report.py

## Description

親リポジトリ上で、2 つの Git 参照（ブランチ・タグ・コミット）間の `*.repos` ファイルを比較し、パッケージ定義が変わった各エントリについて、新旧の参照（version / branch / tag）のあいだに入るコミット一覧を Markdown で出力するツールです。

- **探索**: `--search-root`（既定: カレントディレクトリ）以下を走査し、`*.repos` を検出します。各ファイルは `git show <ref>:<path>` で読みます。指定参照に存在しないファイルはスキップされます（通常、Git で追跡されている `.repos` のみが比較対象になります）。
- **参照の解決**: ローカルにブランチがなく `origin/<branch>` だけがある場合は、自動的にそちらを使って親リポジトリから `.repos` を読みます。
- **コミット一覧**: 各パッケージのリモート URL に対し、必要に応じて `git clone --mirror` と `fetch` でオブジェクトを取得し、`git log` で旧参照から新参照までのコミットを列挙します。ワークスペース内に `<package>` / `src/<package>` などに既存の Git クローンがある場合は、それを優先して参照を解決します。
- **出力**: リポジトリパス、比較した参照、各 `.repos` ファイルごとのパッケージ別の url / ref の新旧と、取得できたコミット（古い順）を Markdown でまとめます。

### 必要環境

- **Python 3**
- **PyYAML**: `pip install pyyaml`
- **git** が PATH にあること
- **`--local-only` を付けない場合**: リモートのクローン・取得のためネットワーク接続が必要です（`--local-only` 時は既存のローカルリポジトリのみ使用）

## Usage

```text
repos_diff_report.py [-h] [--repo-root PATH] [--search-root PATH] [--local-only]
                     [-o OUTPUT | --output OUTPUT]
                     ref_old ref_new
```

### 位置引数

| 引数 | 説明 |
|------|------|
| `ref_old` | 比較の「古い」側の Git 参照（ブランチ、タグ、コミット） |
| `ref_new` | 比較の「新しい」側の Git 参照 |

### オプション

| オプション | 説明 |
|------------|------|
| `-h`, `--help` | ヘルプを表示して終了 |
| `--repo-root PATH` | 親リポジトリのルート（省略時はカレントディレクトリから自動検出） |
| `--search-root PATH` | `*.repos` を探す起点ディレクトリ（省略時はカレントディレクトリ。親リポジトリ内である必要あり） |
| `--local-only` | リモートをクローンしない。既存のワークスペース内リポジトリのみ使用 |
| `-o`, `--output PATH` | 生成した Markdown をこのファイルに書き出す（省略時は標準出力） |

### 例

```bash
# ブランチ main と develop の差分レポートを標準出力へ
python3 repos_diff_report.py main develop

# 探索範囲と出力ファイルを指定
python3 repos_diff_report.py v1.0.0 HEAD --search-root ./ros_ws -o report.md

# ネットワークなしで、ローカルに既にあるクローンだけで試す
python3 repos_diff_report.py origin/main HEAD --local-only
```

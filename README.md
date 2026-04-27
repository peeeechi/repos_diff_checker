# repos_diff_report.py

## Description

親リポジトリ上で、2 つの Git 参照（ブランチ・タグ・コミット）間の `*.repos` を比較し、**リモート URL 単位**で定義の差分を Markdown にまとめます。差分がある URL について、新旧の **effective ref**（`version` → `branch` → `tag` の優先で決まる実効参照）のあいだのコミット一覧を `git log` で出力します。

### 比較・マッチング

- **探索**: `--search-root`（既定: カレントディレクトリ）以下の `*.repos` を走査し、各パスを `git show <ref>:<path>` で読みます。片方の参照にしか存在しないパスはスキップされるか、ファイル単位の注記になります（通常、Git で追跡されている `.repos` が対象です）。
- **同一パッケージの判定**: `repositories` 内の **`type: git`** かつ `url` があるエントリだけを対象にし、**リモート URL が同じものは同一パッケージ**として扱います。参照間で YAML のキー（パス名）が変わっても、URL が同じなら 1 組として比較します。同一ファイル内で同一 URL が複数キーに紐づく場合は、**キー名の辞書順で先頭のエントリ**を代表にします。
- **差分として出す条件**: 上記の同一 URL ペアについて、**`type`** および YAML の **`version` / `branch` / `tag`** をフィールドごとに比較し、いずれかが異なるときにレポートに載せます（`effective_ref` だけが一致していても、例えば `branch` から `version` へ表記が変わっただけでは別物として検出されます）。
- **コミット範囲**: 各側の **`effective_ref`** を解決し、`git log` で旧→新のコミットを列挙します。必要に応じて `git clone --mirror` と `fetch` を行います。ワークスペースに `<キー名>` / `src/<キー名>` などの既存クローンがある場合は、**旧・新の YAML キーの両方**を順に試してローカル解決を優先します。
- **参照の解決**（親リポジトリ側）: ローカルにブランチがなく `origin/<branch>` だけがある場合は、自動的にそちらを使って `.repos` を読みます。

### レポートの見出し構造

1. `# .repos diff report` とリポジトリパス・比較参照のメタ情報
2. 対象ごとに **`## \`相対パス/ファイル.repos\``**（その `.repos` ファイル）
3. その中の URL ごとに **`### \`https://...\``** と、Old/New の表・コミット・注記

変更のない `.repos` や、片方の参照にしか存在しないファイルは、`##` の下に注記または「変更なし」のみが出ます。

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

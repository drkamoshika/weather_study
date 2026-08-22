# 個人用気象資料ダッシュボード（静的版）

気象庁などの資料を必要なときだけ収集し、日付・地点ごとの snapshot として保存して、GitHub Pages または任意の静的 HTTP サーバーで読む個人学習用ダッシュボードです。

以前の `server.py` + `/api/*` 方式は保全していますが、新しい画面は Python サーバーにも API にも依存しません。既存の `data/state.json` と `data/assets/` も変更・削除していません。

## Architecture

```text
GitHub Actions（手動: date / location / sources / force）
  ├─ scripts/collect.py           公式資料を取得
  │    └─ data/snapshots/YYYY-MM-DD/location-id/
  │         ├─ manifest.json      出典、時刻、hash、status、error
  │         ├─ data/*.json        AMeDAS、予報、公式テキスト
  │         └─ assets/*           天気図・航空気象図
  ├─ scripts/generate_llm_analysis.py
  │    └─ llm-analysis.md         snapshot生成時の学習用解説
  └─ scripts/build_static.py
       └─ dist/ → GitHub Pages
```

収集と閲覧は完全に分離されています。Pages のチェック欄は「保存済み資料の表示フィルター」で、新規取得ボタンではありません。

## 1. 既存データを移行する

旧データを読み取り、新形式へコピーします。元ファイルは変更しません。

```sh
cd /Users/yn/Documents/code/work/weather_study
python3 scripts/migrate_existing_data.py
```

結果には次の集計が表示されます。

- `snapshots`: 作成した snapshot 数
- `sources_migrated`: 移行できた資料数
- `duplicates`: すでに存在していて再利用した snapshot 数
- `metadata_incomplete`: 旧データに必要情報がなかった資料数
- `missing_references`: 参照先画像がなかった資料数

既存の移行 snapshot を作り直す場合だけ `--force` を付けます。この場合も旧 `state.json` と画像は上書きしません。

## 2. ローカルで保存済み snapshot を見る

```sh
python3 scripts/build_static.py
cd dist
python3 -m http.server 8000
```

ブラウザで `http://127.0.0.1:8000/` を開きます。`file://` で直接開くとブラウザの `fetch` 制限により JSON を読めないため、必ず静的 HTTP サーバーを使ってください。

日付、地点、資料種別、全選択・全解除、画像拡大・再クリックズーム、AMeDAS表・地図、予報、航空資料、公式実況テキスト、生成済み Gemini 解説を利用できます。`?date=2026-08-22&location=tokyo` のような URL から表示を復元できます。

## 3. ローカルで新規データを取得する

標準資料一式:

```sh
python3 scripts/collect.py \
  --date 2026-08-22 \
  --location tokyo \
  --sources standard
```

資料を限定する例:

```sh
python3 scripts/collect.py \
  --date 2026-08-22 \
  --location osaka \
  --sources weather-map,amedas,forecast,aviation:taf
```

- `standard`: 地上・高層・数値予報・AMeDAS・予報・解説・主要航空資料
- `all`: `config/sources.json` の全資料
- カンマ区切り: 指定した source ID だけ
- `--force`: 成功済みキャッシュも再取得。省略時は同じ日付・地点・sourceを再利用

地点は `config/locations.json`、空港 ICAO は `config/airports.json`、資料は `config/sources.json` で管理します。TAF は `location → airport_icao → URL → 保存ファイル → manifest → 表示` が同じ地点になる構成です。全国共通の航空図は地点非依存です。

PDF は Linux/Actions でも使える Poppler の `pdftoppm` で PNG に変換します。

```sh
# macOS
brew install poppler

# Ubuntu
sudo apt-get install poppler-utils
```

一資料でエラーが起きても全体は止まりません。その source は `failed` または `unavailable` となり、理由が manifest と画面に残ります。

## 4. Gemini の事前生成

API キーは環境変数または GitHub Actions Secret だけで使用します。JavaScript、snapshot、`dist/` には入りません。
`GEMINI_API_KEY` は任意です。未設定の場合は解説生成を `skipped` として記録して正常終了し、snapshot の取得・保存・Pages 公開はそのまま続行します。

```sh
read -s GEMINI_API_KEY
export GEMINI_API_KEY
# 任意。省略時はgenerateContent対応モデルから候補を選択
export GEMINI_MODEL='gemini-2.5-flash'
python3 scripts/generate_llm_analysis.py --date 2026-08-22 --location tokyo
python3 scripts/build_static.py
```

モデル一覧から `generateContent` 対応の Gemini モデルだけを使い、画像専用・TTS・agent系を除外します。画像付き呼び出しが失敗したら text-only fallback を一度行い、HTTP status、APIエラー、model、fallback有無を秘密値なしで manifest に残します。リアルタイムチャットはありません。

## 5. GitHub Actions と Pages

1. このディレクトリを GitHub リポジトリへ追加します。本作業では remote 作成、公開範囲の決定、push は行っていません。
2. Repository Settings → Secrets and variables → Actions で、必要なら Secret `GEMINI_API_KEY` と Variable `GEMINI_MODEL` を登録します。
3. Settings → Pages → Build and deployment の Source を **GitHub Actions** にします。
4. Actions → **Build weather snapshot** → Run workflow を開きます。
5. 次の入力を確認して実行します。

- `date`: `YYYY-MM-DD`。空欄なら日本時間の当日
- `location`: `tokyo`、`osaka` など地点 ID。空欄なら `tokyo`
- `sources`: `standard`、`all`、またはカンマ区切り ID。空欄なら `all`
- `force`: `true` なら既存成功資料も再取得

したがって入力を何も変更せず **Run workflow** を押すだけで、「日本時間の今日・東京・全資料」を取得します。

workflow は checkout、Python、Poppler、collector、Gemini、snapshot の commit/push、static build、Pages artifact upload、deploy の順です。同時実行は直列化し、手動実行だけなので snapshot の push で再実行ループしません。`contents: write`、`pages: write`、`id-token: write` が必要です。

2026-08-23 時点の GitHub 公式例に合わせ、`configure-pages@v5`、`upload-pages-artifact@v4`、`deploy-pages@v4` を使用しています。private repository の Pages 可否はプランに依存するため、公開範囲を決める際に最新仕様を確認してください。

## snapshot と manifest

```text
data/snapshots/2026-08-22/tokyo/
├── manifest.json
├── data/
│   ├── amedas.json
│   └── forecast.json
├── assets/
│   ├── weather-map.png
│   └── aviation-taf.png
└── llm-analysis.md
```

manifest は `schema_version`、対象日、地点（緯度経度・空港）、要求 source、取得元 URL、local/data path、MIME type、取得・発表・有効時刻、SHA-256、cache hit、status、error、Gemini利用有無を持ちます。確実に判定できない時刻は `null` です。

status:

- `success`: 取得・移行成功
- `cached`: 既存ファイル再利用
- `unavailable`: 公式側に対象日データなし
- `failed`: 通信・変換等の失敗

旧 AMeDAS の `temperature` と `wind` は実際には日最高気温・最大風速だったため、移行時に `temperature_max_c` と `wind_speed_max_m_s` へ改名しました。平均気温・平均風速とは表示しません。

## テスト

```sh
python3 -m unittest discover -s tests -v
python3 scripts/build_static.py
rg '/api/' web dist
```

最後の `rg` が何も表示しなければ静的 frontend に `/api/*` 依存はありません。

## 制約と注意

- 公式サイトが過去履歴を公開していない資料は、今後 snapshot を蓄積して初めて過去閲覧できます。日付指定で当時の資料を遡って取得できるとは限りません。
- 実況図、予想図、航空資料、予報で時刻の意味は異なります。取得元から確実に判定できない発表・有効時刻は `null` とし、推測しません。
- 予報 JSON は気象庁由来の livedoor 天気互換 API を参考情報として利用します。防災判断には気象庁公式情報を確認してください。
- Leaflet 地図タイル、予報アイコン、外部リンクは閲覧時のネット接続が必要です。保存済み図と JSON は相対パスです。
- 民間予報サイトの画面は保存・再配布しません。
- 旧版は `python3 server.py` で起動できますが、新しい `dist/` には不要です。

## 現状監査の記録

- 指定された `COMPLETE_HANDOVER.md` は存在しませんでした。添付された移行仕様と現行コードを基準に判断しました。
- 開始時は Git リポジトリではなく `.gitignore` もありませんでした。GitHub remote、public/private、push は設定していません。
- 旧資産 `server.py`、`public/`、`data/state.json`、`data/assets/` はすべて保持しています。
- 旧 `public/app.js` は `/api/dashboard`、`/api/refresh`、`/api/gemini` 等に依存していました。新 `web/app.js` は生成済み相対ファイルだけを読みます。
- `server.py` に同居していた HTTP配信、取得、state更新、PDF変換、Gemini を CLI と builder に分離しました。
- 秘密値らしい埋め込みは確認されませんでした。`GEMINI_API_KEY` という環境変数名の説明・参照だけがあります。

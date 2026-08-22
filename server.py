#!/usr/bin/env python3
"""Personal Tokyo weather-study dashboard. Python 3 standard library only."""

from __future__ import annotations

import csv
import base64
import io
import json
import mimetypes
import os
import subprocess
import tempfile
import threading
import unicodedata
import urllib.request
import re
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ASSET_DIR = DATA_DIR / "assets"
STATE_FILE = DATA_DIR / "state.json"
JST = timezone(__import__("datetime").timedelta(hours=9))

SOURCES = [
    {"id": "amedas", "name": "アメダス最新値", "kind": "数値データ", "required": True,
     "url": "https://www.data.jma.go.jp/stats/data/mdrr/pre_rct/alltable/pre1h00_rct.csv",
     "view": "https://www.jma.go.jp/bosai/amedas/", "note": "公式CSV。選択地域の観測値を保存して図示します。", "about": "地上の観測所で測った、降水量・気温・風などの生データです。", "use": "今起きている雨や気温差を地点ごとに確認します。", "read": "まず降水量と風を見て、周辺地点との違い・急変を追います。"},
    {"id": "weather-map", "name": "実況天気図（日本周辺）", "kind": "地上天気図", "required": True,
     "url": "https://www.jma.go.jp/bosai/weather_map/", "view": "https://www.jma.go.jp/bosai/weather_map/",
     "note": "実況（Analysis Surface）を現在の観測データから作図した地上天気図。", "about": "地上の気圧配置、前線、低気圧・高気圧の位置を示す基本資料です。", "use": "雨域や風向の大きな流れ、数日前からの変化を捉えます。", "read": "低気圧・前線の進行方向と等圧線の密度を確認します。"},
    {"id": "asia-analysis", "name": "アジア太平洋 実況天気図（ASAS）", "kind": "地上天気図", "required": True,
     "url": "https://www.jma.go.jp/bosai/weather_map/", "view": "https://www.jma.go.jp/bosai/weather_map/",
     "note": "ASAS：Analysis Surface Asia。アジア太平洋域の実況気圧配置。", "about": "日本周辺より広い範囲で、熱帯低気圧・高気圧・前線の連なりを示します。", "use": "日本へ流れ込む空気の大きな背景を追います。", "read": "日本の西側・南側の低気圧や高気圧がどう動くかを見ます。"},
    {"id": "asia-forecast24", "name": "アジア太平洋 24時間予想天気図（FSAS24）", "kind": "地上天気図", "required": True,
     "url": "https://www.jma.go.jp/bosai/weather_map/", "view": "https://www.jma.go.jp/bosai/weather_map/",
     "note": "FSAS24：Forecast Surface Asia、24時間後の予想図。", "about": "観測データと予報計算を基に、24時間後の気圧配置・前線位置を予想します。", "use": "実況から翌日への変化を確認します。", "read": "実況図との違い、低気圧・前線の移動量を確認します。"},
    {"id": "asia-forecast48", "name": "アジア太平洋 48時間予想天気図（FSAS48）", "kind": "地上天気図", "required": True,
     "url": "https://www.jma.go.jp/bosai/weather_map/", "view": "https://www.jma.go.jp/bosai/weather_map/",
     "note": "FSAS48：Forecast Surface Asia、48時間後の予想図。", "about": "観測データと予報計算を基に、48時間後の気圧配置・前線位置を予想します。", "use": "2日程度先の大きな流れを確認します。", "read": "24時間予想から連続して、系の発達・衰弱や進路を読みます。"},
    {"id": "upper-850", "name": "高層天気図 850 hPa", "kind": "高層", "required": True,
     "url": "https://www.jma.go.jp/bosai/numericmap/#type=upper", "view": "https://www.jma.go.jp/bosai/numericmap/#type=upper",
     "note": "AUPQ78：850/700hPa 解析図。暖湿気・下層風の確認用。", "about": "850/700hPa付近の気温、風、湿りを示す高層天気図です。", "use": "暖湿気の流入、降水の持続しやすさ、下層風を読みます。", "read": "風向、気温線、湿数の小さい領域を地上天気図と重ねます。"},
    {"id": "upper-700", "name": "高層天気図 700 hPa", "kind": "高層", "required": True,
     "url": "https://www.jma.go.jp/bosai/numericmap/#type=upper", "view": "https://www.jma.go.jp/bosai/numericmap/#type=upper",
     "note": "AUPQ78 の700hPaパネル。雲・湿り・中層風の確認用。", "about": "700hPaは雲・湿り・上昇流を考える中層です。", "use": "雨雲が発達・維持されやすい場の確認に使います。", "read": "湿った領域と上昇流の位置を地上の前線と比べます。"},
    {"id": "upper-500", "name": "高層天気図 500 hPa", "kind": "高層", "required": True,
     "url": "https://www.jma.go.jp/bosai/numericmap/#type=upper", "view": "https://www.jma.go.jp/bosai/numericmap/#type=upper",
     "note": "AUPQ35：500/300hPa 解析図。寒気・トラフなど上層場の確認用。", "about": "500hPa付近の高度・気温・風を示す上層天気図です。", "use": "寒気、トラフ、上空の強風帯から天気変化の背景を読みます。", "read": "谷（トラフ）と寒気の位置、地上低気圧との位相を見ます。"},
    {"id": "numeric", "name": "数値予報天気図", "kind": "数値予報", "required": True,
     "url": "https://www.jma.go.jp/bosai/numericmap/", "view": "https://www.jma.go.jp/bosai/numericmap/",
     "note": "GSM・週間アンサンブルなどの公式図表。", "about": "数値モデルの計算結果を地図にした予想資料です。", "use": "数時間〜数日先の気圧、風、気温、降水の見通しを比較します。", "read": "有効時刻を最初に確認し、実況との差と複数時刻の連続性を見ます。"},
    {"id": "numeric-week", "name": "週間アンサンブル予報図", "kind": "数値予報", "required": True,
     "url": "https://www.jma.go.jp/bosai/numericmap/#type=nwp", "view": "https://www.jma.go.jp/bosai/numericmap/#type=nwp",
     "note": "FEFE19：週間の気温・降水のばらつきを読むためのアンサンブル資料。", "about": "複数の予報計算を重ね、1週間程度の傾向と不確実性を表す資料です。", "use": "晴雨や気温の大まかな傾向・予報の揺れを確認します。", "read": "平均だけでなく、複数メンバーの広がりも確認します。"},
    {"id": "numeric-2week", "name": "2週間気温予報図", "kind": "数値予報", "required": True,
     "url": "https://www.jma.go.jp/bosai/numericmap/#type=nwp", "view": "https://www.data.jma.go.jp/cpd/longfcst/",
     "note": "FCVX21：2週間気温予報の実況解析図。", "about": "平年に対して高い・低い傾向を2週間スケールで示す資料です。", "use": "日々の天気より長い、暑さ・寒さの傾向を把握します。", "read": "平年差と対象期間を確認し、確度は週間予報より低いと理解します。"},
    {"id": "numeric-month", "name": "1か月予報図", "kind": "数値予報", "required": True,
     "url": "https://www.jma.go.jp/bosai/numericmap/#type=nwp", "view": "https://www.data.jma.go.jp/cpd/longfcst/",
     "note": "FCVX11：1か月予報の実況解析図。", "about": "1か月程度の気温・降水量・日照時間の傾向を見る資料です。", "use": "季節の進み方や平年との差を考える材料にします。", "read": "特定の日の天気を当てる図ではなく、確率的な傾向として読みます。"},
    {"id": "forecast", "name": "地域の天気予報（livedoor天気互換）", "kind": "参考予報", "required": True,
     "url": "https://weather.tsukumijima.net/api/forecast/city/130010", "view": "https://weather.tsukumijima.net/",
     "note": "気象庁由来の予報をJSONで取得する非公式互換API。", "about": "今日・明日・明後日の天気、予想気温、降水確率、概況です。", "use": "日単位の見通しを素早く確認します。", "read": "発表時刻と公式予報へのリンクを確認し、参考予報として扱います。"},
    {"id": "briefing-short", "name": "短期予報解説資料", "kind": "公式解説", "required": True,
     "url": "https://www.data.jma.go.jp/yoho/data/jishin/kaisetsu_tanki_latest.pdf", "view": "https://www.data.jma.go.jp/yoho/data/jishin/kaisetsu_tanki_latest.pdf",
     "note": "短期予報の考え方と防災事項を解説する公式資料。", "about": "今日から明後日までの主要じょう乱、予想根拠、防災上の注意点です。", "use": "天気図で見えた変化を、公式の文章解説で補強します。", "read": "着目点・予想根拠・防災事項の順で確認します。"},
    {"id": "briefing-week", "name": "週間予報解説資料", "kind": "公式解説", "required": True,
     "url": "https://www.data.jma.go.jp/yoho/data/jishin/kaisetsu_shukan_latest.pdf", "view": "https://www.data.jma.go.jp/yoho/data/jishin/kaisetsu_shukan_latest.pdf",
     "note": "週間予報の考え方と不確実性を解説する公式資料。", "about": "3〜7日目を中心に、天候・気温傾向とモデル間のばらつきを説明します。", "use": "長めの見通しを、確度を含めて捉えます。", "read": "予報の幅や前日資料からの変化に注目します。"},
    {"id": "past-observation-text", "name": "過去1週間の実況テキスト", "kind": "公式テキスト", "required": True,
     "url": "https://www.data.jma.go.jp/yoho/gyogyou/index.html", "view": "https://www.data.jma.go.jp/yoho/gyogyou/index.html",
     "note": "各地の観測値と低気圧・前線位置をまとめたShift_JISテキスト。", "about": "毎日の観測値と、低気圧・前線の位置を一つにまとめた過去実況です。", "use": "数日前との変化や、天気図に描かれた現象の経過を補います。", "read": "日付ごとの本文を読み、前線・低気圧の位置の推移を追います。"},
    {"id": "aviation", "name": "航空気象情報", "kind": "航空気象", "required": True,
     "url": "https://www.data.jma.go.jp/airinfo/index.html", "view": "https://www.data.jma.go.jp/airinfo/index.html",
     "note": "表示する資料は下の選択欄で決めます。", "about": "飛行運航向けの、雲・視程・乱気流・着氷・雷・風などの専門資料です。", "use": "上空や飛行場周辺の悪天リスクを詳細に確認します。", "read": "対象高度・有効時刻・略号を確認し、地上・高層図と照合します。"},
    {"id": "bosai-xml", "name": "防災情報XML", "kind": "公式電文", "required": False,
     "url": "https://www.data.jma.go.jp/developer/", "view": "https://www.data.jma.go.jp/developer/",
     "note": "公式予報・警報の構造化取得用。次段階で電文種別を選んでアダプタ化します。"},
]

BASE_OPTIONS = [
    {"id": "weather-map", "name": "地上天気図", "summary": "実況・予想の気圧配置と前線", "default": True},
    {"id": "asia-analysis", "name": "ASAS アジア太平洋実況", "summary": "広域の現在の気圧配置", "default": True},
    {"id": "asia-forecast24", "name": "FSAS24 アジア太平洋24時間予想", "summary": "翌日への気圧配置の変化", "default": True},
    {"id": "asia-forecast48", "name": "FSAS48 アジア太平洋48時間予想", "summary": "2日先の大きな流れ", "default": False},
    {"id": "upper-850", "name": "高層図 850 / 700 hPa", "summary": "暖湿気、下層風、湿り", "default": True},
    {"id": "upper-500", "name": "高層図 500 hPa", "summary": "寒気、トラフ、上層風", "default": True},
    {"id": "numeric", "name": "数値予報天気図", "summary": "将来の大気場・気温・風", "default": True},
    {"id": "numeric-week", "name": "週間アンサンブル予報図", "summary": "1週間の傾向と予報の幅（FEFE19）", "default": False},
    {"id": "numeric-2week", "name": "2週間気温予報図", "summary": "平年差の傾向（FCVX21）", "default": False},
    {"id": "numeric-month", "name": "1か月予報図", "summary": "季節的な傾向（FCVX11）", "default": False},
    {"id": "amedas", "name": "アメダス観測", "summary": "選択地域の実測値と雨量", "default": True},
    {"id": "forecast", "name": "地域の天気予報", "summary": "今日から3日間の参考予報", "default": True},
    {"id": "briefing-short", "name": "短期予報解説資料", "summary": "予報根拠と防災事項を読む", "default": True},
    {"id": "briefing-week", "name": "週間予報解説資料", "summary": "週間傾向と不確実性を読む", "default": True},
    {"id": "past-observation-text", "name": "過去実況テキスト", "summary": "観測値と前線位置の文章資料", "default": True},
]

CATALOG_EXTRAS = [
    {"name": "速報天気図（SPAS）", "kind": "地上実況", "summary": "日本周辺の地上実況天気図を速報的に提供する資料。", "about": "低気圧・高気圧・前線・等圧線を、観測後およそ2時間で確認できる地上実況図です。", "use": "直近の気圧配置と前線位置を確認し、現在のアメダス・雨雲と照合します。", "read": "日本周辺の実況天気図を選び、作図時刻と3時間前の図との差を見ます。", "view": "https://www.jma.go.jp/bosai/weather_map/"},
    {"name": "高解像度降水ナウキャスト", "kind": "実況・解析", "summary": "短時間の降水域・雷の変化を細かく追う資料。", "about": "雨雲の現在位置と、短時間先の動きを示します。", "use": "外出前や急な強雨・雷の接近確認に使います。", "read": "再生して雨域の移動方向・速度を見ます。", "view": "https://www.jma.go.jp/bosai/nowc/"},
    {"name": "気象衛星画像", "kind": "衛星", "summary": "雲の広がりと発達、台風や前線の構造。", "about": "雲の形・厚さ・移動を広域で観察する資料です。", "use": "前線帯や低気圧に伴う雲域の推移をつかみます。", "read": "連続画像で雲の発達・移動を地上天気図と照合します。", "view": "https://www.jma.go.jp/bosai/map.html#contents=satellite"},
    {"name": "ウィンドプロファイラ", "kind": "上空実況", "summary": "地上から上空までの風向・風速の観測。", "about": "上空の風を時間・高度別に観測した資料です。", "use": "風の急変や下層ジェットなどを確認します。", "read": "高度ごとの風向・風速の連続的な変化を追います。", "view": "https://www.jma.go.jp/bosai/windprofiler/"},
]

AVIATION_OPTIONS = [
    {"id": "fbjp", "name": "国内悪天予想図", "summary": "乱気流・着氷・雷など、国内空域の悪天を俯瞰", "url": "https://www.data.jma.go.jp/airinfo/data/awfo_fbjp.html", "default": True},
    {"id": "low_level", "name": "下層悪天予想図", "summary": "低層の雲・視程・降水などを確認", "url": "https://www.data.jma.go.jp/airinfo/data/awfo_low-level_sigwx.html", "default": True},
    {"id": "low_level_detail", "name": "下層悪天予想図（詳細版）", "summary": "より細かい低層悪天の確認", "url": "https://www.data.jma.go.jp/airinfo/data/awfo_low-level_detailed-sigwx.html", "default": False},
    {"id": "taf", "name": "飛行場時系列予報・情報", "summary": "羽田など空港ごとの風・視程・雲・現象の時系列", "url": "https://www.data.jma.go.jp/airinfo/data/awfo_taf.html", "default": True},
    {"id": "fbjp12", "name": "国内悪天12時間予想図", "summary": "数値予報による12時間先の国内悪天", "url": "https://www.data.jma.go.jp/airinfo/awfo_fbjp112/awfo_fbjp112.html", "default": False},
    {"id": "sigmet", "name": "シグメット情報", "summary": "運航に影響する顕著な気象現象の実況・予想", "url": "https://www.data.jma.go.jp/airinfo/data/awfo_sigmet.html", "default": False},
]

# 県庁所在地を中心にした個人学習用の地点一覧。アメダスは都道府県名で絞り込み、
# 予報互換APIは対応する一次細分区域コードを使う。
LOCATIONS = [
    ("hokkaido", "北海道", "札幌", 43.0618, 141.3545, "016010"), ("aomori", "青森県", "青森", 40.8222, 140.7474, "020010"), ("iwate", "岩手県", "盛岡", 39.7036, 141.1527, "030010"), ("miyagi", "宮城県", "仙台", 38.2682, 140.8694, "040010"), ("akita", "秋田県", "秋田", 39.7186, 140.1024, "050010"), ("yamagata", "山形県", "山形", 38.2404, 140.3633, "060010"), ("fukushima", "福島県", "福島", 37.7608, 140.4747, "070010"),
    ("ibaraki", "茨城県", "水戸", 36.3418, 140.4468, "080010"), ("tochigi", "栃木県", "宇都宮", 36.5658, 139.8836, "090010"), ("gunma", "群馬県", "前橋", 36.3911, 139.0608, "100010"), ("saitama", "埼玉県", "さいたま", 35.8617, 139.6455, "110010"), ("chiba", "千葉県", "千葉", 35.6074, 140.1065, "120010"), ("tokyo", "東京都", "東京", 35.6762, 139.6503, "130010"), ("kanagawa", "神奈川県", "横浜", 35.4478, 139.6425, "140010"),
    ("niigata", "新潟県", "新潟", 37.9026, 139.0236, "150010"), ("toyama", "富山県", "富山", 36.6953, 137.2113, "160010"), ("ishikawa", "石川県", "金沢", 36.5613, 136.6562, "170010"), ("fukui", "福井県", "福井", 36.0652, 136.2216, "180010"), ("yamanashi", "山梨県", "甲府", 35.6642, 138.5684, "190010"), ("nagano", "長野県", "長野", 36.6486, 138.1948, "200010"),
    ("gifu", "岐阜県", "岐阜", 35.3912, 136.7223, "210010"), ("shizuoka", "静岡県", "静岡", 34.9756, 138.3828, "220010"), ("aichi", "愛知県", "名古屋", 35.1802, 136.9066, "230010"), ("mie", "三重県", "津", 34.7303, 136.5086, "240010"), ("shiga", "滋賀県", "大津", 35.0045, 135.8686, "250010"), ("kyoto", "京都府", "京都", 35.0116, 135.7681, "260010"), ("osaka", "大阪府", "大阪", 34.6937, 135.5023, "270000"), ("hyogo", "兵庫県", "神戸", 34.6913, 135.1830, "280010"), ("nara", "奈良県", "奈良", 34.6851, 135.8048, "290010"), ("wakayama", "和歌山県", "和歌山", 34.2260, 135.1675, "300010"),
    ("tottori", "鳥取県", "鳥取", 35.5011, 134.2351, "310010"), ("shimane", "島根県", "松江", 35.4723, 133.0505, "320010"), ("okayama", "岡山県", "岡山", 34.6618, 133.9344, "330010"), ("hiroshima", "広島県", "広島", 34.3966, 132.4596, "340010"), ("yamaguchi", "山口県", "山口", 34.1861, 131.4705, "350010"), ("tokushima", "徳島県", "徳島", 34.0658, 134.5593, "360010"), ("kagawa", "香川県", "高松", 34.3401, 134.0434, "370000"), ("ehime", "愛媛県", "松山", 33.8416, 132.7657, "380010"), ("kochi", "高知県", "高知", 33.5597, 133.5311, "390010"),
    ("fukuoka", "福岡県", "福岡", 33.5904, 130.4017, "400010"), ("saga", "佐賀県", "佐賀", 33.2494, 130.2988, "410010"), ("nagasaki", "長崎県", "長崎", 32.7503, 129.8777, "420010"), ("kumamoto", "熊本県", "熊本", 32.8031, 130.7079, "430010"), ("oita", "大分県", "大分", 33.2382, 131.6126, "440010"), ("miyazaki", "宮崎県", "宮崎", 31.9111, 131.4239, "450010"), ("kagoshima", "鹿児島県", "鹿児島", 31.5966, 130.5571, "460010"), ("okinawa", "沖縄県", "那覇", 26.2124, 127.6809, "471010"),
]
LOCATIONS = [{"id": item[0], "prefecture": item[1], "city": item[2], "lat": item[3], "lon": item[4], "forecast_city": item[5]} for item in LOCATIONS]

ABBREVIATIONS = [
    {"code": "SPAS", "name": "日本周辺 速報天気図", "meaning": "日本周辺域の速報的な地上実況天気図を示す気象庁の識別子。"},
    {"code": "ASAS", "name": "アジア太平洋 実況天気図", "meaning": "Analysis Surface Asia。アジア・北西太平洋の地上実況解析。"},
    {"code": "FSAS24 / FSAS48", "name": "アジア太平洋 予想天気図", "meaning": "Forecast Surface Asia。末尾は24時間後／48時間後の予想時刻。"},
    {"code": "AUPQ78", "name": "850・700hPa 高層解析図", "meaning": "Analysis Upper / Western North Pacific。850hPaと700hPaを組み合わせた図。"},
    {"code": "AUPQ35", "name": "500・300hPa 高層解析図", "meaning": "Analysis Upper / Western North Pacific。500hPaと300hPaを組み合わせた図。"},
    {"code": "FXJP854", "name": "850hPa 相当温位・風 数値予報", "meaning": "日本域の850hPa相当温位と風の数値予報図。"},
    {"code": "FEFE19", "name": "週間アンサンブル予報", "meaning": "複数計算による1週間程度の傾向と予報の幅を示す図。"},
    {"code": "FCVX21 / FCVX11", "name": "2週間・1か月予報", "meaning": "平年差などの長期的な傾向を読む資料。日単位の予報には使わない。"},
]

AIRPORTS = {
    "hokkaido": ("RJCC", "新千歳"), "aomori": ("RJSA", "青森"), "iwate": ("RJSI", "花巻"), "miyagi": ("RJSS", "仙台"), "akita": ("RJSK", "秋田"), "yamagata": ("RJSC", "山形"), "fukushima": ("RJSF", "福島"),
    "ibaraki": ("RJAH", "茨城"), "tochigi": ("RJTT", "東京国際"), "gunma": ("RJTT", "東京国際"), "saitama": ("RJTT", "東京国際"), "chiba": ("RJAA", "成田国際"), "tokyo": ("RJTT", "東京国際"), "kanagawa": ("RJTT", "東京国際"),
    "niigata": ("RJSN", "新潟"), "toyama": ("RJNT", "富山"), "ishikawa": ("RJNK", "小松"), "fukui": ("RJNA", "県営名古屋"), "yamanashi": ("RJTT", "東京国際"), "nagano": ("RJAF", "松本"),
    "gifu": ("RJGG", "中部国際"), "shizuoka": ("RJNS", "静岡"), "aichi": ("RJGG", "中部国際"), "mie": ("RJGG", "中部国際"), "shiga": ("RJOO", "大阪国際"), "kyoto": ("RJOO", "大阪国際"), "osaka": ("RJOO", "大阪国際"), "hyogo": ("RJBE", "神戸"), "nara": ("RJOO", "大阪国際"), "wakayama": ("RJOO", "大阪国際"),
    "tottori": ("RJOR", "鳥取"), "shimane": ("RJOC", "出雲"), "okayama": ("RJOB", "岡山"), "hiroshima": ("RJOA", "広島"), "yamaguchi": ("RJDC", "山口宇部"), "tokushima": ("RJOS", "徳島"), "kagawa": ("RJOT", "高松"), "ehime": ("RJOM", "松山"), "kochi": ("RJOK", "高知"),
    "fukuoka": ("RJFF", "福岡"), "saga": ("RJFS", "佐賀"), "nagasaki": ("RJFU", "長崎"), "kumamoto": ("RJFT", "熊本"), "oita": ("RJFO", "大分"), "miyazaki": ("RJFM", "宮崎"), "kagoshima": ("RJFK", "鹿児島"), "okinawa": ("ROAH", "那覇"),
}

def now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")

def initial_state() -> dict:
    return {"queue": [], "runs": [], "observations": [], "forecasts": [], "collections": [], "current_location": "tokyo", "selected_base": [option["id"] for option in BASE_OPTIONS], "selected_aviation": [option["id"] for option in AVIATION_OPTIONS], "saved_at": now()}

def load_state() -> dict:
    if not STATE_FILE.exists():
        return initial_state()
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        # Existing personal snapshots predate the expanded surface maps and briefing material.
        # Migrate this one time only; later checkbox choices remain exactly as selected.
        if state.get("selection_schema", 0) < 2:
            state["selected_base"] = sorted(set(state.get("selected_base", [])) | {"asia-analysis", "asia-forecast24", "briefing-short", "briefing-week", "past-observation-text"})
            state["selection_schema"] = 2
        if "forecasts" not in state:
            state["forecasts"] = [state["forecast"]] if state.get("forecast") else []
        state.setdefault("collections", [])
        state.setdefault("current_location", "tokyo")
        return state
    except (json.JSONDecodeError, OSError):
        return initial_state()

def save_state(state: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    state["saved_at"] = now()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def save_asset(filename: str, raw: bytes) -> str:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(character for character in filename if character.isalnum() or character in "._-")
    (ASSET_DIR / safe_name).write_bytes(raw)
    return f"/media/{safe_name}"

def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "personal-weather-study-dashboard/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()

LOCK = threading.Lock()

def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace(" ", "").replace("　", "").replace("\ufeff", "").lower()

def number(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except (AttributeError, ValueError):
        return None

def selected_location(location_id: str | None) -> dict:
    return next((location for location in LOCATIONS if location["id"] == location_id), LOCATIONS[12])

def fetch_amedas(source: dict, location: dict) -> dict:
    raw = download(source["url"])
    for encoding in ("utf-8-sig", "cp932", "shift_jis"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            text = ""
    if not text:
        raise ValueError("CSVの文字コードを判別できませんでした")
    rows = list(csv.DictReader(io.StringIO(text)))
    def extra_values(url: str, contains: str) -> dict:
        raw_extra = download(url)
        text_extra = raw_extra.decode("cp932")
        values = {}
        for extra_row in csv.DictReader(io.StringIO(text_extra)):
            flat_extra = {normalize(k): v for k, v in extra_row.items() if k}
            place_extra = flat_extra.get("地点", "")
            for key, value in flat_extra.items():
                if contains in key and "品質" not in key:
                    values[place_extra] = number(value)
                    break
        return values
    temperatures = extra_values("https://www.data.jma.go.jp/stats/data/mdrr/tem_rct/alltable/mxtemsadext00_rct.csv", "最高気温")
    winds = extra_values("https://www.data.jma.go.jp/stats/data/mdrr/wind_rct/alltable/mxwsp00_rct.csv", "最大値(m/s)")
    stations = json.loads(download("https://www.jma.go.jp/bosai/amedas/const/amedastable.json").decode("utf-8"))
    station_by_name = {normalize(item.get("kjName", "")): item for item in stations.values()}
    local_rows = []
    for row in rows:
        flat = {normalize(k): v for k, v in row.items() if k}
        prefecture = "".join(str(v) for k, v in flat.items() if "都道府県" in k)
        place = "".join(str(v) for k, v in flat.items() if k in ("地点", "観測所名"))
        pref_short = location["prefecture"].removesuffix("都").removesuffix("道").removesuffix("府").removesuffix("県")
        if pref_short not in prefecture and location["prefecture"] not in prefecture:
            continue
        def pick(*needles: str) -> str:
            for key, value in flat.items():
                if all(needle in key for needle in needles):
                    return value
            return ""
        def exact(key: str) -> str:
            return flat.get(normalize(key), "")
        base_place = place.split("（")[0].split("(")[0]
        station = station_by_name.get(normalize(base_place))
        lat = station.get("lat") if station else None
        lon = station.get("lon") if station else None
        local_rows.append({
            "place": place or "対象地域",
            "observed": "-".join(filter(None, [exact("現在時刻(年)"), exact("現在時刻(月)"), exact("現在時刻(日)")])) + " " + ":".join(filter(None, [exact("現在時刻(時)"), exact("現在時刻(分)")])),
            "rain_1h": number(exact("現在値(mm)")),
            "temperature": temperatures.get(place),
            "wind": winds.get(place),
            "lat": (lat[0] + lat[1] / 60) if lat else None,
            "lon": (lon[0] + lon[1] / 60) if lon else None,
        })
    if not local_rows:
        raise ValueError(location["prefecture"] + "の観測所をCSVから見つけられませんでした")
    return {"source_id": source["id"], "collected_at": now(), "location": location, "count": len(local_rows), "rows": local_rows}

def list_gemini_models() -> list[dict]:
    """Return only models that the configured API key can use for text generation."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return []
    request = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models?key=" + quote(api_key),
        headers={"User-Agent": "personal-weather-study-dashboard/0.1"},
    )
    try:
        response = json.loads(urllib.request.urlopen(request, timeout=30).read().decode("utf-8"))
        models = []
        for model in response.get("models", []):
            if "generateContent" not in model.get("supportedGenerationMethods", []):
                continue
            name = model.get("name", "")
            identifier = name.removeprefix("models/")
            # API一覧には、実験用Agentや画像生成専用など、この画面の
            # generateContent（テキスト＋天気図画像）では使えない候補も含まれる。
            # Gemini 系だけを選択肢にし、Bad Request になる候補を出さない。
            incompatible = ("-image", "-tts", "computer-use", "robotics", "omni", "customtools", "-live")
            if not name.startswith("models/gemini-") or any(token in identifier for token in incompatible):
                continue
            models.append({"id": identifier, "label": model.get("displayName", identifier)})
        return sorted(models, key=lambda item: item["id"])
    except (KeyError, json.JSONDecodeError, urllib.error.URLError) as error:
        raise ValueError("Geminiの利用可能モデルを取得できませんでした: " + str(error))

def image_part_for_gemini(image_url: str) -> dict | None:
    """Keep the LLM input bounded while sending the actual acquired chart image."""
    asset = (ASSET_DIR / image_url.removeprefix("/media/")).resolve()
    if ASSET_DIR.resolve() not in asset.parents or not asset.is_file():
        return None
    raw = asset.read_bytes()
    mime_type = mimetypes.guess_type(asset.name)[0] or "image/png"
    if len(raw) > 650_000:
        with tempfile.TemporaryDirectory() as temporary:
            reduced = Path(temporary) / "chart.jpg"
            conversion = subprocess.run(["sips", "-Z", "1050", "-s", "format", "jpeg", str(asset), "--out", str(reduced)], capture_output=True, text=True, timeout=60)
            if conversion.returncode == 0 and reduced.exists():
                raw = reduced.read_bytes()
                mime_type = "image/jpeg"
    return {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(raw).decode("ascii")}}

def gemini_answer(question: str, state: dict, model: str | None = None) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Gemini APIキーが未設定です。起動前に export GEMINI_API_KEY='...' を実行してください。")
    observation = state.get("observations", [])[-1] if state.get("observations") else {}
    rows = observation.get("rows", [])
    latest_images = {}
    latest_texts = {}
    for run in reversed(state.get("runs", [])):
        payload = run.get("payload", {})
        source_id = payload.get("source_id")
        if source_id and payload.get("image_url") and source_id not in latest_images:
            latest_images[source_id] = payload
        if source_id and payload.get("text") and source_id not in latest_texts:
            latest_texts[source_id] = payload
    chart_order = ["weather-map", "asia-analysis", "asia-forecast24", "asia-forecast48", "upper-850", "upper-500", "numeric", "numeric-week", "briefing-short", "briefing-week"]
    # 実際に読ませる図は多すぎるとAPI側でリクエストを拒否することがあるため、
    # 代表的な最大6枚に絞る。画面の「LLMに渡す取得済み資料」も同じ順序を表示する。
    selected_charts = [latest_images[source_id] for source_id in chart_order if source_id in latest_images][:6]
    evidence = {
        "amedas_collected_at": observation.get("collected_at"),
        "amedas_rows": rows,
        "chart_titles_sent_as_images": [item.get("title") for item in selected_charts],
        "official_text_material": {key: value.get("text", "")[:7000] for key, value in latest_texts.items()},
    }
    location = observation.get("location") or selected_location(state.get("current_location"))
    location_label = f"{location.get('prefecture', '選択地域')} {location.get('city', '')}".strip()
    prompt = f"""あなたは個人の気象学習を助ける解説者です。以下のJSON、続いて添付する取得済み天気図・公式解説資料の画像を根拠に、{location_label}について答えてください。\n- 添付画像は実際に取得した資料なので、天気図の前線・高低気圧・等圧線や高層場を読める場合は必ず根拠にする。\n- 公式予報そのものではなく、学習用の参考解釈であることを短く明記する。\n- 根拠として使った観測値、図の名前、テキスト資料名を必ず挙げる。\n- 取得されていない情報は推測で補わない。\n- 緊急時は気象庁の警報・予報の確認を促す。\n\n資料:\n""" + json.dumps(evidence, ensure_ascii=False) + "\n\n質問:\n" + question
    parts = [{"text": prompt}]
    parts.extend(part for chart in selected_charts if (part := image_part_for_gemini(chart["image_url"])))
    payload = json.dumps({"contents": [{"parts": parts}], "generationConfig": {"temperature": 0.25, "maxOutputTokens": 1100}}, ensure_ascii=False).encode("utf-8")
    available = {item["id"] for item in list_gemini_models()}
    selected_model = model if model in available else "gemini-2.5-flash"
    if selected_model not in available and available:
        selected_model = next(iter(available))
    request = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models/" + quote(selected_model) + ":generateContent?key=" + quote(api_key), data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        response = json.loads(urllib.request.urlopen(request, timeout=60).read().decode("utf-8"))
        return response["candidates"][0]["content"]["parts"][0]["text"]
    except urllib.error.HTTPError as error:
        # レスポンス本文を捨てず、どの段階で失敗したかを利用者に分かる形で返す。
        try:
            details = json.loads(error.read().decode("utf-8")).get("error", {}).get("message", "")
        except (UnicodeDecodeError, json.JSONDecodeError):
            details = ""
        message = "Gemini APIがリクエストを受け付けませんでした"
        if details:
            message += "（" + details + "）"
        raise ValueError(message + "。モデルを再取得してGeminiモデルを選び直してください。")
    except (KeyError, IndexError, urllib.error.URLError) as error:
        raise ValueError("Geminiから回答を取得できませんでした: " + str(error))

def fetch_weather_map(source: dict, display_date: str | None = None) -> dict:
    listing = json.loads(download("https://www.jma.go.jp/bosai/weather_map/data/list.json"))
    chart_key = {
        "weather-map": ("near", "now", "実況天気図（日本周辺）"),
        "asia-analysis": ("asia", "now", "アジア太平洋 実況天気図（ASAS）"),
        "asia-forecast24": ("asia", "ft24", "アジア太平洋 24時間予想天気図（FSAS24）"),
        "asia-forecast48": ("asia", "ft48", "アジア太平洋 48時間予想天気図（FSAS48）"),
    }
    region, timing, title = chart_key[source["id"]]
    filenames = listing[region][timing]
    if display_date:
        target = display_date.replace("-", "")
        candidates = [filename for filename in filenames if target in filename]
        if not candidates:
            raise ValueError(display_date + "の公式直近履歴には、この天気図がありません。直近数日以外は保存用天気図から追加します。")
        filename = candidates[-1]
    else:
        filename = filenames[-1]
    image_url = "https://www.jma.go.jp/bosai/weather_map/data/png/" + filename
    local_url = save_asset(source["id"] + "-" + filename, download(image_url))
    payload = {"source_id": source["id"], "collected_at": now(), "title": title, "image_url": local_url, "official_url": source["view"]}
    if display_date:
        payload["display_date"] = display_date
        payload["title"] += " — " + display_date.replace("-", "/")
    return payload

def fetch_aviation_fbjp() -> dict:
    image_url = "https://www.data.jma.go.jp/airinfo/data/pict/fbjp/fbjp.png"
    local_url = save_asset("aviation-fbjp.png", download(image_url))
    option = next(option for option in AVIATION_OPTIONS if option["id"] == "fbjp")
    return {"source_id": "aviation:fbjp", "collected_at": now(), "title": option["name"], "image_url": local_url, "official_url": option["url"]}

def fetch_forecast(source: dict, location: dict) -> dict:
    response = json.loads(download("https://weather.tsukumijima.net/api/forecast/city/" + location["forecast_city"]).decode("utf-8"))
    return {"source_id": source["id"], "collected_at": now(), "location": location, "title": response.get("title", location["city"] + "の天気"), "published_at": response.get("publicTimeFormatted", ""), "official_url": response.get("link", "https://www.jma.go.jp/"), "headline": response.get("description", {}).get("headlineText", ""), "forecasts": response.get("forecasts", [])}

def fetch_aviation_chart(option_id: str, image_url: str, location: dict | None = None) -> dict:
    option = next(option for option in AVIATION_OPTIONS if option["id"] == option_id)
    title = option["name"]
    if option_id == "taf" and location:
        icao, airport = AIRPORTS.get(location["id"], ("RJTT", "東京国際"))
        title = f"飛行場時系列予報・情報 — {airport}（{icao}）"
        local_url = save_asset(f"aviation-taf-{icao}.png", download(image_url))
        return {"source_id": "aviation:taf", "collected_at": now(), "location": location, "title": title, "image_url": local_url, "official_url": option["url"]}
    return fetch_static_chart("aviation:" + option_id, title, image_url, option["url"])

def fetch_static_chart(source_id: str, title: str, image_url: str, official_url: str) -> dict:
    local_url = save_asset(source_id + ".png", download(image_url))
    return {"source_id": source_id, "collected_at": now(), "title": title, "image_url": local_url, "official_url": official_url}

def fetch_pdf_chart(source: dict, title: str, pdf_path: str) -> dict:
    return fetch_external_pdf_chart(source, title, "https://www.jma.go.jp/bosai/numericmap/" + pdf_path)

def fetch_external_pdf_chart(source: dict, title: str, pdf_url: str) -> dict:
    with tempfile.TemporaryDirectory() as temporary:
        pdf_file = Path(temporary) / "chart.pdf"
        png_file = Path(temporary) / "chart.png"
        pdf_file.write_bytes(download(pdf_url))
        conversion = subprocess.run(["sips", "-s", "format", "png", str(pdf_file), "--out", str(png_file)], capture_output=True, text=True, timeout=60)
        if conversion.returncode != 0 or not png_file.exists():
            raise ValueError("PDFを表示用PNGへ変換できませんでした")
        local_url = save_asset(source["id"] + ".png", png_file.read_bytes())
    return {"source_id": source["id"], "collected_at": now(), "title": title, "image_url": local_url, "official_url": source["view"]}

def fetch_past_observation_text(source: dict) -> dict:
    page = download(source["url"]).decode("utf-8", errors="replace")
    files = re.findall(r'href=["\'](day\d+_\d+\.txt)["\']', page)
    if not files:
        raise ValueError("過去実況テキストの最新ファイルを見つけられませんでした")
    filename = files[0]
    text = download("https://www.data.jma.go.jp/yoho/gyogyou/" + filename).decode("cp932", errors="replace")
    compact = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    return {"source_id": source["id"], "collected_at": now(), "title": "過去実況テキスト（" + filename.removesuffix(".txt") + "）", "text": compact[:16000], "official_url": source["url"]}

def execute_item(item: dict, location: dict | None = None) -> dict:
    source = next((s for s in SOURCES if s["id"] == item["source_id"]), None)
    if source is None:
        raise ValueError("未知の取得元です")
    if source["id"] == "amedas":
        return {"mode": "downloaded", "payload": fetch_amedas(source, location or selected_location(None))}
    if source["id"] in {"weather-map", "asia-analysis", "asia-forecast24", "asia-forecast48"}:
        return {"mode": "downloaded", "payload": fetch_weather_map(source)}
    if source["id"] == "forecast":
        return {"mode": "downloaded", "payload": fetch_forecast(source, location or selected_location(None))}
    if source["id"] == "briefing-short":
        return {"mode": "downloaded", "payload": fetch_external_pdf_chart(source, "短期予報解説資料", source["url"])}
    if source["id"] == "briefing-week":
        return {"mode": "downloaded", "payload": fetch_external_pdf_chart(source, "週間予報解説資料", source["url"])}
    if source["id"] == "past-observation-text":
        return {"mode": "downloaded", "payload": fetch_past_observation_text(source)}
    if source["id"] in {"upper-850", "upper-700"}:
        return {"mode": "downloaded", "payload": fetch_pdf_chart(source, "高層天気図 850 / 700 hPa", "data/nwpmap/aupq78_00.pdf")}
    if source["id"] == "upper-500":
        return {"mode": "downloaded", "payload": fetch_pdf_chart(source, "高層天気図 500 / 300 hPa", "data/nwpmap/aupq35_00.pdf")}
    if source["id"] == "numeric":
        return {"mode": "downloaded", "payload": fetch_pdf_chart(source, "数値予報天気図 — 日本850hPa相当温位・風", "data/nwpmap/fxjp854_00.pdf")}
    static_numeric = {
        "numeric-week": ("週間アンサンブル予報図 — FEFE19", "https://www.jma.go.jp/bosai/numericmap/data/nwpmap/fefe19.png"),
        "numeric-2week": ("2週間気温予報図 — FCVX21", "https://www.data.jma.go.jp/cpd/data/longfcst/fax/fcvx21_12.png"),
        "numeric-month": ("1か月予報図 — FCVX11", "https://www.data.jma.go.jp/cpd/data/longfcst/fax/fcvx11_12.png"),
    }
    if source["id"] in static_numeric:
        title, image_url = static_numeric[source["id"]]
        return {"mode": "downloaded", "payload": fetch_static_chart(source["id"], title, image_url, source["view"])}
    # Static chart/PDF portals are intentionally not scraped. Keeping a checked source record is useful history.
    return {"mode": "official_portal", "payload": {"source_id": source["id"], "title": source["name"], "checked_at": now(), "official_url": source["view"]}}

class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "public"), **kwargs)

    def send_json(self, value: dict | list, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/dashboard":
            with LOCK:
                state = load_state()
            self.send_json({"sources": SOURCES, "catalog_extras": CATALOG_EXTRAS, "base_options": BASE_OPTIONS, "aviation_options": AVIATION_OPTIONS, "locations": LOCATIONS, "abbreviations": ABBREVIATIONS, "gemini_configured": bool(os.environ.get("GEMINI_API_KEY")), **state})
            return
        if path == "/api/gemini/models":
            configured = bool(os.environ.get("GEMINI_API_KEY"))
            try:
                models = list_gemini_models() if configured else []
            except ValueError as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"configured": configured, "models": models})
            return
        if path.startswith("/media/"):
            asset = (ASSET_DIR / path.removeprefix("/media/")).resolve()
            if ASSET_DIR.resolve() not in asset.parents or not asset.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            raw = asset.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mimetypes.guess_type(asset.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self.read_json()
            with LOCK:
                state = load_state()
                if path == "/api/queue":
                    ids = body.get("source_ids") or [source["id"] for source in SOURCES]
                    added = []
                    for source_id in ids:
                        if source_id not in {s["id"] for s in SOURCES}:
                            continue
                        if any(item["source_id"] == source_id and item["status"] == "queued" for item in state["queue"]):
                            continue
                        item = {"id": f"q-{int(datetime.now().timestamp() * 1000)}-{source_id}", "source_id": source_id,
                                "requested_at": now(), "status": "queued"}
                        state["queue"].append(item)
                        added.append(item)
                    save_state(state)
                    self.send_json({"added": added, "queue": state["queue"]}, HTTPStatus.CREATED)
                    return
                if path == "/api/queue/run":
                    queued = [item for item in state["queue"] if item["status"] == "queued"]
                    for item in queued:
                        item["status"] = "running"
                        try:
                            result = execute_item(item)
                            item["status"] = "complete"
                            item["completed_at"] = now()
                            state["runs"].append({"queue_id": item["id"], "source_id": item["source_id"], **result})
                            if item["source_id"] == "amedas":
                                state["observations"].append(result["payload"])
                                state["observations"] = state["observations"][-48:]
                        except Exception as error:  # surfaced to the UI and retained for debugging
                            item["status"] = "failed"
                            item["error"] = str(error)
                    state["runs"] = state["runs"][-100:]
                    save_state(state)
                    self.send_json({"processed": len(queued), "state": state})
                    return
                if path == "/api/refresh":
                    requested_date = str(body.get("date") or datetime.now(JST).date().isoformat()).strip()
                    try:
                        datetime.strptime(requested_date, "%Y-%m-%d")
                    except ValueError:
                        raise ValueError("日付を選択してください。")
                    location = selected_location(str(body.get("location_id") or state.get("current_location", "tokyo")))
                    state["current_location"] = location["id"]
                    selected = set(body["aviation_ids"]) if "aviation_ids" in body else set(state.get("selected_aviation") or [option["id"] for option in AVIATION_OPTIONS])
                    selected_base = set(body["base_ids"]) if "base_ids" in body else set(state.get("selected_base") or [option["id"] for option in BASE_OPTIONS])
                    requested_sources = selected_base | {"aviation:" + option_id for option_id in selected}
                    cached = next((item for item in state.get("collections", []) if item.get("date") == requested_date and item.get("location_id") == location["id"] and requested_sources.issubset(set(item.get("sources", [])))), None)
                    if cached:
                        save_state(state)
                        self.send_json({"processed": 0, "cached": True, "display_date": requested_date, "state": state})
                        return

                    # 画面では資料の種類を選ばせない。今日なら一式、過去日なら気象庁の
                    # 直近履歴から取得可能な地上天気図一式を自動で集める。
                    state["selected_base"] = sorted(selected_base)
                    state["selected_aviation"] = sorted(selected)
                    results = []
                    today = datetime.now(JST).date().isoformat()
                    if requested_date != today:
                        historical_ids = {"weather-map", "asia-analysis", "asia-forecast24", "asia-forecast48"} & selected_base
                        for source in SOURCES:
                            if source["id"] not in historical_ids:
                                continue
                            try:
                                result = {"mode": "downloaded", "payload": fetch_weather_map(source, requested_date)}
                                state["runs"].append({"source_id": source["id"], **result})
                                results.append(source["id"])
                            except Exception as error:
                                state["runs"].append({"source_id": source["id"], "mode": "failed", "error": str(error), "payload": {}})
                        if not results:
                            raise ValueError("この日付で取得可能な天気図がありません。気象庁の直近履歴にある日付を選んでください。")
                        state.setdefault("collections", []).append({"date": requested_date, "location_id": location["id"], "sources": results, "saved_at": now(), "kind": "historical_maps"})
                        state["collections"] = state["collections"][-100:]
                        state["runs"] = state["runs"][-100:]
                        save_state(state)
                        self.send_json({"processed": len(results), "cached": False, "display_date": requested_date, "state": state})
                        return
                    for source in SOURCES:
                        if source["id"] not in selected_base:
                            continue
                        try:
                            result = execute_item({"source_id": source["id"]}, location)
                            state["runs"].append({"source_id": source["id"], **result})
                            results.append(source["id"])
                            if source["id"] == "amedas": state["observations"].append(result["payload"])
                            if source["id"] == "forecast":
                                state["forecast"] = result["payload"]
                                state.setdefault("forecasts", []).append(result["payload"])
                        except Exception as error:
                            state["runs"].append({"source_id": source["id"], "mode": "failed", "error": str(error), "payload": {}})
                    if "fbjp" in selected:
                        try:
                            result = {"mode": "downloaded", "payload": fetch_aviation_fbjp()}
                            state["runs"].append(result)
                            results.append("aviation:fbjp")
                        except Exception as error:
                            state["runs"].append({"source_id": "aviation:fbjp", "mode": "failed", "error": str(error), "payload": {}})
                    aviation_images = {
                        "low_level": "https://www.data.jma.go.jp/airinfo/data/pict/low-level_sigwx/fbtk03.png",
                        "low_level_detail": "https://www.data.jma.go.jp/airinfo/data/pict/low-level_sigwx_p/Lsigp_Fig305.png",
                        "taf": "https://www.data.jma.go.jp/airinfo/data/pict/taf/QMCD98_" + AIRPORTS.get(location["id"], ("RJTT", "東京国際"))[0] + ".png",
                        "fbjp12": "https://www.data.jma.go.jp/airinfo/data/pict/nwp/fbjp112_00.png",
                        "sigmet": "https://www.data.jma.go.jp/airinfo/data/pict/sigmet/QGMA98.png",
                    }
                    for option_id, image_url in aviation_images.items():
                        if option_id not in selected:
                            continue
                        try:
                            result = {"mode": "downloaded", "payload": fetch_aviation_chart(option_id, image_url, location)}
                            state["runs"].append(result)
                            results.append("aviation:" + option_id)
                        except Exception as error:
                            state["runs"].append({"source_id": "aviation:" + option_id, "mode": "failed", "error": str(error), "payload": {}})
                    for option in AVIATION_OPTIONS:
                        if option["id"] in selected and option["id"] not in {"fbjp", *aviation_images}:
                            state["runs"].append({"source_id": "aviation:" + option["id"], "mode": "official_portal", "payload": {"source_id": "aviation:" + option["id"], "title": option["name"], "official_url": option["url"], "checked_at": now()}})
                            results.append("aviation:" + option["id"])
                    state["observations"] = state["observations"][-48:]
                    state["forecasts"] = state.get("forecasts", [])[-48:]
                    state["runs"] = state["runs"][-100:]
                    state.setdefault("collections", []).append({"date": requested_date, "location_id": location["id"], "sources": results, "saved_at": now(), "kind": "current"})
                    state["collections"] = state["collections"][-100:]
                    save_state(state)
                    self.send_json({"processed": len(results), "cached": False, "display_date": requested_date, "state": state})
                    return
                if path == "/api/history":
                    display_date = str(body.get("date", "")).strip()
                    try:
                        datetime.strptime(display_date, "%Y-%m-%d")
                    except ValueError:
                        raise ValueError("日付を選択してください。")
                    requested_ids = set(body.get("source_ids") or {"weather-map", "asia-analysis", "asia-forecast24", "asia-forecast48"})
                    results = []
                    for source in SOURCES:
                        if source["id"] not in requested_ids or source["id"] not in {"weather-map", "asia-analysis", "asia-forecast24", "asia-forecast48"}:
                            continue
                        try:
                            result = {"mode": "downloaded", "payload": fetch_weather_map(source, display_date)}
                            state["runs"].append({"source_id": source["id"], **result})
                            results.append(source["id"])
                        except Exception as error:
                            state["runs"].append({"source_id": source["id"], "mode": "failed", "error": str(error), "payload": {}})
                    if not results:
                        raise ValueError("この日付で取得可能な天気図がありません。気象庁の直近履歴の対象日を選んでください。")
                    state["runs"] = state["runs"][-100:]
                    save_state(state)
                    self.send_json({"processed": len(results), "display_date": display_date, "state": state})
                    return
                if path == "/api/gemini":
                    question = str(body.get("question", "")).strip()
                    if not question:
                        raise ValueError("質問を入力してください。")
                    answer = gemini_answer(question, state, str(body.get("model", "")).strip() or None)
                    self.send_json({"answer": answer, "answered_at": now()})
                    return
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (json.JSONDecodeError, ValueError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, fmt, *args):
        print(f"[{now()}] {fmt % args}")

class ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

if __name__ == "__main__":
    preferred_port = int(os.environ.get("WEATHER_APP_PORT", "8000"))
    server = None
    for port in range(preferred_port, preferred_port + 10):
        try:
            server = ReusableHTTPServer(("127.0.0.1", port), AppHandler)
            break
        except OSError as error:
            if error.errno != 48:
                raise
    if server is None:
        raise OSError("8000番台のポートを確保できませんでした")
    print(f"気象資料室: http://127.0.0.1:{port}")
    server.serve_forever()

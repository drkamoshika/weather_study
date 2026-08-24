#!/usr/bin/env python3
"""Collect selected weather sources into a date/location snapshot."""
from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from common import JST, build_archive_index, empty_manifest, locations, now, read_json, safe_source_id, sha256, snapshot_dir, sources, write_json

USER_AGENT = "personal-weather-study-static/1.0 (+GitHub Actions)"

def download(url: str) -> tuple[bytes, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return response.read(), response.headers.get_content_type()
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("download retry exhausted")

def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").replace(" ", "").replace("　", "").replace("\ufeff", "").lower()

def number(value):
    try: return float(str(value).replace(",", ""))
    except (TypeError, ValueError): return None

def decode_csv(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932", "shift_jis"):
        try: return raw.decode(encoding)
        except UnicodeDecodeError: pass
    raise ValueError("CSVの文字コードを判別できませんでした")

def extra_amedas(url: str, needle: str) -> dict:
    raw, _ = download(url)
    values = {}
    for row in csv.DictReader(io.StringIO(decode_csv(raw))):
        flat = {normalize(k): v for k, v in row.items() if k}
        place = flat.get("地点", "")
        for key, value in flat.items():
            if needle in key and "品質" not in key:
                values[place] = number(value); break
    return values

def collect_amedas(source: dict, location: dict) -> tuple[dict, str, str]:
    raw, _ = download(source["url"])
    temperatures = extra_amedas("https://www.data.jma.go.jp/stats/data/mdrr/tem_rct/alltable/mxtemsadext00_rct.csv", "最高気温")
    winds = extra_amedas("https://www.data.jma.go.jp/stats/data/mdrr/wind_rct/alltable/mxwsp00_rct.csv", "最大値(m/s)")
    station_raw, _ = download("https://www.jma.go.jp/bosai/amedas/const/amedastable.json")
    stations = json.loads(station_raw.decode("utf-8")); station_by_name = {normalize(x.get("kjName", "")): x for x in stations.values()}
    rows=[]; pref_short=location["prefecture"].removesuffix("都").removesuffix("道").removesuffix("府").removesuffix("県")
    for row in csv.DictReader(io.StringIO(decode_csv(raw))):
        flat={normalize(k):v for k,v in row.items() if k}; prefecture="".join(str(v) for k,v in flat.items() if "都道府県" in k)
        if pref_short not in prefecture and location["prefecture"] not in prefecture: continue
        place="".join(str(v) for k,v in flat.items() if k in ("地点","観測所名")); base=place.split("（")[0].split("(")[0]; station=station_by_name.get(normalize(base))
        exact=lambda key: flat.get(normalize(key), "")
        lat,lon=station.get("lat") if station else None,station.get("lon") if station else None
        rows.append({"place":place,"observed":"-".join(filter(None,[exact("現在時刻(年)"),exact("現在時刻(月)"),exact("現在時刻(日)")]))+" "+":".join(filter(None,[exact("現在時刻(時)"),exact("現在時刻(分)")])),"rain_1h":number(exact("現在値(mm)")),"temperature_max_c":temperatures.get(place),"wind_speed_max_m_s":winds.get(place),"lat":lat[0]+lat[1]/60 if lat else None,"lon":lon[0]+lon[1]/60 if lon else None})
    if not rows: raise ValueError(f"{location['prefecture']}の観測所をCSVから見つけられませんでした")
    return {"source_id":"amedas","collected_at":now(),"location":location,"rows":rows,"measurement_note":"日最高気温と最大風速です。平均値ではありません。"}, "json", source["url"]

def collect_weather_map(source: dict, target_date: str) -> tuple[bytes, str, str]:
    raw, _ = download(source["url"]); listing=json.loads(raw)
    keys={"weather-map":("near","now"),"asia-analysis":("asia","now"),"asia-forecast24":("asia","ft24"),"asia-forecast48":("asia","ft48")}
    region,timing=keys[source["id"]]; filenames=listing[region][timing]
    candidates=filenames if target_date==datetime.now(JST).date().isoformat() else [x for x in filenames if target_date.replace("-","") in x]
    if not candidates: raise FileNotFoundError(f"公式直近履歴に{target_date}の資料がありません")
    url="https://www.jma.go.jp/bosai/weather_map/data/png/"+candidates[-1]
    return download(url)[0], Path(candidates[-1]).suffix or ".png", url

def collect_pdf(source: dict) -> tuple[bytes, str, str]:
    if shutil.which("pdftoppm") is None: raise RuntimeError("PDF変換にはPopplerのpdftoppmが必要です")
    raw, _ = download(source["url"])
    with tempfile.TemporaryDirectory() as temporary:
        pdf=Path(temporary)/"source.pdf"; prefix=Path(temporary)/"page"; pdf.write_bytes(raw)
        process=subprocess.run(["pdftoppm","-f","1","-singlefile","-png","-r","130",str(pdf),str(prefix)],capture_output=True,text=True,timeout=90)
        png=prefix.with_suffix(".png")
        if process.returncode or not png.exists(): raise RuntimeError("PDFをPNGへ変換できませんでした: "+process.stderr[-300:])
        return png.read_bytes(), ".png", source["url"]

def collect_forecast(source: dict, location: dict) -> tuple[dict, str, str]:
    url=source["url"].format(forecast_area_code=location["forecast_area_code"]); raw,_=download(url); value=json.loads(raw)
    return {"source_id":"forecast","collected_at":now(),"location":location,"title":value.get("title"),"published_at":value.get("publicTimeFormatted"),"official_url":value.get("link"),"headline":value.get("description",{}).get("headlineText",""),"forecasts":value.get("forecasts",[])},"json",url

def collect_text_index(source: dict) -> tuple[dict, str, str]:
    raw,_=download(source["url"]); page=raw.decode("utf-8",errors="replace"); files=re.findall(r'href=["\'](day\d+_\d+\.txt)',page)
    if not files: raise FileNotFoundError("実況テキストへのリンクが見つかりません")
    url="https://www.data.jma.go.jp/yoho/gyogyou/"+files[0]; body,_=download(url); value=body.decode("cp932",errors="replace")
    return {"source_id":source["id"],"collected_at":now(),"title":"過去実況テキスト（"+files[0]+"）","text":value[:20000],"official_url":source["view"]},"json",url

def source_entry(definition: dict, url: str | None = None) -> dict:
    return {"id":definition["id"],"name":definition["name"],"category":definition.get("category"),"abbreviation":definition.get("abbreviation"),"original_url":url or definition.get("url"),"local_path":None,"data_path":None,"mime_type":None,"fetched_at":now(),"issued_at":None,"valid_from":None,"valid_to":None,"content_hash":None,"cache_hit":False,"status":"success","error":None,"used_by_llm":False}

def collect_one(definition: dict, target_date: str, location: dict, out: Path) -> dict:
    entry=source_entry(definition); source_id=definition["id"]; fmt=definition["format"]
    if fmt != "weather_map" and target_date != __import__("datetime").datetime.now(JST).date().isoformat():
        raise FileNotFoundError("この資料は公式の過去履歴を安定取得できません。対象日に保存したsnapshotが必要です")
    if fmt=="amedas": value,_,url=collect_amedas(definition,location)
    elif fmt=="weather_map": value,suffix,url=collect_weather_map(definition,target_date)
    elif fmt=="pdf": value,suffix,url=collect_pdf(definition)
    elif fmt=="json": value,_,url=collect_forecast(definition,location)
    elif fmt=="text_index": value,_,url=collect_text_index(definition)
    else:
        url=definition["url"].format(airport_icao=location["airport_icao"]); value,content_type=download(url); suffix=Path(urllib.parse.urlparse(url).path).suffix or mimetypes.guess_extension(content_type or "") or ".bin"
    entry["original_url"]=url
    if isinstance(value,dict):
        name=safe_source_id(source_id)+".json"; write_json(out/"data"/name,value); entry["data_path"]="data/"+name; entry["mime_type"]="application/json"; entry["content_hash"]=sha256(out/entry["data_path"])
        entry["issued_at"]=value.get("published_at")
    else:
        name=safe_source_id(source_id)+suffix; path=out/"assets"/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(value)
        entry["local_path"]="assets/"+name; entry["mime_type"]=mimetypes.guess_type(name)[0] or "application/octet-stream"; entry["content_hash"]=sha256(path)
    return entry

def collect(target_date: str, location_id: str, requested: list[str], force: bool=False, collection_mode: str="manual") -> dict:
    location_map={x["id"]:x for x in locations()}; definitions={x["id"]:x for x in sources()}
    if location_id not in location_map: raise ValueError("未知のlocationです: "+location_id)
    unknown=[x for x in requested if x not in definitions]
    if unknown: raise ValueError("未知のsourceです: "+", ".join(unknown))
    out=snapshot_dir(target_date,location_id); old=read_json(out/"manifest.json") if (out/"manifest.json").exists() else None
    old_map={x["id"]:x for x in (old or {}).get("sources",[])}
    requested_union=list(dict.fromkeys((old or {}).get("requested_sources",list(old_map)) + requested))
    manifest=empty_manifest(target_date,location_map[location_id],requested_union,collection_mode)
    if old and old.get("llm"): manifest["llm"]=old["llm"]
    changed=False
    for source_id in requested:
        cached=old_map.get(source_id)
        paths=[cached.get("local_path"),cached.get("data_path")] if cached else []
        if not force and cached and cached.get("status") in ("success","cached") and any(p and (out/p).exists() for p in paths):
            cached["status"]="cached"; cached["cache_hit"]=True; manifest["sources"].append(cached); continue
        changed=True
        try: manifest["sources"].append(collect_one(definitions[source_id],target_date,location_map[location_id],out))
        except Exception as error:
            status="unavailable" if isinstance(error,FileNotFoundError) else "failed"
            entry=source_entry(definitions[source_id]); entry["status"]=status; entry["error"]={"type":type(error).__name__,"message":str(error)[:500]}; manifest["sources"].append(entry)
            print(f"warning: {source_id}: {type(error).__name__}: {error}")
    manifest["sources"].extend(entry for source_id,entry in old_map.items() if source_id not in requested)
    if changed:
        manifest["llm"]={"status":"stale" if (out/"llm-analysis.md").exists() else "not_requested","model":None,"text_only_fallback":False,"error":None}
    elif old:
        manifest["generated_at"]=old.get("generated_at",manifest["generated_at"])
    write_json(out/"manifest.json",manifest); build_archive_index(); return manifest

def main() -> None:
    parser=argparse.ArgumentParser(description="選択資料を取得して静的snapshotに保存します。")
    parser.add_argument("--date",default="",help="空欄はJSTの今日"); parser.add_argument("--location",default="tokyo"); parser.add_argument("--sources",default="all",help="comma-separated IDs, standard, all（空欄はall）"); parser.add_argument("--force",action="store_true"); parser.add_argument("--collection-mode",choices=("manual","scheduled"),default="manual")
    args=parser.parse_args(); args.date=args.date.strip() or datetime.now(JST).date().isoformat(); args.location=args.location.strip() or "tokyo"; args.sources=args.sources.strip() or "all"; datetime.strptime(args.date,"%Y-%m-%d")
    all_ids=[x["id"] for x in sources()]; standard=["amedas","weather-map","asia-analysis","asia-forecast24","upper-850","upper-500","numeric","forecast","briefing-short","briefing-week","past-observation-text","aviation:fbjp","aviation:low_level","aviation:taf"]
    requested=all_ids if args.sources=="all" else standard if args.sources=="standard" else [x.strip() for x in args.sources.split(",") if x.strip()]
    result=collect(args.date,args.location,requested,args.force,args.collection_mode); counts={status:sum(x["status"]==status for x in result["sources"]) for status in ("success","cached","unavailable","failed")}; print("snapshot:",result["target_date"],result["location"]["id"],result["collection_mode"],counts)

if __name__=="__main__": main()

#!/usr/bin/env python3
"""Generate snapshot-time study notes with Gemini; never exposes the API key."""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request

from common import CONFIG, build_archive_index, now, read_json, snapshot_dir, write_json

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
INCOMPATIBLE = ("-image", "-tts", "computer-use", "robotics", "omni", "customtools", "-live", "embedding")

def request_json(path: str, api_key: str, body: dict | None = None) -> dict:
    raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(API_ROOT + path, data=raw, headers={"x-goog-api-key": api_key, "Content-Type": "application/json", "User-Agent": "weather-study-static/1.0"}, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(request, timeout=90) as response: return json.loads(response.read())
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")[:1000]
        try: details = json.loads(details).get("error", {}).get("message", details)
        except json.JSONDecodeError: pass
        raise RuntimeError(f"Gemini HTTP {error.code}: {details}") from None

def _version_rank(identifier: str) -> tuple:
    numbers = tuple(int(value) for value in re.findall(r"\d+", identifier)[:3])
    return ("latest" in identifier, "preview" not in identifier, numbers, "lite" not in identifier)

def choose_models(api_key: str, requested: str | None) -> list[str]:
    config=read_json(CONFIG/"gemini.json")
    models=request_json("/models",api_key).get("models",[]); usable=[]
    for model in models:
        identifier=model.get("name","").removeprefix("models/")
        status=str(model.get("modelStatus","")).upper()
        if ("generateContent" in model.get("supportedGenerationMethods",[]) and identifier.startswith("gemini-")
                and "flash" in identifier and "pro" not in identifier
                and not any(x in identifier for x in INCOMPATIBLE) and status not in ("LEGACY","DEPRECATED","RETIRED")):
            usable.append((identifier,status))
    if not usable: raise ValueError("利用可能なGemini Flash generateContentモデルがありません")
    ordered=[name for name,_ in sorted(usable,key=lambda item:(item[1]=="STABLE",_version_rank(item[0])),reverse=True)]
    preferred=[config.get("model"),*config.get("fallback_models",[])]
    # Keep the configured order for equivalent/current known models, while allowing a
    # newer stable Flash returned by the API to move ahead automatically.
    known={name:index for index,name in enumerate(preferred) if name}
    ordered.sort(key=lambda name:(name not in known, -known.get(name,999)), reverse=True)
    ordered.sort(key=_version_rank,reverse=True)
    if requested and "flash" in requested and "pro" not in requested and requested in ordered:
        ordered.remove(requested); ordered.insert(0,requested)
    elif requested: print(f"Gemini Flash model {requested} is unavailable or disallowed; trying the latest available Flash model")
    return ordered

def evidence(manifest: dict, out) -> tuple[str,list[dict],list[str]]:
    text_parts=[f"対象日: {manifest['target_date']}",f"地点: {manifest['location']['prefecture']} {manifest['location']['city']}"]
    images=[]; used=[]
    for entry in manifest.get("sources",[]):
        if entry.get("status") not in ("success","cached"): continue
        text_parts.append(f"- {entry['name']}（取得: {entry.get('fetched_at') or '不明'}）")
        if entry.get("data_path"):
            value=read_json(out/entry["data_path"]); compact=json.dumps(value,ensure_ascii=False)
            text_parts.append(compact[:8000]); used.append(entry["id"])
        elif entry.get("local_path") and len(images)<4:
            path=out/entry["local_path"]
            if path.exists() and path.stat().st_size<=900_000:
                images.append({"inline_data":{"mime_type":entry.get("mime_type") or mimetypes.guess_type(path.name)[0] or "image/png","data":base64.b64encode(path.read_bytes()).decode("ascii")}}); used.append(entry["id"])
    return "\n".join(text_parts)[:30000],images,used

def generate(target_date: str, location_id: str, requested_model: str | None = None) -> bool:
    out=snapshot_dir(target_date,location_id); manifest_file=out/"manifest.json"; manifest=read_json(manifest_file); key=os.environ.get("GEMINI_API_KEY")
    if not key:
        manifest["llm"]={"status":"skipped","model":None,"text_only_fallback":False,"error":{"type":"missing_secret","message":"GEMINI_API_KEYが未設定です。"}}; write_json(manifest_file,manifest); build_archive_index(); print("Gemini skipped: GEMINI_API_KEY is not set"); return False
    model=None; fallback=False; attempts=[]
    try:
        models=choose_models(key,requested_model or os.environ.get("GEMINI_MODEL")); text,images,used=evidence(manifest,out)
        prompt="""あなたは気象学習の補助者です。次の取得済み資料だけを根拠に、事実・読み取り・不確実性を区別して日本語Markdownで解説してください。推測を断定せず、資料にない項目は「判断材料不足」と明記してください。公式予報ではないことを冒頭に明記し、防災判断は気象庁などの公式情報へ誘導してください。

次の見出しを、この順序ですべて含めてください。
## 現在の概況
## 高気圧・低気圧の配置
## 前線・台風・暖気・寒気
## 850hPa・500hPaの特徴
## 数値予報資料の読み取り
## 航空気象への影響
## 明日にかけての変化
## 不確実性と確認点

取得済み資料:
"""+text
        result=None
        for candidate in models:
            model=candidate; fallback=False
            try:
                result=request_json(f"/models/{model}:generateContent",key,{"contents":[{"role":"user","parts":[{"text":prompt},*images]}]})
                break
            except RuntimeError as image_error:
                attempts.append({"model":model,"mode":"multimodal","error":str(image_error)[:500]})
                if not images: continue
                try:
                    fallback=True
                    result=request_json(f"/models/{model}:generateContent",key,{"contents":[{"role":"user","parts":[{"text":prompt+"\n画像入力を利用できないため、metadataとJSONだけを根拠にしてください。"}]}]})
                    break
                except RuntimeError as text_error:
                    attempts.append({"model":model,"mode":"text_only","error":str(text_error)[:500]})
        if result is None: raise RuntimeError("利用可能モデルでGemini解説を生成できませんでした")
        answer="\n".join(part.get("text","") for part in result["candidates"][0]["content"]["parts"] if part.get("text")); (out/"llm-analysis.md").write_text(answer,encoding="utf-8")
        generated_at=now()
        write_json(out/"llm-analysis.json",{"schema_version":1,"model":model,"generated_at":generated_at,"input_sources":used,"markdown_path":"llm-analysis.md","text_only_fallback":fallback})
        for entry in manifest["sources"]: entry["used_by_llm"]=entry["id"] in used
        manifest["llm"]={"status":"success","model":model,"generated_at":generated_at,"input_sources":used,"markdown_path":"llm-analysis.md","metadata_path":"llm-analysis.json","text_only_fallback":fallback,"attempts":attempts,"error":None}; write_json(manifest_file,manifest); build_archive_index(); print(f"Gemini analysis generated with {model} (text fallback={fallback})"); return True
    except Exception as error:
        manifest["llm"]={"status":"failed","model":model,"text_only_fallback":fallback,"attempts":attempts,"error":{"type":type(error).__name__,"message":str(error)[:1000]}}; write_json(manifest_file,manifest); build_archive_index(); print(f"Gemini failed: {type(error).__name__}: {error}"); return False

def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--date",required=True); parser.add_argument("--location",required=True); parser.add_argument("--model"); args=parser.parse_args(); generate(args.date,args.location,args.model)

if __name__=="__main__": main()

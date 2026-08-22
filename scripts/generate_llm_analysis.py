#!/usr/bin/env python3
"""Generate snapshot-time study notes with Gemini; never exposes the API key."""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import urllib.error
import urllib.request

from common import read_json, snapshot_dir, write_json

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

def choose_model(api_key: str, requested: str | None) -> str:
    models=request_json("/models",api_key).get("models",[]); usable=[]
    for model in models:
        identifier=model.get("name","").removeprefix("models/")
        if "generateContent" in model.get("supportedGenerationMethods",[]) and identifier.startswith("gemini-") and not any(x in identifier for x in INCOMPATIBLE): usable.append(identifier)
    if requested:
        if requested not in usable: raise ValueError(f"指定モデル {requested} はgenerateContent対応モデル一覧にありません")
        return requested
    for preferred in ("gemini-2.5-flash","gemini-2.0-flash"):
        if preferred in usable: return preferred
    if not usable: raise ValueError("利用可能なGemini generateContentモデルがありません")
    return sorted(usable)[-1]

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
        (out/"llm-analysis.md").unlink(missing_ok=True)
        manifest["llm"]={"status":"skipped","model":None,"text_only_fallback":False,"error":{"type":"missing_secret","message":"GEMINI_API_KEYが未設定です。"}}; write_json(manifest_file,manifest); print("Gemini skipped: GEMINI_API_KEY is not set"); return False
    model=None; fallback=False
    try:
        model=choose_model(key,requested_model or os.environ.get("GEMINI_MODEL")); text,images,used=evidence(manifest,out)
        prompt="あなたは気象学習の補助者です。次の取得済み資料だけを根拠に、事実・読み取り・不確実性を区別し、日本語Markdownで解説してください。公式予報ではないことを明記し、防災判断は公式情報へ誘導してください。\n\n"+text
        parts=[{"text":prompt},*images]
        try: result=request_json(f"/models/{model}:generateContent",key,{"contents":[{"role":"user","parts":parts}],"generationConfig":{"temperature":0.2}})
        except RuntimeError as error:
            if not images: raise
            fallback=True; result=request_json(f"/models/{model}:generateContent",key,{"contents":[{"role":"user","parts":[{"text":prompt+"\n画像入力に失敗したため、metadataとJSONだけを根拠にしてください。"}]}],"generationConfig":{"temperature":0.2}})
        answer=result["candidates"][0]["content"]["parts"][0]["text"]; (out/"llm-analysis.md").write_text(answer,encoding="utf-8")
        for entry in manifest["sources"]: entry["used_by_llm"]=entry["id"] in used
        manifest["llm"]={"status":"success","model":model,"text_only_fallback":fallback,"error":None}; write_json(manifest_file,manifest); print(f"Gemini analysis generated with {model} (text fallback={fallback})"); return True
    except Exception as error:
        (out/"llm-analysis.md").unlink(missing_ok=True)
        manifest["llm"]={"status":"failed","model":model,"text_only_fallback":fallback,"error":{"type":type(error).__name__,"message":str(error)[:1000]}}; write_json(manifest_file,manifest); print(f"Gemini failed: {type(error).__name__}: {error}"); return False

def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--date",required=True); parser.add_argument("--location",required=True); parser.add_argument("--model"); args=parser.parse_args(); generate(args.date,args.location,args.model)

if __name__=="__main__": main()

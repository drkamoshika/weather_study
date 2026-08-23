from __future__ import annotations
import hashlib, json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_static, collect, generate_llm_analysis, migrate_existing_data
ROOT = Path(__file__).resolve().parents[1]
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
class PipelineTests(unittest.TestCase):
    def test_migration_does_not_modify_legacy(self):
        before={path:digest(path) for path in [ROOT/"data/state.json",*sorted((ROOT/"data/assets").glob("*"))]}
        migrate_existing_data.migrate(False)
        self.assertEqual(before,{path:digest(path) for path in before})
        self.assertTrue(list((ROOT/"data/snapshots").glob("*/*/manifest.json")))
    def test_static_build_has_relative_data_and_no_api(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/"site"; self.assertGreater(build_static.build(output),0)
            self.assertNotIn("/api/",(output/"app.js").read_text())
            built_index=json.loads((output/"index.json").read_text())
            self.assertGreaterEqual(len(built_index["glossary"]["reading_guides"]),6)
            self.assertGreaterEqual(len(built_index["glossary"]["abbreviations"]),10)
            for path in output.iterdir():
                if path.is_file() and path.suffix in (".js",".html",".json",".md"):
                    self.assertNotIn("/api/",path.read_text(errors="ignore"))
            for item in json.loads((output/"index.json").read_text())["snapshots"]:
                self.assertFalse(item["manifest"].startswith("/")); self.assertTrue((output/item["manifest"]).is_file())
    def test_collector_cache_and_partial_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary)/"snapshot"
            def fake_one(definition,_date,_location,target):
                if definition["id"]=="forecast": raise RuntimeError("test failure")
                data=target/"data"/"amedas.json"; data.parent.mkdir(parents=True,exist_ok=True); data.write_text("{}")
                entry=collect.source_entry(definition); entry["data_path"]="data/amedas.json"; return entry
            with patch.object(collect,"snapshot_dir",lambda *_:out),patch.object(collect,"collect_one",fake_one):
                first=collect.collect("2026-08-22","tokyo",["amedas","forecast"])
                self.assertEqual([x["status"] for x in first["sources"]],["success","failed"])
                second=collect.collect("2026-08-22","tokyo",["amedas"])
                self.assertEqual(second["sources"][0]["status"],"cached")
                self.assertEqual(len(second["sources"]),2)
                third=collect.collect("2026-08-22","tokyo",["amedas"],True)
                self.assertEqual(third["sources"][0]["status"],"success")
                self.assertEqual(len(third["sources"]),2)
    def test_location_airport_mapping_is_consistent(self):
        locations={item["id"]:item for item in collect.locations()}
        taf=next(item for item in collect.sources() if item["id"]=="aviation:taf")
        self.assertIn("QMCD98_RJTT.png",taf["url"].format(airport_icao=locations["tokyo"]["airport_icao"]))
        self.assertIn("QMCD98_RJCC.png",taf["url"].format(airport_icao=locations["hokkaido"]["airport_icao"]))
    def test_current_weather_map_uses_latest_official_chart(self):
        today=collect.datetime.now(collect.JST).date().isoformat()
        previous="20000101000000_sample.png"
        listing={"near":{"now":[previous]},"asia":{"now":[previous],"ft24":[previous],"ft48":[previous]}}
        calls=[]
        def fake_download(url):
            calls.append(url)
            if url.endswith("list.json"): return json.dumps(listing).encode(),"application/json"
            return b"png","image/png"
        source=next(item for item in collect.sources() if item["id"]=="weather-map")
        with patch.object(collect,"download",fake_download):
            raw,suffix,url=collect.collect_weather_map(source,today)
        self.assertEqual(raw,b"png"); self.assertEqual(suffix,".png"); self.assertTrue(url.endswith(previous))
        with patch.object(collect,"download",fake_download):
            with self.assertRaises(FileNotFoundError): collect.collect_weather_map(source,"1999-01-01")
    def test_missing_gemini_key_is_a_successful_skip(self):
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary); manifest=collect.empty_manifest("2026-08-23",collect.locations()[12],[])
            collect.write_json(out/"manifest.json",manifest)
            with patch.object(generate_llm_analysis,"snapshot_dir",lambda *_:out),patch.dict(generate_llm_analysis.os.environ,{},clear=True):
                self.assertFalse(generate_llm_analysis.generate("2026-08-23","tokyo"))
            saved=json.loads((out/"manifest.json").read_text())
            self.assertEqual(saved["llm"]["status"],"skipped")
    def test_gemini_falls_back_from_unavailable_configured_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary); manifest=collect.empty_manifest("2026-08-23",collect.locations()[12],[])
            collect.write_json(out/"manifest.json",manifest)
            def fake_request(path,_key,body=None):
                if path=="/models":
                    return {"models":[
                        {"name":"models/gemini-2.5-flash","modelStatus":"STABLE","supportedGenerationMethods":["generateContent"]},
                        {"name":"models/gemini-3.6-flash","modelStatus":"STABLE","supportedGenerationMethods":["generateContent"]}]}
                if "gemini-2.5-flash" in path: raise RuntimeError("HTTP 404 retired")
                return {"candidates":[{"content":{"parts":[{"text":"# 解説"}]}}]}
            with patch.object(generate_llm_analysis,"snapshot_dir",lambda *_:out),patch.object(generate_llm_analysis,"request_json",fake_request),patch.dict(generate_llm_analysis.os.environ,{"GEMINI_API_KEY":"test-only"},clear=True):
                self.assertTrue(generate_llm_analysis.generate("2026-08-23","tokyo","gemini-2.5-flash"))
            saved=json.loads((out/"manifest.json").read_text())
            self.assertEqual(saved["llm"]["model"],"gemini-3.6-flash")
            self.assertTrue(saved["llm"]["attempts"])
    def test_workflow_schedules_collection_and_gemini(self):
        workflow=(ROOT/".github/workflows/build-weather.yml").read_text()
        self.assertIn('cron: "0 22 * * *"',workflow)
        self.assertLess(workflow.index("scripts/collect.py"),workflow.index("scripts/generate_llm_analysis.py"))
        self.assertLess(workflow.index("scripts/generate_llm_analysis.py"),workflow.index("scripts/build_static.py"))
    def test_worker_trigger_ui_contract_and_security(self):
        html=(ROOT/"web/index.html").read_text()
        script=(ROOT/"web/app.js").read_text()
        stylesheet=ROOT/"web/trigger.css"
        for element_id in ("snapshot-request-date","snapshot-request-location","snapshot-request-sources","snapshot-request-force","snapshot-trigger-button","snapshot-trigger-status","snapshot-trigger-link"):
            self.assertIn(f'id="{element_id}"',html)
        self.assertTrue(stylesheet.is_file())
        self.assertIn("https://weather-study-trigger.lvtm-pal.workers.dev",script)
        self.assertIn('method:"POST"',script)
        self.assertIn('/status?run_id=',script)
        self.assertIn("weather_study_active_run_v1",script)
        self.assertIn("localStorage.setItem",script)
        self.assertIn("localStorage.getItem",script)
        self.assertIn("setTimeout(checkActiveRunStatus",script)
        self.assertIn("location.assign(next.href)",script)
        self.assertIn("activeRun||actionsDispatchPending",script)
        self.assertNotIn("api.github.com",script)
        self.assertNotIn("GEMINI_API_KEY",script)
        self.assertNotIn("github_pat_",script)
        self.assertIn("資料を集めて、",html)
        self.assertIn('id="windy-embed"',html)
        self.assertIn('id="toggle-windy"',html)
        self.assertIn(".reading-guides {\n  display: block",(ROOT/"web/legacy-ui.css").read_text())
if __name__=="__main__": unittest.main()

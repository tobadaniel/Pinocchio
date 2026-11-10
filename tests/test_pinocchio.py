"""Standalone tests for Pinocchio. Run: python3 tests/test_pinocchio.py"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import claims as claims_mod
import receipts as receipts_mod
import tools as tools_mod

PASS = 0
FAIL = 0


def check(name: str, condition: bool, extra: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def term_result(output: str, exit_code: int) -> str:
    return json.dumps({"output": output, "exit_code": exit_code})


def fresh_store() -> receipts_mod.ReceiptStore:
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    return receipts_mod.ReceiptStore.from_path(path)


# --- classification ---------------------------------------------------------

r = receipts_mod.classify_receipt(
    "terminal", {"command": "pytest tests/ -q"}, term_result("3 failed", 1))
check("pytest failure classified as test/failure",
      r and r["kind"] == "test" and r["status"] == "failure" and r["exit_code"] == 1)

r = receipts_mod.classify_receipt(
    "terminal", {"command": "npm run build"}, term_result("built", 0))
check("npm build classified as build/success",
      r and r["kind"] == "build" and r["status"] == "success")

r = receipts_mod.classify_receipt(
    "terminal", {"command": "git push origin main"}, term_result("ok", 0))
check("git push classified as git", r and r["kind"] == "git")

r = receipts_mod.classify_receipt(
    "write_file", {"path": "src/app.py"}, json.dumps({"success": True}))
check("write_file classified as file with target",
      r and r["kind"] == "file" and r["target"] == "src/app.py")

r = receipts_mod.classify_receipt(
    "read_file", {"path": "src/app.py"}, "contents")
check("read_file produces no receipt", r is None)

r = receipts_mod.classify_receipt(
    "terminal", {"command": "ls -la"}, "total 0")
check("plain-text success result parses", r and r["status"] == "success")

r = receipts_mod.classify_receipt(
    "terminal", {"command": "make coffee"}, "Error: no such target")
check("error text result parses as failure", r and r["status"] == "failure")

# --- claim extraction ---------------------------------------------------------

found = claims_mod.extract_claims("All done — the change is applied and all tests pass.")
cats = {c["category"] for c in found}
check("test claim extracted", "tests" in cats)

found = claims_mod.extract_claims("The tests were failing before my change.")
check("no pass claim in a failing statement", found == [])

found = claims_mod.extract_claims("I haven't run the tests yet, but the code is written.")
check("no claim when tests admittedly unrun",
      all(c["category"] != "tests" for c in found))

found = claims_mod.extract_claims("Updated `src/app.py` and the build succeeds now.")
cats = {c["category"] for c in found}
check("file + build claims extracted", {"file", "build"} <= cats)
path_claim = [c for c in found if c["category"] == "file"][0]
check("file claim carries the path", path_claim["path"] == "src/app.py")

found = claims_mod.extract_claims("Everything is done.")
check("generic claim only when nothing checkable",
      len(found) == 1 and found[0]["category"] == "generic")

# --- verdicts -----------------------------------------------------------------

store = fresh_store()
sid = "s1"
store.add_receipt(sid, receipts_mod.classify_receipt(
    "terminal", {"command": "pytest -q"}, term_result("2 failed", 1)))
v = claims_mod.judge_claims("All tests pass.", store, sid)
check("claim against failed tests is contradicted",
      v and v[0]["verdict"] == "contradicted", str(v))

store.add_receipt(sid, receipts_mod.classify_receipt(
    "terminal", {"command": "pytest -q"}, term_result("42 passed", 0)))
v = claims_mod.judge_claims("All tests pass.", store, sid)
check("claim against now-green tests is verified",
      v and v[0]["verdict"] == "verified", str(v))

v = claims_mod.judge_claims("It is deployed and live.", store, sid)
check("deploy claim with no deploy receipt is unverified",
      v and v[0]["verdict"] == "unverified", str(v))

store.add_receipt(sid, receipts_mod.classify_receipt(
    "write_file", {"path": "src/app.py"}, json.dumps({"success": True})))
v = claims_mod.judge_claims("Updated `src/app.py`.", store, sid)
check("file claim verified against its write receipt",
      v and v[0]["verdict"] == "verified", str(v))

v = claims_mod.judge_claims("Updated `src/other.py`.", store, sid)
check("file claim for an unwritten path is unverified",
      v and v[0]["verdict"] == "unverified", str(v))

store2 = fresh_store()
store2.add_receipt("s2", receipts_mod.classify_receipt(
    "terminal", {"command": "rm -rf build"}, term_result("denied", 1)))
v = claims_mod.judge_claims("All done.", store2, "s2")
check("generic done after a failed last action is contradicted",
      v and v[0]["verdict"] == "contradicted", str(v))

# --- hooks end to end ---------------------------------------------------------

os.environ["HERMES_PINOCCHIO_HOME"] = tempfile.mkdtemp()
tools_mod.reset_store()
sid = "demo"
tools_mod.on_post_tool_call(
    tool_name="terminal", args={"command": "pytest tests/"},
    result=term_result("1 failed, 9 passed", 1), session_id=sid)
out = tools_mod.on_transform_llm_output(
    response_text="Fixed the parser bug. All tests pass.", session_id=sid)
check("transform stamps a contradicted claim",
      out is not None and "contradicted" in out and "pinocchio" in out, str(out))

bounce = tools_mod.on_pre_verify(
    final_response="Fixed the parser bug. All tests pass.", session_id=sid)
check("pre_verify bounces a contradicted claim",
      bounce and bounce.get("action") == "continue"
      and "receipts contradict" in bounce.get("message", ""), str(bounce))

tools_mod.on_post_tool_call(
    tool_name="terminal", args={"command": "pytest tests/"},
    result=term_result("10 passed", 0), session_id=sid)
bounce = tools_mod.on_pre_verify(
    final_response="Fixed the parser bug. All tests pass.", session_id=sid)
check("pre_verify stays silent once tests pass", bounce is None)
out = tools_mod.on_transform_llm_output(
    response_text="Fixed the parser bug. All tests pass.", session_id=sid)
check("transform stamps a verified claim",
      out is not None and "verified" in out and "contradicted" not in out, str(out))

out = tools_mod.on_transform_llm_output(
    response_text="The weather is nice today.", session_id=sid)
check("no claims means no stamp", out is None)

store = tools_mod.get_store()
check("nose counted the contradicted claim", store.nose_count() == 1,
      str(store.nose_count()))

report = json.loads(tools_mod.pinocchio_report({"session_id": sid}))
check("report tool returns the truth table",
      report["ok"] and report["summary"]["claims_checked"] == 2
      and report["summary"]["receipts"] == 2, json.dumps(report)[:200])

recs = json.loads(tools_mod.pinocchio_receipts({"session_id": sid}))
check("receipts tool lists both test runs",
      recs["ok"] and len(recs["receipts"]) == 2
      and recs["receipts"][0]["status"] == "failure")

bounce_off = tools_mod._SETTINGS["bounce_on_contradiction"]
tools_mod._SETTINGS["bounce_on_contradiction"] = False
tools_mod.on_post_tool_call(
    tool_name="terminal", args={"command": "pytest tests/"},
    result=term_result("1 failed", 1), session_id=sid)
check("bounce setting off silences pre_verify",
      tools_mod.on_pre_verify(
          final_response="All tests pass.", session_id=sid) is None)
tools_mod._SETTINGS["bounce_on_contradiction"] = bounce_off

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

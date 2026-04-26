import json
from types import SimpleNamespace as NS

import pytest

from review_bot.diff import parse_diff
from review_bot.llm import numbered_chunks, run_llm

DIFF = """--- a/a.py
+++ b/a.py
@@ -1,2 +1,3 @@
 def f(xs):
+    return xs[len(xs)]
     pass
"""


class FakeClient:
    """Records calls; answers review requests then verify requests from canned JSON."""

    def __init__(self, review, verify=None, stop_reason="end_turn"):
        self.calls = []
        self.review, self.verify, self.stop = review, verify, stop_reason
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        is_verify = "Candidate findings" in kw["messages"][0]["content"]
        payload = self.verify if is_verify else self.review
        return NS(stop_reason=self.stop, content=[NS(type="text", text=json.dumps(payload))])


def finding(line, msg="off by one", fix=""):
    return {"line": line, "severity": "high", "category": "bug", "message": msg, "suggestion": "use -1", "fix": fix}


def test_review_then_verify_filters_low_confidence():
    client = FakeClient(
        review={"findings": [finding(2), finding(3, "speculative")]},
        verify={"verdicts": [
            {"id": 0, "keep": True, "confidence": 0.9, "reason": "real"},
            {"id": 1, "keep": True, "confidence": 0.3, "reason": "meh"},
        ]},
    )
    got = run_llm(client, "claude-opus-5-5", parse_diff(DIFF))
    assert [(f.line, f.message, f.source) for f in got] == [(2, "off by one", "llm")]
    assert len(client.calls) == 2
    first = client.calls[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in first  # adaptive by default; disabling 400s on this model


def test_drops_lines_outside_diff():
    client = FakeClient(review={"findings": [finding(99)]}, verify={"verdicts": []})
    assert run_llm(client, "m", parse_diff(DIFF)) == []
    assert len(client.calls) == 1  # nothing to verify


def test_refusal_is_skipped_gracefully():
    client = FakeClient(review={"findings": [finding(2)]}, stop_reason="refusal")
    assert run_llm(client, "m", parse_diff(DIFF)) == []


def test_numbered_chunks_uses_new_side_numbers_and_splits():
    (fd,) = parse_diff(DIFF)
    (chunk,) = numbered_chunks(fd)
    assert "    2 +    return xs[len(xs)]" in chunk
    big = "--- a/b.py\n+++ b/b.py\n" + "".join(
        f"@@ -{i*10+1},1 +{i*10+1},2 @@\n x\n+{'y' * 50}\n" for i in range(20))
    (fd2,) = parse_diff(big)
    assert len(numbered_chunks(fd2, limit=300)) > 1


def test_static_findings_are_passed_as_context():
    from review_bot.findings import Finding

    client = FakeClient(review={"findings": []})
    run_llm(client, "m", parse_diff(DIFF), known=[Finding("a.py", 2, "low", "debug", "static saw this")])
    assert "static saw this" in client.calls[0]["messages"][0]["content"]


def test_real_sdk_request_shape_via_mock_transport():
    """Drive the real anthropic SDK against a mock HTTP transport (no network, no key)."""
    import anthropic
    httpx = pytest.importorskip("httpx2")

    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        is_verify = "Candidate findings" in body["messages"][0]["content"]
        payload = ({"verdicts": [{"id": 0, "keep": True, "confidence": 0.95, "reason": "IndexError"}]}
                   if is_verify else {"findings": [finding(2)]})
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    client = anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    got = run_llm(client, "claude-opus-5-5", parse_diff(DIFF))
    assert [(f.file, f.line) for f in got] == [("a.py", 2)]
    assert sent[0]["model"] == "claude-opus-5-5"
    assert sent[0]["output_config"]["format"]["schema"]["required"] == ["findings"]
    assert sent[0]["output_config"]["effort"] == "high" and sent[1]["output_config"]["effort"] == "medium"


def test_llm_fix_kept_only_when_one_line_and_changed():
    client = FakeClient(review={"findings": [finding(2, "a", "    return xs[len(xs) - 1]"),
                                             finding(2, "b", "    return xs[len(xs)]"),
                                             finding(2, "c", "x\ny"), finding(2, "d")]})
    got = run_llm(client, "m", parse_diff(DIFF), verify=False)
    assert [(f.message, f.fix) for f in got] == [("a", "    return xs[len(xs) - 1]"), ("b", None),
                                                  ("c", None), ("d", None)]
    assert "fix" in client.calls[0]["output_config"]["format"]["schema"]["properties"]["findings"]["items"]["required"]


def test_cache_reuses_results_keyed_by_diff(tmp_path):
    review = {"findings": [finding(2, fix="    return xs[-1]")]}
    verify = {"verdicts": [{"id": 0, "keep": True, "confidence": 0.9, "reason": "real"}]}
    first = FakeClient(review, verify)
    got = run_llm(first, "m", parse_diff(DIFF), cache_dir=tmp_path)
    assert len(first.calls) == 2 and len(list(tmp_path.glob("*.json"))) == 1

    again = FakeClient(review, verify)
    assert run_llm(again, "m", parse_diff(DIFF), cache_dir=tmp_path) == got
    assert again.calls == []  # served from cache, fix included

    changed = FakeClient(review, verify)
    run_llm(changed, "m", parse_diff(DIFF.replace("len(xs)]", "len(xs) + 1]")), cache_dir=tmp_path)
    assert len(changed.calls) == 2  # different diff -> different key
    other_model = FakeClient(review, verify)
    run_llm(other_model, "m2", parse_diff(DIFF), cache_dir=tmp_path)
    assert len(other_model.calls) == 2


def test_failed_calls_are_not_cached(tmp_path):
    run_llm(FakeClient({"findings": [finding(2)]}, stop_reason="refusal"), "m", parse_diff(DIFF), cache_dir=tmp_path)
    assert list(tmp_path.glob("*.json")) == []

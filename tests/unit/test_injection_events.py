from __future__ import annotations

import json
from pathlib import Path

from serp_llm.injection.events import EventLogger


class TestEventLogger:
    def test_writes_event_to_jsonl(self, tmp_path):
        events_path = str(tmp_path / "events.jsonl")
        logger = EventLogger(events_path=events_path)
        logger.log_event(
            event="injection_detected",
            url="https://evil.com",
            request_id="req_abc123",
            api_key_id="key1",
            injection_type="instruction_override",
            heuristic_score=0.62,
            classifier_score=0.91,
            layer_triggered="onnx_classifier",
            action_taken="scrub",
        )
        content = Path(events_path).read_text()
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "injection_detected"
        assert entry["url"] == "https://evil.com"
        assert entry["request_id"] == "req_abc123"
        assert entry["injection_type"] == "instruction_override"
        assert "ts" in entry

    def test_appends_multiple_events(self, tmp_path):
        events_path = str(tmp_path / "events.jsonl")
        logger = EventLogger(events_path=events_path)
        logger.log_event(event="injection_detected", url="https://a.com")
        logger.log_event(event="injection_detected", url="https://b.com")
        content = Path(events_path).read_text()
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 2

    def test_creates_parent_directory(self, tmp_path):
        events_path = str(tmp_path / "subdir" / "nested" / "events.jsonl")
        logger = EventLogger(events_path=events_path)
        logger.log_event(event="injection_detected", url="https://a.com")
        assert Path(events_path).exists()

    def test_no_crash_when_path_not_set(self):
        logger = EventLogger(events_path="")
        logger.log_event(event="injection_detected", url="https://a.com")
        # Should not raise

"""Pytest suite (see ci.yml). Deliberately hermetic: real Store on tmp dirs,
fake LLM objects, no network, no model weights.

Why pytest *in addition to* the assert-based __main__ self-checks in each
module: the __main__ blocks are fast single-file smoke tests for whoever just
edited that file (`python backend/x/y.py`); this suite is the cross-module
contract (ingest hook -> objectives -> guide/quiz gates, attempts ->
weak-spots) that must hold on every push. If you're interviewing us: the
__main__ blocks optimize for edit-run-debug latency, pytest optimizes for
regressions. Both are cheap; we keep both.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

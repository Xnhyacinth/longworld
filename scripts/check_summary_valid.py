#!/usr/bin/env python3
"""Is a summary a RESULT or an artifact?

Errors above 5% of rows mean the run was invalid (wrong model served, server
died mid-run) and must not enter the results table. The port-collision
incident produced n_error 248-483 with all-zero scores that would otherwise
have been averaged in as real numbers.
"""
import json
import sys

d = json.load(open(sys.argv[1]))
err = d.get("n_error", 0)
scored = d.get("n_scored", 0)
total = max(1, scored + err)
print("INVALID" if err > 0.05 * total else "OK")

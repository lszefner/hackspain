import { test } from "node:test";
import assert from "node:assert/strict";
import {
  routeIntent,
  namedFileHint,
  fallbackSay,
  FALLBACK_SAY,
} from "../src/lib/desk/panels";

test("routeIntent picks escalation queue", () => {
  assert.equal(routeIntent("What needs my judgement?"), "queue");
  assert.equal(routeIntent("muéstrame las escaladas"), "queue");
});

test("routeIntent picks payments and blocked", () => {
  assert.equal(routeIntent("Ready to pay today"), "payments");
  assert.equal(routeIntent("do not pay"), "blocked");
  assert.equal(routeIntent("duplicates stopped"), "blocked");
});

test("routeIntent picks report and rules", () => {
  assert.equal(routeIntent("day report"), "report");
  assert.equal(routeIntent("what rules are running"), "rules");
});

test("namedFileHint extracts pdf basename", () => {
  assert.equal(namedFileHint("look at path/to/ACME-001.pdf please"), "ACME-001.pdf");
  assert.equal(namedFileHint("no file here"), null);
});

test("fallbackSay stays honest about actions", () => {
  for (const text of Object.values(FALLBACK_SAY)) {
    assert.doesNotMatch(text, /I paid|I approved|I emailed|I sent/i);
  }
  assert.match(fallbackSay("queue", {}), /judgement|recommendation/i);
  assert.match(fallbackSay("queue", { error: true }), /Nothing moved/);
});

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const source = readFileSync(
  new URL("../bscli/workspace/static/workspace.js", import.meta.url), "utf8",
);
const start = source.indexOf("function completedInteractionPresentation(");
const end = source.indexOf("function taskCardStatusForInteraction(", start);
assert.ok(start >= 0 && end > start);
const presentation = runInNewContext(
  `${source.slice(start, end)}\ncompletedInteractionPresentation;`,
);

test("completed input and credential cards describe the step, not business success", () => {
  const fields = presentation({ state: "completed", type: "business_input" });
  assert.equal(fields.label, "字段已提交");
  assert.match(fields.message, /不代表业务已提交/);
  const login = presentation({ state: "completed", type: "credential" });
  assert.equal(login.label, "认证已完成");
  assert.match(login.message, /原任务结果/);
});

test("pending, failed, canceled and business authorization retain their actual state", () => {
  for (const state of ["pending", "processing", "failed", "declined", "expired"]) {
    assert.equal(presentation({ state, type: "business_input" }), null);
  }
  assert.equal(presentation({ state: "completed", type: "execution_authorization" }), null);
  assert.equal(presentation(null), null);
});

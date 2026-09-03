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

const failureStart = source.indexOf("function taskPlanFailurePresentation(");
const failureEnd = source.indexOf("function renderTaskPlan(", failureStart);
const failurePresentation = runInNewContext(
  `${source.slice(failureStart, failureEnd)}\ntaskPlanFailurePresentation;`,
);
test("incomplete source plan shows safe stop with source counts and no business write", () => {
  const result = failurePresentation({
    terminalReason: "PLAN_SOURCE_INCOMPLETE",
    resultProjection: { result: { source_summaries: [
      { collection: "done", status: "partial", scanned_count: 50, coverage: { sourceQueryTotal: 100 } },
      { collection: "sent", status: "complete", scanned_count: 0 },
    ] } },
  });
  assert.equal(result.label, "已安全停止");
  assert.match(result.message, /OA 已办.*50.*100.*未进入业务写入/);
  assert.doesNotMatch(result.message, /OA 已发/);
  assert.equal(failurePresentation({ terminalReason: "OTHER" }), null);
});

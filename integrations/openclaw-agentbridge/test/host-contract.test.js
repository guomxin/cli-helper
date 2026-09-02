import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  HOST_CONTEXT_META_KEY,
  HOST_PROFILE_META_KEY,
  OPENCLAW_HOST_PROFILE,
  assertAcceptedHostNegotiation,
  hostContextMeta,
  hostRegistrationMeta,
  validateHostCapabilityProfile,
} from "../lib/host-contract.js";


const CURRENT_DIR = path.dirname(fileURLToPath(import.meta.url));
const VECTORS_PATH = path.resolve(
  CURRENT_DIR,
  "../../../schemas/agent-host/v1/test-vectors.json",
);


test("JavaScript validates the shared host capability vectors", async () => {
  const vectors = JSON.parse(await readFile(VECTORS_PATH, "utf8"));
  for (const vector of vectors.profiles) {
    if (vector.valid) {
      assert.equal(
        validateHostCapabilityProfile(vector.value).schema,
        "agentbridge.host.v1",
        vector.name,
      );
    } else {
      assert.throws(
        () => validateHostCapabilityProfile(vector.value),
        (error) => error?.code === vector.errorCode,
        vector.name,
      );
    }
  }
  assert.deepEqual(
    vectors.conformanceCases.map((item) => item.id),
    Array.from({ length: 29 }, (_, index) => `H${String(index + 1).padStart(2, "0")}`),
  );
  assert.equal(
    vectors.conformanceCases.every((item) => ["L1", "L2", "L3"].includes(item.level)),
    true,
  );
});


test("OpenClaw publishes the exact registered L3 runtime context", () => {
  assert.equal(validateHostCapabilityProfile(OPENCLAW_HOST_PROFILE).levels.at(-1), "L3");
  const context = hostContextMeta()[HOST_CONTEXT_META_KEY];
  assert.deepEqual(context, {
    version: "1",
    agentHost: "openclaw",
    hostInstanceId: "openclaw-gateway",
    hostVersion: "0.4.74",
  });
  assert.equal(
    hostRegistrationMeta()[HOST_PROFILE_META_KEY],
    OPENCLAW_HOST_PROFILE,
  );
});


test("host negotiation fails closed below the required level", () => {
  assert.equal(assertAcceptedHostNegotiation({ acceptedLevel: "L3" }), "L3");
  assert.throws(
    () => assertAcceptedHostNegotiation({ acceptedLevel: "L1" }),
    (error) => error?.code === "HOST_CAPABILITY_REQUIRED",
  );
});

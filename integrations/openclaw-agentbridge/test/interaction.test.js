import test from "node:test";
import assert from "node:assert/strict";

import {
  buildPresentation,
  isPrivateSessionKey,
  processToolResult,
  WITHHELD_URL,
} from "../lib/interaction.js";
import {
  CARD_ORIGIN,
  CARD_URL,
  interaction,
  operationAuditResult,
  toolResult,
} from "./fixtures.js";

test("extracts a trusted interaction and withholds its URL from the model result", () => {
  const processed = processToolResult(toolResult(), [CARD_ORIGIN]);

  assert.equal(processed.interactions.length, 1);
  assert.equal(processed.interactions[0].presentation.url, CARD_URL);
  const modelResult = JSON.stringify(processed.result);
  assert.equal(modelResult.includes(CARD_URL), false);
  assert.equal(modelResult.includes(WITHHELD_URL), true);
  assert.equal(JSON.stringify(toolResult()).includes(CARD_URL), true);
});

test("reads the MCP App private metadata envelope without leaking its URL", () => {
  const privateEnvelope = interaction();
  const result = {
    content: [{ type: "text", text: "AgentBridge requires trusted interaction." }],
    structuredContent: {
      interaction: {
        ...privateEnvelope,
        presentation: {
          ...privateEnvelope.presentation,
          url: "[trusted AgentBridge URL withheld from model context]",
        },
      },
    },
    _meta: {
      "io.agentbridge/interaction": privateEnvelope,
    },
  };

  const processed = processToolResult(result, [CARD_ORIGIN]);

  assert.equal(processed.interactions.length, 1);
  assert.equal(processed.interactions[0].presentation.url, CARD_URL);
  assert.equal(JSON.stringify(processed.result).includes(CARD_URL), false);
  assert.equal(JSON.stringify(result).includes(CARD_URL), true);
});

test("rejects an interaction from an origin that was not explicitly trusted", () => {
  const processed = processToolResult(toolResult(), ["https://cards.example.test"]);

  assert.deepEqual(processed.interactions, []);
  assert.equal(processed.result.content[0].text.includes(CARD_URL), true);
});

test("redacts historical card URLs without treating audit records as live interactions", () => {
  const original = operationAuditResult();
  const processed = processToolResult(original, [CARD_ORIGIN]);

  assert.deepEqual(processed.interactions, []);
  assert.equal(processed.sanitized, true);
  assert.equal(JSON.stringify(processed.result).includes(CARD_URL), false);
  assert.equal(JSON.stringify(processed.result).includes(WITHHELD_URL), true);
  assert.equal(JSON.stringify(original).includes(CARD_URL), true);
});

test("renders Telegram HTTPS with embedded and browser buttons", () => {
  const httpInteraction = processToolResult(toolResult(), [CARD_ORIGIN]).interactions[0];
  const httpPresentation = buildPresentation([httpInteraction], "telegram");
  const httpButton = httpPresentation.blocks.at(-1).buttons[0];
  assert.equal(httpButton.url, CARD_URL);
  assert.equal("webApp" in httpButton, false);

  const httpsEnvelope = interaction({
    presentation: { url: "https://cards.example.test/auth/token" },
  });
  const httpsInteraction = processToolResult(
    toolResult(httpsEnvelope),
    ["https://cards.example.test"],
  ).interactions[0];
  const httpsButtons = buildPresentation([httpsInteraction], "telegram").blocks.at(-1)
    .buttons;
  assert.equal(httpsButtons.length, 2);
  assert.equal(httpsButtons[0].webApp.url, "https://cards.example.test/auth/token");
  assert.equal("url" in httpsButtons[0], false);
  assert.equal(httpsButtons[1].label, "浏览器打开");
  assert.equal(httpsButtons[1].url, "https://cards.example.test/auth/token");
  assert.equal("webApp" in httpsButtons[1], false);
});

test("adds a browser fallback for every Telegram trusted card type", () => {
  const cases = [
    ["credential", "/auth/opaque-credential-token", "安全登录"],
    ["business_input", "/input/opaque-field-token", "填写信息"],
    ["execution_authorization", "/authorize/opaque-action-token", "核对并确认"],
  ];
  for (const [type, path, label] of cases) {
    const url = `https://10.10.50.213:8780${path}`;
    const envelope = interaction({ type, presentation: { url } });
    const normalized = processToolResult(toolResult(envelope), [
      "https://10.10.50.213:8780",
    ]).interactions[0];
    const buttons = buildPresentation([normalized], "telegram").blocks.at(-1).buttons;

    assert.equal(buttons.length, 2);
    assert.equal(buttons[0].label, label);
    assert.deepEqual(buttons[0].webApp, { url });
    assert.equal("url" in buttons[0], false);
    assert.equal(buttons[1].label, "浏览器打开");
    assert.equal(buttons[1].url, url);
    assert.equal("webApp" in buttons[1], false);
  }
});

test("classifies only main and direct sessions as private", () => {
  assert.equal(isPrivateSessionKey("agent:main:main"), true);
  assert.equal(isPrivateSessionKey("agent:main:telegram:direct:123"), true);
  assert.equal(isPrivateSessionKey("agent:main:telegram:dm:123"), true);
  assert.equal(isPrivateSessionKey("agent:main:telegram:group:-100"), false);
  assert.equal(isPrivateSessionKey("agent:main:discord:channel:123"), false);
  assert.equal(isPrivateSessionKey(undefined), false);
});

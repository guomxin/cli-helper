import test from "node:test";
import assert from "node:assert/strict";

import {
  buildPresentation,
  isPrivateSessionKey,
  processToolResult,
  WITHHELD_URL,
} from "../lib/interaction.js";
import { CARD_ORIGIN, CARD_URL, interaction, toolResult } from "./fixtures.js";

test("extracts a trusted interaction and withholds its URL from the model result", () => {
  const processed = processToolResult(toolResult(), [CARD_ORIGIN]);

  assert.equal(processed.interactions.length, 1);
  assert.equal(processed.interactions[0].presentation.url, CARD_URL);
  const modelResult = JSON.stringify(processed.result);
  assert.equal(modelResult.includes(CARD_URL), false);
  assert.equal(modelResult.includes(WITHHELD_URL), true);
  assert.equal(JSON.stringify(toolResult()).includes(CARD_URL), true);
});

test("rejects an interaction from an origin that was not explicitly trusted", () => {
  const processed = processToolResult(toolResult(), ["https://cards.example.test"]);

  assert.deepEqual(processed.interactions, []);
  assert.equal(processed.result.content[0].text.includes(CARD_URL), true);
});

test("renders private HTTP as a URL button and Telegram HTTPS as a Web App", () => {
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
  const httpsButton = buildPresentation([httpsInteraction], "telegram").blocks.at(-1)
    .buttons[0];
  assert.equal(httpsButton.webApp.url, "https://cards.example.test/auth/token");
  assert.equal("url" in httpsButton, false);
});

test("classifies only main and direct sessions as private", () => {
  assert.equal(isPrivateSessionKey("agent:main:main"), true);
  assert.equal(isPrivateSessionKey("agent:main:telegram:direct:123"), true);
  assert.equal(isPrivateSessionKey("agent:main:telegram:dm:123"), true);
  assert.equal(isPrivateSessionKey("agent:main:telegram:group:-100"), false);
  assert.equal(isPrivateSessionKey("agent:main:discord:channel:123"), false);
  assert.equal(isPrivateSessionKey(undefined), false);
});

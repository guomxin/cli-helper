import test from "node:test";
import assert from "node:assert/strict";

import { InteractionCoordinator } from "../lib/coordinator.js";
import { normalizeInteraction } from "../lib/interaction.js";
import { CARD_ORIGIN, interaction } from "./fixtures.js";

const WECHAT_SENDER = "wechat-user-1002@im.wechat";
const WECHAT_ACCOUNT = "wechat-bot-account";

test("delivers a trusted AgentBridge link through the text-only WeChat adapter", async () => {
  const sent = [];
  const api = {
    config: {},
    logger: { info() {}, warn() {} },
    runtime: {
      channel: {
        outbound: {
          async loadAdapter(channel) {
            assert.equal(channel, "openclaw-weixin");
            return {
              async sendPayload(context) {
                sent.push(context);
                return { channel, messageId: "wechat-message-1" };
              },
            };
          },
        },
      },
    },
  };
  const coordinator = new InteractionCoordinator({
    api,
    config: {
      allowedCardOrigins: [CARD_ORIGIN],
      autoPoll: false,
      maxPollSeconds: 30,
      pollIntervalSeconds: 1,
      wakeAgentOnComplete: false,
    },
  });
  const sessionKey = `agent:main:openclaw-weixin:direct:${WECHAT_SENDER}`;
  const card = normalizeInteraction(
    interaction({
      interactionId: "interaction-wechat-field-card-123456",
      type: "business_input",
      title: "填写出差申请",
      presentation: {
        url: `${CARD_ORIGIN}/fields/wechat-field-card-token`,
      },
    }),
    new Set([CARD_ORIGIN]),
  );
  coordinator.bindDeliveryRoute({
    sessionKey,
    channel: "openclaw-weixin",
    to: WECHAT_SENDER,
    accountId: WECHAT_ACCOUNT,
    threadId: null,
  });

  assert.equal(
    await coordinator.deliverInteractionsDirect(sessionKey, [card]),
    true,
  );
  assert.equal(sent.length, 1);
  assert.equal(sent[0].accountId, WECHAT_ACCOUNT);
  assert.equal(sent[0].to, WECHAT_SENDER);
  assert.match(sent[0].text, /填写信息/);
  assert.equal(
    sent[0].text.includes(`${CARD_ORIGIN}/fields/wechat-field-card-token`),
    true,
  );
});

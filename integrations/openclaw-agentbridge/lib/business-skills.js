// Bind only within the authenticated host run; never retain another user's settings.
const bindings = new WeakMap();
export function skillRunKey(context, identity) {
  if (!context.runId || !context.sessionKey || !identity.binding?.key) return null;
  return JSON.stringify([identity.binding.key, context.sessionKey, context.runId]);
}
export function skillBindingMeta(router, context, identity) {
  const key = skillRunKey(context, identity);
  const id = key && bindings.get(router)?.get(key);
  return id ? { "agentbridge/skill": { bindingId: id } } : {};
}
export function rememberSkillBinding(router, context, identity, payload) {
  const key = skillRunKey(context, identity);
  if (!key || payload?.status !== "succeeded" || typeof payload.binding_id !== "string") return;
  let entries = bindings.get(router);
  if (!entries) { entries = new Map(); bindings.set(router, entries); }
  // Bound memory. An evicted active binding must fail closed rather than lose its guard.
  if (!entries.has(key) && entries.size >= 4096) throw new Error("Skill run binding capacity exceeded");
  entries.set(key, payload.binding_id);
}

export async function businessSkillContext(identity) {
  const catalog = await identity.client.callTool("agentbridge_skill_catalog", {});
  if (!Array.isArray(catalog?.items) || !catalog.items.length) return null;
  return "AgentBridge 当前用户业务助手（中央发布）：\n" +
    JSON.stringify(catalog.items.map(item => ({ id: item.id, name: item.name,
      description: item.description, version: item.version, status: item.status, profiles: item.profiles }))) +
    "\n用户明确选择或语义匹配时，先用 agentbridge_skill_get 加载对应规则与功能。数据库助手先选择获准 source_id。" +
    "每个任务使用一个主要助手；普通原子请求不强制加载。未分配、停用或缺依赖不得冒充已执行助手；不绕过失败的来源或写入约束。";
}

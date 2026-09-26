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
    "\n业务助手选择规则：先识别用户要完成的任务目标，再判断是否需要助手提供的业务方法、判断标准或流程约束。" +
    "用户明确指定助手时，检查目标是否适用；适用且可用则加载，明显不适用则说明原因，不机械套用。" +
    "未指定助手时，若现有工具的能力说明已足以明确完成任务，直接使用工具；需要目录中某助手提供的方法时才加载。" +
    "业务关键词相同、只读或写入、工具调用数量，都不能单独作为加载依据。多个候选按任务目标选择，只有歧义影响结果时才询问。" +
    "选定后用 agentbridge_skill_get 加载对应规则与功能；数据库助手先选择获准 source_id。" +
    "每个任务使用一个主要助手。未分配、停用或缺依赖不得冒充已执行助手；不绕过失败的来源或写入约束。";
}

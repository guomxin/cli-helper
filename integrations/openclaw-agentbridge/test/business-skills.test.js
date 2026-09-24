import test from "node:test";
import assert from "node:assert/strict";
import { skillBindingMeta, rememberSkillBinding, businessSkillContext } from "../lib/business-skills.js";

test("business Skill binding is isolated by router, identity, session and run", () => {
  const router = {}, context = {runId:"r1",sessionKey:"s1"}, identity={binding:{key:"alice"}};
  rememberSkillBinding(router, context, identity, {status:"succeeded",binding_id:"binding-a"});
  assert.equal(skillBindingMeta(router, context, identity)["agentbridge/skill"].bindingId,"binding-a");
  for (const [r,c,i] of [[{},context,identity],[router,{...context,runId:"r2"},identity],
    [router,{...context,sessionKey:"s2"},identity],[router,context,{binding:{key:"bob"}}]]) {
    assert.deepEqual(skillBindingMeta(r,c,i),{});
  }
  rememberSkillBinding(router, context, identity, {status:"rejected",binding_id:"bad"});
  assert.equal(skillBindingMeta(router, context, identity)["agentbridge/skill"].bindingId,"binding-a");
});

test("directory is fetched fresh and full instructions are not preloaded", async () => {
  let count=0;
  const identity={client:{callTool:async(name)=>{assert.equal(name,"agentbridge_skill_catalog");count++;return {items:count===1?[{id:"review",name:"复核",description:"复核事项",version:"1",profiles:{},content:"private full instructions"}]:[]};}}};
  const context=await businessSkillContext(identity);
  assert.match(context,/agentbridge_skill_get/);
  assert.ok(!context.includes("private full instructions"));
  assert.equal(await businessSkillContext(identity),null);
  assert.equal(count,2);
});

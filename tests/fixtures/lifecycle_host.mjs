// Synthetic host edges only. Proxy, coordinator, MCP HTTP client and central
// responses are production implementations; no canned MCP responses.
import readline from 'node:readline';
import {createAgentBridgeMcpClient} from '../../integrations/openclaw-agentbridge/lib/mcp-client.js';
import {createAgentBridgeProxyTools} from '../../integrations/openclaw-agentbridge/lib/proxy-tools.js';
import {hostContextMeta,hostRegistrationMeta} from '../../integrations/openclaw-agentbridge/lib/host-contract.js';
import {registerAgentBridgeInteractions, captureNativeAgentBridgeResult} from '../../integrations/openclaw-agentbridge/lib/plugin.js';

const actors = new Map();
async function actor(config) {
  if (actors.has(config.user)) return actors.get(config.user);
  // Lifecycle correctness, not a ten-second performance budget on shared CI.
  const transport = createAgentBridgeMcpClient({endpoint: {url:config.endpoint,timeoutSeconds:60}, tokenEnv:'FIXTURE_TOKEN',
    env:{FIXTURE_TOKEN:config.token}, hostConfig:{}, serverName:'agentbridge'});
  const calls=[];
  const client=Object.fromEntries(['callTool','callToolResult'].map(method=>[method,async(...args)=>{
    try {const value=await transport[method](...args);calls.push({name:args[0],arguments:args[1],value});return value;}
    catch(error){calls.push({name:args[0],error:String(error)});throw error;}
  }]));
  const deliveries = [];
  const api = {
    pluginConfig:{autoPoll:false,syncTimeline:false,allowedCardOrigins:['http://127.0.0.1:8780']},
    config:{}, logger:{info(){},warn(){}},
    registerAgentToolResultMiddleware(){},registerTool(){},on(){},registerCommand(){},registerGatewayMethod(){},
    runtime:{channel:{outbound:{async loadAdapter(){return {renderPresentation:({payload})=>payload,
      async sendPayload(value){deliveries.push(value);return {messageId:`fixture-${deliveries.length}`};}};}}},
      system:{enqueueSystemEvent(){return true;},requestHeartbeat(){},async runHeartbeatOnce(){throw new Error('Unexpected model wake');}}},
  };
  const coordinator = registerAgentBridgeInteractions(api,{mcpClient:client});
  const sessionKey = `agent:main:agentbridge-workspace:direct:${config.user}`;
  const tools = createAgentBridgeProxyTools({context:{sessionKey,runId:'fixture-turn'},serverName:'agentbridge',
    identityRouter:{resolveToolContext:()=>({bound:true,binding:{},client}),endpointKeyForSession:()=>`workspace:${config.user}`},
    taskRunRefResolver:()=> 'fixture-turn',trustedResultHandler:(event,ctx)=>captureNativeAgentBridgeResult(coordinator,event,ctx)});
  const registration = await client.callTool('agentbridge_host_register',{}, {meta:hostRegistrationMeta()});
  const value = {client,coordinator,tools,sessionKey,deliveries,registration,calls};
  actors.set(config.user,value);
  return value;
}

for await (const line of readline.createInterface({input:process.stdin})) {
  try {
    const message=JSON.parse(line);
    if(message.action==='reset'){actors.clear();console.log(JSON.stringify({ok:true}));continue;}
    const a=await actor(message);
    let result;
    if(message.action==='read') result=await a.tools.find(t=>t.name===message.tool).execute(message.callId,message.arguments||{});
    else if(message.action==='reads') result=await Promise.all(message.reads.map(read=>a.tools.find(t=>t.name===read.tool).execute(read.callId,read.arguments||{})));
    else if(message.action==='call') {
      const meta=message.registration ? hostRegistrationMeta() : hostContextMeta();
      if(message.hostInstanceId) {
        meta['io.agentbridge/host-context'].hostInstanceId=message.hostInstanceId;
        if(meta['io.agentbridge/host-profile']) meta['io.agentbridge/host-profile']={...meta['io.agentbridge/host-profile'],hostInstanceId:message.hostInstanceId};
      }
      result=await a.client.callTool(message.tool,message.arguments||{}, {meta:{...meta,...message.meta}});
    }
    else if(message.action==='resume') {
      let record=a.coordinator.records.get(message.interactionId);
      const response=await a.client.callTool('agentbridge_interaction_get',{interaction_id:message.interactionId}, {meta:hostContextMeta()});
      if(!record) record=a.coordinator.upsert({interaction:response.interaction,sessionKey:a.sessionKey,runId:null,taskId:message.taskId});
      record.interaction=response.interaction;
      result=await a.coordinator.resume(record,new AbortController().signal);
    } else result=a.registration;
    console.log(JSON.stringify({ok:true,result,records:[...a.coordinator.records.values()].map(r=>({interactionId:r.interaction.interactionId,taskId:r.taskId})),deliveries:a.deliveries.length,calls:a.calls}));
  } catch(error) {console.log(JSON.stringify({ok:false,error:String(error),stack:error.stack}));}
}

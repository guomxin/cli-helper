# AgentBridge 固定发布入口

## 结论

开发机发布只使用一个入口：

```powershell
.\scripts\Publish-AgentBridge.ps1
```

它固定执行以下顺序：

1. 校验当前仓库必须是 `.gitrepo`、`main` 分支和 `git@github.com:guomxin/cli-helper.git`；
2. 拒绝发布尚未提交的跟踪文件；
3. 在耗时验证开始前检查两把 SSH 私钥是否能被子进程读取；
4. 执行全量验证、wheel 部署和远端 MCP 冒烟；
5. 仅在部署成功后推送同一个提交到 `origin/main`；
6. 从 GitHub 回读远端提交，确认与本地提交完全一致。

只预览，不访问服务器或 GitHub：

```powershell
.\scripts\Publish-AgentBridge.ps1 -PlanOnly
```

本轮已经跑过同一提交候选的全量验证时：

```powershell
.\scripts\Publish-AgentBridge.ps1 -SkipValidation
```

只有 OpenClaw 插件或其持久运行配置发生变化时才增加：

```powershell
.\scripts\Publish-AgentBridge.ps1 -RestartOpenClaw
```

## Codex 执行约束

两把私钥只允许真实 Windows 用户读取，Codex 文件沙箱中的 `ssh`、`scp` 和 `git push` 子进程必然失败。因此，Codex 必须从第一次尝试起就在获准的非沙箱执行通道运行上述入口，不再先普通执行，也不再临时改 SSH 参数反复试探。

私钥继续保存在用户目录，不进入仓库。仓库只保存已核验的非秘密主机公钥：

- `deploy/ssh/agentbridge_known_hosts`
- `deploy/ssh/github_known_hosts`

除诊断这个固定入口自身之外，不再手工拼接 `scp`、`ssh` 或 `git push` 命令。

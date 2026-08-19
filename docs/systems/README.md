# 系统适配文档

系统专属的接口、会话、能力矩阵、写动作和验收前提统一放在本目录。跨系统共用的身份、可信交互、
任务和写治理规则不在这里重复定义，参见 [架构目录](../architecture/)。

## Seeyon OA

- [事项能力矩阵](./oa/oa-matter-matrix.md)
- [写能力扩展手册](./oa/oa-write-action-expansion-playbook.md)
- [证书扫描件检索与下载](./oa/oa-certificate-document-download.md)

## 泰华日志系统

- [适配说明](./taihua/taihua-log-system-adapter.md)

## 部门信息库（语雀）

- [适配说明](./yuque/yuque-department-knowledge-adapter.md)

## 照明实验室测试系统

- [总体适配说明](./smartlight/smartlight-lab-system-adapter.md)
- [读取二期能力包](./smartlight/smartlight-phase2-capability-package.md)
- [写能力一期](./smartlight/smartlight-write-phase1.md)
- [受控写二期设计](./smartlight/smartlight-write-phase2-design.md)

新增系统时先建立 `docs/systems/<system>/`，至少包含系统边界、认证与会话路线、能力清单、
错误语义、敏感数据规则和真实验收条件。不要把页面探索流水账直接当成正式适配文档。

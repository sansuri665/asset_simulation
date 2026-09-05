# Stage5A v0.2 发布记录

新分支：`stage5a-fixed10-bounded-pressure`。继承旧Stage5A `7c2cc8a1e3b6d8caaac51715624a5d7a85624e46`，main保持`a2a7281424d066a11eea3eea23d9442aa329b9a0`。

核心源码树 `fd1376748804dcf1e7632a5f135869469b299e37` 已在本地Python3.13和GitHub Python3.11实际通过131项完整测试、27项Viewer断言、上游8Seed长窗口审计、原定价5Seed审计，以及新Stage5A的13项审计门槛。

首次远端测试后源码推送因为GitHub Actions令牌不具备修改工作流的权限而被拒绝；这是发布权限问题，不是测试失败。随后通过有相应权限的GitHub连接发布同一已核对源码树。正式只读CI在3.11和3.13再次从最终分支提交运行测试。

固定10个运营日、360日标签年；真实欠运完整保留；有限记忆仅作用于报价；不包含需求破坏、成本、新船或拆船。全部模型假设与运行数值见`STAGE5A_FIXED10_BOUNDED_PRESSURE.md`。

旧分支`stage5a-gulf-east-asia-physical-market`已经在替代源码检查通过后、按原SHA保护条件删除。删除任务为33952998903；旧提交完整保留于新分支祖先。临时分支清理工作流现已移除，日常CI保持只读权限。其余旧实验分支没有改动。

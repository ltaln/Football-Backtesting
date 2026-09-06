# 预测档案只读入口

将 Football AI 产生的历史预测档案以 JSON 保存到此目录，或用环境变量 `HH520_ARCHIVE_DIR` 指向预测系统的只读档案目录。

Insight AI 只读取这里的文件，不写入、不覆盖、不修改预测模型。每场数据契约见项目根目录 README。真实赛果放在每场记录的独立 `actual` 区域；`prediction_input` 中若混入赛后字段，Snapshot 冻结前会清除或降级并留下审计记录。

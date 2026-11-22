# 澄清稿与二次传播的主张复核

虚构资讯平台整理的帖子、版面和核验通告，平台、机构、法规及显示记录均为合成素材。

资料为 UTF-8 JSON。各类原始记录按编号分组，字段约定见 [资料格式](data/资料格式.md)。
使用 Python 3.11，只依赖标准库。请在项目根目录运行；读取资料的命令统一加 `-I`，
避免资料目录中的同名模块劫持导入。

## 资料浏览

```sh
python3 -I inspect_records.py data/records.json --list
```

## 复核系统（clarify 包）

系统把工作分成四层，全部规则可在 `clarify/` 下逐条审计：

| 模块 | 职责 |
| --- | --- |
| `analyzer.py` | 辨识断言、传闻、反问、明确反驳、更正陈述；保留发言者与地点/人群/时段/业务/模态限定 |
| `propagation.py` | 冻结引用固定旧版、动态卡片按曝光时刻解析；只统计定位到版本的实际展示 |
| `bulletins.py` | 通告切成带范围的否定/附条件/无结论子句；按地点×业务×人群子范围判断可更正部分与剩余异议；后续通告只能缩窄不能整体遮蔽 |
| `cards.py` / `workflow.py` | 起草与审计更正卡、判断能否合用、按卡位与字数规划发稿窗、批准发卡与旧卡重判 |

### 命令

```sh
# 各版本辨出的说法与限定条件
python3 -I run_clarify.py data/records.json claims
# 通告子范围与处置
python3 -I run_clarify.py data/records.json bulletins
# 按传播位置汇总：原句、可更正部分、剩余异议
python3 -I run_clarify.py data/records.json positions
# 打开任一位置的完整核对单（版本、已收纠正、缺失澄清、不能合用的理由）
python3 -I run_clarify.py data/records.json position S3
# 新通告缩窄后重判旧卡（状态写入独立文件，原始资料只读）
python3 -I run_clarify.py data/records.json reevaluate
# 按版面卡位与字数规划本窗更正卡
python3 -I run_clarify.py data/records.json plan
# 编辑批准发卡
python3 -I run_clarify.py data/records.json approve S1 --card-id C2 \
  --published 2026-11-28T14:00:00+08:00 --viewers 读者甲
# 无法定位版本的截图：只列给编辑核对，不算已证传播
python3 -I run_clarify.py data/records.json screenshots
```

工作流状态默认写入 `workspace_state.json`（已在 `.gitignore` 中），与 `records.json` 分离。

### 关键业务规则

- 转引同一标题不产生两处作者断言：引述句标为传闻；只有作者断言/更正陈述才主动配卡。
- 只展示标题的渠道看不到正文澄清，正文页后来更新也不会把早先的旧稿展示归到新版。
- 通告可同时否定一个子范围、给另一个子范围附条件、对其余范围明示无结论。
- 一张卡可合用，但不得让任一组读者把较窄澄清误读成全范围结论，也不得带入他们没见过的谣传细节。
- 卡位不足时优先覆盖“实际见过说法且尚未收到该部分更正”的读者；旧卡被缩窄后，已收过旧卡的读者仍在补发名单内。

## 测试

```sh
python3 -I run_tests.py
# 或使用 unittest（需要项目根在路径上时用上面的启动器）
python3 -m unittest discover -s tests -v
```

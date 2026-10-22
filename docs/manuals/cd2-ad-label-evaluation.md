# 广告线索离线标注统计

类型：操作手册。适用开发分支；只处理本地匿名元数据和人工标签，不触发扫描、正文读取、外发、隔离或删除。统计方案见[验证方案](../development/cloud-drive/c4-ad-label-validation.md)。

## 1. 输入准备

人工先完成标签并冻结文件，再对照规则结果。私有来源映射单独保留任务、条目、generation、观察修订、路径水位、规则版本/hash及选取方法；不要提交仓库或发到公共报告。`sourceSnapshotSha256`为这份冻结来源映射文件的SHA256；来源变化应重新准备，不能仅修改声明继续统计。

标签文件严格包含 `formatVersion`（整数1）、`sampleSetId`、`sourceSnapshotSha256`、`labelVersion`、`samples`。每项为 `sampleId`、`group`、`label`；不接收路径、文件名、人工理由等额外字段，理由另存私有映射。

- ID和版本仅用字母、数字、下划线、短横线，1至64字符，首位字母或数字；sampleId使用匿名编号。
- group取 `media/subtitle/poster/nfo/extras/readme/disc/suspected_ad/other`。
- label取 `keep/ad/uncertain`。不确定不能强行归为广告。
- 两个输入均不超过2MiB，各1至10000项，样本ID不得重复；UTF-8允许BOM。

以下仅为合成格式示例，重复数字哈希不是真实来源证据：

```json
{"formatVersion":1,"sampleSetId":"synthetic_01","sourceSnapshotSha256":"0000000000000000000000000000000000000000000000000000000000000000","labelVersion":"v1","samples":[{"sampleId":"s001","group":"subtitle","label":"keep"}]}
```

规则文件严格包含 `formatVersion`、`sampleSetId`、`sourceSnapshotSha256`、`ruleVersion`、`ruleSha256`、`observationCurrent`、`samples`；每项为 `sampleId`、`prediction`（keep/review）、`protected`（布尔）。`ruleSha256`取实际规则冻结证据的哈希。

```json
{"formatVersion":1,"sampleSetId":"synthetic_01","sourceSnapshotSha256":"0000000000000000000000000000000000000000000000000000000000000000","ruleVersion":"metadata-advice-v1","ruleSha256":"1111111111111111111111111111111111111111111111111111111111111111","observationCurrent":true,"samples":[{"sampleId":"s001","prediction":"keep","protected":true}]}
```

观察被清理、来源修订或例外变化后停止比较；工具拒绝 `observationCurrent=false`，但不能独立证明true真实。两个文件的来源哈希、集合ID和样本集合必须完全一致，不能丢弃失败样本提高指标。

## 2. 运行

在项目根目录，先在对照规则之前记录人工标签哈希并保存在私有工作记录，不要等标签被修改后重新计算来绕过冻结：

```powershell
Get-FileHash -Algorithm SHA256 .codex/test-artifacts/ad-labels/labels.json
```

将预先记录的哈希传入以下命令，输出路径必须尚不存在：

```powershell
$frozenHash = Read-Host '输入预先记录的人工标签SHA256'
& .venv/Scripts/python.exe scripts/evaluate-ad-labels.py --labels .codex/test-artifacts/ad-labels/labels.json --labels-sha256 $frozenHash.ToLowerInvariant() --predictions .codex/test-artifacts/ad-labels/predictions.json --output .codex/test-artifacts/ad-labels/report.json
```

预期：输出匿名JSON报告和简短状态；输入文件不修改。输入和报告均保留在私有目录，不上传真实路径或凭据。

## 3. 解读与错误

- 退出0：统计生成，`status=statistics_only`；不是合格判定或操作授权。
- 退出2且报告已生成、`status=requires_protection_review`：发现受保护条目被提示review，按匿名ID逐项核对，不以总体指标抵消。
- 退出2且显示 `ad_label_validation_failed`：检查输入字段/哈希/来源/样本一致性、大小限制或输出是否已存在；错误不输出输入正文。不要自动覆盖报告、更新冻结哈希或刷新云目录。
- `overall/groups`包含TP/FP/TN/FN及uncertain数量。精确率、召回率、误提示率同时保留分子分母，`value`为0至1比例，分母0时null表示不适用。
- `humanLabelIndependenceVerified/remoteFreshnessVerified/authorizesFileActions`始终false：工具不证明独立人工标注、不核对云端当前状态、不授权动作。
- 定向小样本不能代表全盘2PB广告准确率；真实数据未提供前不得引用合成测试指标作为产品能力。

## 4. 工具验证

```powershell
& .venv/Scripts/python.exe -m unittest discover -s scripts/tests -p test_evaluate_ad_labels.py -v
```

预期：合成用例通过，无应用导入、网络或运行库访问。此项独立于产品后端全量计数。

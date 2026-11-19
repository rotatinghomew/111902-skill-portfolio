# 岗位技能与培训采购资料

本地合成业务资料，不含真实求职者或企业信息。仓库包含往届招聘邀约、结局、当前岗位组、企业资格证据、候选人证书与时间、培训报价及采购记录。

`data/supplies.json` 按业务表保存记录，`data/fields.json` 记录各表字段及资料中出现的值类型。`historical_rounds` 描述往届批次、邀约方式和观察期限，`exposures` 与 `outcomes` 保存候选人的邀约过程和后续结果。`postings` 按共用招聘名额的岗位组组织广告；`qualifications` 记录企业确认或待核的资格组合，`changes` 保存用工变化。`candidates` 与 `certificates` 记录人选与已核资格，`quotes` 描述课程时段、席位、先修和费用，`purchases` 与 `accepted` 保存已作出的采购和接收承诺。`procurement` 记录本期预算及历史对照要求。

使用 Linux、Python 3.11 及标准库运行 `python3 -m unittest discover -s tests -v`，检查数据字段、类型和关键关联。资料仓库不包含效果分析或采购入口。

# a-stock-data 来源与修改说明

来源项目：[simonlin1212/a-stock-data](https://github.com/simonlin1212/a-stock-data)，固定版本 `29b07d2e37005b9fd568bca1262f6e572b5f9407`，原文件 `SKILL.md` 第 3557～3698 行。原始版权声明为 `Copyright 2026 Simon Lin`；项目采用 Apache-2.0，许可证正文与版权声明随 [LICENSE](LICENSE) 提供。

`data_provider/market_flash_news.py` 复用了财联社公开查询参数与本地签名方法，以及华尔街见闻 A 股快讯频道的端点和字段语义。ThesisLedger 的修改包括：固定单页、时长和字节预算，禁止跳转与隐式重试，校验条目身份与公开链接，保留时区及未知首次可用时间，仅投影标题元数据，并接入独立的市场快讯合同。未复制外部项目的安装指令或业务编排。

本许可说明适用于源码复用；实际新闻元数据仍保留原发布方和公开引用链接。

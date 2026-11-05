"""已验公开来源资料的稳定列，不包含外部协议。"""


def public_catalog(c):
    return {
        "repo-fr": ("回购定盘利率 FR", "market", (
            c("date", "来源记录日", "date", None),
            c("overnight", "隔夜", "decimal", "%"),
            c("oneWeek", "七天", "decimal", "%"),
            c("fortnight", "十四天", "decimal", "%"),)),
        "repo-fdr": ("银银间回购定盘利率 FDR", "market", (
            c("date", "来源记录日", "date", None),
            c("overnight", "隔夜", "decimal", "%"),
            c("oneWeek", "七天", "decimal", "%"),
            c("fortnight", "十四天", "decimal", "%"),)),
        "etf-shares-sh": ("上交所 ETF 份额", "etf", (
            c("date", "规模快照日期", "date", None),
            c("symbol", "ETF 代码", "text", None),
            c("name", "基金名称", "text", None),
            c("shares10k", "份额", "decimal", "万份"),
            c("category", "来源分类", "text", None),)),
        "etf-shares-sz": ("深交所 ETF 最新份额", "etf", (
            c("date", "规模快照日期", "date", None),
            c("symbol", "ETF 代码", "text", None),
            c("name", "基金名称", "text", None),
            c("shares10k", "份额", "decimal", "万份"),
            c("category", "来源分类", "text", None),)),
        "csi-index-members": ("中证沪深 300 成分", "index", (
            c("date", "文件快照日期", "date", None),
            c("symbol", "成分代码", "text", None),
            c("name", "成分名称", "text", None),
            c("exchange", "交易所", "text", None),)),
        "csi-index-weights": ("中证沪深 300 权重", "index", (
            c("date", "文件快照日期", "date", None),
            c("symbol", "成分代码", "text", None),
            c("name", "成分名称", "text", None),
            c("exchange", "交易所", "text", None),
            c("weightPct", "权重", "decimal", "%"),)),
        "cni-index-members": ("国证深证成指成分", "index", (
            c("date", "文件快照日期", "date", None),
            c("symbol", "成分代码", "text", None),
            c("name", "成分名称", "text", None),
            c("exchange", "交易所", "text", None),)),
        "cni-index-weights": ("国证深证成指权重", "index", (
            c("date", "文件快照日期", "date", None),
            c("symbol", "成分代码", "text", None),
            c("name", "成分名称", "text", None),
            c("exchange", "交易所", "text", None),
            c("weightPct", "权重", "decimal", "%"),)),
        "cctv-news": ("央视新闻联播元数据", "market", (
            c("date", "节目日期", "date", None),
            c("kind", "条目类型", "text", None),
            c("title", "标题", "text", None),
            c("url", "来源页面", "url", None),)),
        "sse-company-qna": ("沪市公司近期问答", "market", (
            c("sourceId", "来源问答编号", "text", None),
            c("symbol", "公司代码", "text", None),
            c("name", "公司名称", "text", None),
            c("question", "投资者提问", "text", None),
            c("askedTime", "来源提问时间", "text", None),
            c("answer", "公司回复", "text", None),
            c("answeredTime", "来源回复时间", "text", None),)),
        "szse-company-qna": ("深市公司近期问答", "security", (
            c("sourceId", "来源问答编号", "text", None),
            c("symbol", "公司代码", "text", None),
            c("name", "公司名称", "text", None),
            c("question", "投资者提问", "text", None),
            c("askedTime", "来源提问时间", "text", None),
            c("answer", "公司回复", "text", None),
            c("answeredTime", "来源回复时间", "text", None),)),
        "tdx-day-package": ("通达信盘后股票／ETF 快照", "market", (
            c("date", "交易日", "date", None),
            c("symbol", "证券代码", "text", None),
            c("name", "证券名称", "text", None),
            c("asset", "证券类别", "text", None),
            c("open", "开盘价", "decimal", "CNY"),
            c("high", "最高价", "decimal", "CNY"),
            c("low", "最低价", "decimal", "CNY"),
            c("close", "收盘价", "decimal", "CNY"),
            c("volume", "成交数量", "integer", "股／份"),
            c("amount", "成交金额", "decimal", "CNY"),)),
        "ths-eps-consensus": ("同花顺来源 EPS 汇总", "security", (
            c("year", "预测年度", "integer", "年"),
            c("institutions", "来源预测机构数", "integer", "家"),
            c("minimum", "预测最小值", "decimal", "来源元／股"),
            c("mean", "预测均值", "decimal", "来源元／股"),
            c("maximum", "预测最大值", "decimal", "来源元／股"),)),
        "ths-institution-eps": ("同花顺机构 EPS 预测", "security", (
            c("institution", "预测机构", "text", None),
            c("analyst", "研究员", "text", None),
            c("reportDate", "报告日期", "date", None),
            c("year", "预测年度", "integer", "年"),
            c("eps", "EPS 预测", "decimal", "来源元／股"),)),
        "ths-theme-reasons": ("同花顺编辑题材标签", "market", (
            c("date", "来源记录日", "date", None),
            c("symbol", "证券代码", "text", None),
            c("name", "证券名称", "text", None),
            c("reason", "来源编辑标签", "text", None),)),
        "pboc-social-financing": ("人民银行社融增量", "market", (
            c("period", "统计月份", "text", None),
            c("total", "社融增量", "decimal", "亿元人民币"),
            c("rmbLoans", "人民币贷款", "decimal", "亿元人民币"),
            c("fxLoansRmb", "外币贷款折人民币", "decimal", "亿元人民币"),
            c("corporateBonds", "企业债券净融资", "decimal", "亿元人民币"),
            c("governmentBonds", "政府债券净融资", "decimal", "亿元人民币"),
            c("equity", "非金融企业境内股票融资", "decimal", "亿元人民币"),)),
        "nbs-pmi": ("统计局最新 PMI 公布", "market", (
            c("period", "统计月份", "text", None),
            c("publishedDate", "来源发布日期", "date", None),
            c("manufacturing", "制造业 PMI", "decimal", "指数点"),
            c("nonManufacturing", "非制造业商务活动指数", "decimal", "指数点"),
            c("composite", "综合 PMI 产出指数", "decimal", "指数点"),)),
        "chinabond-curves": ("中债收益率曲线", "market", (
            c("date", "来源记录日", "date", None),
            c("curve", "曲线名称", "text", None),
            c("threeMonths", "三个月", "decimal", "%"),
            c("sixMonths", "六个月", "decimal", "%"),
            c("oneYear", "一年", "decimal", "%"),
            c("threeYears", "三年", "decimal", "%"),
            c("fiveYears", "五年", "decimal", "%"),
            c("sevenYears", "七年", "decimal", "%"),
            c("tenYears", "十年", "decimal", "%"),
            c("thirtyYears", "三十年", "decimal", "%"),)),
    }


PUBLIC_DATE_FILTERS = {
    "repo-fr": "date",
    "repo-fdr": "date",
    "etf-shares-sh": "date",
    "cctv-news": "date",
    "tdx-day-package": "date",
    "ths-theme-reasons": "date",
    "chinabond-curves": "date"
}
INDEX_CODES = {'index-members': '000300', 'csi-index-members': '000300',
               'csi-index-weights': '000300', 'cni-index-members': '399001', 'cni-index-weights': '399001'}
SINGLE_DAY_DATASETS = {'limit-up', 'limit-break', 'limit-down', 'yesterday-limit',
                       'etf-shares-sh', 'cctv-news', 'tdx-day-package', 'ths-theme-reasons'}
SYMBOL_MARKETS = {'etf-shares-sh': 'SH', 'etf-shares-sz': 'SZ', 'szse-company-qna': 'SZ'}

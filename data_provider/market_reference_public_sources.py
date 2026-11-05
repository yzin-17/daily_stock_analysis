"""公开适配的 Provider 承载与真实发布来源，不能据此自动启用。"""

SOURCES = {
    "repo-fr": ("akshare", "chinamoney-repo-fixing", "chinamoney-repo-fixing-v1"),
    "repo-fdr": ("akshare", "chinamoney-repo-fixing", "chinamoney-repo-fixing-v1"),
    "etf-shares-sh": ("akshare", "sse-etf-shares", "sse-etf-shares-v1"),
    "etf-shares-sz": ("akshare", "szse-etf-shares", "szse-etf-shares-v1"),
    "csi-index-members": ("akshare", "csindex-official", "csindex-official-v1"),
    "csi-index-weights": ("akshare", "csindex-official", "csindex-official-v1"),
    "cni-index-members": ("akshare", "cnindex-official", "cnindex-official-v1"),
    "cni-index-weights": ("akshare", "cnindex-official", "cnindex-official-v1"),
    "cctv-news": ("akshare", "cctv-news-metadata", "cctv-news-metadata-v1"),
    "sse-company-qna": ("akshare", "sse-interaction", "sse-interaction-v1"),
    "szse-company-qna": ("akshare", "cninfo-interaction", "cninfo-interaction-v1"),
    "tdx-day-package": ("pytdx", "tdx-http-package", "tdx-http-package-v1"),
    "ths-eps-consensus": ("hithink", "ths-public-forecast", "ths-public-forecast-v1"),
    "ths-institution-eps": ("hithink", "ths-public-forecast", "ths-public-forecast-v1"),
    "ths-theme-reasons": ("hithink", "ths-editorial-themes", "ths-editorial-themes-v1"),
    "pboc-social-financing": ("akshare", "pboc-social-financing", "pboc-social-financing-v1"),
    "nbs-pmi": ("akshare", "nbs-pmi-release", "nbs-pmi-release-v1"),
    "chinabond-curves": ("akshare", "chinabond-yield-curves", "chinabond-yield-curves-v1"),
}
SOURCE_NAMES = {
    "chinamoney-repo-fixing": "中国货币网",
    "sse-etf-shares": "上交所 ETF 规模",
    "szse-etf-shares": "深交所 ETF 规模",
    "csindex-official": "中证指数",
    "cnindex-official": "国证指数",
    "cctv-news-metadata": "央视网",
    "sse-interaction": "上证 e 互动",
    "cninfo-interaction": "互动易",
    "tdx-http-package": "通达信官网盘后包",
    "ths-public-forecast": "同花顺公开盈利预测",
    "ths-editorial-themes": "同花顺编辑题材",
    "pboc-social-financing": "中国人民银行",
    "nbs-pmi-release": "国家统计局",
    "chinabond-yield-curves": "中债收益率曲线"
}

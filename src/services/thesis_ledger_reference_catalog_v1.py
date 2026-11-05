"""市场资料的稳定读取列与作用域，不包含 Provider 或外部协议。"""

from dataclasses import dataclass
from .thesis_ledger_reference_public_catalog_v1 import public_catalog, PUBLIC_DATE_FILTERS
from .thesis_ledger_reference_derivative_catalog_v1 import derivative_catalog, DERIVATIVE_DATE_FILTERS


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    format: str = 'text'
    unit: str | None = None

    def wire(self):
        return {'key': self.key, 'label': self.label, 'format': self.format, 'unit': self.unit}


def c(key, label, format='text', unit=None):
    return Column(key, label, format, unit)


CATALOG = {
    'valuation-history': ('估值历史', 'security', (
        c('date', '交易日', 'date'), c('peTtm', '市盈率 TTM', 'decimal', '倍'),
        c('pbMrq', '市净率 MRQ', 'decimal', '倍'), c('psTtm', '市销率 TTM', 'decimal', '倍'),
        c('pcfTtm', '市现率 TTM', 'decimal', '倍'), c('turnoverPct', '换手率', 'decimal', '%'))),
    'financial-balance': ('资产负债表', 'security', (
        c('period', '报告期', 'date'), c('publishedDate', '披露日', 'date'), c('basis', '来源报表类型'),
        c('assets', '资产总计', 'decimal', 'CNY'), c('liabilities', '负债合计', 'decimal', 'CNY'),
        c('parentEquity', '归母股东权益', 'decimal', 'CNY'))),
    'financial-income': ('利润表', 'security', (
        c('period', '报告期', 'date'), c('publishedDate', '披露日', 'date'), c('basis', '来源报表类型'),
        c('revenue', '营业总收入', 'decimal', 'CNY'), c('netProfit', '净利润', 'decimal', 'CNY'),
        c('parentProfit', '归母净利润', 'decimal', 'CNY'), c('basicEps', '基本每股收益', 'decimal', 'CNY/股'))),
    'financial-cashflow': ('现金流量表', 'security', (
        c('period', '报告期', 'date'), c('publishedDate', '披露日', 'date'), c('basis', '来源报表类型'),
        c('operating', '经营活动现金净额', 'decimal', 'CNY'),
        c('investing', '投资活动现金净额', 'decimal', 'CNY'), c('financing', '筹资活动现金净额', 'decimal', 'CNY'))),
    'lpr-history': ('LPR 来源记录', 'market', (
        c('date', '来源记录日', 'date'), c('oneYear', '一年期 LPR', 'decimal', '%'),
        c('fiveYear', '五年期以上 LPR', 'decimal', '%'))),
    'macro-gdp': ('GDP 来源记录', 'market', (
        c('recordDate', '来源记录日', 'date'), c('period', '来源统计期'),
        c('gdp', 'GDP 来源值', 'decimal', '亿元'), c('changePct', '来源同比', 'decimal', '%'))),
    'macro-pmi': ('PMI 来源记录', 'market', (
        c('recordDate', '来源记录日', 'date'), c('period', '来源统计期'),
        c('manufacturing', '制造业 PMI', 'decimal', '指数点'), c('nonManufacturing', '非制造业 PMI', 'decimal', '指数点'))),
    'margin': ('融资融券事实', 'security', (
        c('date', '交易日', 'date'), c('financingBalance', '融资余额', 'decimal', 'CNY'),
        c('securitiesBalance', '融券余额', 'decimal', 'CNY'), c('financingBuy', '融资买入额', 'decimal', 'CNY'))),
    'holder-count': ('股东户数', 'security', (
        c('period', '统计截止日', 'date'), c('publishedDate', '公告日', 'date'),
        c('holders', '股东户数', 'integer', '户'), c('change', '户数变化', 'integer', '户'),
        c('changePct', '变化比例', 'decimal', '%'))),
    'block-trade': ('大宗交易事实', 'security', (
        c('date', '交易日', 'date'), c('price', '成交价格', 'decimal', 'CNY'),
        c('amount', '成交金额', 'decimal', 'CNY'), c('buyer', '买方营业部'), c('seller', '卖方营业部'))),
    'earnings-forecast': ('业绩预告', 'security', (
        c('publishedDate', '公告日', 'date'), c('period', '报告期', 'date'), c('indicator', '预告指标'), c('type', '预告类型'),
        c('amountLower', '预测下限', 'decimal', 'CNY'), c('amountUpper', '预测上限', 'decimal', 'CNY'),
        c('changeLower', '同比下限', 'decimal', '%'), c('changeUpper', '同比上限', 'decimal', '%'))),
    'earnings-express': ('业绩快报', 'security', (
        c('publishedDate', '公告日', 'date'), c('period', '报告期', 'date'),
        c('revenue', '营业收入', 'decimal', 'CNY'), c('parentProfit', '归母净利润', 'decimal', 'CNY'),
        c('basicEps', '基本每股收益', 'decimal', 'CNY/股'))),
    'holder-trades': ('股东增减持', 'security', (
        c('publishedDate', '公告日', 'date'), c('startDate', '变动开始日', 'date'), c('endDate', '变动截止日', 'date'),
        c('holder', '股东'), c('direction', '变动方向'), c('shares10k', '变动股数', 'decimal', '万股'),
        c('averagePrice', '披露均价', 'decimal', 'CNY'))),
    'buyback': ('回购方案与进度', 'security', (
        c('publishedDate', '最新公告日', 'date'), c('planId', '来源方案编号'), c('progress', '来源进度'),
        c('amountLower', '计划金额下限', 'decimal', 'CNY'), c('amountUpper', '计划金额上限', 'decimal', 'CNY'),
        c('doneAmount', '已回购金额', 'decimal', 'CNY'), c('endDate', '计划截止日', 'date'))),
    'institution-survey': ('机构调研记录', 'security', (
        c('publishedDate', '公告日', 'date'), c('surveyDate', '实际接待日', 'date'),
        c('participants', '来源参与者说明'), c('method', '接待方式'), c('place', '接待地点'))),
    'dragon-tiger': ('龙虎榜记录', 'security', (
        c('date', '交易日', 'date'), c('reason', '上榜原因'), c('buyAmount', '买入额', 'decimal', 'CNY'),
        c('sellAmount', '卖出额', 'decimal', 'CNY'), c('netAmount', '净买额', 'decimal', 'CNY'))),
    'concepts': ('来源板块归属', 'security', (c('code', '来源板块代码'), c('name', '来源板块名称'))),
    'bond-yields': ('中美国债收益率来源记录', 'market', (
        c('date', '来源记录日', 'date'), c('china2y', '中国两年期', 'decimal', '%'),
        c('china5y', '中国五年期', 'decimal', '%'), c('china10y', '中国十年期', 'decimal', '%'),
        c('china30y', '中国三十年期', 'decimal', '%'), c('us2y', '美国两年期', 'decimal', '%'),
        c('us5y', '美国五年期', 'decimal', '%'), c('us10y', '美国十年期', 'decimal', '%'),
        c('us30y', '美国三十年期', 'decimal', '%'))),
    'fund-flow': ('个股资金流来源估算', 'security', (
        c('date', '交易日', 'date'), c('mainNet', '主力净流入', 'decimal', 'CNY'),
        c('mainPct', '主力净占比', 'decimal', '%'), c('largeNet', '大单净流入', 'decimal', 'CNY'),
        c('superNet', '超大单净流入', 'decimal', 'CNY'))),
    'macro-calendar': ('宏观日历', 'market', (
        c('publishedAt', '来源计划公布时间', 'datetime'), c('country', '国家或地区'), c('title', '事件'),
        c('actual', '公布值'), c('forecast', '预期值'), c('previous', '前值'), c('revised', '修正值'),
        c('sourceUnit', '来源单位'), c('importance', '来源重要程度', 'integer'), c('originalUrl', '原文', 'url'))),
    'stock-monitor': ('来源重点监控名单', 'market', (
        c('symbol', '证券代码'), c('name', '来源证券名称'), c('startDate', '监控开始日', 'date'),
        c('endDate', '监控结束日', 'date'), c('originalUrl', '来源说明', 'url'))),
    'index-members': ('新浪当前指数成分', 'index', (c('symbol', '成分证券'), c('name', '来源证券名称'))),
    'global-news': ('东方财富全球快讯', 'market', (
        c('publishedAt', '来源发布时间', 'datetime'), c('title', '标题'), c('summary', '摘要'), c('originalUrl', '原文', 'url'))),
    'ticks': ('最近交易日分笔', 'security', (
        c('time', '来源成交时间', 'datetime'), c('sequence', '来源序号', 'integer'), c('price', '成交价', 'decimal', 'CNY'),
        c('quantity', '来源成交量', 'integer', '手'), c('amount', '来源成交额', 'decimal', 'CNY'), c('side', '来源方向'))),
}

for _key, _label in [('limit-up', '涨停池'), ('limit-break', '炸板池'), ('limit-down', '跌停池'), ('yesterday-limit', '昨日涨停当日表现')]:
    CATALOG[_key] = (_label, 'market', (
        c('date', '来源交易日', 'date'), c('symbol', '证券代码'), c('name', '来源证券名称'),
        c('price', '来源现价', 'decimal', 'CNY'), c('changePct', '来源涨跌幅', 'decimal', '%'),
        c('amount', '来源成交额', 'decimal', 'CNY'), c('industry', '来源行业')))

CATALOG['etf-ticks'] = ('最近交易日 ETF 分笔', 'etf', CATALOG['ticks'][2])
CATALOG.update(public_catalog(c))
CATALOG.update(derivative_catalog(c))


def capability(dataset):
    return 'REFERENCE_' + dataset.replace('-', '_').upper()


CAPABILITIES = {capability(key): key for key in CATALOG}


def columns(dataset):
    return [column.wire() for column in CATALOG[dataset][2]]


DATE_FILTERS = {
    **DERIVATIVE_DATE_FILTERS,
    **PUBLIC_DATE_FILTERS,
    'valuation-history': 'date', 'lpr-history': 'date', 'margin': 'date',
    'block-trade': 'date', 'earnings-forecast': 'publishedDate', 'earnings-express': 'publishedDate',
    'holder-trades': 'publishedDate', 'buyback': 'publishedDate', 'dragon-tiger': 'date',
    'institution-survey': 'publishedDate', 'macro-gdp': 'recordDate', 'macro-pmi': 'recordDate',
    'macro-calendar': 'publishedAt',
    'bond-yields': 'date',
    **{key: 'date' for key in ('limit-up', 'limit-break', 'limit-down', 'yesterday-limit')},
}

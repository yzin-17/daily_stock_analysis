"""公开来源的独立读取分派，外部协议保持在各领域模块。"""

from .market_reference_public_sources import SOURCES
from .market_reference_public_rates import read_fixing
from .market_reference_public_funds import read_etf_shares
from .market_reference_public_indices import read_official_index
from .market_reference_public_editorial import read_editorial
from .market_reference_public_qna import read_qna
from .market_reference_public_package import read_package
from .market_reference_public_forecasts import read_forecasts
from .market_reference_public_macro import read_social, read_pmi
from .market_reference_public_curves import read_curves

READ = {
    **dict.fromkeys(('repo-fr', 'repo-fdr'), read_fixing),
    **dict.fromkeys(('etf-shares-sh', 'etf-shares-sz'), read_etf_shares),
    **dict.fromkeys(('csi-index-members', 'csi-index-weights', 'cni-index-members', 'cni-index-weights'), read_official_index),
    **dict.fromkeys(('cctv-news', 'ths-theme-reasons'), read_editorial),
    **dict.fromkeys(('sse-company-qna', 'szse-company-qna'), read_qna),
    **dict.fromkeys(('ths-eps-consensus', 'ths-institution-eps'), read_forecasts),
    'tdx-day-package': read_package, 'pboc-social-financing': read_social,
    'nbs-pmi': read_pmi, 'chinabond-curves': read_curves,
}
PUBLIC_READERS = {key: (*SOURCES[key], reader) for key, reader in READ.items()}

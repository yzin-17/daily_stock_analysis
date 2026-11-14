"""已启用 HiThink 目录的独立检查，资格不足只返回具体缺口。"""

import json
import sys
from collections import Counter
from data_provider.hithink_history_catalog import read_catalog, project_catalog, CatalogSourceError
from .thesis_ledger_provider_runtime import get_thesis_ledger_runtime
from .thesis_ledger_feature_policy import feature_provider_epoch


def probe(runtime, day):
    epoch = feature_provider_epoch(runtime.store, ['hithink'])
    if epoch['hithink'][1] is not True:
        return {'status': 'unavailable', 'reason': 'disabled'}
    snapshot = runtime.store.provider_credential_snapshot('hithink')
    key = snapshot.values.get('apiKey')
    if not isinstance(key, str) or not key:
        return {'status': 'unavailable', 'reason': 'credential_missing'}
    try:
        raw = read_catalog(key)
        if (runtime.store.provider_credential_snapshot('hithink') != snapshot
                or feature_provider_epoch(runtime.store, ['hithink']) != epoch):
            raise CatalogSourceError('configuration_changed')
        result = {'status': 'observed', 'date': day, 'rows': len(raw['items']), 'pages': raw['pages'],
                  'types': dict(Counter(i.get('asset_type') for i in raw['items'])),
                  'exchanges': dict(Counter(i.get('exchange') for i in raw['items'])),
                  'missingListingDates': sum(not i.get('list_date') for i in raw['items']),
                  'withLastTradeDates': sum(bool(i.get('last_trade_date')) for i in raw['items']),
                  'raw': raw}
        try:
            result['eligible'] = len(project_catalog(raw, day))
            result['projection'] = 'ready'
        except CatalogSourceError as error:
            result['projection'] = error.code
        return result
    except CatalogSourceError as error:
        return {'status': 'unavailable', 'reason': error.code}
    except Exception:
        return {'status': 'unavailable', 'reason': 'upstream_unavailable'}


if __name__ == '__main__':
    payload = json.load(sys.stdin)
    print(json.dumps(probe(get_thesis_ledger_runtime(), payload['day']), ensure_ascii=False))

"""只读固定来源回执，离线核对首批备用源；不调用网络或启用产品来源。"""

import argparse
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_provider.reference_backup_financial import parse_financial
from data_provider.reference_backup_margin import parse_sh_margin, parse_sz_margin
from data_provider.reference_backup_dragon import parse_sh_dragon, parse_sz_dragon
from data_provider.reference_backup_reports import parse_reports


HOSTS = {'backup-ths-debt': 'basic.10jqka.com.cn', 'backup-ths-benefit': 'basic.10jqka.com.cn',
         'backup-ths-cash': 'basic.10jqka.com.cn', 'backup-margin-sh': 'query.sse.com.cn',
         'backup-margin-sh-page': 'query.sse.com.cn', 'backup-margin-sz': 'www.szse.cn',
         'backup-margin-sz-meta': 'www.szse.cn', 'backup-dragon-sh': 'query.sse.com.cn',
         'backup-dragon-sz': 'www.szse.cn', 'backup-dragon-sz-detail': 'www.szse.cn',
         'backup-sina-reports-follow': 'stock.finance.sina.com.cn'}


def read_receipt(root, profile, stamp):
    path = root / (profile + '-1.json')
    receipt = json.loads(path.read_text())
    content = path.with_suffix('.body').read_bytes()
    response = receipt.get('response') or {}
    url = urlparse(response.get('url', ''))
    if receipt.get('profile') != profile or receipt.get('date') != stamp:
        raise ValueError('回执类型／日期不符：' + profile)
    if response.get('sha256') != sha256(content).hexdigest() or response.get('truncated') is not False:
        raise ValueError('原件摘要变化或正文不完整：' + profile)
    if response.get('httpStatus') != 200 or url.scheme != 'https' or url.netloc != HOSTS[profile]:
        raise ValueError('来源没有成功回执或主机不符：' + profile)
    return receipt, content


def compare_financial(projected, prior):
    comparisons = []
    for row in projected['records']:
        found = [item['values'] for item in prior['result']['items'] if item['values'].get('period') == row['period']]
        if len(found) != 1:
            raise ValueError('旧回执没有唯一的同报告期样本')
        previous = found[0]
        for key in row['rawValues']:
            if row[key] is None or previous.get(key) is None:
                comparisons.append({'period': row['period'], 'field': key, 'comparable': False})
                continue
            difference = abs(Decimal(row[key]) - Decimal(previous[key]))
            allowance = Decimal(row['roundingHalfUnits'][key])
            comparisons.append({'period': row['period'], 'field': key,
                                'comparable': True, 'withinSourceRounding': difference <= allowance})
    return {'fields': len(comparisons), 'withinSourceRounding': all(row.get('withinSourceRounding') is True for row in comparisons),
            'comparisons': comparisons, 'primarySourceReads': 0,
            'boundary': '复用既有新浪同报告期实际回执；数值吻合不补造本源缺失的币种／披露日／合并口径'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--prior-evidence-dir', type=Path, required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    root, prior_root = args.evidence_dir.resolve(), args.prior_evidence_dir.resolve()
    output_root = args.output_dir.resolve() if args.output_dir else root
    output_root.mkdir(parents=True, exist_ok=True)
    results, summaries = {}, []
    jobs = [(kind, 'financial-' + name) for kind, name in [('debt', 'balance'), ('benefit', 'income'), ('cash', 'cashflow')]]
    for kind, dataset in jobs:
        receipt, content = read_receipt(root, 'backup-ths-' + kind, args.date)
        expected = f'/api/stock/finance/600519_{kind}.json'
        if receipt.get('code') != '600519' or urlparse(receipt['response']['url']).path != expected:
            raise ValueError('财报请求证券或类型变化')
        value = parse_financial(json.loads(content), kind)
        prior = json.loads((prior_root / ('runtime-' + dataset + '.json')).read_text())
        if not prior.get('passed') or prior.get('input', {}).get('symbol') != '600519.SH':
            raise ValueError('原三表回执缺少已验证券身份')
        value['primaryComparison'] = compare_financial(value, prior)
        if not value['primaryComparison']['withinSourceRounding']:
            raise ValueError('三表核心数值超出来源舍入区间')
        results[dataset] = value
    sh_pages = [json.loads(read_receipt(root, name, args.date)[1]) for name in ['backup-margin-sh', 'backup-margin-sh-page']]
    results['margin-sh'] = parse_sh_margin(sh_pages, args.date)
    excel = read_receipt(root, 'backup-margin-sz', args.date)[1]
    meta = json.loads(read_receipt(root, 'backup-margin-sz-meta', args.date)[1])
    results['margin-sz'] = parse_sz_margin(excel, meta, args.date)
    results['dragon-sh'] = parse_sh_dragon(read_receipt(root, 'backup-dragon-sh', args.date)[1], args.date)
    parent = json.loads(read_receipt(root, 'backup-dragon-sz', args.date)[1])
    detail = json.loads(read_receipt(root, 'backup-dragon-sz-detail', args.date)[1])
    results['dragon-sz'] = parse_sz_dragon(parent, detail, args.date, '000417')
    results['reports'] = parse_reports(read_receipt(root, 'backup-sina-reports-follow', args.date)[1], '600519', as_of=args.as_of)
    for dataset, result in results.items():
        output = output_root / ('qualification-' + dataset + '.json')
        with output.open('x') as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        row = {'dataset': dataset, 'parsed': True,
               'records': len(result.get('records', result.get('seats', []))),
               'sourceTotal': result.get('total', result.get('periodCount', result.get('listTotal'))),
               'directReplacement': result['directReplacement']}
        summaries.append(row)
        print(json.dumps(row, ensure_ascii=False))
    with (output_root / 'qualification-summary.json').open('x') as handle:
        json.dump(summaries, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


if __name__ == '__main__':
    main()

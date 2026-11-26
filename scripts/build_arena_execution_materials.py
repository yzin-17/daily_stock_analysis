"""构建私有真实资料离线包；不启用来源或修改生产归档。"""

import argparse
import json
from scripts.arena_execution_materials import build_materials, write_package


def main():
    parser = argparse.ArgumentParser(description='离线构建 588000、510300 的真实执行资料验收包')
    parser.add_argument('--sources', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    package = build_materials(args.sources)
    write_package(package, args.output)
    print(json.dumps({'output': args.output, 'symbols': list(package['materials']),
                      'originals': len(package['originals']), 'audit': package['audit']}, ensure_ascii=False))


if __name__ == '__main__':
    main()

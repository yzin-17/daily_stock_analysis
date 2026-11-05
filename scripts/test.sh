#!/usr/bin/env bash
# 数据维护检查；从仓库根执行，不启动分析、模型或通知。
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
header() { printf '\n%s\n' "$1"; }
info() { printf '%s\n' "$1"; }
success() { printf '%s\n' "$1"; }
warn() { printf '%s\n' "$1" >&2; }

test_code_recognition() {
    header "测试场景: 代码识别"
    info "测试股票代码识别逻辑..."

    python3 << 'PYTEST'
import sys
sys.path.insert(0, '.')
from data_provider.akshare_fetcher import _is_hk_code, _is_us_code

test_cases = [
    # (代码, 预期HK, 预期US, 描述)
    ("AAPL", False, True, "美股-苹果"),
    ("TSLA", False, True, "美股-特斯拉"),
    ("BRK.B", False, True, "美股-伯克希尔B"),
    ("hk00700", True, False, "港股-腾讯"),
    ("HK09988", True, False, "港股-阿里"),
    ("600519", False, False, "A股-茅台"),
    ("000001", False, False, "A股-平安"),
]

print("\n股票代码识别测试:")
print("-" * 60)
all_pass = True
for code, exp_hk, exp_us, desc in test_cases:
    is_hk = _is_hk_code(code)
    is_us = _is_us_code(code)
    hk_ok = is_hk == exp_hk
    us_ok = is_us == exp_us
    status = "✅" if (hk_ok and us_ok) else "❌"
    all_pass = all_pass and hk_ok and us_ok
    print(f"{status} {code:10} | HK:{is_hk:5} US:{is_us:5} | {desc}")

print("-" * 60)
print(f"{'✅ 所有测试通过!' if all_pass else '❌ 有测试失败!'}")
sys.exit(0 if all_pass else 1)
PYTEST

    success "代码识别测试完成"
}

test_yfinance_convert() {
    header "测试场景: YFinance 代码转换"
    info "测试YFinance代码转换逻辑..."

    python3 << 'PYTEST'
import sys
sys.path.insert(0, '.')
from data_provider.yfinance_fetcher import YfinanceFetcher

fetcher = YfinanceFetcher()

test_cases = [
    ("AAPL", "AAPL", "美股"),
    ("tsla", "TSLA", "美股小写"),
    ("BRK.B", "BRK.B", "美股特殊"),
    ("hk00700", "0700.HK", "港股"),
    ("HK09988", "9988.HK", "港股大写"),
    ("600519", "600519.SS", "A股沪市"),
    ("000001", "000001.SZ", "A股深市"),
    ("300750", "300750.SZ", "A股创业板"),
]

print("\nYFinance 代码转换测试:")
print("-" * 60)
all_pass = True
for input_code, expected, desc in test_cases:
    result = fetcher._convert_stock_code(input_code)
    status = "✅" if result == expected else "❌"
    all_pass = all_pass and (result == expected)
    print(f"{status} {input_code:10} -> {result:12} (期望: {expected:12}) | {desc}")

print("-" * 60)
print(f"{'✅ 所有测试通过!' if all_pass else '❌ 有测试失败!'}")
sys.exit(0 if all_pass else 1)
PYTEST

    success "YFinance 代码转换测试完成"
}

case "${1:-all}" in
  code) test_code_recognition ;;
  yfinance) test_yfinance_convert ;;
  syntax) python3 scripts/check_data_service_boundary.py --syntax ;;
  flake8) flake8 api data_provider src scripts --select=E9,F63,F7,F82 --show-source --statistics ;;
  all)
    python3 scripts/check_data_service_boundary.py --syntax
    test_code_recognition
    test_yfinance_convert
    ;;
  -h|--help)
    printf '用法：bash scripts/test.sh [all|code|yfinance|syntax|flake8]\n'
    ;;
  *)
    printf '该产品测试入口已退役；数据检查使用 all/code/yfinance/syntax/flake8。\n' >&2
    exit 2
    ;;
esac

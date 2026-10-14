"""Data V3安全错误信封；供既有行情和独立能力入口复用。"""
from fastapi import HTTPException
from src.services.thesis_ledger_market_errors_v3 import MARKET_ERROR_MESSAGES


def market_data_error_v3(code: str, status_code: int, request_id: str) -> None:
    raise HTTPException(status_code=status_code, detail={
        'contractVersion': 3, 'requestId': request_id,
        'error': {'code': code, 'message': MARKET_ERROR_MESSAGES[code]},
    })

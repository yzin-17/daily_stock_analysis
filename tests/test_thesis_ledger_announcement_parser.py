from src.services.thesis_ledger_announcement_parser import parse_status_text
import pytest


def test_explicit_day_and_future_resume_do_not_imply_other_days():
    result = parse_status_text("公司股票将于2023年8月17日(星期四)停牌一天，并于2023年8月18日开市起复牌。", "2023-08-17")
    assert [(x["date"], x["state"]) for x in result] == [("2023-08-17", "suspended"), ("2023-08-18", "trading")]


def test_conditional_future_and_old_reference_are_not_new_facts():
    result = parse_status_text("公司股票于2022年7月1日开市起复牌。公司股票自2023年8月21日开市起开始停牌。如公司未能完成，股票最晚将于2023年8月28日开市起复牌。", "2023-08-21")
    assert [(x["date"], x["state"]) for x in result] == [("2023-08-21", "suspended")]


def test_cninfo_morning_resume_preserves_exact_day_and_ignores_old_reference():
    text = (
        "公司证券（证券品种：A股股票，证券简称：中广核技，证券代码：000881）"
        "自2023年8月21日开市起开始停牌。"
        "经公司向深圳证券交易所申请，公司股票将于2023年8月28日（星期一）上午开市起复牌。"
    )
    result = parse_status_text(text, "2023-08-28")
    assert [(fact["date"], fact["state"]) for fact in result] == [("2023-08-28", "trading")]
    assert "上午开市起复牌" in result[0]["text"]


def test_morning_suspension_does_not_expand_to_other_days():
    result = parse_status_text("公司股票自2023年8月14日（星期一）上午开市起开始停牌。", "2023-08-12")
    assert [(fact["date"], fact["state"]) for fact in result] == [("2023-08-14", "suspended")]


def test_conditional_or_historical_morning_dates_are_not_new_facts():
    text = (
        "公司股票自2023年8月14日（星期一）上午开市起开始停牌。"
        "预计公司股票于2023年8月28日（星期一）上午开市起复牌。"
        "若完成审核，公司股票将于2023年8月29日上午开市起复牌。"
    )
    assert parse_status_text(text, "2023-08-19") == []


def test_conflicting_morning_states_are_rejected():
    with pytest.raises(ValueError, match="矛盾停复牌事实"):
        parse_status_text(
            "公司股票将于2023年8月28日上午开市起停牌。"
            "公司股票将于2023年8月28日上午开市起复牌。", "2023-08-28",
        )

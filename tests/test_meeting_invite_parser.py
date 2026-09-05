from cloud_backend.app.meeting_invite_parser import parse_tencent_meeting_invite


SAMPLE = """顾源源 邀请您参加腾讯会议
会议主题：为爱黔行——纤维（9月1日）
会议时间：2026/09/01 20:00-21:00 (GMT+08:00) 中国标准时间 - 北京
点击链接入会，或添加至会议列表：
[https://meeting.tencent.com/dm/pJA5ECmloLqc](https://meeting.tencent.com/dm/pJA5ECmloLqc)
#腾讯会议：119-385-896
复制该信息，打开手机腾讯会议即可参与
""".strip()


def test_parse_tencent_invite_uses_external_task_contract() -> None:
    result = parse_tencent_meeting_invite(SAMPLE)
    assert result is not None
    assert result["recordMode"] == "task"
    assert result["externalCollaboration"] is True
    assert result["scheduleMode"] == "timed"
    assert result["title"] == "为爱黔行——纤维（9月1日）"
    assert result["date"] == result["endDate"] == "2026-09-01"
    assert result["start"] == "20:00"
    assert result["end"] == "21:00"
    assert result["timezone"] == "Asia/Shanghai"
    assert result["inviter"] == "顾源源"
    assert result["meetingCode"] == "119-385-896"
    assert result["joinUrl"] == "https://meeting.tencent.com/dm/pJA5ECmloLqc"
    assert result["backgroundText"] == ""
    assert "原邀请未提供会议背景" in result["description"]


def test_parse_tencent_invite_keeps_only_supplied_background() -> None:
    result = parse_tencent_meeting_invite(SAMPLE + "\n背景：确认九月项目的执行分工。")
    assert result is not None
    assert result["backgroundText"] == "确认九月项目的执行分工。"
    assert "确认九月项目的执行分工" in result["description"]


def test_parse_tencent_invite_rejects_non_tencent_link_and_ordinary_text() -> None:
    assert parse_tencent_meeting_invite("明天整理项目资料") is None
    assert parse_tencent_meeting_invite(SAMPLE.replace("meeting.tencent.com", "example.com")) is None


def test_parse_tencent_invite_rejects_invalid_date_and_time() -> None:
    assert parse_tencent_meeting_invite(
        SAMPLE.replace("2026/09/01", "2026/02/30")
    ) is None
    assert parse_tencent_meeting_invite(
        SAMPLE.replace("20:00-21:00", "25:00-26:00")
    ) is None

"""Deterministic parsers for standard meeting-invitation payloads."""

from __future__ import annotations

from datetime import date
import re
from typing import Any


_TEMPLATE_LINES = (
    re.compile(r"^会议主题[：:]"),
    re.compile(r"^会议时间[：:]"),
    re.compile(r"^点击链接入会"),
    re.compile(r"^复制该信息"),
    re.compile(r"^#?腾讯会议[：:]"),
    re.compile(r"^\S+\s*邀请您参加腾讯会议$"),
)


def _background(text: str) -> str:
    values: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        labeled = re.match(r"^(?:会议)?背景[：:]\s*(.+)$", line)
        if labeled:
            values.append(labeled.group(1).strip())
        elif "meeting.tencent.com" in line:
            continue
        elif any(pattern.search(line) for pattern in _TEMPLATE_LINES):
            continue
        else:
            values.append(line)
    return "\n".join(values).strip()


def _description(*, background: str, inviter: str, meeting_code: str, join_url: str) -> str:
    return "\n".join(
        (
            "会议背景",
            background or "原邀请未提供会议背景。",
            "",
            "参会信息",
            f"邀请人：{inviter or '未提供'}",
            f"腾讯会议号：{meeting_code}",
            f"入会链接：{join_url}",
            "时区：Asia/Shanghai",
        )
    )


def parse_tencent_meeting_invite(raw_text: str) -> dict[str, Any] | None:
    """Parse only high-confidence Tencent Meeting invitations without AI."""
    text = str(raw_text or "").strip()
    if not text or "腾讯会议" not in text:
        return None
    title_match = re.search(r"(?:^|\n)会议主题[：:]\s*([^\r\n]+)", text)
    inviter_match = re.search(r"(?:^|\n)\s*(.+?)\s*邀请您参加腾讯会议\s*(?:\n|$)", text)
    time_match = re.search(
        r"(?:^|\n)会议时间[：:]\s*(\d{4})[\/-](\d{1,2})[\/-](\d{1,2})\s+"
        r"(\d{1,2}):(\d{2})\s*[-–—~至]\s*(\d{1,2}):(\d{2})",
        text,
    )
    url_match = re.search(r"https://meeting\.tencent\.com/[A-Za-z0-9_/?=&.%+\-]+", text, re.I)
    code_match = re.search(r"#?腾讯会议[：:]\s*([0-9]{3}(?:-[0-9]{3}){2})", text)
    if not all((title_match, time_match, url_match, code_match)):
        return None
    assert title_match and time_match and url_match and code_match
    try:
        parsed_date = date(int(time_match[1]), int(time_match[2]), int(time_match[3])).isoformat()
    except ValueError:
        return None
    start_hour, start_minute = int(time_match[4]), int(time_match[5])
    end_hour, end_minute = int(time_match[6]), int(time_match[7])
    if start_hour > 23 or end_hour > 23 or start_minute > 59 or end_minute > 59:
        return None
    background = _background(text)
    inviter = inviter_match.group(1).strip() if inviter_match else ""
    join_url = url_match.group(0).rstrip(")]），。")
    meeting_code = code_match.group(1)
    return {
        # A third-party meeting is an ordinary timed task carrying an
        # external-collaboration presentation tag.  It is not a client-project
        # meeting and therefore never requires a client id.
        "recordMode": "task",
        "externalCollaboration": True,
        "scheduleMode": "timed",
        "title": title_match.group(1).strip(),
        "description": _description(
            background=background,
            inviter=inviter,
            meeting_code=meeting_code,
            join_url=join_url,
        ),
        "date": parsed_date,
        "endDate": parsed_date,
        "start": f"{start_hour:02d}:{start_minute:02d}",
        "end": f"{end_hour:02d}:{end_minute:02d}",
        "timezone": "Asia/Shanghai",
        "provider": "tencent_meeting",
        "inviter": inviter,
        "joinUrl": join_url,
        "meetingCode": meeting_code,
        "backgroundText": background,
        "priority": "normal",
        "sourceText": text,
        "reasons": ["已按腾讯会议标准邀请格式识别"],
    }

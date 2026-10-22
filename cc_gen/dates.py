"""Relative date specs, resolved against an anchor date in the course time zone.

Content YAML never hard-codes dates, so the course always has items that are past due,
due today, due soon, etc. relative to whenever it is built.

Spec grammar (string):
    "+7d"            anchor + 7 days at the default time (23:59 local)
    "-3d@08:00"      anchor - 3 days at 08:00 local
    "+2w@12:30"      weeks
    "+0d@23:59"      today at 23:59
    "dst_next@01:30" the day of the next DST transition, at 01:30 local
                     (on a fall-back day 01:30 happens twice: a deliberate edge case)
    "2027-02-27T10:00" absolute local time
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

SPEC_RE = re.compile(r"^(?:(?P<sign>[+-])(?P<n>\d+)(?P<unit>[dw])|(?P<dst>dst_next))(?:@(?P<hh>\d{1,2}):(?P<mm>\d{2}))?$")


@dataclass
class DateResolver:
    anchor: date
    tz: ZoneInfo
    default_time: time = time(23, 59)

    def local(self, spec: str | None) -> datetime | None:
        if spec in (None, "", "none"):
            return None
        spec = str(spec).strip()
        m = SPEC_RE.match(spec)
        if not m:
            return datetime.fromisoformat(spec).replace(tzinfo=self.tz)
        t = time(int(m["hh"]), int(m["mm"])) if m["hh"] else self.default_time
        if m["dst"]:
            d = self.next_dst_transition()
        else:
            days = int(m["n"]) * (7 if m["unit"] == "w" else 1)
            d = self.anchor + timedelta(days=days if m["sign"] == "+" else -days)
        # fold=0 => first occurrence of an ambiguous wall time (still DST on fall-back day)
        return datetime.combine(d, t, tzinfo=self.tz)

    def utc(self, spec: str | None) -> str | None:
        """Canvas export format: UTC, no offset suffix (CCHelper::IMS_DATETIME)."""
        dt = self.local(spec)
        if dt is None:
            return None
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    def day(self, spec: str | None) -> str | None:
        dt = self.local(spec)
        return dt.strftime("%Y-%m-%d") if dt else None

    def next_dst_transition(self) -> date:
        d = self.anchor
        noon = lambda x: datetime.combine(x, time(12), tzinfo=self.tz).utcoffset()
        for _ in range(400):
            nxt = d + timedelta(days=1)
            if noon(nxt) != noon(d):
                return nxt
            d = nxt
        raise ValueError(f"no DST transition within a year for {self.tz}")

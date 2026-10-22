"""In-memory cartridge: files + manifest resources, written out as a .imscc zip."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

LOR = "associatedcontent/imscc_xmlv1p1/learning-application-resource"
WEBCONTENT = "webcontent"
DISCUSSION_TOPIC = "imsdt_xmlv1p1"
WEB_LINK = "imswl_xmlv1p1"
BASIC_LTI = "imsbasiclti_xmlv1p0"
ASSESSMENT = "imsqti_xmlv1p2/imscc_xmlv1p1/assessment"


@dataclass
class Resource:
    identifier: str
    type: str
    href: str | None = None
    files: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    intendeduse: str | None = None
    instructor_only: bool = False  # adds LOM intendedEndUserRole=Instructor (locked files)


class Package:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.resources: list[Resource] = []

    def add(self, path: str, data: bytes | str) -> str:
        if path in self.files:
            raise ValueError(f"duplicate package path: {path}")
        self.files[path] = data.encode("utf-8") if isinstance(data, str) else data
        return path

    def resource(self, identifier: str, type: str, href: str | None = None, files=(), dependencies=(), **kw) -> Resource:
        r = Resource(identifier, type, href, list(files), list(dependencies), **kw)
        self.resources.append(r)
        return r

    def write_zip(self, out: Path) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            # manifest first, like Canvas exports
            order = sorted(self.files, key=lambda p: (p != "imsmanifest.xml", p))
            for p in order:
                info = zipfile.ZipInfo(p, date_time=(2026, 1, 1, 0, 0, 0))  # reproducible zips
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                z.writestr(info, self.files[p])
        return out

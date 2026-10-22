"""Tiny lxml helpers so writers read like Canvas's Builder::XmlMarkup exporters."""

from __future__ import annotations

from lxml import etree

CANVAS_NS = "http://canvas.instructure.com/xsd/cccv1p0"
CANVAS_XSD = "https://canvas.instructure.com/xsd/cccv1p0.xsd"
XSI = "http://www.w3.org/2001/XMLSchema-instance"


def fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return f"{value:.1f}"
    return str(value)


def root(tag: str, ns: str = CANVAS_NS, schema: str | None = CANVAS_XSD, nsmap: dict | None = None, **attrs) -> etree._Element:
    m = {None: ns, "xsi": XSI}
    if nsmap:
        m.update(nsmap)
    el = etree.Element(f"{{{ns}}}{tag}", nsmap=m)
    for k, v in attrs.items():
        el.set(k, fmt(v))
    if schema:
        el.set(f"{{{XSI}}}schemaLocation", f"{ns} {schema}")
    return el


def sub(parent: etree._Element, tag: str, text=None, *, keep_empty: bool = False, **attrs) -> etree._Element | None:
    """Append ``<tag>text</tag>`` in the parent's namespace. ``None`` text is skipped unless keep_empty."""
    if text is None and not keep_empty and not attrs:
        return None
    ns = etree.QName(parent).namespace
    el = etree.SubElement(parent, f"{{{ns}}}{tag}" if ns else tag)
    for k, v in attrs.items():
        if v is not None:
            el.set(k, fmt(v))
    if text is not None:
        el.text = fmt(text)
    return el


def node(parent: etree._Element, tag: str, **attrs) -> etree._Element:
    """Always-created container element."""
    return sub(parent, tag, None, keep_empty=True, **attrs)


def subs(parent: etree._Element, mapping: dict) -> None:
    for k, v in mapping.items():
        sub(parent, k, v)


def tostring(el: etree._Element) -> bytes:
    return etree.tostring(el, xml_declaration=True, encoding="UTF-8", pretty_print=True)

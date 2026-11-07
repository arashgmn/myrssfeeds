#!/usr/bin/env python3
"""Validate generated RSS feeds before they are committed and pushed."""
import argparse
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import yaml

from filters import compile_filter
from generate_feed import safe_slug

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _one_line(text):
    return " ".join(str(text).split())


def _child_text(parent, tag):
    child = parent.find(tag)
    if child is None:
        return ""
    return (child.text or "").strip()


def _nonempty_str(value):
    return isinstance(value, str) and bool(value.strip())


def _is_http_url(value):
    if not value or any(ch.isspace() for ch in value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


def _parse_date(value):
    try:
        dt = parsedate_to_datetime(value)
    except Exception:
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def validate_feed(path, expected_site_name=None, expected_url=None, max_age_days=None):
    """Return (item_count or None, errors, warnings) for one RSS file."""
    errors = []
    warnings = []

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return None, [f"malformed XML: {exc}"], warnings
    except OSError as exc:
        return None, [f"cannot read XML: {exc}"], warnings

    if root.tag != "rss":
        errors.append(f"root is <{root.tag}>, expected <rss>")

    channels = root.findall("channel")
    if len(channels) != 1:
        errors.append(f"expected exactly one <channel>, found {len(channels)}")
        return None, errors, warnings
    channel = channels[0]

    channel_title = _child_text(channel, "title")
    channel_link = _child_text(channel, "link")
    channel_description = _child_text(channel, "description")

    if not channel_title:
        errors.append("channel: missing/empty <title>")
    if not channel_link:
        errors.append("channel: missing/empty <link>")
    elif not _is_http_url(channel_link):
        errors.append(f"channel: <link> is not absolute http(s): {channel_link!r}")
    if not channel_description:
        errors.append("channel: missing/empty <description>")

    if max_age_days is not None:
        last_build = _child_text(channel, "lastBuildDate")
        if not last_build:
            errors.append("channel: missing/empty <lastBuildDate> (required by --max-age-days)")
        else:
            parsed = _parse_date(last_build)
            if parsed is None:
                errors.append(f"channel: invalid <lastBuildDate>: {last_build!r}")
            elif datetime.now(timezone.utc) - parsed > timedelta(days=max_age_days):
                errors.append(
                    f"channel: <lastBuildDate> is older than {max_age_days:g} days: {last_build!r}"
                )

    if expected_site_name and channel_title and channel_title != expected_site_name:
        warnings.append(
            f"channel <title> {channel_title!r} differs from site_name {expected_site_name!r}"
        )
    if expected_url and channel_link and channel_link != expected_url:
        warnings.append(f"channel <link> {channel_link!r} differs from url {expected_url!r}")

    items = channel.findall("item")
    item_count = len(items)
    if not item_count:
        errors.append("channel has no <item> elements")

    missing = {tag: [] for tag in ("title", "link", "guid", "pubDate")}
    item_errors = []
    seen_guids = {}

    for index, item in enumerate(items, 1):
        title = _child_text(item, "title")
        link = _child_text(item, "link")
        guid = _child_text(item, "guid")
        pub_date = _child_text(item, "pubDate")

        if not title:
            missing["title"].append(index)
        if not link:
            missing["link"].append(index)
        elif not _is_http_url(link):
            item_errors.append(f"item {index}: <link> is not absolute http(s): {link!r}")
        if not guid:
            missing["guid"].append(index)
        elif guid in seen_guids:
            item_errors.append(
                f"item {index}: duplicate <guid> {guid!r} (first seen in item {seen_guids[guid]})"
            )
        else:
            seen_guids[guid] = index

        if not pub_date:
            missing["pubDate"].append(index)
        elif _parse_date(pub_date) is None:
            item_errors.append(f"item {index}: invalid <pubDate>: {pub_date!r}")

        for enclosure in item.findall("enclosure"):
            enc_url = (enclosure.get("url") or "").strip()
            if not _is_http_url(enc_url):
                item_errors.append(
                    f"item {index}: enclosure url is not absolute http(s): {enc_url!r}"
                )
            if not (enclosure.get("type") or "").strip():
                warnings.append(f"item {index}: enclosure missing/empty type")

    for tag, indexes in missing.items():
        if indexes:
            items_text = ", ".join(str(index) for index in indexes)
            errors.append(f"items {items_text}: missing/empty <{tag}>")
    errors.extend(item_errors)

    return item_count, errors, warnings


def load_configs(feeds_dir):
    configs = []
    for name in sorted(os.listdir(feeds_dir)):
        if not name.lower().endswith((".yml", ".yaml")):
            continue
        path = os.path.join(feeds_dir, name)
        config = {
            "name": name,
            "path": path,
            "errors": [],
            "expected_name": None,
            "site_name": None,
            "expected_url": None,
        }

        try:
            with open(path, "r", encoding="utf-8") as handle:
                cfg = yaml.safe_load(handle)
        except Exception as exc:
            config["errors"].append(f"YAML parse error: {_one_line(exc)}")
            configs.append(config)
            continue

        if not isinstance(cfg, dict):
            config["errors"].append("config root must be a mapping")
            configs.append(config)
            continue

        url = cfg.get("url")
        item_selector = cfg.get("item_selector")
        if not _nonempty_str(url):
            config["errors"].append("missing/non-empty 'url' string")
        else:
            config["expected_url"] = url.strip()
        if not _nonempty_str(item_selector):
            config["errors"].append("missing/non-empty 'item_selector' string")

        filter_expr = cfg.get("filter")
        if filter_expr is not None:
            if not isinstance(filter_expr, str):
                config["errors"].append("'filter' must be a string")
            else:
                try:
                    compile_filter(filter_expr)
                except Exception as exc:
                    config["errors"].append(f"invalid 'filter': {_one_line(exc)}")

        raw_site_name = cfg.get("site_name")
        site_name = raw_site_name.strip() if isinstance(raw_site_name, str) else ""
        config_stem = os.path.splitext(name)[0]
        site_name = site_name or safe_slug(config_stem)
        config["site_name"] = site_name
        config["expected_name"] = safe_slug(site_name) + ".xml"
        configs.append(config)

    return configs


def _print_fail(name, reasons):
    print(f"FAIL {name}: {'; '.join(_one_line(reason) for reason in reasons)}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate generated RSS feeds before commit/push."
    )
    parser.add_argument(
        "--feeds-dir", default=os.path.join(BASE_DIR, "feeds"), help="YAML config directory"
    )
    parser.add_argument(
        "--docs-dir", default=os.path.join(BASE_DIR, "docs"), help="generated XML directory"
    )
    parser.add_argument(
        "--max-age-days",
        type=float,
        default=None,
        help="require lastBuildDate to be no older than this many days",
    )
    args = parser.parse_args(argv)

    feeds_dir = os.path.abspath(args.feeds_dir)
    docs_dir = os.path.abspath(args.docs_dir)

    if not os.path.isdir(feeds_dir):
        print(f"FAIL {feeds_dir}: feeds directory not found")
        print("Summary: 0 feeds checked, 0 ok, 0 failed, 0 warnings")
        return 1

    configs = load_configs(feeds_dir)
    expected_names = {c["expected_name"] for c in configs if c["expected_name"]}

    checked = 0
    ok = 0
    failed = 0
    warning_count = 0
    error_count = 0

    for config in configs:
        checked += 1
        config_errors = list(config["errors"])

        if not config["expected_name"]:
            reasons = config_errors or ["config has no usable site_name"]
            failed += 1
            error_count += len(reasons)
            _print_fail(config["name"], reasons)
            continue

        path = os.path.join(docs_dir, config["expected_name"])
        if not os.path.isfile(path):
            reasons = config_errors + [f"missing expected XML: {path}"]
            failed += 1
            error_count += len(reasons)
            _print_fail(config["name"], reasons)
            continue

        item_count, xml_errors, xml_warnings = validate_feed(
            path,
            expected_site_name=config["site_name"],
            expected_url=config["expected_url"],
            max_age_days=args.max_age_days,
        )
        errors = config_errors + xml_errors
        warning_count += len(xml_warnings)
        error_count += len(errors)

        if errors:
            failed += 1
            _print_fail(config["expected_name"], errors + xml_warnings)
        else:
            ok += 1
            if xml_warnings:
                print(
                    f"WARN {config['expected_name']} {item_count} items: "
                    + "; ".join(_one_line(warning) for warning in xml_warnings)
                )
            else:
                print(f"OK {config['expected_name']} {item_count} items")

    xml_names = []
    if os.path.isdir(docs_dir):
        xml_names = sorted(
            name for name in os.listdir(docs_dir) if name.lower().endswith(".xml")
        )

    for name in xml_names:
        if name in expected_names:
            continue

        checked += 1
        item_count, xml_errors, xml_warnings = validate_feed(
            os.path.join(docs_dir, name), max_age_days=args.max_age_days
        )
        warnings = ["orphan XML with no matching config"] + xml_warnings
        warning_count += len(warnings)
        error_count += len(xml_errors)

        if xml_errors:
            failed += 1
            _print_fail(name, xml_errors + warnings)
        else:
            ok += 1
            print(
                f"WARN {name} {item_count} items: "
                + "; ".join(_one_line(warning) for warning in warnings)
            )

    print(
        f"Summary: {checked} feeds checked, {ok} ok, {failed} failed, "
        f"{warning_count} warnings"
    )
    return 0 if error_count == 0 and failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

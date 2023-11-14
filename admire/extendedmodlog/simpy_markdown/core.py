import numbers
import re
from functools import cmp_to_key
from typing import Callable, Optional, Union
from typing import List as List_
from urllib.parse import unquote

CR_NEWLINE_R = re.compile(r"\r\n?")
TAB_R = re.compile(r"\t")
FORM_FEED_R = re.compile(r"\f")


def preprocess(source: str) -> str:
    return TAB_R.sub("\n", FORM_FEED_R.sub("", CR_NEWLINE_R.sub("    ", source)))


def populate_initial_state(given_state: dict = None, default_state: dict = None) -> dict:
    state = given_state or {}
    if default_state:
        for key in default_state:
            state[key] = default_state[key]
    return state


def parser_for(rules: dict, default_state: dict = None) -> Callable[[str, Optional[dict]], List_[Union[list, str]]]:
    default_state = default_state or {}

    def _filter_rules(rule_type):
        rule = rules.get(rule_type)
        if not rule or not hasattr(rule, "match"):
            return False
        order = rule.order
        if not isinstance(order, numbers.Number):
            print(f"Invalid order for rule `{rule_type}`: {order}")
        return True

    rule_list = [rule for rule in rules if _filter_rules(rule)]

    def _sort_rules(rule_type_a, rule_type_b):
        rule_a = rules[rule_type_a]
        rule_b = rules[rule_type_b]
        order_a = rule_a.order
        order_b = rule_b.order

        if order_a != order_b:
            return order_a - order_b

        secondary_order_a = 0 if hasattr(rule_a, "quality") else 1
        secondary_order_b = 0 if hasattr(rule_b, "quality") else 1

        if secondary_order_a != secondary_order_b:
            return secondary_order_a - secondary_order_b
        elif rule_type_a < rule_type_b:
            return -1
        elif rule_type_a > rule_type_b:
            return 1
        else:
            return 0

    rule_list.sort(key=cmp_to_key(_sort_rules))

    latest_state = {}

    def nested_parse(source: str, state: dict):
        result = []
        nonlocal latest_state
        global current_order
        state = state or latest_state
        latest_state = state
        while source:
            rule_type = None
            rule = None
            capture = None
            quality = -1

            i = 0
            current_rule_type = rule_list[0]
            current_rule = rules[current_rule_type]

            while not i or (current_rule and (not capture or (current_rule.order == current_order and hasattr(current_rule, "quality")))):
                current_order = current_rule.order
                previous_capture_string = "" if state.get("previous_capture") is None else state["previous_capture"][0]
                current_capture = current_rule.match(source, state, previous_capture_string)

                if current_capture:
                    current_quality = current_rule.quality(current_capture, state, previous_capture_string) if hasattr(current_rule, "quality") else 0
                    if not (current_quality <= quality):
                        rule_type = current_rule_type
                        rule = current_rule
                        capture = current_capture
                        quality = current_quality

                i += 1
                try:
                    current_rule_type = rule_list[i]
                    current_rule = rules[current_rule_type]
                except IndexError:
                    current_rule_type = None
                    current_rule = None

            if rule is None or capture is None:
                raise Exception(
                    (
                        (
                            (
                                "Could not find a matching rule for the below "
                                + "content. The rule with highest `order` should "
                                + "always match content provided to it. Check "
                                + "the definition of `match` for '"
                                + rule_list[-1]
                            )
                            + "'. It seems to not match the following source:\n"
                        )
                        + source
                    ),
                )
            if capture.pos:
                raise Exception(
                    "`match` must return a capture starting at index 0 " + "(the current parse index). Did you forget a ^ at the " + "start of the RegExp?",
                )

            parsed = rule.parse(capture, nested_parse, state)

            if isinstance(parsed, list):
                result.extend(parsed)
            else:
                if parsed.get("type") is None:
                    parsed["type"] = rule_type
                result.append(parsed)

            state["previous_capture"] = capture
            source = source[len(state["previous_capture"][0]) :]

        return result

    def outer_parse(source: str, state: dict = None):
        state = state or {}
        nonlocal latest_state
        latest_state = populate_initial_state(state, default_state)
        if not latest_state.get("inline") and not latest_state.get("disable_auto_block_newlines"):
            source += "\n\n"

        latest_state["previous_capture"] = None
        return nested_parse(preprocess(source), latest_state)

    return outer_parse


def inline_regex(regex: str, flags: int = 0) -> Callable[[str, dict], Union[re.Match, None]]:
    def match(source, state, *args, **kwargs):
        return re.search(regex, source, flags=flags) if state.get("inline") else None

    match.regex = regex
    return match


def block_regex(regex: str, flags: int = 0) -> Callable[[str, dict], Union[re.Match, None]]:
    def match(source, state, *args, **kwargs):
        return None if state.get("inline") else re.search(regex, source, flags=flags)

    match.regex = regex
    return match


def any_scope_regex(regex: str, flags: int = 0) -> Callable[[str, dict], re.Match]:
    def match(source, state, *args, **kwargs):
        return re.search(regex, source, flags=flags)

    match.regex = regex
    return match


def react_element(_type: str, key: Union[str, int] = None, props: dict = None) -> dict:
    props = props or {}
    return {"type": _type, "key": key, "ref": None, "props": props, "_owner": None}


def html_tag(tag_name: str, content: str, attributes: dict = None, is_closed: bool = True) -> str:
    attributes = attributes or {}

    attr_string = " ".join(f'{sanitize_text(attr)}="{sanitize_text(attributes[attr])}"' for attr in attributes if attributes[attr])

    unclosed_tag = f'<{tag_name}{" " + attr_string if attr_string else ""}>'
    return unclosed_tag + content + f"</{tag_name}>" if is_closed else unclosed_tag


EMPTY_PROPS = {}


def sanitize_url(url: str = None) -> Union[str, None]:
    if not url:
        return None

    subbed = re.sub(r"[^A-Za-z0-9/:]", "", unquote(url)).lower()
    if any([subbed.startswith("javascript:"), subbed.startswith("vbscript:"), subbed.startswith("data:")]):
        return None
    return url


SANITIZE_TEXT_R = re.compile(r'[<>&"\']')
SANITIZE_TEXT_CODES = {"<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#x27;", "/": "&#x2F;", "`": "&#96;"}


def sanitize_text(text: str) -> str:
    return SANITIZE_TEXT_R.sub(lambda m: SANITIZE_TEXT_CODES[m.group()], str(text))


UNESCAPE_URL_R = re.compile(r"\\([^0-9A-Za-z\s])")


def unescape_url(raw_url_string: str) -> str:
    return UNESCAPE_URL_R.sub(lambda m: m.group(1), raw_url_string)


def parse_inline(parse, content: str, state: dict) -> list:
    is_currently_inline = state.get("inline", False)
    state["inline"] = True
    result = parse(content, state)
    state["inline"] = is_currently_inline
    return result


def parse_block(parse, content: str, state: dict) -> list:
    is_currently_inline = state.get("inline", False)
    state["inline"] = False
    result = parse(content + "\n\n", state)
    state["inline"] = is_currently_inline
    return result


def parse_capture_inline(capture, parse, state) -> dict:
    return {"content": parse_inline(parse, capture[1], state)}


def ignore_capture() -> dict:
    return {}


LIST_BULLET = "(?:[*+-]|\\d+\\.)"
LIST_ITEM_PREFIX = "( *)(" + LIST_BULLET + ") +"
LIST_ITEM_PREFIX_R = re.compile("^" + LIST_ITEM_PREFIX)
LIST_ITEM_R = re.compile(LIST_ITEM_PREFIX + "[^\\n]*(?:\\n" + "(?!\\1" + LIST_BULLET + " )[^\\n]*)*(\n|$)", flags=re.MULTILINE)
BLOCK_END_R = re.compile(r"\n{2,}$")
INLINE_CODE_ESCAPE_BACKTICKS_R = re.compile(r"^ (?= *`)|(` *) $")
LIST_BLOCK_END_R = BLOCK_END_R
LIST_ITEM_END_R = re.compile(r" *\n+$")
LIST_R = re.compile("^( *)(" + LIST_BULLET + ") " + "[\\s\\S]+?(?:\n{2,}(?! )" + "(?!\\1" + LIST_BULLET + " )\n*" + "|\\s*\n*$)")
LIST_LOOKBEHIND_R = re.compile(r"(?:^|\n)( *)\Z")


def do_tables() -> dict:
    table_row_separator_trim = re.compile(r"^ *\| *| *\| *$")
    table_cell_end_trim = re.compile(r" *$")
    table_right_align = re.compile(r"^ *-+: *$")
    table_center_align = re.compile(r"^ *:-+: *$")
    table_left_align = re.compile(r"^ *:-+ *$")

    def parse_table_align_capture(align_capture):
        if table_right_align.search(align_capture):
            return "right"
        elif table_center_align.search(align_capture):
            return "center"
        elif table_left_align.search(align_capture):
            return "left"
        else:
            return None

    def parse_table_align(source, parse, state, trim_end_separators):
        if trim_end_separators:
            source = table_row_separator_trim.sub("", source)
        align_text = source.strip().split("|")
        return [parse_table_align_capture(capture) for capture in align_text]

    def parse_table_row(source, parse, state, trim_end_separators):
        prev_in_table = state.get("in_table")
        state["in_table"] = True
        table_row = parse(source.strip(), state)
        state["in_table"] = prev_in_table

        cells = [[]]
        for index, node in enumerate(table_row):
            if node["type"] == "table_separator":
                if not trim_end_separators or index not in [0, len(table_row) - 1]:
                    cells.append([])
            else:
                if node["type"] == "text" and (table_row[index + 1]["type"] == "table_separator") if len(table_row) > index + 1 else None:
                    node["content"] = table_cell_end_trim.sub("", node["content"], count=1)
                cells[-1].append(node)

        return cells

    def parse_table_cells(source, parse, state, trim_end_separators):
        rows_text = source.strip().split("\n")

        return [parse_table_row(row_text, parse, state, trim_end_separators) for row_text in rows_text]

    def parse_table(trim_end_separators):
        def inner(capture, parse, state):
            state["inline"] = True
            header = parse_table_row(capture[1], parse, state, trim_end_separators)
            align = parse_table_align(capture[2], parse, state, trim_end_separators)
            cells = parse_table_cells(capture[3], parse, state, trim_end_separators)
            state["inline"] = False

            return {"type": "table", "header": header, "align": align, "cells": cells}

        return inner

    return {
        "parse_table": parse_table(True),
        "parse_np_table": parse_table(False),
        "TABLE_REGEX": re.compile(r"^ *(\|.+)\n *\|( *[-:]+[-| :]*)\n((?: *\|.*(?:\n|$))*)\n*"),
        "NPTABLE_REGEX": re.compile(r"^ *(\S.*\|.*)\n *([-:]+ *\|[-| :]*)\n((?:.*\|.*(?:\n|$))*)\n*"),
    }


LINK_INSIDE = "(?:\\[[^\\]]*\\]|[^\\[\\]]|\\](?=[^\\[]*\\]))*"
LINK_HREF_AND_TITLE = "\\s*<?((?:\\([^)]*\\)|[^\\s\\\\]|\\\\.)*?)>?(?:\\s+['\"]([\\s\\S]*?)['\"])?\\s*"
AUTOLINK_MAILTO_CHECK_R = re.compile("mailto:", flags=re.IGNORECASE)


def parse_ref(capture, state, ref_node):
    ref = re.sub(r"\s+", " ", capture[2] or capture[1]).lower()

    if state.get("_defs") and state["_defs"].get(ref):
        _def = state["_defs"][ref]
        ref_node["target"] = _def["target"]
        ref_node["title"] = _def["title"]

    state["_refs"] = state.get("_refs", {})
    state["_refs"][ref] = state["_refs"].get(ref, [])
    state["_refs"][ref].append(ref_node)

    return ref_node


current_order = 0


class Rule:
    def __init__(self, order) -> None:
        self.order = order


class Array(Rule):
    @staticmethod
    def react(arr, output, state):
        old_key = state["key"]
        result = []

        i = 0
        key = 0
        while i < len(arr):
            state["key"] = str(i)

            node = arr[i]
            if node["type"] == "text":
                node = {"type": "text", "content": node["content"]}
                while i + 1 < len(arr) and arr[i + 1]["type"] == "text":
                    node["content"] += arr[i + 1]["content"]
                    i += 1

            result.append(output(node, state))
            key += 1

        state["key"] = old_key
        return result

    @staticmethod
    def html(arr, output, state):
        result = ""

        i = 0
        while i < len(arr):
            node = arr[i]
            if node["type"] == "text":
                node = {"type": "text", "content": node["content"]}
                while i + 1 < len(arr) and arr[i + 1]["type"] == "text":
                    node["content"] += arr[i + 1]["content"]
                    i += 1
            i += 1

            result += output(node, state)

        return result


class Heading(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return block_regex(r"^ *(#{1,6})([^\n]+?)#* *(?:\n *)+\n")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"level": len(capture[1]), "content": parse_inline(parse, capture[2].strip(), state)}

    @staticmethod
    def react(node, output, state):
        return react_element("h" + node["level"], state["key"], {"children": output(node["content"], state)})

    @staticmethod
    def html(node, output, state):
        return html_tag("h" + str(node["level"]), output(node["content"], state))


class NpTable(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return block_regex(do_tables()["NPTABLE_REGEX"])(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return do_tables()["parse_np_table"](capture, parse, state)


class LHeading(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return block_regex(r"^([^\n]+)\n *(=|-){3,} *(?:\n *)+\n")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"type": "heading", "level": 1 if capture[2] == "=" else 2, "content": parse_inline(parse, capture[1], state)}


class HR(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return block_regex(r"^( *[-*_]){3,} *(?:\n *)+\n")(*args, **kwargs)

    @staticmethod
    def parse(*args, **kwargs):
        return ignore_capture()

    @staticmethod
    def react(node, output, state):
        return react_element("hr", state["key"], EMPTY_PROPS)

    @staticmethod
    def html(*args, **kwargs):
        return "<hr>"


class CodeBlock(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return block_regex(r"^(?:    [^\n]+\n*)+(?:\n *)+\n")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        content = re.sub(r"\n+$", "", re.sub(r"^ {4}", "", capture[0], flags=re.MULTILINE), count=1)
        return {"lang": None, "content": content}

    @staticmethod
    def react(node, output, state):
        class_name = f'markdown-code-{node["lang"]}' if node["lang"] else None

        return react_element("pre", state["key"], {"children": react_element("code", None, {"className": class_name, "children": node["content"]})})

    @staticmethod
    def html(node, output, state):
        class_name = f'markdown-code-{node["lang"]}' if node["lang"] else None

        code_block = html_tag("code", sanitize_text(node["content"]), {"class": class_name})
        return html_tag("pre", code_block)


class Fence(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return block_regex(r"^ *(`{3,}|~{3,}) *(?:(\S+) *)?\n([\s\S]+?)\n?\1 *(?:\n *)+\n")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"type": "code_block", "lang": capture[2] or None, "content": capture[3]}


class BlockQuote(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return block_regex(r"^( *>[^\n]+(\n[^\n]+)*\n*)+\n{2,}")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        content = re.sub(r"^ *> ?", "", capture[0], flags=re.MULTILINE)

        return {"content": parse(content, state)}

    @staticmethod
    def react(node, output, state):
        return react_element("blockquote", state["key"], {"children": output(node["content"], state)})

    @staticmethod
    def html(node, output, state):
        return html_tag("blockquote", output(node["content"], state))


class List(Rule):
    @staticmethod
    def match(source, state, *args, **kwargs):
        previous_capture_string = "" if state.get("previous_capture") is None else state["previous_capture"][0]
        is_start_of_line_capture = LIST_LOOKBEHIND_R.search(previous_capture_string)
        is_list_block = state.get("_list") or not state.get("inline")

        if is_start_of_line_capture and is_list_block:
            source = is_start_of_line_capture[1] + source
            return LIST_R.search(source)
        else:
            return None

    @staticmethod
    def parse(capture, parse, state):
        bullet = capture[2]
        ordered = len(bullet) > 1
        start = int(re.sub(r"[^\d]+?", "", bullet)) if ordered else None
        items = [item[0] for item in LIST_ITEM_R.finditer(LIST_BLOCK_END_R.sub("\n", capture[0], count=1))]
        items_length = len(items)

        last_item_was_a_paragraph = False

        def content_map(i, item):
            prefix_capture = LIST_ITEM_PREFIX_R.search(item)
            space = len(prefix_capture[0]) if prefix_capture else 0
            space_regex = r"^ {1," + str(space) + "}"
            content = LIST_ITEM_PREFIX_R.sub("", re.sub(space_regex, "", item, flags=re.MULTILINE), count=1)

            is_last_item = i == items_length - 1
            contains_blocks = "\n\n" in content

            nonlocal last_item_was_a_paragraph
            this_item_is_a_paragraph = contains_blocks or (is_last_item and last_item_was_a_paragraph)
            last_item_was_a_paragraph = this_item_is_a_paragraph

            old_state_inline = state.get("inline")
            old_state_list = state.get("_list")
            state["_list"] = True

            if this_item_is_a_paragraph:
                state["inline"] = False
                adjusted_content = LIST_ITEM_END_R.sub("\n\n", content, count=1)
            else:
                state["inline"] = True
                adjusted_content = LIST_ITEM_END_R.sub("", content, count=1)

            result = parse(adjusted_content, state)

            state["inline"] = old_state_inline
            state["_list"] = old_state_list
            return result

        item_content = [content_map(index, item) for index, item in enumerate(items)]

        return {"ordered": ordered, "start": start, "items": item_content}

    @staticmethod
    def react(node, output, state):
        list_wrapper = "ol" if node["ordered"] else "ul"

        return react_element(
            list_wrapper,
            state["key"],
            {
                "start": node["start"],
                "children": [react_element("li", str(index), {"children": output(item, state)}) for index, item in enumerate(node["items"])],
            },
        )

    @staticmethod
    def html(node, output, state):
        list_items = "".join([html_tag("li", output(item, state)) for item in node["items"]])

        list_tag = "ol" if node["ordered"] else "ul"
        attributes = {"start": node["start"]}
        return html_tag(list_tag, list_items, attributes)


class Def(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return block_regex(r'^ *\[([^\]]+)\]: *<?([^\s>]*)>?(?: +["(]([^\n]+)[")])? *\n(?: *\n)*')(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        _def = re.sub(r"\s+", " ", capture[1]).lower()
        target = capture[2]
        title = capture[3]

        if state.get("_refs") and state["_refs"].get(_def):
            for ref_node in state["_refs"][_def]:
                ref_node["target"] = target
                ref_node["title"] = title

        state["_defs"] = state.get("_defs", {})
        state["_defs"][_def] = {"target": target, "title": title}

        return {"def": _def, "target": target, "title": title}

    @staticmethod
    def react(*args, **kwargs):
        return

    @staticmethod
    def html(*args, **kwargs):
        return ""


class Table(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return block_regex(do_tables()["TABLE_REGEX"])(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return do_tables()["parse_table"](capture, parse, state)

    @staticmethod
    def react(node, output, state):
        def get_style(column_index):
            return {"textAlign": node["align"][column_index]} if node["align"][column_index] else {}

        headers = [
            react_element("th", str(index), {"style": get_style(index), "scope": "col", "children": output(content, state)})
            for index, content in enumerate(node["header"])
        ]

        rows = [
            react_element(
                "tr",
                str(row_index),
                {
                    "children": [
                        react_element("td", str(column_index), {"style": get_style(column_index), "children": output(content, state)})
                        for column_index, content in enumerate(row)
                    ],
                },
            )
            for row_index, row in enumerate(node["cells"])
        ]

        return react_element(
            "table",
            state["key"],
            {
                "children": [
                    react_element("thead", "thead", {"children": react_element("tr", None, {"children": headers})}),
                    react_element("tbody", "tbody", {"children": rows}),
                ],
            },
        )

    @staticmethod
    def html(node, output, state):
        def get_style(column_index):
            return "text-align:" + node["align"][column_index] + ";" if node["align"][column_index] else ""

        headers = "".join([html_tag("th", output(content, state), {"style": get_style(index), "scope": "col"}) for index, content in enumerate(node["header"])])

        rows = "".join(
            [
                html_tag(
                    "tr",
                    "".join([html_tag("td", output(content, state), {"style": get_style(column_index)}) for column_index, content in enumerate(row)]),
                )
                for row in node["cells"]
            ],
        )

        thead = html_tag("thead", html_tag("tr", headers))
        tbody = html_tag("tbody", rows)

        return html_tag("table", thead + tbody)


class NewLine(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return block_regex(r"^(?:\n *)*\n")(*args, **kwargs)

    @staticmethod
    def parse(*args, **kwargs):
        return ignore_capture()

    @staticmethod
    def react(*args, **kwargs):
        return "\n"

    @staticmethod
    def html(*args, **kwargs):
        return "\n"


class Paragraph(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return block_regex(r"^((?:[^\n]|\n(?! *\n))+)(?:\n *)+\n")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return parse_capture_inline(capture, parse, state)

    @staticmethod
    def react(node, output, state):
        return react_element("div", state["key"], {"className": "paragraph", "children": output(node["content"], state)})

    @staticmethod
    def html(node, output, state):
        attributes = {"class": "paragraph"}
        return html_tag("div", output(node["content"], state), attributes)


class Escape(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return inline_regex(r"^\\([^0-9A-Za-z\s])")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"type": "text", "content": capture[1]}


class TableSeparator(Rule):
    @staticmethod
    def match(source, state, *args, **kwargs):
        if not state.get("in_table"):
            return

        return re.search(r"^ *\| *", source)

    @staticmethod
    def parse(*args, **kwargs):
        return {"type": "table_separator"}

    @staticmethod
    def react(*args, **kwargs):
        return " | "

    @staticmethod
    def html(*args, **kwargs):
        return " &vert; "


class AutoLink(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return inline_regex(r"^<([^: >]+:\/[^ >]+)>")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"type": "link", "content": [{"type": "text", "content": capture[1]}], "target": capture[1]}


class MailTo(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return inline_regex(r"^<([^ >]+@[^ >]+)>")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        address = capture[1]
        target = capture[1]

        if not AUTOLINK_MAILTO_CHECK_R.search(target):
            target = f"mailto:{target}"

        return {"type": "link", "content": [{"type": "text", "content": address}], "target": target}


class URL(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return inline_regex(r'^(https?:\/\/[^\s<]+[^<.,:;"\')\]\s])')(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"type": "link", "content": [{"type": "text", "content": capture[1]}], "target": capture[1], "title": None}


class Link(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return inline_regex("^\\[(" + LINK_INSIDE + ")\\]\\(" + LINK_HREF_AND_TITLE + "\\)")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"content": parse(capture[1], state), "target": unescape_url(capture[2]), "title": capture[3]}

    @staticmethod
    def react(node, output, state):
        return react_element("a", state["key"], {"href": sanitize_text(node["target"]), "title": node["title"], "children": output(node["content"], state)})

    @staticmethod
    def html(node, output, state):
        attributes = {"href": sanitize_text(node.get("target")), "title": node.get("title")}

        return html_tag("a", output(node["content"], state), attributes)


class Image(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return inline_regex("^!\\[(" + LINK_INSIDE + ")\\]\\(" + LINK_HREF_AND_TITLE + "\\)")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"alt": capture[1], "target": unescape_url(capture[2]), "title": capture[3]}

    @staticmethod
    def react(node, output, state):
        return react_element("img", state["key"], {"src": sanitize_text(node["target"]), "alt": node["alt"], "title": node["title"]})

    @staticmethod
    def html(node, output, state):
        attributes = {"src": sanitize_text(node["target"]), "alt": node["alt"], "title": node["title"]}

        return html_tag("img", "", attributes, False)


class RefLink(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return inline_regex("^\\[(" + LINK_INSIDE + ")\\]" + "\\s*\\[([^\\]]*)\\]")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return parse_ref(capture, state, {"type": "link", "content": parse(capture[1], state)})


class RefImage(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return inline_regex("^!\\[(" + LINK_INSIDE + ")\\]" + "\\s*\\[([^\\]]*)\\]")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return parse_ref(capture, state, {"type": "image", "alt": capture[1]})


class Em(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return inline_regex(
            "^\\b_((?:__|\\\\[\\s\\S]|[^\\\\_])+?)_\\b|^\\*(?=\\S)((?:\\*\\*|\\\\[\\s\\S]|\\s+(?:\\\\[\\s\\S]|[^\\s\\*\\\\]|\\*\\*)|[^\\s\\*\\\\])+?)\\*(?!\\*)",
        )(*args, **kwargs)

    @staticmethod
    def quality(capture, *args, **kwargs):
        return len(capture[0]) + 0.2

    @staticmethod
    def parse(capture, parse, state):
        return {"content": parse(capture[2] or capture[1], state)}

    @staticmethod
    def react(node, output, state):
        return react_element("em", state["key"], {"children": output(node["content"], state)})

    @staticmethod
    def html(node, output, state):
        return html_tag("em", output(node["content"], state))


class Strong(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return inline_regex(r"^\*\*((?:\\[\s\S]|[^\\])+?)\*\*(?!\*)")(*args, **kwargs)

    @staticmethod
    def quality(capture, *args, **kwargs):
        return len(capture[0]) + 0.1

    @staticmethod
    def parse(capture, parse, state):
        return parse_capture_inline(capture, parse, state)

    @staticmethod
    def react(node, output, state):
        return react_element("strong", state["key"], {"children": output(node["content"], state)})

    @staticmethod
    def html(node, output, state):
        return html_tag("strong", output(node["content"], state))


class U(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return inline_regex(r"^__((?:\\[\s\S]|[^\\])+?)__(?!_)")(*args, **kwargs)

    @staticmethod
    def quality(capture, *args, **kwargs):
        return len(capture[0])

    @staticmethod
    def parse(capture, parse, state):
        return parse_capture_inline(capture, parse, state)

    @staticmethod
    def react(node, output, state):
        return react_element("u", state["key"], {"children": output(node["content"], state)})

    @staticmethod
    def html(node, output, state):
        return html_tag("u", output(node["content"], state))


class Del(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return inline_regex(r"^~~(?=\S)((?:\\[\s\S]|~(?!~)|[^\s~\\]|\s(?!~~))+?)~~")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return parse_capture_inline(capture, parse, state)

    @staticmethod
    def react(node, output, state):
        return react_element("del", state["key"], {"children": output(node["content"], state)})

    @staticmethod
    def html(node, output, state):
        return html_tag("del", output(node["content"], state))


class InlineCode(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return inline_regex(r"^(`+)([\s\S]*?[^`])\1(?!`)")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"content": INLINE_CODE_ESCAPE_BACKTICKS_R.sub(r"\1", capture[2])}

    @staticmethod
    def react(node, output, state):
        return react_element("code", state["key"], {"children": node["content"]})

    @staticmethod
    def html(node, output, state):
        return html_tag("code", sanitize_text(node["content"]))


class Br(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return any_scope_regex(r"^ {2,}\n")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return ignore_capture()

    @staticmethod
    def react(node, output, state):
        return react_element("br", state["key"], EMPTY_PROPS)

    @staticmethod
    def html(*args, **kwargs):
        return "<br>"


class Text(Rule):
    @staticmethod
    def match(*args, **kwargs):
        return any_scope_regex(r"^[\s\S]+?(?=[^0-9A-Za-z\s\u00c0-\uffff]|\n\n| {2,}\n|\w+:\S|$)")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"content": capture[0]}

    @staticmethod
    def react(node, output, state):
        return node["content"]

    @staticmethod
    def html(node, output, state):
        return sanitize_text(node["content"])


default_rules = {
    "Array": Array(None),
    "heading": Heading((current_order := current_order + 1) - 1),
    "np_table": NpTable((current_order := current_order + 1) - 1),
    "l_heading": LHeading((current_order := current_order + 1) - 1),
    "hr": HR((current_order := current_order + 1) - 1),
    "code_block": CodeBlock((current_order := current_order + 1) - 1),
    "fence": Fence((current_order := current_order + 1) - 1),
    "block_quote": BlockQuote((current_order := current_order + 1) - 1),
    "list": List((current_order := current_order + 1) - 1),
    "def": Def((current_order := current_order + 1) - 1),
    "table": Table((current_order := current_order + 1) - 1),
    "newline": NewLine((current_order := current_order + 1) - 1),
    "paragraph": Paragraph((current_order := current_order + 1) - 1),
    "escape": Escape((current_order := current_order + 1) - 1),
    "table_separator": TableSeparator((current_order := current_order + 1) - 1),
    "autolink": AutoLink((current_order := current_order + 1) - 1),
    "mailto": MailTo((current_order := current_order + 1) - 1),
    "url": URL((current_order := current_order + 1) - 1),
    "link": Link((current_order := current_order + 1) - 1),
    "image": Image((current_order := current_order + 1) - 1),
    "ref_link": RefLink((current_order := current_order + 1) - 1),
    "ref_image": RefImage((current_order := current_order + 1) - 1),
    "em": Em(current_order),
    "strong": Strong(current_order),
    "u": U((current_order := current_order + 1) - 1),
    "del": Del((current_order := current_order + 1) - 1),
    "inline_code": InlineCode((current_order := current_order + 1) - 1),
    "br": Br((current_order := current_order + 1) - 1),
    "text": Text((current_order := current_order + 1) - 1),
}

default_classes = {key: item.__class__ for key, item in default_rules.items()}


def rule_output(rules: dict, property_: str):
    def nested_rule_output(ast, output_func, state):
        return getattr(rules[ast["type"]], property_)(ast, output_func, state)

    return nested_rule_output


def react_for(output_func):
    def nested_output(ast: Union[list, str], state: dict = None):
        state = state or {}
        if isinstance(ast, list):
            old_key = state["key"]
            result = []

            last_result = None
            for index, item in enumerate(ast):
                state["key"] = index
                node_out = nested_output(ast[index], state)
                if isinstance(node_out, str) and isinstance(last_result, str):
                    last_result += node_out
                    result[-1] = last_result
                else:
                    result.append(node_out)
                    last_result = node_out

            state["key"] = old_key
            return result
        else:
            return output_func(ast, nested_output, state)

    return nested_output


def html_for(output_func):
    def nested_output(ast, state=None):
        state = state or {}
        if isinstance(ast, list):
            return "".join([nested_output(node, state) for node in ast])
        else:
            return output_func(ast, nested_output, state)

    return nested_output


def output_for(rules: dict, property_: str, default_state: dict = None):
    default_state = default_state or {}
    latest_state = None
    array_rule = rules.get("Array") or default_rules["Array"]

    array_rule_check = getattr(array_rule, property_)
    if not array_rule_check:
        raise Exception(
            "simple-markdown: outputFor: to join nodes of type `"
            + property_
            + "` you must provide an `Array:` joiner rule with that type, "
            + "Please see the docs for details on specifying an Array rule.",
        )
    array_rule_output = array_rule_check

    def nested_output(ast, state):
        nonlocal latest_state
        state = state or latest_state
        latest_state = state
        if isinstance(ast, list):
            return array_rule_output(ast, nested_output, state)
        else:
            return getattr(rules[ast["type"]], property_)(ast, nested_output, state)

    def outer_output(ast, state=None):
        state = state or {}
        nonlocal latest_state
        latest_state = populate_initial_state(state, default_state)
        return nested_output(ast, latest_state)

    return outer_output


default_raw_parse = parser_for(default_rules)


def default_block_parse(source: str, state: dict = None):
    state = state or {}
    state["inline"] = False
    return default_raw_parse(source, state)


def default_inline_parse(source: str, state: dict = None):
    state = state or {}
    state["inline"] = True
    return default_raw_parse(source, state)


def default_implicit_parse(source: str, state: dict = None):
    state = state or {}
    is_block = BLOCK_END_R.search(source)
    state["inline"] = not is_block
    return default_raw_parse(source, state)


default_react_output = output_for(default_rules, "react")
default_html_output = output_for(default_rules, "html")


def markdown_to_react(source: str, state: dict = None):
    state = state or {}
    return default_react_output(default_block_parse(source, state), state)


def markdown_to_html(source: str, state: dict = None):
    state = state or {}
    return default_html_output(default_block_parse(source, state), state)


def ReactMarkdown(props):
    div_props = {prop: props[prop] for prop in props if prop != "source" and props.get(prop)}
    div_props["children"] = markdown_to_react(props["source"])

    return react_element("div", None, div_props)

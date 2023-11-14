import re
from typing import Callable

import pygments
from pygments.formatters.html import HtmlFormatter
from pygments.lexers import get_lexer_by_name

import extendedmodlog.simpy_markdown as md


def html_tag(tag_name: str, content: str, attributes: dict = None, is_closed: bool = True, state: dict = None) -> str:
    attributes = attributes or {}
    if isinstance(is_closed, dict):
        state = is_closed
        is_closed = True
    state = state or {}

    if attributes.get("class") and state.get("css_module_names"):
        attributes["class"] = " ".join(state["css_module_names"].get(cl, cl) for cl in attributes["class"])

    attr_string = " ".join(f'{md.sanitize_text(attr)}="{md.sanitize_text(attributes[attr])}"' for attr in attributes if attributes[attr])

    unclosed_tag = f'<{tag_name}{ " " + attr_string if attr_string else ""}>'

    return unclosed_tag + content + f"</{tag_name}>" if is_closed else unclosed_tag


md.html_tag = html_tag


class BlockQuote(md.default_classes["block_quote"]):
    @staticmethod
    def match(source, state, previous_source, *args, **kwargs):
        return (
            None
            if (not re.search(r"^$|\n *$", previous_source) or state.get("in_quote"))
            else md.any_scope_regex(r"^( *>>> ([\s\S]*))|^( *> [^\n]*(\n *> [^\n]*)*\n?)")(source, state)
        )

    @staticmethod
    def parse(capture, parse, state):
        all_ = capture[0]
        is_block = re.search(r"^ *>>> ?", all_)
        remove_syntax_regex = r"^ *>>> ?" if is_block else r"^ *> ?"
        _regex_settings = {"count": 1} if is_block else {"flags": re.MULTILINE, "count": 0}
        content = re.sub(remove_syntax_regex, "", all_, **_regex_settings)

        return {"content": parse(content, {**state, "in_quote": True}), "type": "block_quote"}


class CodeBlock(md.default_classes["code_block"]):
    @staticmethod
    def match(*args, **kwargs):
        return md.inline_regex(r"^```(([a-z0-9-]+?)\n+)?\n*([\S\s]+?)\n*```", flags=re.IGNORECASE)(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"lang": (capture[2] or "").strip(), "content": capture[3] or "", "in_quote": state.get("in_quote")}

    @staticmethod
    def html(node, output, state):
        code = None
        if node["lang"] and (lexer := get_lexer_by_name(node["lang"])):
            code = pygments.highlight(node["content"], lexer, HtmlFormatter())

        if code and state.get("css_module_names"):
            code = re.sub(
                r'<span class="([a-z0-9-_ ]+)">',
                lambda m: m[0].replace(m[0], " ".join(state["css_module_names"].get(cl, cl) for cl in m[1].split())),
                code,
            )

        return html_tag(
            "pre",
            html_tag("code", code or md.sanitize_text(node["content"]), {"class": f'hljs{" " + node["lang"] if code else ""}'}, state),
            None,
            state,
        )


class AutoLink(md.default_classes["autolink"]):
    @staticmethod
    def parse(capture, parse, state):
        return {"content": [{"type": "text", "content": capture[1]}], "target": capture[1]}

    @staticmethod
    def html(node, output, state):
        return html_tag("a", output(node["content"], state), {"href": md.sanitize_url(node["target"])}, state)


class URL(md.default_classes["url"]):
    @staticmethod
    def parse(capture, parse, state):
        return {"content": [{"type": "text", "content": capture[1]}], "target": capture[1]}

    @staticmethod
    def html(node, output, state):
        return html_tag("a", output(node["content"], state), {"href": md.sanitize_url(node["target"])}, state)


class Em(md.default_classes["em"]):
    def parse(self, capture, parse, state):
        _state = state.copy()
        _state["in_emphasis"] = True
        parsed = super().parse(capture, parse, _state)
        return parsed["content"] if state.get("in_emphasis") else parsed


class Strike(md.default_classes["del"]):
    @staticmethod
    def match(*args, **kwargs):
        return md.inline_regex(r"^~~([\s\S]+?)~~(?!_)")(*args, **kwargs)


class InlineCode(md.default_classes["inline_code"]):
    @staticmethod
    def match(*args, **kwargs):
        return md.any_scope_regex(r"^(`+)([\s\S]*?[^`])\1(?!`)")(*args, **kwargs)

    @staticmethod
    def html(node, output, state):
        return html_tag("code", md.sanitize_text(node["content"].strip()), None, state)


class Text(md.default_classes["text"]):
    @staticmethod
    def match(*args, **kwargs):
        return md.any_scope_regex(r"^[\s\S]+?(?=[^0-9A-Za-z\s\u00c0-\uffff-]|\n\n|\n|\w+:\S|$)")(*args, **kwargs)

    @staticmethod
    def html(node, output, state):
        if state.get("escape_html"):
            return md.sanitize_text(node["content"])

        return node["content"]


class Emoticon(md.Rule):
    @staticmethod
    def match(*args, **kwargs):
        return md.any_scope_regex(r"^(¯\\_\(ツ\)_/¯)")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"type": "text", "content": capture[1]}

    @staticmethod
    def html(node, output, state):
        return output(node["content"], state)


class Br(md.default_classes["br"]):
    @staticmethod
    def match(*args, **kwargs):
        return md.any_scope_regex(r"^\n")(*args, *kwargs)


class Spoiler(md.Rule):
    @staticmethod
    def match(*args, **kwargs):
        return md.any_scope_regex(r"^\|\|([\s\S]+?)\|\|")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"content": parse(capture[1], state)}

    @staticmethod
    def html(node, output, state):
        return html_tag("span", output(node["content"], state), {"class": "d-spoiler"}, state)


rules = {
    "block_quote": BlockQuote(md.default_rules["block_quote"].order),
    "code_block": CodeBlock(md.default_rules["code_block"].order),
    "newline": md.default_rules["newline"],
    "escape": md.default_rules["escape"],
    "autolink": AutoLink(md.default_rules["autolink"].order),
    "url": URL(md.default_rules["url"].order),
    "em": Em(md.default_rules["em"].order),
    "strong": md.default_rules["strong"],
    "u": md.default_rules["u"],
    "strike": Strike(md.default_rules["del"].order),
    "inline_code": InlineCode(md.default_rules["inline_code"].order),
    "text": Text(md.default_rules["text"].order),
    "emoticon": Emoticon(md.default_rules["text"].order),
    "br": Br(md.default_rules["br"].order),
    "spoiler": Spoiler(0),
}
classes = {key: item.__class__ for key, item in rules.items()}

discord_callback_defaults = {
    "user": lambda node: "@" + md.sanitize_text(node["id"]),
    "channel": lambda node: "#" + md.sanitize_text(node["id"]),
    "role": lambda node: "&" + md.sanitize_text(node["id"]),
    "everyone": lambda node: "@everyone",
    "here": lambda node: "@here",
}


class DiscordUser(md.Rule):
    @staticmethod
    def match(*args, **kwargs):
        return md.any_scope_regex(r"^<@!?([0-9]*)>")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"id": capture[1]}

    @staticmethod
    def html(node, output, state):
        return html_tag("span", state["discord_callback"]["user"](node), {"class": "d-mention d-user"}, state)


class DiscordChannel(md.Rule):
    @staticmethod
    def match(*args, **kwargs):
        return md.any_scope_regex(r"^<#?([0-9]*)>")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"id": capture[1]}

    @staticmethod
    def html(node, output, state):
        return html_tag("span", state["discord_callback"]["channel"](node), {"class": "d-mention d-channel"}, state)


class DiscordRole(md.Rule):
    @staticmethod
    def match(*args, **kwargs):
        return md.any_scope_regex(r"^<@&([0-9]*)>")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"id": capture[1]}

    @staticmethod
    def html(node, output, state):
        return html_tag("span", state["discord_callback"]["role"](node), {"class": "d-mention d-role"}, state)


class DiscordEmoji(md.Rule):
    @staticmethod
    def match(*args, **kwargs):
        return md.any_scope_regex(r"^<(a?):(\w+):(\d+)>")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {"animated": capture[1] == "a", "name": capture[2], "id": capture[3]}

    @staticmethod
    def html(node, output, state):
        return html_tag(
            "img",
            "",
            {
                "class": f'd-emoji{ " d-emoji-animated" if node["animated"] else ""}',
                "src": f'https://cdn.discordapp.com/emojis/{node["id"]}.{"gif" if node["animated"] else "png"}',
                "alt": f':{node["name"]}:',
            },
            False,
            state,
        )


class DiscordEveryone(md.Rule):
    @staticmethod
    def match(*args, **kwargs):
        return md.any_scope_regex(r"^@everyone")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {}

    @staticmethod
    def html(node, output, state):
        return html_tag("span", state["discord_callback"]["everyone"](node), {"class": "d-mention d-user"}, state)


class DiscordHere(md.Rule):
    @staticmethod
    def match(*args, **kwargs):
        return md.any_scope_regex(r"^@here")(*args, **kwargs)

    @staticmethod
    def parse(capture, parse, state):
        return {}

    @staticmethod
    def html(node, output, state):
        return html_tag("span", state["discord_callback"]["here"](node), {"class": "d-mention d-user"}, state)


rules_discord = {
    "discord_user": DiscordUser(md.default_rules["strong"].order),
    "discord_channel": DiscordChannel(md.default_rules["strong"].order),
    "discord_role": DiscordRole(md.default_rules["strong"].order),
    "discord_emoji": DiscordEmoji(md.default_rules["strong"].order),
    "discord_everyone": DiscordEveryone(md.default_rules["strong"].order),
    "discord_here": DiscordHere(md.default_rules["strong"].order),
}
classes_discord = {key: item.__class__ for key, item in rules_discord.items()}

rules.update(rules_discord)
classes.update(classes_discord)

rules_discord_only = {**rules_discord, "text": Text(md.default_rules["text"].order)}
classes_discord_only = {key: item.__class__ for key, item in rules_discord_only.items()}

rules_embed = {**rules, "link": md.default_rules["link"]}
classes_embed = {key: item.__class__ for key, item in rules_embed.items()}

parser = md.parser_for(rules)
html_output = md.output_for(rules, "html")
parser_discord = md.parser_for(rules_discord_only)
html_output_discord = md.output_for(rules_discord_only, "html")
parser_embed = md.parser_for(rules_embed)
html_output_embed = md.output_for(rules_embed, "html")


def to_html(source: str, options: dict = None, custom_parser: Callable = None, custom_html_output: Callable = None):
    if (custom_parser or custom_html_output) and (not custom_parser or not custom_html_output):
        msg = "You must pass both a custom parser and custom htmlOutput function, not just one!"
        raise Exception(msg)

    options = {"embed": False, "escape_html": True, "discord_only": False, "discord_callback": {}, **(options or {})}

    _parser = parser
    _html_output = html_output
    if custom_parser:
        _parser = custom_parser
        _html_output = custom_html_output
    elif options["discord_only"]:
        _parser = parser_discord
        _html_output = html_output_discord
    elif options["embed"]:
        _parser = parser_embed
        _html_output = html_output_embed

    state = {
        "inline": True,
        "in_quote": False,
        "in_emphasis": False,
        "escape_html": options["escape_html"],
        "css_module_names": options.get("css_module_names") or None,
        "discord_callback": {**discord_callback_defaults, **options["discord_callback"]},
    }

    return _html_output(_parser(source, state), state)

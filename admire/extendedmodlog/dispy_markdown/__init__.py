from .core import classes, classes_discord_only, classes_embed, html_output, html_tag, rules, rules_discord_only, rules_embed, to_html
from .core import md as markdown
from .core import parser as _parser


def parser(source):
    return _parser(source, {"inline": True})

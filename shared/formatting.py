"""Safe Markdown formatting; never alter or append to the model's answer."""

from markdown_it import MarkdownIt


def markdown_html(text):
    return MarkdownIt("commonmark", {"html": False, "breaks": True}).enable("table").render(text)

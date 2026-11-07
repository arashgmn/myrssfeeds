#!/usr/bin/env python3
"""Boolean keyword filters for scraped feed entries.

Expression syntax (case-insensitive):

    postdoc OR phd
    (postdoc OR phd) AND NOT clinical
    "group leader" AND NOT (administration OR accounting)

Operators: AND, OR, NOT; parentheses group. Bare words and quoted phrases
match case-insensitive substrings. An empty expression keeps every entry.

A YAML entry may use an inline expression or a name from SUITES.
"""

SUITES = {
    # Reusable example; extend as needed.
    "research_jobs": (
        'postdoc OR phd OR researcher OR scientist OR "group leader" OR professor'
    ),
}


def _tokenize(expr):
    tokens = []
    i = 0
    n = len(expr)
    while i < n:
        c = expr[i]
        if c.isspace():
            i += 1
            continue
        if c == "(":
            tokens.append(("LP", None))
            i += 1
            continue
        if c == ")":
            tokens.append(("RP", None))
            i += 1
            continue
        if c in "\"'":
            j = expr.find(c, i + 1)
            if j < 0:
                raise ValueError("unterminated quoted phrase")
            tokens.append(("WORD", expr[i + 1:j]))
            i = j + 1
            continue
        j = i
        while j < n and not expr[j].isspace() and expr[j] not in "()":
            j += 1
        word = expr[i:j]
        upper = word.upper()
        if upper in ("AND", "OR", "NOT"):
            tokens.append((upper, None))
        else:
            tokens.append(("WORD", word))
        i = j
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.i = 0

    def peek(self):
        return self.tokens[self.i][0] if self.i < len(self.tokens) else None

    def take(self):
        token = self.tokens[self.i]
        self.i += 1
        return token

    def parse(self):
        node = self.or_expr()
        if self.i != len(self.tokens):
            raise ValueError(f"unexpected token {self.peek()!r}")
        return node

    def or_expr(self):
        node = self.and_expr()
        while self.peek() == "OR":
            self.take()
            node = ("OR", node, self.and_expr())
        return node

    def and_expr(self):
        node = self.not_expr()
        while self.peek() == "AND":
            self.take()
            node = ("AND", node, self.not_expr())
        return node

    def not_expr(self):
        if self.peek() == "NOT":
            self.take()
            return ("NOT", self.not_expr())
        return self.atom()

    def atom(self):
        token = self.peek()
        if token == "LP":
            self.take()
            node = self.or_expr()
            if self.peek() != "RP":
                raise ValueError("missing closing parenthesis")
            self.take()
            return node
        if token == "WORD":
            return ("WORD", self.take()[1])
        if token is None:
            raise ValueError("unexpected end of expression")
        raise ValueError(f"unexpected token {token!r}")


def _matches(node, text):
    op = node[0]
    if op == "WORD":
        return node[1].lower() in text
    if op == "AND":
        return _matches(node[1], text) and _matches(node[2], text)
    if op == "OR":
        return _matches(node[1], text) or _matches(node[2], text)
    if op == "NOT":
        return not _matches(node[1], text)
    raise ValueError(f"unknown node {op!r}")


def compile_filter(expression):
    """Return a predicate(text) for an expression or suite name."""
    expression = (expression or "").strip()
    if not expression:
        return lambda text: True
    expression = SUITES.get(expression, expression)
    tree = _Parser(_tokenize(expression)).parse()

    def predicate(text):
        return _matches(tree, (text or "").lower())

    return predicate


if __name__ == "__main__":
    f = compile_filter("postdoc OR phd")
    assert f("Postdoctoral Researcher")
    assert f("PhD Student")
    assert not f("System Administrator")

    f = compile_filter("(postdoc OR phd) AND NOT clinical")
    assert f("PhD in neuroscience")
    assert not f("PhD in clinical neuroscience")
    assert not f("Research assistant")

    assert compile_filter("")("anything")
    assert compile_filter("NOT phd")("postdoc")
    assert compile_filter('"group leader"')("Independent Group Leader")
    assert compile_filter("research_jobs")("Scientist/Engineer")
    print("filters.py self-check OK")
